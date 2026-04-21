"""Unit tests for ``core/config.py`` — the YAML config loader.

Covers dataclass shape contracts, happy paths, missing-file defaults,
malformed-file handling (hard error for singletons, skip for modes),
mode-filter edge cases, validation, UTF-8 BOM tolerance, and the
api-key-never-logged regression gate.

Every test constructs its own ``tmp_path``-rooted ``data_dir``; no shared
fixtures; never touches ``%LOCALAPPDATA%``.
"""

from __future__ import annotations

import dataclasses
import logging
import sys
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from nova.core.config import (
    AppConfig,
    ExcludedAppConfig,
    ExclusionConfig,
    ModeConfig,
    NovaConfig,
    UserSettings,
    _DuplicateKeyRejectingLoader,
    _is_valid_mode_stem,
    load_config,
)
from nova.core.exceptions import ConfigError
from nova.core.types import BluntnessLevel

# --- Helpers ----------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _minimal_mode_yaml(name: str = "coding") -> str:
    return f"name: {name}\napps:\n  - name: VS Code\n    executable: code\n"


def _bootstrap_data_dir(tmp_path: Path) -> Path:
    """Create a minimal valid data_dir with one mode, exclusions, and settings."""
    data_dir = tmp_path / "nova"
    (data_dir / "modes").mkdir(parents=True)
    _write(data_dir / "modes" / "coding.yaml", _minimal_mode_yaml())
    _write(data_dir / "exclusions.yaml", "excluded_apps: []\nexcluded_title_patterns: []\n")
    _write(data_dir / "settings.yaml", "bluntness: direct\n")
    return data_dir


# --- Dataclass shape tests --------------------------------------------------


def test_all_classes_are_dataclasses_with_slots() -> None:
    """Every config class is a dataclass AND uses ``slots=True``.

    Frozen-ness is locked separately by
    :func:`test_frozen_instance_mutation_raises`.
    """
    classes = (
        NovaConfig,
        ModeConfig,
        AppConfig,
        ExclusionConfig,
        ExcludedAppConfig,
        UserSettings,
    )
    for cls in classes:
        assert dataclasses.is_dataclass(cls), f"{cls.__name__} is not a dataclass"
        # ``slots=True`` generates ``__slots__`` on the class — absence here
        # would mean a ``__dict__`` slipped in.
        assert hasattr(cls, "__slots__"), f"{cls.__name__} missing __slots__"


def test_frozen_instance_mutation_raises() -> None:
    app = AppConfig(name="X", executable="x")
    with pytest.raises(FrozenInstanceError):
        app.name = "Y"  # type: ignore[misc]


def test_collection_fields_are_tuples() -> None:
    app = AppConfig(name="X", executable="x", args=("--flag",))
    assert isinstance(app.args, tuple)
    mode = ModeConfig(name="m", apps=(app,))
    assert isinstance(mode.apps, tuple)
    assert isinstance(mode.folders, tuple)
    assert isinstance(mode.urls, tuple)
    exc = ExclusionConfig()
    assert isinstance(exc.excluded_apps, tuple)
    assert isinstance(exc.excluded_title_patterns, tuple)


def test_user_settings_field_set_is_exact() -> None:
    """`UserSettings` must NOT carry ``api_key`` or ``telemetry_opt_in``."""
    names = {f.name for f in fields(UserSettings)}
    assert names == {"bluntness", "skip_briefing_if_recent", "briefing_recency_threshold_minutes"}


def test_nova_config_field_order_and_types() -> None:
    names = [f.name for f in fields(NovaConfig)]
    assert names == ["db_path", "data_dir", "modes", "exclusions", "settings", "api_key"]


# --- Happy path tests -------------------------------------------------------


