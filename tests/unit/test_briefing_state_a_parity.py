"""Visual parity between the new Ritual+Skin pipeline and setup's ``_render_state_a``.

The pre-flag note for Story 3.3 (epic-3-story-preflags.md:24-33) mandates
that the bare-``nova`` boot State A render and setup's first-run State A
render produce identical visible output. Two renderers, one product
contract.

Plain-text parity (AC #20) is the **core product contract** — it locks
panel chrome (title, border characters, padding) and body copy. ANSI
parity (companion test) is a **styling-regression guard** with documented
brittleness on Rich version upgrades; it is NOT a ship blocker on its
own when the plain-text test still passes.
"""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from nova.adapters.rich.skin import RichSkinAdapter
from nova.core.types import BriefingState, CapabilityTier
from nova.setup.__main__ import _render_state_a as setup_render_state_a
from nova.systems.brain.models import BriefingAggregate
from nova.systems.ritual.system import RitualSystem


def _build_console(*, color_system: str | None = None) -> Console:
    return Console(
        record=True,
        file=StringIO(),
        width=80,
        color_system=color_system,  # type: ignore[arg-type]
        legacy_windows=False,
        force_terminal=True,
    )


async def _render_via_pipeline(*, color_system: str | None) -> str:
    """Render State A through the new Ritual + Rich-Skin pipeline."""
    aggregate = BriefingAggregate(
        last_session=None,
        last_snapshot=None,
        last_seed=None,
        available_modes=(),
        recent_memory=(),
    )
    view_model = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.FIRST_RUN,
        tier=CapabilityTier.OFFLINE,  # tier-orthogonal — should not affect output
    )
    console = _build_console(color_system=color_system)
    await RichSkinAdapter(console=console).render_briefing_card(view_model)
    return console.export_text()


def _render_via_setup(*, color_system: str | None) -> str:
    """Render State A through setup's existing direct-Panel renderer."""
    console = _build_console(color_system=color_system)
    setup_render_state_a(console)
    return console.export_text()


@pytest.mark.asyncio
async def test_state_a_plain_text_matches_setup_render() -> None:
    """Core product contract — plain-text output is identical between renderers.

    Locks panel chrome (title text "N.O.V.A.", border characters,
    padding) + body copy (the two locked intro lines). ANSI styling is
    NOT compared here — see ``test_state_a_ansi_byte_stream_matches_setup_render``.

    A failure here means the user-visible product diverged. **This is
    the test that must always pass.**
    """
    pipeline_output = await _render_via_pipeline(color_system=None)
    setup_output = _render_via_setup(color_system=None)
    assert pipeline_output == setup_output, (
        "RichSkinAdapter State A plain-text output diverged from setup's "
        "_render_state_a. The bare-`nova` boot State A render and setup's "
        "first-run State A render must produce identical visible output."
    )


@pytest.mark.brittle
@pytest.mark.asyncio
async def test_state_a_ansi_byte_stream_matches_setup_render() -> None:
    """Styling-regression guard — ANSI byte stream is identical between renderers.

    STRICTER than the plain-text parity — catches drift in style markers,
    color codes, and Rich-version-specific spacing.

    Marked ``brittle`` (review finding D2) so default ``pytest`` runs
    deselect it — opt in with ``pytest -m brittle`` after a Rich /
    terminal-stack change to verify the ANSI bytes still match. The
    plain-text parity test (above) remains in the default suite as
    the IRONCLAD product-contract guard.

    BRITTLENESS NOTE: this test will fail on Rich version upgrades or
    terminal-detection differences even when the user-visible product
    is unchanged. If the plain-text parity passes but this ANSI test
    fails, the rendered output is still correct — investigate whether
    the styling change is visually meaningful, update the test snapshot
    if not, or fix the renderer if it is. Never silence this test by
    removing the marker without a triage note.
    """
    pipeline_console = _build_console(color_system="truecolor")
    aggregate = BriefingAggregate(
        last_session=None,
        last_snapshot=None,
        last_seed=None,
        available_modes=(),
        recent_memory=(),
    )
    view_model = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.FIRST_RUN,
        tier=CapabilityTier.OFFLINE,
    )
    await RichSkinAdapter(console=pipeline_console).render_briefing_card(view_model)
    pipeline_ansi = pipeline_console.export_text(styles=True)

    setup_console = _build_console(color_system="truecolor")
    setup_render_state_a(setup_console)
    setup_ansi = setup_console.export_text(styles=True)

    assert pipeline_ansi == setup_ansi, (
        "ANSI byte stream diverged between RichSkinAdapter and setup's "
        "_render_state_a. Plain-text parity test should be checked first — "
        "if that passes, this failure is a styling-impl drift, not a "
        "product regression. See the test docstring for triage guidance."
    )
