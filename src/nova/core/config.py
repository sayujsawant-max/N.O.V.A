"""YAML config loader for N.O.V.A. — single source of truth for file-based config.

This module is the ONLY YAML reader in the codebase. Every other module
consumes configuration through an injected :class:`NovaConfig` dataclass.

Schema is pinned in ``docs/config-schemas.md`` (Story 1.0). Any change to
fields, defaults, or validation rules flows through a new numbered story —
do not silently edit.

Loader contract
---------------
- :func:`load_config` is a pure function. It reads YAML files from ``data_dir``
  (typically ``%LOCALAPPDATA%/nova/`` in production, ``tmp_path`` in tests) and
  returns an immutable :class:`NovaConfig` with frozen dataclasses all the way
  down.
- Malformed YAML in singletons (``settings.yaml``, ``exclusions.yaml``) →
  :class:`~nova.core.exceptions.ConfigError`. Malformed YAML in a mode file →
  file skipped with warning (other modes still load).
- ``api_key`` is promoted out of ``settings`` — empty string and whitespace
  normalize to ``None`` at load time so tier logic sees ``str | None`` with no
  "present but useless" third state.

YAML safety
-----------
Every load path routes through :class:`_DuplicateKeyRejectingLoader`, a
``yaml.SafeLoader`` subclass that rejects duplicate keys at the same mapping
level. Bare ``yaml.load`` and non-``SafeLoader`` subclasses are forbidden —
locked by ``tests/unit/core/test_config.py::test_config_does_not_use_unsafe_yaml_load``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import cast

import yaml

from nova.core.exceptions import ConfigError
from nova.core.types import BluntnessLevel

logger = logging.getLogger("nova.core.config")

_MODE_STEM_RE: re.Pattern[str] = re.compile(r"[a-z0-9][a-z0-9-]*")

# Reserved Windows filenames per docs/config-schemas.md + Story 1.0
# deferred-work. Match the lower-cased stem against this set.
_RESERVED_WIN_STEMS: frozenset[str] = (
    frozenset({"con", "prn", "aux", "nul"})
    | frozenset(f"com{i}" for i in range(1, 10))
    | frozenset(f"lpt{i}" for i in range(1, 10))
)

_ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})

# Structured log ``extra`` marker consumed by Story 5.4's tier-notice handler.
_TIER_NOTICE_EXTRA: dict[str, str] = {"surface": "tier-notice"}


# --- YAML loader subclass ---------------------------------------------------


class _DuplicateKeyRejectingLoader(yaml.SafeLoader):
    """``SafeLoader`` subclass that rejects duplicate keys at the same mapping level.

    PyYAML's default ``SafeLoader`` silently accepts duplicate keys
    (last-wins) — a footgun flagged in Story 1.0's code review
    (``deferred-work.md``). Overriding ``construct_mapping`` to raise on a
    duplicate closes the path.

    Still a ``SafeLoader`` subclass — only safe tags are resolvable, so the
    ``# noqa: S506`` annotation at the single ``yaml.load(...)`` call site
    is accurate.
    """


_YAML_MERGE_TAG = "tag:yaml.org,2002:merge"


def _construct_mapping_reject_duplicates(
    loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[object, object]:
    # ``loader.construct_object`` is untyped in ``types-pyyaml``; narrow via
    # an explicit Callable cast so no ``# type: ignore`` is needed (Story 1.4
    # precedent: cast at typed-stubs-`Any` narrowing boundaries).
    construct: Callable[..., object] = cast("Callable[..., object]", loader.construct_object)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        # Merge keys (``<<: *anchor``) are rejected explicitly per Story 1.6
        # code-review decision (2026-04-14). Stock PyYAML expands them via
        # ``flatten_mapping``; we refuse both expansion and the silent
        # no-op, pushing users toward a single explicit mapping per file.
        if key_node.tag == _YAML_MERGE_TAG:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                "merge keys not supported",
                key_node.start_mark,
            )
        key = construct(key_node, deep=deep)
        try:
            already_present = key in mapping
        except TypeError as err:
            # Complex/unhashable mapping keys (``? [a,b]: v``) raise
            # ``TypeError: unhashable type`` from ``dict.__contains__``.
            # Wrap as ConstructorError so the outer loaders translate it
            # through the same opaque-ConfigError path as other structural
            # YAML failures.
            raise yaml.constructor.ConstructorError(
                None,
                None,
                "unhashable mapping key",
                key_node.start_mark,
            ) from err
        if already_present:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"duplicate key {key!r} in mapping",
                key_node.start_mark,
            )
        mapping[key] = construct(value_node, deep=deep)
    return mapping


_DuplicateKeyRejectingLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_reject_duplicates,
)


# --- Frozen dataclasses -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Single app entry inside a :class:`ModeConfig`.

    Matches ``docs/config-schemas.md`` §Mode schema ``apps[]``. ``executable``
    is stored verbatim; normalization (case-folding, ``.exe`` stripping) is a
    match-time concern owned by Hands/Eyes.
    """

    name: str
    executable: str
    args: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ModeConfig:
    """Workspace-mode definition loaded from a single ``modes/<stem>.yaml`` file.

    The file stem is the canonical mode identifier and lives as the dict key
    in :attr:`NovaConfig.modes`; ``name`` here is display-only.
    """

    name: str
    apps: tuple[AppConfig, ...]
    folders: tuple[str, ...] = field(default_factory=tuple)
    urls: tuple[str, ...] = field(default_factory=tuple)
    is_default: bool = False