def test_load_config_happy_path(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    config = load_config(data_dir)
    assert config.data_dir == data_dir
    assert config.db_path == data_dir / "nova.db"
    assert set(config.modes) == {"coding"}
    assert config.modes["coding"].name == "coding"
    assert config.settings.bluntness is BluntnessLevel.DIRECT
    assert config.api_key is None


def test_load_config_two_modes_keyed_by_stem(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "modes" / "study.yaml", _minimal_mode_yaml(name="Study Session"))
    config = load_config(data_dir)
    assert set(config.modes) == {"coding", "study"}
    # The dict key is the stem, NOT the display name.
    assert config.modes["study"].name == "Study Session"
    assert "Study Session" not in config.modes


def test_api_key_promoted_out_of_settings(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "settings.yaml", 'api_key: "sk-ant-abc123"\nbluntness: calm\n')
    config = load_config(data_dir)
    assert config.api_key == "sk-ant-abc123"
    # Settings dataclass does not expose the key.
    assert not hasattr(config.settings, "api_key")


def test_app_executable_stored_verbatim(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "modes" / "coding.yaml",
        "name: coding\napps:\n  - name: VS Code\n    executable: Code.EXE\n",
    )
    config = load_config(data_dir)
    # No normalization at load time — Hands/Eyes handle case/`.exe` at match time.
    assert config.modes["coding"].apps[0].executable == "Code.EXE"


# --- Missing-file tests -----------------------------------------------------


def test_missing_data_dir_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="data directory missing"):
        load_config(tmp_path / "does-not-exist")


def test_data_dir_path_is_file_raises_config_error(tmp_path: Path) -> None:
    fake = tmp_path / "not-a-dir"
    fake.write_text("hi", encoding="utf-8")
    with pytest.raises(ConfigError, match="data directory path is not a directory"):
        load_config(fake)


def test_missing_settings_yaml_applies_defaults(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    (data_dir / "settings.yaml").unlink()
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert config.settings.bluntness is BluntnessLevel.DIRECT
    assert config.settings.skip_briefing_if_recent is True
    assert config.settings.briefing_recency_threshold_minutes == 60
    assert config.api_key is None
    assert any("settings.yaml not found" in r.getMessage() for r in caplog.records)


def test_missing_exclusions_yaml_warns_and_defaults(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    (data_dir / "exclusions.yaml").unlink()
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert config.exclusions.excluded_apps == ()
    assert config.exclusions.excluded_title_patterns == ()
    assert any("exclusions.yaml not found" in r.getMessage() for r in caplog.records)


def test_missing_modes_dir_warns_and_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = tmp_path / "nova"
    data_dir.mkdir()
    # No modes/, no exclusions.yaml, no settings.yaml.
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert config.modes == {}
    assert any("modes/ directory missing" in r.getMessage() for r in caplog.records)


def test_empty_modes_dir_warns_and_empty(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """modes/ exists but contains zero files → warn + empty modes dict."""
    data_dir = tmp_path / "nova"
    (data_dir / "modes").mkdir(parents=True)
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert config.modes == {}
    assert any("zero loadable modes" in r.getMessage() for r in caplog.records), (
        "expected zero-loadable-modes warning when modes/ exists but is empty"
    )


def test_modes_dir_with_only_invalid_files_warns_and_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """modes/ exists with files but every one is skipped → zero-loadable warning fires."""
    data_dir = tmp_path / "nova"
    (data_dir / "modes").mkdir(parents=True)
    # Three files, all unloadable: wrong extension, hidden, and a valid-
    # looking .yaml that fails top-level name validation.
    _write(data_dir / "modes" / "wrong.yml", "name: x\napps:\n  - name: a\n    executable: b\n")
    _write(data_dir / "modes" / ".hidden.yaml", "name: y\napps:\n  - name: a\n    executable: b\n")
    _write(
        data_dir / "modes" / "broken.yaml",
        "apps:\n  - name: x\n    executable: y\n",  # missing top-level name → skipped
    )
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert config.modes == {}
    assert any("zero loadable modes" in r.getMessage() for r in caplog.records)


def test_empty_settings_yaml_applies_defaults(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "settings.yaml", "# only a comment\n\n")
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert config.settings.bluntness is BluntnessLevel.DIRECT
    assert any("is empty" in r.getMessage() for r in caplog.records)


# --- Malformed-file tests ---------------------------------------------------


def test_settings_parse_error_raises(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "settings.yaml", "bluntness: [unclosed\n")
    with pytest.raises(ConfigError, match="malformed config: parse error"):
        load_config(data_dir)


def test_exclusions_parse_error_raises(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "exclusions.yaml", ": : :\n  bad: [\n")
    with pytest.raises(ConfigError, match="malformed config: parse error"):
        load_config(data_dir)


def test_settings_non_mapping_root_raises(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "settings.yaml", "- a\n- b\n")
    with pytest.raises(ConfigError, match="malformed config: non-mapping root"):
        load_config(data_dir)


def test_duplicate_keys_in_settings_raises(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "settings.yaml", "bluntness: direct\nbluntness: calm\n")
    with pytest.raises(ConfigError, match="malformed config: duplicate key"):
        load_config(data_dir)


def test_duplicate_keys_in_mode_file_skips_mode(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "modes" / "study.yaml",
        "name: study\nname: duplicate\napps:\n  - name: x\n    executable: y\n",
    )
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert set(config.modes) == {"coding"}
    assert any("duplicate keys" in r.getMessage() for r in caplog.records)


def test_mode_parse_error_skipped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "modes" / "broken.yaml", "name: [unclosed\n")
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert "broken" not in config.modes
    assert "coding" in config.modes
    assert any("parse error" in r.getMessage() for r in caplog.records)


def test_mode_non_mapping_root_skipped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "modes" / "list-root.yaml", "- a\n- b\n")
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert "list-root" not in config.modes
    assert any("non-mapping root" in r.getMessage() for r in caplog.records)


def test_mode_missing_name_skipped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "modes" / "no-name.yaml",
        "apps:\n  - name: x\n    executable: y\n",
    )
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert "no-name" not in config.modes
    assert any("missing/empty name" in r.getMessage() for r in caplog.records)


