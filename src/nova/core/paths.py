"""Shared path-validation module for N.O.V.A.

Rejects Windows path pathologies before they reach the filesystem:
reserved names (``CON``, ``PRN``, ``AUX``, ``NUL``, ``COM1``–``COM9``,
``LPT1``–``LPT9``), invalid characters (``<``, ``>``, ``:``, ``"``,
``|``, ``?``, ``*``), trailing dots or spaces, and paths exceeding the
current host's supported length.

Pure core module — performs no I/O beyond :meth:`pathlib.Path.resolve`
and a single stat call (resolved-path-is-file check). Has no imports
from ``nova.adapters.*`` or ``nova.systems.*`` — enforced by AST guard
tests in ``tests/unit/core/test_core_isolation.py``.

Patterns consulted
------------------
- **#1 Two-function clock indirection** — applied to
  :func:`_get_max_path_length`. Tests monkeypatch via the module
  attribute ``paths._get_max_path_length`` so the indirection is
  preserved.
- **#4 Error-translation-at-boundary** — :class:`OSError` from
  ``Path.resolve(strict=False)`` is caught and translated to
  :class:`~nova.core.exceptions.ConfigError` via ``raise ... from err``.
- **#5 Singleton hard-fail** — the first-run setup flow that consumes
  this module treats the data-dir path as a singleton: one violation,
  one hard-fail, no skip-on-error.

Known limitations
-----------------
- UNC paths (``\\\\server\\share\\...``) are not tested in T1. They may
  pass or fail incidentally but their behavior is not contracted.
- Windows long-path support requires both a manifest opt-in and the
  HKLM registry flag. This module probes the registry flag only.
"""

from __future__ import annotations

import sys
from pathlib import Path, PureWindowsPath

from nova.core.exceptions import ConfigError

# Reserved Windows filenames per Microsoft's filesystem rules.
# Case-insensitive match; applies with or without extension.
# Total: 22 names (4 fixed + 9 COM + 9 LPT).
_RESERVED_WIN_NAMES: frozenset[str] = (
    frozenset({"con", "prn", "aux", "nul"})
    | frozenset(f"com{i}" for i in range(1, 10))
    | frozenset(f"lpt{i}" for i in range(1, 10))
)

# Characters disallowed in any path segment on Windows.
# ``/`` and ``\`` are path separators so they cannot appear inside a
# single segment once parsed. ``:`` is disallowed everywhere except the
# drive-letter anchor (``C:\``), which is skipped during segment walks.
_INVALID_SEGMENT_CHARS: frozenset[str] = frozenset({"<", ">", ":", '"', "|", "?", "*"})

# Conservative Windows path limit when the long-path opt-in is not
# enabled (or detection fails). The Win32 API's ``MAX_PATH`` constant.
_WIN_DEFAULT_MAX_PATH: int = 260

# Windows long-path limit when ``LongPathsEnabled`` is set in HKLM.
_WIN_LONG_MAX_PATH: int = 32767

# POSIX-friendly generous fallback for non-Windows dev machines. Chosen
# so unit tests run in any dev environment without tripping the limit
# artificially. Production runs on Windows; this branch exists only so
# the module remains unit-testable off-platform.
_POSIX_DEV_FALLBACK: int = 4096


def _get_max_path_length() -> int:
    """Canonical host path-limit query — single source of truth.

    Pattern #1 (two-function clock indirection). Tests monkeypatch via
    ``paths._get_max_path_length`` (module attribute); never
    ``from nova.core.paths import _get_max_path_length`` — that binds
    at import time and defeats the monkeypatch.

    On Windows, probes ``HKLM\\SYSTEM\\CurrentControlSet\\Control\\``
    ``FileSystem\\LongPathsEnabled``. Returns ``32767`` when the opt-in
    is set, ``260`` otherwise. Registry failures fall back to ``260``
    (conservative — rejecting a path the OS would have accepted is
    preferable to silently accepting a path the OS would fail on).

    On non-Windows dev environments, returns a generous fallback
    (``4096``) so unit tests and dev machines don't trip the limit
    artificially.
    """
    if sys.platform != "win32":
        return _POSIX_DEV_FALLBACK
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\FileSystem",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
        return _WIN_LONG_MAX_PATH if int(value) == 1 else _WIN_DEFAULT_MAX_PATH
    except (OSError, ValueError):
        # OSError covers registry access failures (key missing, access
        # denied); ValueError covers non-numeric REG_SZ values if an
        # admin set the flag incorrectly. Both fall back to the
        # conservative 260 — rejecting a path the OS would have
        # accepted is preferable to silently accepting one the OS would
        # fail on.
        return _WIN_DEFAULT_MAX_PATH


