"""Brain-layer domain models consumed through :mod:`nova.ports.brain`.

These frozen dataclasses are the portable cross-system vocabulary for the
Brain system: session records, memory items, briefing aggregates, deletion
previews, and the transparency-model projection. Per Story 1.9 AC #5, every
sequence-valued field is a ``tuple[T, ...]`` (not ``list[T]``) so frozen-
dataclass guarantees extend to the containers themselves, not just the outer
attribute binding. Story 1.3 established the tuple-over-list precedent for
frozen-dataclass sequence fields (see ``ModeRestored.apps_launched`` in
``nova.core.events``).

Only ``.models`` crosses system boundaries (Story 1.9 AC #8). Ports may
import any type declared here; system-internal Brain logic lives in a future
``systems/brain/system.py`` (Story 3.1 scope) that does NOT cross into other
systems' ports.

Note on ``ModeInfo`` vs ``ModeConfig``: ``ModeInfo`` (declared here) is the
Brain-layer projection returned through ``BrainPort.load_briefing_aggregate``
— mode name plus usage metadata (``last_used_at``). ``ModeConfig`` (in
:mod:`nova.core.config`) is the full file-backed schema with apps, folders,
and URLs. They are distinct types with non-overlapping field sets; Story 1.9
AC #5 locks this distinction via
``test_mode_info_is_distinct_from_mode_config``.
"""

from __future__ import annotations

from dataclasses import dataclass

from nova.core.types import MemoryCategory
from nova.systems.eyes.models import WorkspaceSnapshot


@dataclass(frozen=True)
class Session:
    """Row-shaped session record returned by ``BrainPort.load_last_session``.

    ``ended_at`` is ``None`` for in-flight sessions (the current one, or a
    crash-recovered incomplete session). ``is_complete`` is ``False`` for
    crash-recovery paths (Story 3.10) even if the row carries an
    ``ended_at`` timestamp.
    """

    id: int
    started_at: str
    ended_at: str | None
    mode_name: str | None
    is_complete: bool


@dataclass(frozen=True)
class SessionData:
    """Durable session payload passed to ``BrainPort.store_session``.

    ``seed_text`` is ``None`` when the user skipped the shutdown seed
    prompt. ``mode_name`` is ``None`` when the session exited without ever
    selecting a mode (e.g., cancelled first-run setup).
    """

    seed_text: str | None
    mode_name: str | None
    duration_seconds: int
    ended_at: str


@dataclass(frozen=True)
class SessionSummary:
    """Summary projection of a session, surfaced to briefings and shutdown.

    Distinct from :class:`Session` in that this is the BriefingAggregate-facing
    shape — includes ``duration_seconds`` precomputed — whereas :class:`Session`
    is the adapter-facing row shape.
    """

    session_id: int
    started_at: str
    ended_at: str | None
    duration_seconds: int
    mode_name: str | None
    is_complete: bool


@dataclass(frozen=True)
class MemoryItem:
    """Row-shaped memory record returned by ``BrainPort.query_memory``."""

    id: int
    category: MemoryCategory
    content: str
    created_at: str


@dataclass(frozen=True)
class ModeInfo:
    """Brain-layer mode projection: name + usage metadata.

    Distinct from :class:`nova.core.config.ModeConfig`. See the module
    docstring for the rationale behind the two-type split.
    """

    name: str
    last_used_at: str | None


@dataclass(frozen=True)
class DeletionPreview:
    """Preview of a pending deletion, returned by ``BrainPort.delete_matching``.

    The preview is immutable; the caller passes it back to
    ``BrainPort.confirm_deletion`` to actually perform the deletion.
    ``target`` is an opaque reference (e.g., ``"mode 'opaque'"``) — never
    a raw app name or window title (project-context.md sensitive-content
    rule).
    """

    target: str
    affected_tables: tuple[str, ...]
    items_to_delete: int


@dataclass(frozen=True)
class DeletionResult:
    """Outcome of a confirmed deletion, returned by ``BrainPort.confirm_deletion``."""

    target: str
    items_deleted: int
    success: bool


@dataclass(frozen=True)
class TransparencyModel:
    """T1 transparency snapshot returned by ``BrainPort.get_transparency_model``.

    Story 1.9 ships the minimal shape — aggregate counts across the four
    T1 tables. Story 5.1 extends this to include per-category breakdowns,
    excluded-app opacity placeholders, and the full audit view.
    """

    sessions_count: int
    memory_items_count: int
    snapshots_count: int
    audit_log_count: int


@dataclass(frozen=True)
class BriefingAggregate:
    """One-shot read aggregate returned by ``BrainPort.load_briefing_aggregate``.

    Consumed by Ritual to build the BriefingViewModel (architecture.md T1
    Continuity Loop). All sequence fields are ``tuple[T, ...]`` for true
    immutability under ``frozen=True``.

    Cross-system model reference: ``last_snapshot`` is
    :class:`nova.systems.eyes.models.WorkspaceSnapshot` — one system's
    models importing another's is the explicit cross-system contract
    (Story 1.9 AC #8: ``.models`` is the one portable cross-system
    suffix).
    """

    last_session: SessionSummary | None
    last_snapshot: WorkspaceSnapshot | None
    last_seed: str | None
    available_modes: tuple[ModeInfo, ...]
    recent_memory: tuple[MemoryItem, ...]


__all__: list[str] = [
    "BriefingAggregate",
    "DeletionPreview",
    "DeletionResult",
    "MemoryItem",
    "ModeInfo",
    "Session",
    "SessionData",
    "SessionSummary",
    "TransparencyModel",
]