@dataclass(frozen=True, slots=True)
class ExcludedAppConfig:
    """Single entry in :attr:`ExclusionConfig.excluded_apps`."""

    name: str
    match: str


@dataclass(frozen=True, slots=True)
class ExclusionConfig:
    """Sensitive-context exclusion list loaded from ``exclusions.yaml``."""

    excluded_apps: tuple[ExcludedAppConfig, ...] = field(default_factory=tuple)
    excluded_title_patterns: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class UserSettings:
    """User preferences loaded from ``settings.yaml``.

    Does NOT carry ``api_key``. The key is promoted to :attr:`NovaConfig.api_key`
    at load time so the empty-string normalization has a single source of truth.
    """

    bluntness: BluntnessLevel = BluntnessLevel.DIRECT
    skip_briefing_if_recent: bool = True
    briefing_recency_threshold_minutes: int = 60


@dataclass(frozen=True, slots=True)
class NovaConfig:
    """Single source of truth for all configuration.

    Loaded once at startup via :func:`load_config` and passed into the
    composition root. Immutable thereafter.
    """

    db_path: Path
    data_dir: Path
    modes: dict[str, ModeConfig]
    exclusions: ExclusionConfig
    settings: UserSettings
    api_key: str | None


__all__: list[str] = [
    "AppConfig",
    "ConfigError",
    "ExcludedAppConfig",
    "ExclusionConfig",
    "ModeConfig",
    "NovaConfig",
    "UserSettings",
    "load_config",
]


# --- YAML read helpers ------------------------------------------------------


def _load_yaml_text(text: str) -> object:
    """Parse YAML text via the duplicate-key-rejecting ``SafeLoader`` subclass.

    Returns ``object`` (not ``Any``) to force explicit narrowing at every
    downstream call site — matches the Story 1.4 precedent of avoiding
    ``typing.Any`` propagation from typed-stubs packages.
    """
    # types-pyyaml returns Any; narrow to ``object`` at the single YAML
    # boundary per Story 1.4 precedent.
    return cast(
        object,
        yaml.load(text, Loader=_DuplicateKeyRejectingLoader),  # noqa: S506
    )


def _read_yaml_file(path: Path) -> object:
    """Read a YAML file, tolerating a leading UTF-8 BOM.

    Returns the parsed Python object (``None`` for empty documents). Raises
    ``yaml.YAMLError`` subclasses (including
    ``yaml.constructor.ConstructorError`` for duplicate keys) on failure —
    callers decide whether to skip or halt.
    """
    text = path.read_text(encoding="utf-8-sig")
    return _load_yaml_text(text)


# --- Mode-stem predicate ----------------------------------------------------


def _is_reserved_stem(stem: str) -> bool:
    """True when ``stem`` (case-folded) is a reserved Windows filename."""
    return stem.lower() in _RESERVED_WIN_STEMS


def _is_valid_mode_stem(stem: str) -> bool:
    """True when ``stem`` is a valid mode identifier per ``docs/config-schemas.md``.

    Rules:
    - non-empty
    - no ``.`` characters (``config.overrides.yaml`` must not silently load
      as mode ``config``)
    - not a reserved Windows filename (checked via :func:`_is_reserved_stem`)
    - matches kebab-case regex (leading alnum, then alnum/hyphen)
    """
    if not stem or "." in stem:
        return False
    if _is_reserved_stem(stem):
        return False
    return _MODE_STEM_RE.fullmatch(stem) is not None


# --- Validators -------------------------------------------------------------


