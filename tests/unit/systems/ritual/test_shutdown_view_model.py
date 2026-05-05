"""Tests for :meth:`RitualSystem.begin_shutdown` (Story 3.7).

Each test constructs a :class:`ShutdownState` fixture, calls
``begin_shutdown``, and asserts the returned :class:`ShutdownViewModel`
matches the AC's field-by-field expectations. Skin rendering is tested
separately in ``test_skin_adapter.py``.
"""

from __future__ import annotations

import pytest

from nova.systems.ritual.models import ShutdownState, ShutdownViewModel
from nova.systems.ritual.system import RitualSystem


def _state(
    *,
    session_id: int = 42,
    started_at: str = "2026-04-01T10:00:00+00:00",
    ended_at: str = "2026-04-01T10:30:00+00:00",
    active_mode_stem: str | None = "coding",
    active_mode_display_name: str | None = "Coding",
    apps_used: tuple[str, ...] = ("VS Code",),
) -> ShutdownState:
    return ShutdownState(
        session_id=session_id,
        started_at=started_at,
        ended_at=ended_at,
        active_mode_stem=active_mode_stem,
        active_mode_display_name=active_mode_display_name,
        apps_used=apps_used,
    )


@pytest.mark.asyncio
async def test_begin_shutdown_returns_locked_title_and_prompt() -> None:
    ritual = RitualSystem()
    vm = await ritual.begin_shutdown(_state())
    assert vm.title == "Session ending"
    assert vm.prompt_text == "What should you pick up tomorrow?"


@pytest.mark.asyncio
async def test_begin_shutdown_renders_mode_label_when_active_mode_present() -> None:
    ritual = RitualSystem()
    vm = await ritual.begin_shutdown(_state(active_mode_display_name="Coding"))
    assert vm.mode_label == "Mode: Coding"


@pytest.mark.asyncio
async def test_begin_shutdown_omits_mode_label_when_no_active_mode() -> None:
    ritual = RitualSystem()
    vm = await ritual.begin_shutdown(_state(active_mode_stem=None, active_mode_display_name=None))
    assert vm.mode_label is None


@pytest.mark.asyncio
async def test_begin_shutdown_renders_duration_label_via_format_duration_seconds() -> None:
    ritual = RitualSystem()
    vm = await ritual.begin_shutdown(
        _state(
            started_at="2026-04-01T10:00:00+00:00",
            ended_at="2026-04-01T11:23:00+00:00",
        )
    )
    assert vm.duration_label == "Duration: 1h 23m"


@pytest.mark.asyncio
async def test_begin_shutdown_clamps_negative_duration_to_zero() -> None:
    """Clock skew defense — ended_at < started_at clamps to 0s."""
    ritual = RitualSystem()
    vm = await ritual.begin_shutdown(
        _state(
            started_at="2026-04-01T11:00:00+00:00",
            ended_at="2026-04-01T10:00:00+00:00",
        )
    )
    assert vm.duration_label == "Duration: 0s"


@pytest.mark.asyncio
async def test_begin_shutdown_zero_duration_renders_as_0s() -> None:
    ritual = RitualSystem()
    vm = await ritual.begin_shutdown(
        _state(
            started_at="2026-04-01T10:00:00+00:00",
            ended_at="2026-04-01T10:00:00+00:00",
        )
    )
    assert vm.duration_label == "Duration: 0s"


@pytest.mark.asyncio
async def test_begin_shutdown_renders_apps_label_with_comma_separated_names() -> None:
    ritual = RitualSystem()
    vm = await ritual.begin_shutdown(_state(apps_used=("VS Code", "Postman")))
    assert vm.apps_label == "Apps: VS Code, Postman"


@pytest.mark.asyncio
async def test_begin_shutdown_omits_apps_label_when_apps_used_empty() -> None:
    ritual = RitualSystem()
    vm = await ritual.begin_shutdown(_state(apps_used=()))
    assert vm.apps_label is None


@pytest.mark.asyncio
async def test_begin_shutdown_escapes_commas_in_app_names() -> None:
    """Comma-disambiguation per Story 3.3's _escape_label_value precedent."""
    ritual = RitualSystem()
    vm = await ritual.begin_shutdown(_state(apps_used=("Foo, Bar",)))
    assert vm.apps_label == "Apps: Foo\\, Bar"


@pytest.mark.asyncio
async def test_begin_shutdown_passes_session_id_through() -> None:
    ritual = RitualSystem()
    vm = await ritual.begin_shutdown(_state(session_id=42))
    assert vm.session_id == 42


@pytest.mark.asyncio
async def test_begin_shutdown_handles_trailing_z_iso_strings() -> None:
    """Trailing-Z normalization in diff_iso_seconds — no fromisoformat error."""
    ritual = RitualSystem()
    vm = await ritual.begin_shutdown(
        _state(
            started_at="2026-04-01T10:00:00Z",
            ended_at="2026-04-01T10:30:00Z",
        )
    )
    assert vm.duration_label == "Duration: 30m"


@pytest.mark.asyncio
async def test_begin_shutdown_returns_shutdown_view_model_type() -> None:
    """Type-check the return — Protocol satisfaction by the SkinPort signature."""
    ritual = RitualSystem()
    vm = await ritual.begin_shutdown(_state())
    assert isinstance(vm, ShutdownViewModel)