def test_mode_zero_valid_apps_skipped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "modes" / "empty-apps.yaml",
        "name: empty-apps\napps:\n  - name: ''\n    executable: ''\n",
    )
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert "empty-apps" not in config.modes
    assert any("zero valid apps" in r.getMessage() for r in caplog.records)


# --- Mode-filter tests ------------------------------------------------------


def test_yml_extension_ignored(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "modes" / "other.yml", _minimal_mode_yaml(name="other"))
    config = load_config(data_dir)
    assert "other" not in config.modes
    assert set(config.modes) == {"coding"}


def test_bak_extension_ignored(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "modes" / "other.yaml.bak", _minimal_mode_yaml(name="other"))
    config = load_config(data_dir)
    assert "other" not in config.modes


def test_hidden_file_ignored(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "modes" / ".hidden.yaml", _minimal_mode_yaml(name="hidden"))
    config = load_config(data_dir)
    assert "hidden" not in config.modes
    assert ".hidden" not in config.modes


def test_mode_stem_with_dot_ignored(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "modes" / "foo.override.yaml", _minimal_mode_yaml(name="foo"))
    config = load_config(data_dir)
    # Stem contains a dot → silently skipped (editor byproducts shouldn't noise logs).
    assert "foo" not in config.modes
    assert "foo.override" not in config.modes


def test_is_valid_mode_stem_rejects_reserved_windows_names() -> None:
    for name in ("con", "CON", "Nul", "aux", "prn", "com1", "COM9", "lpt1", "LPT9"):
        assert not _is_valid_mode_stem(name), f"{name!r} should be rejected"


def test_is_valid_mode_stem_kebab_case_rules() -> None:
    assert _is_valid_mode_stem("coding")
    assert _is_valid_mode_stem("study-session")
    assert _is_valid_mode_stem("a1")
    assert not _is_valid_mode_stem("")
    assert not _is_valid_mode_stem("-leading")
    assert not _is_valid_mode_stem("UPPER")
    assert not _is_valid_mode_stem("has_underscore")
    assert not _is_valid_mode_stem("dot.inside")


def test_modes_path_is_file_raises_config_error(tmp_path: Path) -> None:
    data_dir = tmp_path / "nova"
    data_dir.mkdir()
    # modes/ is a file, not a directory.
    (data_dir / "modes").write_text("not a directory", encoding="utf-8")
    with pytest.raises(ConfigError, match="modes path is not a directory"):
        load_config(data_dir)


# --- Mode validation tests --------------------------------------------------


