"""Tests for ``nova.setup.completion`` — capture-status + completion-panel renders.

Story 2.4 Group D / G.34. Rich output is asserted via ``Console.record``
and the plain-text capture (``console.export_text``) so ANSI escapes are
normalized out of assertions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from nova.core.config import AppConfig, ExclusionConfig, ModeConfig, NovaConfig, UserSettings
from nova.core.types import SnapshotType
from nova.setup.completion import (
    BANNED_PHRASES,
    render_capture_status,
    render_completion_panel,
)
from nova.setup.initial_capture import CaptureResult
from nova.systems.eyes.models import WorkspaceSnapshot


def _mk_capture(
    *,
    status: str,
    captured: int = 0,
    dropped: int = 0,
) -> CaptureResult:
    return CaptureResult(
        snapshot=WorkspaceSnapshot(
            captured_at="2026-04-17T12:00:00+00:00",
            snapshot_type=SnapshotType.STARTUP,
            windows=(),
        ),
        status=status,  # type: ignore[arg-type]
        windows_captured=captured,
        windows_dropped=dropped,
    )


def _mk_mode(name: str, stem: str | None = None) -> tuple[str, ModeConfig]:
    mode = ModeConfig(
        name=name,
        apps=(AppConfig(name="Code", executable="code", args=()),),
    )
    return stem or name.lower().replace(" ", "-"), mode


def _mk_config(modes: dict[str, ModeConfig], api_key: str | None = None) -> NovaConfig:
    return NovaConfig(
        db_path=Path("C:/fake/nova.db"),
        data_dir=Path("C:/fake"),
        modes=modes,
        exclusions=ExclusionConfig(),
        settings=UserSettings(),
        api_key=api_key,
    )


# ---------------------------------------------------------------------------
# render_capture_status — AC #19
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "captured", "dropped", "expected_substring"),
    [
        ("full", 4, 0, "Captured initial workspace snapshot (4 apps)"),
        ("partial", 2, 1, "Captured 2 of 3 apps; setup will continue"),
        ("empty", 0, 0, "Workspace capture is empty"),
        ("unavailable", 0, 0, "Workspace capture unavailable"),
    ],
)
def test_capture_status_line_matches_spec(
    status: str, captured: int, dropped: int, expected_substring: str
) -> None:
    console = Console(record=True, width=80)
    render_capture_status(console, _mk_capture(status=status, captured=captured, dropped=dropped))
    out = console.export_text(clear=True)
    assert expected_substring in out


def test_capture_status_full_uses_green_check() -> None:
    console = Console(record=True, width=80, color_system="truecolor")
    render_capture_status(console, _mk_capture(status="full", captured=3))
    assert "✓" in console.export_text(clear=True)


def test_capture_status_degraded_uses_amber_warn() -> None:
    for status in ("partial", "empty", "unavailable"):
        console = Console(record=True, width=80, color_system="truecolor")
        captured_count = 1 if status == "partial" else 0
        dropped_count = 1 if status == "partial" else 0
        render_capture_status(
            console,
            _mk_capture(status=status, captured=captured_count, dropped=dropped_count),
        )
        assert "⚠" in console.export_text(clear=True), f"status={status} missing warning symbol"


def test_capture_status_exhaustive_dispatch() -> None:
    """Unknown status raises ``AssertionError`` — exhaustiveness guard."""
    bogus = CaptureResult(
        snapshot=WorkspaceSnapshot(
            captured_at="2026-04-17T12:00:00+00:00",
            snapshot_type=SnapshotType.STARTUP,
            windows=(),
        ),
        status="bogus",  # type: ignore[arg-type]
        windows_captured=0,
        windows_dropped=0,
    )
    console = Console(record=True, width=80)
    with pytest.raises(AssertionError):
        render_capture_status(console, bogus)


def test_capture_status_never_surfaces_raw_app_names() -> None:
    """AC #19 — status line contains only counts, never identities."""
    console = Console(record=True, width=80)
    render_capture_status(console, _mk_capture(status="full", captured=3))
    out = console.export_text(clear=True)
    assert "code" not in out.lower()
    assert "chrome" not in out.lower()


# ---------------------------------------------------------------------------
# render_completion_panel — AC #18, #20
# ---------------------------------------------------------------------------


