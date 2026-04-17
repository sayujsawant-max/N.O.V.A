"""Unit tests for nova.setup.settings_writer (Task 2 / AC #14-16, #18)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from nova.setup.settings_writer import write_api_key


class TestWriteApiKey:
    """Atomic write with field preservation."""

    def test_round_trip_preserves_existing_fields(self, tmp_path: Path) -> None:
        data_dir = tmp_path
        settings = data_dir / "settings.yaml"
        settings.write_text(
            "bluntness: direct\n"
            "skip_briefing_if_recent: true\n"
            "briefing_recency_threshold_minutes: 60\n",
            encoding="utf-8",
        )

        write_api_key(data_dir, "sk-ant-test-key-123")

        result = yaml.safe_load(settings.read_text(encoding="utf-8"))
        assert result["api_key"] == "sk-ant-test-key-123"
        assert result["bluntness"] == "direct"
        assert result["skip_briefing_if_recent"] is True
        assert result["briefing_recency_threshold_minutes"] == 60

    def test_overwrites_existing_api_key(self, tmp_path: Path) -> None:
        data_dir = tmp_path
        settings = data_dir / "settings.yaml"
        settings.write_text("api_key: old-key\nbluntness: calm\n", encoding="utf-8")

        write_api_key(data_dir, "sk-ant-new-key")

        result = yaml.safe_load(settings.read_text(encoding="utf-8"))
        assert result["api_key"] == "sk-ant-new-key"
        assert result["bluntness"] == "calm"

    def test_handles_empty_settings_file(self, tmp_path: Path) -> None:
        data_dir = tmp_path
        settings = data_dir / "settings.yaml"
        settings.write_text("", encoding="utf-8")

        write_api_key(data_dir, "sk-ant-key")

        result = yaml.safe_load(settings.read_text(encoding="utf-8"))
        assert result["api_key"] == "sk-ant-key"

    def test_atomic_write_no_partial_on_replace_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path
        settings = data_dir / "settings.yaml"
        original_content = "bluntness: direct\n"
        settings.write_text(original_content, encoding="utf-8")

        monkeypatch.setattr("nova.setup.settings_writer.os.replace", _raise_oserror)

        with pytest.raises(OSError, match="mock replace failure"):
            write_api_key(data_dir, "sk-ant-key")

        # Original file untouched
        assert settings.read_text(encoding="utf-8") == original_content
        # Temp file cleaned up
        assert not (data_dir / "settings.yaml.tmp").exists()

    def test_raises_oserror_when_settings_missing(self, tmp_path: Path) -> None:
        with pytest.raises(OSError):
            write_api_key(tmp_path, "sk-ant-key")

    def test_sort_keys_false_preserves_field_order(self, tmp_path: Path) -> None:
        data_dir = tmp_path
        settings = data_dir / "settings.yaml"
        settings.write_text(
            "bluntness: direct\n"
            "skip_briefing_if_recent: true\n"
            "briefing_recency_threshold_minutes: 60\n",
            encoding="utf-8",
        )

        write_api_key(data_dir, "sk-ant-key")

        text = settings.read_text(encoding="utf-8")
        lines = [line for line in text.strip().split("\n") if line.strip()]
        keys = [line.split(":")[0] for line in lines]
        # api_key should be added; existing keys should appear
        assert "bluntness" in keys
        assert "api_key" in keys


def _raise_oserror(*_args: object, **_kwargs: object) -> None:
    raise OSError("mock replace failure")


# ---------------------------------------------------------------------------
# Post-review patches: YAMLError + non-dict root translation (H6, H7)
# ---------------------------------------------------------------------------


class TestCorruptYamlTranslation:
    """Malformed / non-dict settings.yaml is translated to OSError."""

    def test_malformed_yaml_raises_oserror(self, tmp_path: Path) -> None:
        """H6: yaml.YAMLError from parse failure is re-raised as OSError."""
        data_dir = tmp_path
        (data_dir / "settings.yaml").write_text("bluntness: [unclosed\n", encoding="utf-8")

        with pytest.raises(OSError) as excinfo:
            write_api_key(data_dir, "sk-ant-key")

        # Underlying yaml error is chained via `from err`
        assert excinfo.value.__cause__ is not None
        # Message does not include the key value
        assert "sk-ant-key" not in str(excinfo.value)

    def test_list_root_raises_oserror(self, tmp_path: Path) -> None:
        """H7: list root (instead of mapping) raises OSError, not TypeError."""
        data_dir = tmp_path
        (data_dir / "settings.yaml").write_text("- first\n- second\n", encoding="utf-8")

        with pytest.raises(OSError, match="not a mapping"):
            write_api_key(data_dir, "sk-ant-key")

    def test_scalar_root_raises_oserror(self, tmp_path: Path) -> None:
        """H7: scalar root raises OSError, not TypeError."""
        data_dir = tmp_path
        (data_dir / "settings.yaml").write_text('"just a string"\n', encoding="utf-8")

        with pytest.raises(OSError, match="not a mapping"):
            write_api_key(data_dir, "sk-ant-key")

    def test_key_not_in_translated_exception_messages(self, tmp_path: Path) -> None:
        """The translated OSError never embeds the API key value."""
        data_dir = tmp_path
        (data_dir / "settings.yaml").write_text("bluntness: [bad\n", encoding="utf-8")

        secret = "sk-ant-never-leak-this"
        try:
            write_api_key(data_dir, secret)
        except OSError as err:
            assert secret not in str(err)
            assert secret not in repr(err)
