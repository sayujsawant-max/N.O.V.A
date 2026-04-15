"""RitualPort — briefing assembly + shutdown ceremony.

Story 1.9 (AC #4) pins two T1 methods: :meth:`RitualPort.build_briefing`
(consume aggregate, return render-ready view model) and
:meth:`RitualPort.begin_shutdown` (open the seed-capture flow).

Ritual owns ceremony logic; Nerve decides when ceremonies run
(project-context.md:68). Ritual itself does NOT persist — it orchestrates
Brain for persistence (``Ritual -> Brain: store_session(...)`` in the T1
continuity loop). Persistence is BrainPort's surface, not RitualPort's.

Port rules (architecture.md:948-986):

- :class:`RitualPort` is a :class:`typing.Protocol` (structural subtyping).
- Every method is ``async def`` with an ellipsis body.
- No adapter-specific types — only domain types from ``core/types``,
  ``systems/brain/models``, and ``systems/ritual/models``.
"""

from __future__ import annotations

from typing import Protocol

from nova.core.types import BriefingState, CapabilityTier
from nova.systems.brain.models import BriefingAggregate
from nova.systems.ritual.models import BriefingViewModel, ShutdownData


class RitualPort(Protocol):
    """Briefing + shutdown ceremony surface owned by Ritual."""

    async def build_briefing(
        self,
        aggregate: BriefingAggregate,
        state: BriefingState,
        tier: CapabilityTier,
    ) -> BriefingViewModel: ...

    async def begin_shutdown(self) -> ShutdownData: ...


__all__: list[str] = [
    "RitualPort",
]
