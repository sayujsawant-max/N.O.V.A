"""VoicePort — personality text generation (briefings, summaries, confirmations).

Story 1.9 (AC #4) pins three T1 methods:
:meth:`VoicePort.generate_prose_enrichment` (briefing-time Claude prose),
:meth:`VoicePort.generate_restore_summary` (post-mode-restore one-liner),
:meth:`VoicePort.generate_shutdown_confirmation` (seed-acknowledgement line).

All three return plain ``str``. Wrapping the return in a single-field
dataclass (``BriefingText`` / ``ResponseText`` / ``ProseEnrichment``) adds
no T1 value — no invariant, no metadata. If Story 3.3+ decides prose-
enrichment needs to carry metadata (``tier_used``, ``tokens_consumed``),
that story introduces ``systems/voice/models.py``.

Tier behavior (architecture.md:811, 333-337): Full tier invokes Claude
through :class:`nova.adapters.claude.reasoning.ClaudeReasoningAdapter`.
Degraded / Offline tiers return cached or pre-computed structured strings
— ``generate_prose_enrichment`` may return ``None`` to signal "no prose
for this briefing" (Ritual then renders without the optional prose row).

Port rules (architecture.md:948-986, 1463):

- :class:`VoicePort` is a :class:`typing.Protocol` (structural subtyping).
- Every method is ``async def`` with an ellipsis body.
- Adapter types (``anthropic.Message``, API response objects) stay trapped
  in ``adapters/claude/reasoning.py`` — only domain types and primitives
  cross this boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from nova.systems.brain.models import BriefingAggregate
from nova.systems.hands.models import ActionResult


class VoicePort(Protocol):
    """Personality text-generation surface owned by Voice."""

    async def generate_prose_enrichment(self, aggregate: BriefingAggregate) -> str | None: ...

    async def generate_restore_summary(
        self, results: Sequence[ActionResult], context: str
    ) -> str: ...

    async def generate_shutdown_confirmation(self, seed: str) -> str: ...


__all__: list[str] = [
    "VoicePort",
]
