"""BrainPort — memory, sessions, seeds, snapshots, transparency, deletion.

Story 3.1 reshapes the Story 1.9 Protocol stub: the three aggregate
stubs (``load_last_session``, ``store_session``,
``load_briefing_aggregate``) are removed in favor of six granular
persisted-fact methods the epic 3.1 / 3.2 ACs specify.
``SqliteBrainAdapter`` (:mod:`nova.adapters.sqlite.brain`) is the T1
concrete implementation.

Epic 5 methods (``query_memory``, ``delete_matching``,
``confirm_deletion``, ``get_transparency_model``) stay as Protocol
declarations; Story 3.1's adapter stubs each with
``NotImplementedError("Epic 5 scope")`` until Epic 5 owns them.

Port rules (architecture.md:948-986):

- :class:`BrainPort` is a :class:`typing.Protocol` (structural subtyping).
- Every method is ``async def`` with an ellipsis body.
- No adapter-specific types in signatures — only domain types from
  ``core/types`` and ``systems/{brain,eyes}/models``.
- No imports from ``adapters/`` or from another system's internals.
"""

from __future__ import annotations

from typing import Protocol

from nova.systems.brain.models import (
    DeletionPreview,
    DeletionResult,
    MemoryItem,
    SessionSummary,
    TransparencyModel,
    WorkspaceSnapshotInput,
)
from nova.systems.eyes.models import WorkspaceSnapshot


class BrainPort(Protocol):
    """Persisted-fact surface owned by Brain.

    T1 scope (Story 3.1): session lifecycle, seed retrieval, snapshot
    storage/retrieval. Epic 5 scope: memory query, deletion, transparency.
    Story 3.2 will add ``get_mode_last_used`` for Nerve-side
    BriefingAggregate assembly.
    """

    async def create_session(self, mode_name: str | None, *, started_at: str | None) -> int: ...

    async def end_session(
        self,
        session_id: int,
        *,
        seed_text: str | None,
        summary: str | None,
        is_complete: bool,
    ) -> str: ...

    async def get_last_session(self) -> SessionSummary | None: ...

    async def get_last_seed(self) -> str | None: ...

    async def store_snapshot(self, session_id: int, snapshot: WorkspaceSnapshotInput) -> None: ...

    async def get_last_snapshot_for_session(self, session_id: int) -> WorkspaceSnapshot | None: ...

    async def query_memory(self, query: str) -> list[MemoryItem]: ...

    async def delete_matching(self, target: str) -> DeletionPreview: ...

    async def confirm_deletion(self, preview: DeletionPreview) -> DeletionResult: ...

    async def get_transparency_model(self) -> TransparencyModel: ...


__all__: list[str] = [
    "BrainPort",
]