def validate_data_dir(path: Path) -> None:
    """Validate a Windows data-directory path; raise ``ConfigError`` on violation.

    Resolution contract: resolves ``path`` via
    ``Path.resolve(strict=False)`` before validating. Callers may pass
    either a pre-resolved or raw path — both are accepted. The caller
    owns the input; this function neither mutates it nor creates any
    filesystem state.

    Validation order
    ----------------
    1. Translate :class:`OSError` from ``Path.resolve`` to
       ``ConfigError`` via ``raise ... from err`` (Pattern #4).
    2. Reject paths longer than the host's supported limit
       (see :func:`_get_max_path_length`) — fail fast with the length
       message before walking segments.
    3. Walk every segment on both the pre-resolve and post-resolve
       forms. For each segment, reject invalid characters (including
       ASCII control chars 0x01–0x1F and DEL 0x7F), trailing dot /
       trailing space, and reserved Windows names (case-insensitive,
       with or without extension). The pre-resolve walk uses
       :class:`PureWindowsPath` so the rules are applied uniformly
       across platforms and so trailing-dot/space checks remain
       reachable (``Path.resolve()`` on Windows strips them from
       middle segments, which would defeat a post-resolve-only walk).
    4. Reject if the resolved path exists and is a file (not a
       directory). A non-existent path is acceptable — the caller will
       create it.

    Raises:
        ConfigError: On any violation. Messages name the offending
            segment and reason only — never the full input path, no
            filesystem metadata, no stack traces.
    """
    try:
        resolved = path.resolve(strict=False)
    except OSError as err:
        raise ConfigError("Path could not be resolved.") from err

    if len(str(resolved)) > _get_max_path_length():
        raise ConfigError(
            "Path too long for this system. Shorten the path or enable Windows long-path support."
        )

    # Strip Windows extended-length namespace prefix (``\\?\`` /
    # ``\\.\``) before parsing. Pathlib sometimes returns the
    # namespaced form on long paths; validation should apply to the
    # underlying path, not the prefix. Without this strip, the anchor
    # ``\\?\C:\`` would trip the invalid-``?`` rule on a legitimate
    # long path.
    raw = str(path)
    resolved_str = str(resolved)
    for prefix in ("\\\\?\\", "\\\\.\\"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
        if resolved_str.startswith(prefix):
            resolved_str = resolved_str[len(prefix) :]

    # Validate both pre-resolve and post-resolve segments. Pre-resolve
    # catches trailing-dot/space on middle segments (resolve strips
    # these on Windows); post-resolve catches CWD-derived pathologies
    # when the input was relative.
    seen: set[str] = set()
    for parts_source in (PureWindowsPath(raw).parts, PureWindowsPath(resolved_str).parts):
        _validate_parts(parts_source, seen)

    if resolved.exists() and not resolved.is_dir():
        raise ConfigError("Path exists and is a file, not a directory.")


def _is_drive_absolute_anchor(anchor: str) -> bool:
    """Return True iff ``anchor`` is exactly ``"<letter>:\\\\"`` (3 chars).

    The drive-absolute anchor shape is exempt from per-character
    validation so the drive-letter colon passes. Any other anchor
    shape is validated like every other segment — which catches
    degraded parses such as ``Path("C:\\foo\\a:b\\...")`` where
    pathlib reinterprets the embedded ``a:`` as a drive-relative
    anchor.
    """
    return len(anchor) == 3 and anchor[0].isalpha() and anchor[1] == ":" and anchor[2] == "\\"


def _validate_parts(parts: tuple[str, ...], seen: set[str]) -> None:
    """Walk ``parts`` applying :func:`_validate_segment` to each non-anchor segment.

    ``seen`` deduplicates across the pre-resolve / post-resolve walks
    so an identical segment is not re-validated (and so the error
    message for a common pathology fires from the first walk, not the
    second).
    """
    if not parts:
        return
    anchor = parts[0]
    if not _is_drive_absolute_anchor(anchor) and anchor not in seen:
        _validate_segment(anchor)
        seen.add(anchor)
    for segment in parts[1:]:
        if segment in seen:
            continue
        _validate_segment(segment)
        seen.add(segment)


def _validate_segment(segment: str) -> None:
    """Validate a single path segment; raise ``ConfigError`` on violation.

    Opacity rule: the raised message names only the segment and
    reason — never the full input path or filesystem metadata.
    """
    if not segment:
        raise ConfigError("Path contains an empty segment.")

    for ch in segment:
        # Reject the documented disallowed set plus ASCII control
        # characters (0x01–0x1F) and DEL (0x7F) — Microsoft's
        # filesystem rules disallow all of these. Catching them here
        # means a hostile or corrupted path never reaches ``mkdir``
        # with a cryptic ``OSError``.
        if ch in _INVALID_SEGMENT_CHARS or ord(ch) < 0x20 or ord(ch) == 0x7F:
            raise ConfigError(
                f"Path segment {segment!r} contains invalid character {ch!r}. "
                "Choose a different segment."
            )

    if segment.endswith(" ") or segment.endswith("."):
        raise ConfigError(
            f"Path segment {segment!r} ends with a dot or space. Remove the trailing character."
        )

    # Reserved-name check: case-insensitive, stripping any extension.
    # ``"CON.txt"``, ``"con"``, and ``"Con"`` all match ``"con"``.
    stem = segment.split(".", 1)[0].lower()
    if stem in _RESERVED_WIN_NAMES:
        raise ConfigError(
            f"Path segment {segment!r} is a reserved Windows name. Choose a different segment."
        )
