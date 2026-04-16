"""Unit tests for ``nova.setup.__main__`` — first-run entrypoint.

Covers Story 2.1 AC #23 (validate-only branch) and #41 (State A
rendering), plus Story 2.2 AC #19-22 (API key step wiring).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

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


@patch("nova.setup.__main__.run_api_key_step", return_value=False)
def test_main_no_args_renders_state_a_and_exits(
    _mock_api_key: MagicMock,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running without flags renders State A and returns 0."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
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


# --- Story 2.2: API key step wiring (AC #19-22) -----------------------------


@patch("nova.setup.__main__.run_api_key_step", return_value=True)
def test_main_calls_api_key_step_after_state_a(
    mock_api_key: MagicMock,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main([])`` calls ``run_api_key_step`` after rendering State A."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    result = main([])
    assert result == EXIT_OK
    mock_api_key.assert_called_once()
    # State A still rendered
    out = capsys.readouterr().out
    assert "N.O.V.A." in out


@patch("nova.setup.__main__.run_api_key_step", return_value=True)
def test_main_exits_zero_when_key_configured(
    _mock: MagicMock,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit code is 0 regardless of whether key was configured."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert main([]) == EXIT_OK


@patch("nova.setup.__main__.run_api_key_step", return_value=False)
def test_main_exits_zero_when_key_skipped(
    _mock: MagicMock,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit code is 0 when API key is skipped."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert main([]) == EXIT_OK


def test_validate_only_branch_unchanged_after_story_22(tmp_path: Path) -> None:
    """Story 2.2 wiring does not affect --validate-only branch (regression)."""
    assert main(["--validate-only", str(tmp_path / "ok")]) == EXIT_OK
    assert main(["--validate-only", str(tmp_path / "CON")]) == EXIT_CONFIG_ERROR


@patch("nova.setup.__main__.run_api_key_step", return_value=False)
@patch("nova.setup.__main__._resolve_data_dir", return_value=None)
def test_main_skips_api_key_when_localappdata_missing(
    _mock_resolve: MagicMock,
    _mock_api_key: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When LOCALAPPDATA is not set, skip API key step gracefully."""
    result = main([])
    assert result == EXIT_OK
    _mock_api_key.assert_not_called()
    out = capsys.readouterr().out
    assert "LOCALAPPDATA" in out