def _validate_args(value: object, stem: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        logger.warning("mode apps[].args not a list — substituting empty", extra={"stem": stem})
        return ()
    cleaned: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            logger.warning(
                "mode apps[].args contains non-string entry — substituting empty",
                extra={"stem": stem},
            )
            return ()
        cleaned.append(entry)
    return tuple(cleaned)


def _validate_app_entry(entry: object, stem: str) -> AppConfig | None:
    if not isinstance(entry, dict):
        logger.warning("mode apps[] entry is not a mapping — dropped", extra={"stem": stem})
        return None
    name = entry.get("name")
    executable = entry.get("executable")
    if not isinstance(name, str) or not name.strip():
        logger.warning("mode apps[] entry missing/empty name — dropped", extra={"stem": stem})
        return None
    if not isinstance(executable, str) or not executable.strip():
        logger.warning("mode apps[] entry missing/empty executable — dropped", extra={"stem": stem})
        return None
    args = _validate_args(entry.get("args", []), stem)
    return AppConfig(name=name, executable=executable, args=args)


def _validate_folders(value: object, stem: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        logger.warning("mode folders not a list — substituting empty", extra={"stem": stem})
        return ()
    cleaned: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            logger.warning("mode folders contains non-string entry — dropped", extra={"stem": stem})
            continue
        # Use ``PureWindowsPath`` for deterministic cross-platform semantics:
        # an absolute folder on a Windows-target app means a drive-anchored
        # path (``C:\...``). Platform-default ``Path(entry).is_absolute()``
        # flips behavior between Windows and POSIX CI for the same input.
        if not PureWindowsPath(entry).is_absolute():
            logger.warning(
                "mode folders contains non-absolute path — dropped", extra={"stem": stem}
            )
            continue
        cleaned.append(entry)
    return tuple(cleaned)


def _validate_urls(value: object, stem: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        logger.warning("mode urls not a list — substituting empty", extra={"stem": stem})
        return ()
    cleaned: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            logger.warning(
                "mode urls contains non-string/empty entry — dropped", extra={"stem": stem}
            )
            continue
        lower = entry.lower()
        matched = False
        for scheme in _ALLOWED_URL_SCHEMES:
            prefix = f"{scheme}://"
            if lower.startswith(prefix) and len(entry) > len(prefix):
                matched = True
                break
        if not matched:
            logger.warning(
                "mode urls contains disallowed-scheme entry — dropped", extra={"stem": stem}
            )
            continue
        cleaned.append(entry)
    return tuple(cleaned)


def _validate_mode(stem: str, data: object) -> ModeConfig | None:
    if not isinstance(data, dict):
        logger.warning("mode file has non-mapping root — skipped", extra={"stem": stem})
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        logger.warning("mode file missing/empty name — skipped", extra={"stem": stem})
        return None
    raw_apps = data.get("apps")
    if not isinstance(raw_apps, list):
        logger.warning("mode file missing/invalid apps — skipped", extra={"stem": stem})
        return None
    apps: list[AppConfig] = []
    for entry in raw_apps:
        app = _validate_app_entry(entry, stem)
        if app is not None:
            apps.append(app)
    if not apps:
        logger.warning(
            "mode file has zero valid apps after validation — skipped",
            extra={"stem": stem},
        )
        return None
    folders = _validate_folders(data.get("folders", []), stem)
    urls = _validate_urls(data.get("urls", []), stem)
    raw_is_default = data.get("is_default", False)
    if not isinstance(raw_is_default, bool):
        logger.warning("mode is_default non-bool — substituting False", extra={"stem": stem})
        is_default = False
    else:
        is_default = raw_is_default
    return ModeConfig(
        name=name,
        apps=tuple(apps),
        folders=folders,
        urls=urls,
        is_default=is_default,
    )


def _validate_excluded_apps(value: object) -> tuple[ExcludedAppConfig, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        logger.warning("exclusions.excluded_apps not a list — substituting empty")
        return ()
    cleaned: list[ExcludedAppConfig] = []
    for entry in value:
        if not isinstance(entry, dict):
            logger.warning("exclusions.excluded_apps entry is not a mapping — dropped")
            continue
        name = entry.get("name")
        match = entry.get("match")
        if not isinstance(name, str) or not name.strip():
            logger.warning("exclusions.excluded_apps entry missing/empty name — dropped")
            continue
        if not isinstance(match, str) or not match.strip():
            logger.warning("exclusions.excluded_apps entry missing/empty match — dropped")
            continue
        cleaned.append(ExcludedAppConfig(name=name, match=match))
    return tuple(cleaned)


def _validate_title_patterns(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        logger.warning("exclusions.excluded_title_patterns not a list — substituting empty")
        return ()
    cleaned: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            logger.warning("exclusions.excluded_title_patterns entry empty/non-string — dropped")
            continue
        cleaned.append(entry)
    return tuple(cleaned)


def _validate_exclusions(data: dict[str, object]) -> ExclusionConfig:
    return ExclusionConfig(
        excluded_apps=_validate_excluded_apps(data.get("excluded_apps", [])),
        excluded_title_patterns=_validate_title_patterns(data.get("excluded_title_patterns", [])),
    )


def _validate_bluntness(value: object) -> BluntnessLevel:
    if value is None:
        return BluntnessLevel.DIRECT
    if not isinstance(value, str):
        logger.warning("bluntness not a string — falling back to direct")
        return BluntnessLevel.DIRECT
    try:
        return BluntnessLevel(value)
    except ValueError:
        logger.warning("unknown bluntness value — falling back to direct")
        return BluntnessLevel.DIRECT


def _validate_skip_briefing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    logger.warning("skip_briefing_if_recent non-bool — substituting True")
    return True


def _validate_threshold(value: object) -> int:
    if value is None:
        return 60
    # Booleans are ``int`` subclasses in Python — reject explicitly BEFORE the
    # ``isinstance(int)`` check to prevent YAML ``true`` silently becoming 1.
    if isinstance(value, bool):
        logger.warning("briefing_recency_threshold_minutes is bool — substituting 60")
        return 60
    if not isinstance(value, int):
        logger.warning("briefing_recency_threshold_minutes non-int — substituting 60")
        return 60
    if value < 0:
        logger.warning("briefing_recency_threshold_minutes negative — substituting 60")
        return 60
    return value


def _normalize_api_key(value: object) -> str | None:
    """Normalize the raw YAML ``api_key`` value.

    Empty / whitespace-only strings normalize to ``None``. For non-empty
    strings the surrounding whitespace is stripped — a YAML value like
    ``api_key: " sk-ant-abc "`` would otherwise fail Anthropic auth with no
    useful error. This matches the whitespace-is-absent rule (Story 1.0) and
    keeps the tier-logic contract single-path.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped


def _validate_settings(data: dict[str, object]) -> tuple[UserSettings, str | None]:
    settings = UserSettings(
        bluntness=_validate_bluntness(data.get("bluntness")),
        skip_briefing_if_recent=_validate_skip_briefing(data.get("skip_briefing_if_recent")),
        briefing_recency_threshold_minutes=_validate_threshold(
            data.get("briefing_recency_threshold_minutes")
        ),
    )
    api_key = _normalize_api_key(data.get("api_key"))
    return settings, api_key


# --- Singleton & modes-dir loaders ------------------------------------------


def _load_singleton(path: Path, source: str) -> dict[str, object] | None:
    """Load a singleton YAML file. Returns ``None`` iff the file does not exist.

    Hard-errors on parse failure, duplicate keys, and non-mapping root per
    the schema doc's singleton rules.
    """
    if not path.exists():
        return None
    try:
        parsed = _read_yaml_file(path)
    except yaml.constructor.ConstructorError as err:
        raise ConfigError(_translate_constructor_error(err)) from err
    except yaml.YAMLError as err:
        raise ConfigError("malformed config: parse error") from err
    except UnicodeDecodeError as err:
        raise ConfigError("malformed config: encoding error") from err
    except OSError as err:
        raise ConfigError("malformed config: I/O error") from err
    if parsed is None:
        logger.warning("%s is empty — applying defaults", source, extra={"file": str(path)})
        return {}
    if not isinstance(parsed, dict):
        raise ConfigError("malformed config: non-mapping root")
    return cast(dict[str, object], parsed)


_CONSTRUCTOR_ERROR_MESSAGES: tuple[tuple[str, str], ...] = (
    ("merge keys not supported", "malformed config: merge keys not supported"),
    ("unhashable mapping key", "malformed config: invalid mapping key"),
)


def _translate_constructor_error(err: yaml.constructor.ConstructorError) -> str:
    """Map a ``ConstructorError`` problem string to an opaque ``ConfigError`` message."""
    problem = err.problem or ""
    for needle, message in _CONSTRUCTOR_ERROR_MESSAGES:
        if needle in problem:
            return message
    return "malformed config: duplicate key"


def _constructor_error_mode_warning(err: yaml.constructor.ConstructorError) -> str:
    """Map a ``ConstructorError`` problem string to a mode-file skip reason."""
    problem = err.problem or ""
    if "merge keys not supported" in problem:
        return "mode file uses merge keys — skipped"
    if "unhashable mapping key" in problem:
        return "mode file has invalid mapping key — skipped"
    return "mode file has duplicate keys — skipped"


def _load_modes(modes_dir: Path) -> dict[str, ModeConfig]:
    """Load every valid mode file in ``modes_dir``. Skip-on-error at the file level.

    Any I/O, encoding, or YAML structural failure on a single mode file is
    logged with ``extra={"stem": stem}`` and the loader moves on to the next
    file — one bad mode never aborts the whole load.
    """
    modes: dict[str, ModeConfig] = {}
    entries = sorted(modes_dir.iterdir(), key=lambda p: p.name)
    for entry in entries:
        if not entry.is_file():
            continue
        if entry.suffix != ".yaml":
            continue
        if entry.name.startswith("."):
            continue
        stem = entry.stem
        if _is_reserved_stem(stem):
            logger.warning("mode file uses reserved Windows stem — skipped", extra={"stem": stem})
            continue
        if not _is_valid_mode_stem(stem):
            continue
        try:
            parsed = _read_yaml_file(entry)
        except yaml.constructor.ConstructorError as err:
            logger.warning(_constructor_error_mode_warning(err), extra={"stem": stem})
            continue
        except yaml.YAMLError:
            logger.warning("mode file YAML parse error — skipped", extra={"stem": stem})
            continue
        except UnicodeDecodeError:
            logger.warning("mode file encoding error — skipped", extra={"stem": stem})
            continue
        except OSError:
            logger.warning("mode file I/O error — skipped", extra={"stem": stem})
            continue
        if parsed is None:
            logger.warning("mode file is empty — skipped", extra={"stem": stem})
            continue
        mode = _validate_mode(stem, parsed)
        if mode is not None:
            modes[stem] = mode
    return modes


# --- Public entry point -----------------------------------------------------


def load_config(data_dir: Path) -> NovaConfig:
    """Load every YAML config file under ``data_dir`` into an immutable :class:`NovaConfig`.

    See ``docs/config-schemas.md`` for the pinned schema. Side-effect-free
    apart from YAML reads and log output — does NOT create the data
    directory, write any file, or touch ``%LOCALAPPDATA%``.
    """
    if not data_dir.exists():
        raise ConfigError("data directory missing")
    if not data_dir.is_dir():
        raise ConfigError("data directory path is not a directory")

    modes_dir = data_dir / "modes"
    exclusions_path = data_dir / "exclusions.yaml"
    settings_path = data_dir / "settings.yaml"
    db_path = data_dir / "nova.db"

    # Modes
    modes: dict[str, ModeConfig]
    if not modes_dir.exists():
        logger.warning("modes/ directory missing — zero modes configured", extra=_TIER_NOTICE_EXTRA)
        modes = {}
    elif not modes_dir.is_dir():
        raise ConfigError("modes path is not a directory")
    else:
        modes = _load_modes(modes_dir)
        if not modes:
            # Directory exists but yielded zero modes (empty directory OR every
            # mode file skipped by validation). Per AC #8, warn — operator
            # needs to know no modes are available before the briefing renders
            # in State A.
            logger.warning(
                "modes/ directory has zero loadable modes — zero modes configured",
                extra=_TIER_NOTICE_EXTRA,
            )

    default_modes = sorted(stem for stem, cfg in modes.items() if cfg.is_default)
    if len(default_modes) > 1:
        logger.warning(
            "multiple modes set is_default=true — tie-break resolves at query time",
            extra={"stems": default_modes},
        )

    # Exclusions
    exclusions_data = _load_singleton(exclusions_path, "exclusions.yaml")
    if exclusions_data is None:
        logger.warning(
            "exclusions.yaml not found — zero exclusion protection",
            extra=_TIER_NOTICE_EXTRA,
        )
        exclusions = ExclusionConfig()
    else:
        exclusions = _validate_exclusions(exclusions_data)

    # Settings
    settings_data = _load_singleton(settings_path, "settings.yaml")
    if settings_data is None:
        logger.warning(
            "settings.yaml not found — applying defaults, offline-local-only tier",
            extra=_TIER_NOTICE_EXTRA,
        )
        settings = UserSettings()
        api_key: str | None = None
    else:
        settings, api_key = _validate_settings(settings_data)

    return NovaConfig(
        db_path=db_path,
        data_dir=data_dir,
        modes=modes,
        exclusions=exclusions,
        settings=settings,
        api_key=api_key,
    )