def test_folders_relative_path_dropped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "modes" / "coding.yaml",
        "name: coding\n"
        "apps:\n  - name: x\n    executable: y\n"
        "folders:\n  - relative/path\n  - C:/absolute/path\n",
    )
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    folders = config.modes["coding"].folders
    assert "relative/path" not in folders
    assert "C:/absolute/path" in folders
    assert any("non-absolute path" in r.getMessage() for r in caplog.records)


def test_urls_file_scheme_dropped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "modes" / "coding.yaml",
        "name: coding\n"
        "apps:\n  - name: x\n    executable: y\n"
        "urls:\n  - file:///etc/passwd\n  - javascript:alert(1)\n  - https://example.com\n",
    )
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    urls = config.modes["coding"].urls
    assert urls == ("https://example.com",)
    assert any("disallowed-scheme" in r.getMessage() for r in caplog.records)


def test_urls_http_and_https_kept(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "modes" / "coding.yaml",
        "name: coding\n"
        "apps:\n  - name: x\n    executable: y\n"
        "urls:\n  - http://a.example\n  - HTTPS://b.example\n",
    )
    config = load_config(data_dir)
    assert "http://a.example" in config.modes["coding"].urls
    assert "HTTPS://b.example" in config.modes["coding"].urls


def test_apps_args_non_list_substituted_empty(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "modes" / "coding.yaml",
        "name: coding\napps:\n  - name: x\n    executable: y\n    args: not-a-list\n",
    )
    config = load_config(data_dir)
    assert config.modes["coding"].apps[0].args == ()


def test_is_default_non_bool_falls_back_false(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "modes" / "coding.yaml",
        "name: coding\napps:\n  - name: x\n    executable: y\nis_default: 'yes'\n",
    )
    config = load_config(data_dir)
    assert config.modes["coding"].is_default is False


def test_multiple_is_default_logs_warning_and_preserves_both(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "modes" / "coding.yaml",
        "name: coding\napps:\n  - name: x\n    executable: y\nis_default: true\n",
    )
    _write(
        data_dir / "modes" / "study.yaml",
        "name: study\napps:\n  - name: x\n    executable: y\nis_default: true\n",
    )
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert config.modes["coding"].is_default is True
    assert config.modes["study"].is_default is True
    assert any("multiple modes set is_default" in r.getMessage() for r in caplog.records)


# --- Exclusions validation tests --------------------------------------------


def test_exclusions_empty_string_match_dropped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "exclusions.yaml",
        "excluded_apps:\n  - name: empty\n    match: ''\n  - name: ok\n    match: ok\n",
    )
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    names = [e.name for e in config.exclusions.excluded_apps]
    assert names == ["ok"]


def test_exclusions_empty_title_pattern_dropped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "exclusions.yaml",
        "excluded_title_patterns:\n  - ''\n  - '  '\n  - banking\n",
    )
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert config.exclusions.excluded_title_patterns == ("banking",)


def test_exclusions_missing_name_dropped(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "exclusions.yaml",
        "excluded_apps:\n  - match: orphan\n  - name: ok\n    match: ok\n",
    )
    config = load_config(data_dir)
    names = [e.name for e in config.exclusions.excluded_apps]
    assert names == ["ok"]


def test_exclusions_malformed_entry_dropped_others_load(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "exclusions.yaml",
        "excluded_apps:\n  - 'not a mapping'\n  - name: keepass\n    match: keepass\n",
    )
    config = load_config(data_dir)
    assert [e.name for e in config.exclusions.excluded_apps] == ["keepass"]


# --- Settings validation tests ----------------------------------------------


def test_settings_invalid_bluntness_falls_back_direct(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "settings.yaml", "bluntness: chaotic\n")
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert config.settings.bluntness is BluntnessLevel.DIRECT
    assert any("unknown bluntness" in r.getMessage() for r in caplog.records)


def test_settings_ruthless_falls_back_direct(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "settings.yaml", "bluntness: ruthless\n")
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert config.settings.bluntness is BluntnessLevel.DIRECT


def test_api_key_empty_string_normalizes_to_none(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "settings.yaml", "api_key: ''\n")
    config = load_config(data_dir)
    assert config.api_key is None


def test_api_key_whitespace_normalizes_to_none(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "settings.yaml", "api_key: '   '\n")
    config = load_config(data_dir)
    assert config.api_key is None


