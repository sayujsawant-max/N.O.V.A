"""Unit tests for ``nova.core.paths`` — Windows data-directory validator.

Covers AC #27–34 of Story 2.1:
- Reserved Windows names (22 × 4 variants)
- Invalid characters (7 × 3 positions)
- Trailing dots / spaces (3 segment positions × 2 endings)
- Drive-letter colon positive
- Monkeypatched long-path check (Pattern #1 indirection)
- Windows-only registry detection (3 branches)
- Opaque-message rule
- Path-is-file rejection
- ``OSError`` translation at boundary

Platform note
-------------
The validator targets Windows path semantics. Tests that construct
drive-letter strings (``"C:\\..."``) and assume Windows-style segment
parsing use the Windows-specific ``PureWindowsPath`` machinery inside
``validate_data_dir``, but rely on ``Path.resolve()`` for length and
existence checks — the latter depends on the runtime platform. Marking
the module ``windows_only`` ensures the tests run on the platform
they're contracted against.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from nova.core import paths
from nova.core.exceptions import ConfigError

pytestmark = [
    pytest.mark.windows_only,
    pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics required"),
]

# --- Helpers ----------------------------------------------------------------

# 22 reserved Windows names: 4 fixed + 9 COM + 9 LPT.
_RESERVED_NAMES: tuple[str, ...] = (
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
)

# AC #27 variants applied to each reserved name.
_RESERVED_VARIANTS: tuple[tuple[str, str], ...] = (
    ("last", "C:\\data\\{name}"),
    ("middle", "C:\\data\\{name}\\sub"),
    ("with-extension", "C:\\data\\{name}.txt"),
    ("mixed-case", "C:\\data\\{name_mixed}"),
)

# Characters disallowed anywhere except the drive-letter anchor.
_INVALID_CHARS: tuple[str, ...] = ("<", ">", ":", '"', "|", "?", "*")


def _mixed_case(name: str) -> str:
    """Return ``name`` with alternating case — e.g., ``"CON"`` → ``"cOn"``."""
    return "".join(ch.lower() if i % 2 == 0 else ch.upper() for i, ch in enumerate(name))


# --- Reserved-name tests (AC #27) ------------------------------------------

# ``pytest.param`` instances carry id metadata. ``list[object]`` is
# type-correct; mypy rejects ``list[pytest.param]`` because ``param`` is
# a function, not a type.
_RESERVED_CASES: list[object] = []
for _name in _RESERVED_NAMES:
    for _variant_id, _template in _RESERVED_VARIANTS:
        _rendered = _template.format(name=_name, name_mixed=_mixed_case(_name))
        _RESERVED_CASES.append(pytest.param(_rendered, id=f"{_name}-{_variant_id}"))


@pytest.mark.parametrize("raw_path", _RESERVED_CASES)
def test_reserved_windows_name_rejected(raw_path: str) -> None:
    """Every reserved name, in every variant, must raise ``ConfigError``.

    AC #27 — 22 names × 4 variants = 88 parametrized cases.
    """
    with pytest.raises(ConfigError, match="reserved Windows name"):
        paths.validate_data_dir(Path(raw_path))


def test_reserved_name_set_size() -> None:
    """Sanity check: the reserved-name set has exactly 22 entries."""
    assert len(paths._RESERVED_WIN_NAMES) == 22


# --- Invalid-character tests (AC #28) --------------------------------------

_INVALID_CHAR_CASES: list[object] = []
for _ch in _INVALID_CHARS:
    for _position_id, _template in (
        ("first", "C:\\a{ch}b\\mid\\last"),
        ("middle", "C:\\first\\a{ch}b\\last"),
        ("last", "C:\\first\\mid\\a{ch}b"),
    ):
        _INVALID_CHAR_CASES.append(
            pytest.param(_template.format(ch=_ch), id=f"{_ch!r}-{_position_id}")
        )


@pytest.mark.parametrize("raw_path", _INVALID_CHAR_CASES)
def test_invalid_character_rejected(raw_path: str) -> None:
    """Each disallowed character at each segment position must raise.

    AC #28 — 7 chars × 3 positions = 21 parametrized cases.
    """
    with pytest.raises(ConfigError, match="invalid character"):
        paths.validate_data_dir(Path(raw_path))


# --- Trailing-dot / trailing-space tests (AC #29) --------------------------


@pytest.mark.parametrize(
    "raw_path",
    [
        pytest.param("C:\\first.\\mid\\last", id="dot-first"),
        pytest.param("C:\\first\\mid.\\last", id="dot-middle"),
        pytest.param("C:\\first\\mid\\last.", id="dot-last"),
        pytest.param("C:\\first \\mid\\last", id="space-first"),
        pytest.param("C:\\first\\mid \\last", id="space-middle"),
        pytest.param("C:\\first\\mid\\last ", id="space-last"),
    ],
)
def test_trailing_dot_or_space_rejected(raw_path: str) -> None:
    """Trailing dot or space on any segment position must raise."""
    with pytest.raises(ConfigError, match="ends with a dot or space"):
        paths.validate_data_dir(Path(raw_path))


# --- Drive-letter colon positive test (AC #30) -----------------------------


@pytest.mark.parametrize(
    "raw_path",
    [
        pytest.param("C:\\foo", id="c-drive"),
        pytest.param("D:\\bar\\baz", id="d-drive-nested"),
    ],
)
def test_drive_letter_colon_accepted(raw_path: str, tmp_path: Path) -> None:
    """The drive-letter colon at index 1 of the anchor is NOT rejected.

    Uses a monkeypatch-safe approach: construct the path but don't
    require it to exist on disk. ``validate_data_dir`` accepts
    non-existent paths as valid (the caller creates them).
    """
    # Validation must not raise on a well-formed Windows drive path.
    paths.validate_data_dir(Path(raw_path))


# --- Monkeypatched long-path test (AC #31) ---------------------------------


def test_long_path_rejected_via_module_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    """Long-path rejection must use the module-attribute indirection.

    AC #31 — monkeypatches ``paths._get_max_path_length`` and asserts
    the indirection propagates (confirms Pattern #1 wiring; a
    ``from paths import _get_max_path_length`` local binding would
    defeat the monkeypatch).
    """
    monkeypatch.setattr(paths, "_get_max_path_length", lambda: 30)
    too_long = Path("C:\\" + "a" * 40)
    with pytest.raises(ConfigError, match="Path too long for this system"):
        paths.validate_data_dir(too_long)


def test_short_path_passes_when_limit_small(monkeypatch: pytest.MonkeyPatch) -> None:
    """A path under the monkeypatched limit must not trip the length check."""
    monkeypatch.setattr(paths, "_get_max_path_length", lambda: 4096)
    paths.validate_data_dir(Path("C:\\foo\\bar"))


# --- Windows-only registry detection tests (AC #31a) -----------------------


@pytest.mark.windows_only
@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry required")
def test_get_max_path_length_long_enabled() -> None:
    """``LongPathsEnabled == 1`` → limit is 32767.

    Patches ``winreg`` calls so the test is independent of the host's
    actual registry state.
    """
    import winreg

    mock_key = patch.object(winreg, "OpenKey").start()
    mock_query = patch.object(winreg, "QueryValueEx").start()
    try:
        mock_key.return_value.__enter__.return_value = "fake-handle"
        mock_key.return_value.__exit__.return_value = False
        mock_query.return_value = (1, winreg.REG_DWORD)
        assert paths._get_max_path_length() == 32767
    finally:
        patch.stopall()


@pytest.mark.windows_only
@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry required")
def test_get_max_path_length_long_disabled() -> None:
    """``LongPathsEnabled == 0`` → limit is 260 (default ``MAX_PATH``)."""
    import winreg

    mock_key = patch.object(winreg, "OpenKey").start()
    mock_query = patch.object(winreg, "QueryValueEx").start()
    try:
        mock_key.return_value.__enter__.return_value = "fake-handle"
        mock_key.return_value.__exit__.return_value = False
        mock_query.return_value = (0, winreg.REG_DWORD)
        assert paths._get_max_path_length() == 260
    finally:
        patch.stopall()


@pytest.mark.windows_only
@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry required")
def test_get_max_path_length_registry_error_falls_back_to_260() -> None:
    """Registry open failure falls back to 260; no exception propagates."""
    import winreg

    mock_key = patch.object(winreg, "OpenKey", side_effect=OSError("access denied")).start()
    try:
        assert paths._get_max_path_length() == 260
    finally:
        patch.stopall()
    assert mock_key.called


# --- Opaque-message test (AC #32) ------------------------------------------


@pytest.mark.parametrize(
    "raw_path",
    [
        pytest.param("C:\\users\\operator\\secret-data\\CON", id="reserved-leaks-host"),
        pytest.param("C:\\users\\operator\\secret-data\\bad<", id="invalid-leaks-host"),
        pytest.param("C:\\users\\operator\\secret-data\\trailing.", id="trailing-leaks-host"),
    ],
)
def test_error_message_does_not_contain_full_input_path(raw_path: str) -> None:
    """AC #32 — messages name the offending segment but never the full path.

    Asserts:
    1. Parent-directory tokens (``secret-data``, ``operator``, ``users``)
       do not appear in the error message.
    2. The message contains at most one backslash — the only path
       separator allowed is the one inside the segment's ``repr()``
       (which renders ``"C:\\\\foo"`` as ``"C:\\\\\\\\foo"`` if ever
       rendered, but normal segments have none). This is the real
       opacity invariant: no path reconstruction should appear.
    """
    with pytest.raises(ConfigError) as exc_info:
        paths.validate_data_dir(Path(raw_path))
    message = str(exc_info.value)
    for parent_token in ("secret-data", "operator", "users"):
        assert parent_token not in message, (
            f"Opacity leak: message contains parent path token {parent_token!r}. "
            f"message={message!r}"
        )
    # Structural opacity: a message that contains the full resolved
    # path would include multiple backslashes. The only legitimate
    # backslash in a segment-only message comes from a repr-escaped
    # segment containing a backslash, which is impossible here (no
    # segment we construct contains one). So zero backslashes is the
    # strict invariant.
    assert "\\" not in message, (
        f"Opacity leak: message contains a path separator. message={message!r}"
    )


# --- Path-is-file test (AC #33) --------------------------------------------


def test_existing_file_rejected(tmp_path: Path) -> None:
    """A resolved path pointing to a file (not a directory) must raise."""
    file_path = tmp_path / "not_a_dir"
    file_path.write_text("contents", encoding="utf-8")
    with pytest.raises(ConfigError, match="is a file, not a directory"):
        paths.validate_data_dir(file_path)


def test_existing_directory_accepted(tmp_path: Path) -> None:
    """A resolved path pointing to an existing directory is accepted."""
    # ``tmp_path`` itself is a directory — validate should return None.
    paths.validate_data_dir(tmp_path)


def test_nonexistent_path_accepted(tmp_path: Path) -> None:
    """A non-existent path is acceptable — the caller will create it."""
    paths.validate_data_dir(tmp_path / "does-not-exist-yet")


# --- OSError translation test (AC #34) -------------------------------------


def test_os_error_from_resolve_translated_to_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Path.resolve`` raising ``OSError`` must translate to ``ConfigError``.

    AC #34 + Pattern #4 — chaining via ``raise ... from err`` is
    required so ``__cause__`` is populated.
    """
    sentinel_error = OSError("simulated resolution failure")

    def _raise(self: Path, strict: bool = False) -> Path:
        raise sentinel_error

    monkeypatch.setattr(Path, "resolve", _raise)

    with pytest.raises(ConfigError, match="Path could not be resolved") as exc_info:
        paths.validate_data_dir(Path("C:\\whatever"))
    # ``raise ... from err`` populates ``__cause__`` (Pattern #4).
    assert exc_info.value.__cause__ is sentinel_error
