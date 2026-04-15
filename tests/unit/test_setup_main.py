"""Unit tests for ``nova.setup.__main__`` — first-run entrypoint.

Covers Story 2.1 AC #23 (validate-only branch) and #41 (State A
rendering). The full interactive wizard is Stories 2.2–2.4; these
tests only exercise the scaffolding landed in 2.1.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from nova.setup.__main__ import (
    EXIT_CONFIG_ERROR,
    EXIT_OK,
    _handle_validate_only,
    _render_state_a,
    main,
)

# --- --validate-only branch (AC #23) ---------------------------------------


def test_validate_only_accepts_valid_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A valid path produces ``✓`` on stdout and exit 0."""
    console = Console(force_terminal=False, no_color=True)
    result = _handle_validate_only(str(tmp_path / "ok"), console)
    assert result == EXIT_OK
    out = capsys.readouterr().out
    assert "Path is valid" in out


def test_validate_only_rejects_reserved_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reserved Windows name produces ``✗`` + ``Setup stopped.`` and exit 1."""
    console = Console(force_terminal=False, no_color=True)
    result = _handle_validate_only(str(tmp_path / "CON"), console)
    assert result == EXIT_CONFIG_ERROR
    out = capsys.readouterr().out
    assert "reserved Windows name" in out
    assert "Setup stopped" in out


def test_validate_only_message_contains_no_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Failure output is a single-line reason — no traceback / exception class name."""
    console = Console(force_terminal=False, no_color=True)
    _handle_validate_only(str(tmp_path / "bad<"), console)
    out = capsys.readouterr().out
    assert "Traceback" not in out
    assert "ConfigError" not in out


def test_main_validate_only_exits_zero_on_valid(tmp_path: Path) -> None:
    """``main(["--validate-only", <ok>])`` returns 0."""
    assert main(["--validate-only", str(tmp_path / "ok")]) == EXIT_OK


def test_main_validate_only_exits_one_on_invalid(tmp_path: Path) -> None:
    """``main(["--validate-only", <reserved>])`` returns 1."""
    assert main(["--validate-only", str(tmp_path / "NUL")]) == EXIT_CONFIG_ERROR


# --- State A rendering (AC #41) --------------------------------------------


def test_main_no_args_renders_state_a_and_exits(capsys: pytest.CaptureFixture[str]) -> None:
    """Running without flags renders State A and returns 0."""
    result = main([])
    assert result == EXIT_OK
    out = capsys.readouterr().out
    assert "N.O.V.A." in out
    assert "First session. No history yet." in out
    assert "Running setup to create your workspace modes." in out


def test_render_state_a_is_side_effect_only(capsys: pytest.CaptureFixture[str]) -> None:
    """``_render_state_a`` writes to stdout; its return type is ``None``."""
    console = Console(force_terminal=False, no_color=True)
    _render_state_a(console)
    out = capsys.readouterr().out
    # Panel title + body text must all appear.
    assert "N.O.V.A." in out
    assert "Personal AI Session Companion" in out


def test_state_a_output_has_no_emoji(capsys: pytest.CaptureFixture[str]) -> None:
    """Voice doctrine: no emoji in operational output. ``✓``/``✗``/``⚠`` are OK (not emoji)."""
    console = Console(force_terminal=False, no_color=True)
    _render_state_a(console)
    out = capsys.readouterr().out
    # Emoji sentinels that are most likely to slip into "friendly" UI copy.
    for forbidden in ["🚀", "✨", "🎉", "👋", "😊"]:
        assert forbidden not in out, f"Emoji {forbidden!r} leaked into State A output."


def test_state_a_output_has_no_exclamation_marks(capsys: pytest.CaptureFixture[str]) -> None:
    """Voice doctrine: no exclamation marks in operational output."""
    console = Console(force_terminal=False, no_color=True)
    _render_state_a(console)
    out = capsys.readouterr().out
    assert "!" not in out