# --- Story 2.5 AC #3 — _normalize_api_key behavior lock --------------------


@pytest.mark.parametrize(
    ("yaml_body", "expected"),
    [
        # Normal key — untouched.
        ('api_key: "sk-ant-abc"\n', "sk-ant-abc"),
        # Surrounding whitespace stripped (YAML `api_key: " sk-ant-abc "`
        # would otherwise fail Anthropic auth).
        ('api_key: " sk-ant-abc "\n', "sk-ant-abc"),
        # Empty string → None.
        ('api_key: ""\n', None),
        # Whitespace-only → None.
        ("api_key: '   '\n", None),
        # YAML null (``api_key:`` with no value) → None.
        ("api_key:\n", None),
        # Integer literal — non-string types silently normalize to None
        # per Story 2.2 _normalize_api_key.
        ("api_key: 123\n", None),
        # List literal — non-string types silently normalize to None.
        ("api_key: [sk-ant-abc]\n", None),
    ],
    ids=[
        "plain_string",
        "strips_surrounding_whitespace",
        "empty_string_is_none",
        "whitespace_only_is_none",
        "yaml_null_is_none",
        "int_is_none",
        "list_is_none",
    ],
)
def test_normalize_api_key_variants(tmp_path: Path, yaml_body: str, expected: str | None) -> None:
    """Story 2.5 AC #3 — lock the ``_normalize_api_key`` contract."""
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "settings.yaml", yaml_body)
    config = load_config(data_dir)
    assert config.api_key == expected


def test_normalize_api_key_absent_key_is_none(tmp_path: Path) -> None:
    """Story 2.5 AC #3 — the YAML key being absent entirely normalizes to None."""
    data_dir = _bootstrap_data_dir(tmp_path)
    # File present but empty mapping — no ``api_key:`` line at all.
    _write(data_dir / "settings.yaml", "bluntness: direct\n")
    config = load_config(data_dir)
    assert config.api_key is None


def test_briefing_threshold_zero_accepted(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "settings.yaml", "briefing_recency_threshold_minutes: 0\n")
    config = load_config(data_dir)
    assert config.settings.briefing_recency_threshold_minutes == 0


def test_briefing_threshold_negative_falls_back_60(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "settings.yaml", "briefing_recency_threshold_minutes: -5\n")
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert config.settings.briefing_recency_threshold_minutes == 60
    assert any("negative" in r.getMessage() for r in caplog.records)


def test_briefing_threshold_float_falls_back_60(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "settings.yaml", "briefing_recency_threshold_minutes: 59.5\n")
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert config.settings.briefing_recency_threshold_minutes == 60


def test_briefing_threshold_bool_falls_back_60(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """YAML ``true`` must NOT silently become 1 via int subclass chain."""
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "settings.yaml", "briefing_recency_threshold_minutes: true\n")
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert config.settings.briefing_recency_threshold_minutes == 60
    assert any("is bool" in r.getMessage() for r in caplog.records)


def test_skip_briefing_non_bool_substituted_true(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "settings.yaml", "skip_briefing_if_recent: maybe\n")
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert config.settings.skip_briefing_if_recent is True


# --- UTF-8 BOM & unknown-keys tests -----------------------------------------


