"""BrainPort — memory, sessions, transparency, deletion.

Story 1.9 (AC #4) pins the T1 method set. Additions belong to the
downstream stories that ship the consuming logic (Story 3.1 ships
:class:`nova.adapters.sqlite.brain.SqliteBrainAdapter`; Stories 5.1/5.2
extend the transparency / deletion surfaces).

Port rules (architecture.md:948-986):

- :class:`BrainPort` is a :class:`typing.Protocol` (structural subtyping).
- Every method is ``async def`` with an ellipsis body.
- No adapter-specific types in signatures — only domain types from
  ``core/types`` and ``systems/brain/models``.
- No imports from ``adapters/`` or from another system's internals.
"""

from __future__ import annotations

from typing import Protocol

from nova.systems.brain.models import (
    BriefingAggregate,
    DeletionPreview,
    DeletionResult,
    MemoryItem,
    Session,
    SessionData,
    TransparencyModel,
)


class BrainPort(Protocol):
    """Memory / session / transparency / deletion surface owned by Brain."""

    async def load_last_session(self) -> Session | None: ...

    async def store_session(self, session: SessionData) -> None: ...

    async def load_briefing_aggregate(self) -> BriefingAggregate: ...

    async def query_memory(self, query: str) -> list[MemoryItem]: ...

    async def delete_matching(self, target: str) -> DeletionPreview: ...

    async def confirm_deletion(self, preview: DeletionPreview) -> DeletionResult: ...

    async def get_transparency_model(self) -> TransparencyModel: ...


__all__: list[str] = [
    "BrainPort",
]