def test_completion_panel_lists_single_mode() -> None:
    stem, mode = _mk_mode("coding")
    config = _mk_config({stem: mode})
    console = Console(record=True, width=80)
    render_completion_panel(console, config)
    out = console.export_text(clear=True)
    assert "You have 1 mode ready: coding" in out
    assert "uv run nova" in out
    assert "Setup complete" in out


def test_completion_panel_lists_multiple_modes_case_insensitive_sort() -> None:
    stem1, mode1 = _mk_mode("Coding", stem="coding")
    stem2, mode2 = _mk_mode("ad hoc mode", stem="ad-hoc-mode")
    stem3, mode3 = _mk_mode("Research", stem="research")
    config = _mk_config({stem1: mode1, stem2: mode2, stem3: mode3})
    console = Console(record=True, width=80)
    render_completion_panel(console, config)
    out = console.export_text(clear=True)
    assert "You have 3 modes ready:" in out
    # Case-insensitive sort: "ad hoc mode" < "Coding" < "Research".
    assert "ad hoc mode, Coding, Research" in out


def test_completion_panel_uses_bold_uv_run_nova() -> None:
    stem, mode = _mk_mode("coding")
    console = Console(record=True, width=80, color_system="truecolor")
    render_completion_panel(console, _mk_config({stem: mode}))
    # export_text strips styling but the command text must still be present.
    out = console.export_text(clear=True)
    assert "uv run nova" in out


def test_completion_panel_no_emoji_no_banned_phrases() -> None:
    stem, mode = _mk_mode("coding")
    console = Console(record=True, width=80)
    render_completion_panel(console, _mk_config({stem: mode}))
    out = console.export_text(clear=True)
    for phrase in BANNED_PHRASES:
        assert phrase.lower() not in out.lower(), f"banned phrase leaked: {phrase}"
    # Explicit whitelist — only these non-ASCII codepoints may appear.
    # * ``✓ ✗ ⚠ —`` — operational/personality symbols (Story 2.3 UX contract)
    # * U+2500–U+257F — Rich Panel box-drawing borders (rendering primitive,
    #   not content). Story 2.3's ``TestUxVoice`` uses the same allowance.
    allowed_non_ascii = {"✓", "✗", "⚠", "—"}
    for ch in out:
        if ord(ch) < 0x80:
            continue
        if 0x2500 <= ord(ch) <= 0x257F:
            continue  # box-drawing is structural
        assert ch in allowed_non_ascii, f"disallowed non-ASCII char: {ch!r}"


def test_completion_panel_does_not_scan_filesystem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #18 — completion panel reads from ``NovaConfig.modes``, not disk.

    Monkeypatches ``Path.iterdir`` and ``Path.exists`` to raise; render
    must still succeed because the panel sources from ``config.modes``.
    """

    def _boom(*_a: object, **_kw: object) -> object:
        raise OSError("filesystem probe forbidden in completion panel")

    monkeypatch.setattr(Path, "iterdir", _boom)
    monkeypatch.setattr(Path, "exists", _boom)

    stem, mode = _mk_mode("coding")
    console = Console(record=True, width=80)
    render_completion_panel(console, _mk_config({stem: mode}))
    assert "coding" in console.export_text(clear=True)


def test_completion_panel_zero_modes_renders_recovery_copy() -> None:
    """Defense in depth — Dev Notes § "Mode enumeration for the completion message"."""
    console = Console(record=True, width=80)
    render_completion_panel(console, _mk_config({}))
    out = console.export_text(clear=True)
    assert "no modes ready" in out.lower()
    assert "re-run setup.bat" in out.lower()


def test_completion_panel_renders_cleanly_at_80_columns() -> None:
    """AC #21 — no mid-line wrap on a realistic mode list."""
    modes = {
        "coding": _mk_mode("coding", "coding")[1],
        "study": _mk_mode("study", "study")[1],
        "research": _mk_mode("research", "research")[1],
    }
    console = Console(record=True, width=80)
    render_completion_panel(console, _mk_config(modes))
    out = console.export_text(clear=True)
    # No rendered line exceeds 80 cols.
    for line in out.splitlines():
        assert len(line) <= 80, f"line exceeds 80 cols: {line!r}"