def test_utf8_bom_tolerated(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    settings_path = data_dir / "settings.yaml"
    # Write with explicit BOM + body.
    settings_path.write_bytes(b"\xef\xbb\xbfbluntness: calm\n")
    config = load_config(data_dir)
    assert config.settings.bluntness is BluntnessLevel.CALM


def test_unknown_keys_silently_ignored(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "settings.yaml",
        "bluntness: direct\nfuture_t2_field: enabled\n",
    )
    _write(
        data_dir / "modes" / "coding.yaml",
        "name: coding\napps:\n  - name: x\n    executable: y\n    future_field: z\n"
        "t2_root_field: true\n",
    )
    config = load_config(data_dir)
    assert config.settings.bluntness is BluntnessLevel.DIRECT
    assert config.modes["coding"].name == "coding"


# --- Tier-notice & API-key regression guards --------------------------------


def test_tier_notice_surface_attached_to_missing_file_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Locks Story 5.4's handoff contract.

    ``extra={...}`` kwargs become LogRecord attributes accessible via
    ``getattr(record, key, default)`` — Python's ``logging.Logger`` merges
    them at record construction.
    """
    data_dir = _bootstrap_data_dir(tmp_path)
    (data_dir / "exclusions.yaml").unlink()
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    load_config(data_dir)
    surfaces = [getattr(r, "surface", None) for r in caplog.records]
    assert "tier-notice" in surfaces


def test_api_key_never_appears_in_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Regression gate: no log record may contain the api_key substring.

    Force at least one WARNING emission by pairing the real ``api_key`` with
    an invalid ``bluntness`` value — guarantees ``caplog.records`` is
    non-empty so the leak-scan loop has teeth. Without the forced record the
    loop would be vacuously true.
    """
    secret = "sk-ant-supersecretvalue-DO-NOT-LOG"
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "settings.yaml",
        f"api_key: {secret}\nbluntness: chaotic\n",
    )
    caplog.set_level(logging.DEBUG, logger="nova.core.config")
    config = load_config(data_dir)
    assert config.api_key == secret
    # Precondition: at least one log record was emitted (the bluntness
    # fallback WARNING). Without this guard the leak-scan loop is vacuous.
    assert len(caplog.records) >= 1, "expected at least one log record from the forced warning"
    for record in caplog.records:
        # ``extra={}`` kwargs are merged into the LogRecord as attributes;
        # scan every string-valued attribute plus the formatted message.
        assert secret not in record.getMessage()
        for attr_name in dir(record):
            if attr_name.startswith("_"):
                continue
            value = getattr(record, attr_name, None)
            if isinstance(value, str):
                assert secret not in value, f"api_key leaked into LogRecord.{attr_name}"


def test_api_key_whitespace_stripped(tmp_path: Path) -> None:
    """Trailing/leading whitespace in the stored api_key is stripped, not preserved."""
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "settings.yaml", 'api_key: "  sk-ant-with-pad  "\n')
    config = load_config(data_dir)
    assert config.api_key == "sk-ant-with-pad"


# --- Merge-key / unhashable / I/O regression tests --------------------------


def test_merge_keys_rejected_in_singleton(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "settings.yaml",
        "defaults: &d\n  bluntness: direct\n<<: *d\n",
    )
    with pytest.raises(ConfigError, match="merge keys not supported"):
        load_config(data_dir)


def test_merge_keys_skip_mode_file(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "modes" / "merge.yaml",
        "base: &b\n  name: base\n<<: *b\napps:\n  - name: x\n    executable: y\n",
    )
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert "merge" not in config.modes
    assert any("merge keys" in r.getMessage() for r in caplog.records)


def test_unhashable_key_rejected_in_singleton(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "settings.yaml",
        "? [a, b]\n: v\n",
    )
    with pytest.raises(ConfigError, match="invalid mapping key"):
        load_config(data_dir)


def test_singleton_permission_error_surfaces_as_config_error(tmp_path: Path) -> None:
    """OSError from read_text must be wrapped as ConfigError, not propagate raw."""
    data_dir = _bootstrap_data_dir(tmp_path)
    with (
        patch.object(Path, "read_text", side_effect=PermissionError("denied")),
        pytest.raises(ConfigError, match="malformed config: I/O error"),
    ):
        load_config(data_dir)


