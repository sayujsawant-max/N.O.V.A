"""Ritual-layer domain models consumed through :mod:`nova.ports.ritual`.

``BriefingViewModel`` is the render-ready contract Ritual produces from a
:class:`nova.systems.brain.models.BriefingAggregate` plus briefing state and
capability tier. ``ShutdownData`` is the seed/prompt carrier for the
shutdown flow.

Only ``.models`` crosses system boundaries (Story 1.9 AC #8).

Design rule — prose is additive, not structural (architecture.md:715):
``prose_enrichment`` is optional Claude-generated flavor rendered *after*
the structured fields by Skin, never *instead of* them. When cloud is
unreachable (Degraded / Offline tiers), ``prose_enrichment`` is ``None``
and the structured fields alone are always sufficient to render a complete
briefing.
"""

from __future__ import annotations

from dataclasses import dataclass

from nova.core.types import BriefingState, CapabilityTier
from nova.systems.brain.models import ModeInfo


@dataclass(frozen=True)
class BriefingViewModel:
    """Render-ready briefing output consumed by ``SkinPort.render_briefing_card``.

    Every field is explicit — no hidden state, no lazy-computed properties.
    Skin renders each field mechanically; any logic to resolve ``None``
    values happens in Ritual before the view model is handed off.

    ``auto_start_setup`` is ``True`` only for :class:`BriefingState.FIRST_RUN`
    (State A) — the first-run card auto-transitions into the setup wizard
    per Epic 2. ``available_modes`` and ``suggested_mode`` are empty /
    ``None`` for State A.
    """

    state: BriefingState
    tier: CapabilityTier
    title: str
    prompt_text: str | None
    auto_start_setup: bool
    seed_text: str | None
    last_mode: str | None
    last_duration_seconds: int | None
    last_apps: tuple[str, ...]
    available_modes: tuple[ModeInfo, ...]
    suggested_mode: ModeInfo | None
    prose_enrichment: str | None


@dataclass(frozen=True)
class ShutdownData:
    """Opening payload of the shutdown flow, returned by ``RitualPort.begin_shutdown``.

    ``prompt_text`` is the Voice-generated (or tier-degraded structured)
    seed prompt. ``last_context`` is an opaque summary of what the user
    was working on — ``None`` if the session never accumulated context
    (e.g., first-run session exiting immediately after setup).
    """

    session_id: int
    prompt_text: str
    last_context: str | None


__all__: list[str] = [
    "BriefingViewModel",
    "ShutdownData",
]