def test_mode_file_permission_error_skips_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A single mode file raising OSError must NOT abort the whole load."""
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "modes" / "study.yaml", _minimal_mode_yaml(name="study"))

    real_read_text = Path.read_text

    def _selective_read_text(
        self: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if self.name == "study.yaml":
            raise PermissionError("denied")
        return real_read_text(self, encoding=encoding, errors=errors)

    caplog.set_level(logging.WARNING, logger="nova.core.config")
    with patch.object(Path, "read_text", _selective_read_text):
        config = load_config(data_dir)
    assert "coding" in config.modes
    assert "study" not in config.modes
    assert any("I/O error" in r.getMessage() for r in caplog.records)


def test_singleton_unicode_decode_error_surfaces_as_config_error(tmp_path: Path) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    # Write latin-1-encoded smart quote — invalid UTF-8 byte sequence.
    (data_dir / "settings.yaml").write_bytes(b"bluntness: \x92direct\x92\n")
    with pytest.raises(ConfigError, match="malformed config: encoding error"):
        load_config(data_dir)


def test_mode_file_unicode_decode_error_skips_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = _bootstrap_data_dir(tmp_path)
    (data_dir / "modes" / "bad.yaml").write_bytes(b"name: \x92bad\x92\n")
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert "bad" not in config.modes
    assert "coding" in config.modes
    assert any("encoding error" in r.getMessage() for r in caplog.records)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows refuses to create reserved-name files")
def test_reserved_stem_integration_with_real_con_yaml(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Integration: a real ``modes/con.yaml`` triggers the warn-and-skip path."""
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(data_dir / "modes" / "con.yaml", _minimal_mode_yaml(name="reserved"))
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    assert "con" not in config.modes
    assert any("reserved Windows stem" in r.getMessage() for r in caplog.records)


def test_folders_absolute_posix_path_dropped_on_windows_target(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """PureWindowsPath semantics: POSIX-absolute paths (no drive) are dropped.

    Locks the AC #13 cross-platform determinism — ``/home/user/foo`` is
    rejected regardless of which OS the test runs on, because the loader
    uses ``PureWindowsPath(entry).is_absolute()``.
    """
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "modes" / "coding.yaml",
        "name: coding\n"
        "apps:\n  - name: x\n    executable: y\n"
        "folders:\n  - /home/user/foo\n  - C:/absolute/path\n",
    )
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    config = load_config(data_dir)
    folders = config.modes["coding"].folders
    assert "/home/user/foo" not in folders
    assert "C:/absolute/path" in folders
    assert any("non-absolute path" in r.getMessage() for r in caplog.records)


def test_mode_level_warnings_carry_stem_extra(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Every mode-level validator warning must carry ``extra={"stem": stem}``."""
    data_dir = _bootstrap_data_dir(tmp_path)
    _write(
        data_dir / "modes" / "coding.yaml",
        "name: coding\n"
        "apps:\n  - name: x\n    executable: y\n"
        "folders:\n  - relative/path\n"
        "urls:\n  - file:///etc/passwd\n",
    )
    caplog.set_level(logging.WARNING, logger="nova.core.config")
    load_config(data_dir)
    # At least one warning should carry stem="coding" in its extra payload.
    stem_records = [r for r in caplog.records if getattr(r, "stem", None) == "coding"]
    assert stem_records, "expected mode-level warnings to carry stem='coding' extra"


# --- YAML-safety static-analysis gate ---------------------------------------


def test_config_does_not_use_unsafe_yaml_load() -> None:
    """No AST call to ``yaml.load(...)`` without a ``Loader=`` kwarg.

    Parses ``config.py`` via ``ast`` and inspects every actual function call
    (ignoring docstring / comment text). Also asserts that the custom loader
    subclasses ``yaml.SafeLoader`` so a future rename to a non-safe parent
    fails the gate.
    """
    import ast

    import nova.core.config as config_module

    source = Path(config_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    yaml_load_calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "load"
            and isinstance(func.value, ast.Name)
            and func.value.id == "yaml"
        ):
            yaml_load_calls.append(node)
    assert yaml_load_calls, "expected at least one yaml.load(...) call in config.py"
    for call in yaml_load_calls:
        kwarg_names = {kw.arg for kw in call.keywords if kw.arg is not None}
        assert "Loader" in kwarg_names, (
            f"Unsafe yaml.load(...) call without Loader= at line {call.lineno}"
        )
    assert issubclass(_DuplicateKeyRejectingLoader, yaml.SafeLoader)


# --- __all__ / re-export smoke test -----------------------------------------


def test_core_package_reexports_config_names() -> None:
    import nova.core as core_pkg

    assert core_pkg.AppConfig is AppConfig
    assert core_pkg.ModeConfig is ModeConfig
    assert core_pkg.NovaConfig is NovaConfig
    assert core_pkg.ExclusionConfig is ExclusionConfig
    assert core_pkg.ExcludedAppConfig is ExcludedAppConfig
    assert core_pkg.UserSettings is UserSettings
    assert core_pkg.load_config is load_config
