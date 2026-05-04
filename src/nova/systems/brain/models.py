"""Brain-layer domain models consumed through :mod:`nova.ports.brain`.

These frozen dataclasses are the portable cross-system vocabulary for the
Brain system: session summaries, memory items, briefing aggregates,
workspace-snapshot inputs, deletion previews, and the transparency-model
projection. Per Story 1.9 AC #5, every sequence-valued field is a
``tuple[T, ...]`` (not ``list[T]``) so frozen-dataclass guarantees extend
to the containers themselves, not just the outer attribute binding.
Story 1.3 established the tuple-over-list precedent for frozen-dataclass
sequence fields (see ``ModeRestored.apps_launched`` in
``nova.core.events``).

Only ``.models`` crosses system boundaries (Story 1.9 AC #8). Ports may
import any type declared here; system-internal Brain logic lives in the
``SqliteBrainAdapter`` (Story 3.1, :mod:`nova.adapters.sqlite.brain`).

Note on ``ModeInfo`` vs ``ModeConfig``: ``ModeInfo`` (declared here) is the
Brain-layer projection used in briefing assembly — mode name plus usage
metadata (``last_used_at``). ``ModeConfig`` (in :mod:`nova.core.config`)
is the full file-backed schema with apps, folders, and URLs. They are
distinct types with non-overlapping field sets; Story 1.9 AC #5 locks
this distinction via ``test_mode_info_is_distinct_from_mode_config``.

Story 3.1 reshape
-----------------
- ``SessionSummary`` gains ``summary: str | None`` (the session-summary
  text field persisted in ``sessions.summary``). The adapter reads it
  back in ``get_last_session``.
- ``WorkspaceSnapshotInput`` (NEW) — typed inbound DTO for
  ``BrainPort.store_snapshot``. Carries the flat shape that matches
  Story 2.4's locked ``workspace_snapshots.workspace_data`` JSON (three
  fields: ``apps``, ``focused_app``, ``mode_name``) plus ``captured_at``
  and ``snapshot_type`` for the row columns. The adapter serializes the
  last four fields into the JSON; ``captured_at`` goes straight to its
  column. No raw ``dict`` crosses the port boundary.
- ``Session`` and ``SessionData`` (Story 1.9 stub shapes for
  ``load_last_session`` / ``store_session``) are removed — both methods
  are dropped from ``BrainPort`` in Story 3.1 and no adapter ever
  implemented them.
"""

from __future__ import annotations

from dataclasses import dataclass

from nova.core.types import MemoryCategory, SnapshotType
from nova.systems.eyes.models import WorkspaceSnapshot


@dataclass(frozen=True)
class SessionSummary:
    """Summary projection of a session, surfaced to briefings and shutdown.

    Returned by ``BrainPort.get_last_session`` (Story 3.1). The adapter
    computes ``duration_seconds`` from ``started_at`` and ``ended_at``
    (or ``0`` when ``ended_at is None`` — the interrupted-session
    convention). ``is_complete`` is coerced from SQLite's INTEGER
    ``0`` / ``1`` to Python ``bool`` at the adapter boundary.

    ``summary`` (Story 3.1 addition) is the ``sessions.summary`` column
    — free-form text written by Story 3.7's shutdown flow, ``None`` for
    the setup-session row (Story 2.4 writes ``NULL``) and for any
    session that exited without a summary.
    """

    session_id: int
    started_at: str
    ended_at: str | None
    duration_seconds: int
    mode_name: str | None
    summary: str | None
    is_complete: bool


@dataclass(frozen=True)
class WorkspaceSnapshotInput:
    """Typed inbound DTO for ``BrainPort.store_snapshot`` (Story 3.1).

    The adapter writes ``captured_at`` directly into
    ``workspace_snapshots.captured_at``, ``snapshot_type`` (via
    ``str(...)``) into the matching column, and serializes
    ``{apps, focused_app, mode_name}`` into the ``workspace_data`` JSON
    column using the Story 2.4-locked compact shape:
    ``{"apps":[...],"focused_app":...,"mode_name":...}`` with
    ``separators=(",",":"), ensure_ascii=False, allow_nan=False``.

    ``apps`` is ``tuple[str, ...]`` (not ``list[str]``) per Story 1.9
    AC #5 / cross-cutting-patterns.md #3 — sequence fields on frozen
    dataclasses must be tuples for genuine immutability.

    No raw ``dict`` crosses the port boundary — callers construct this
    typed input; the adapter owns JSON ser/deser internally.
    """

    captured_at: str
    snapshot_type: SnapshotType
    apps: tuple[str, ...]
    focused_app: str | None
    mode_name: str | None


@dataclass(frozen=True)
class MemoryItem:
    """Row-shaped memory record returned by ``BrainPort.query_memory``.

    Epic 5 scope — Story 3.1's adapter stubs ``query_memory`` with
    ``NotImplementedError("Epic 5 scope")`` but the domain model ships
    today so downstream briefing/memory consumers (Stories 3.7 seed
    capture, Epic 4 memory accumulation) have the type to reference.
    """

    id: int
    category: MemoryCategory
    content: str
    created_at: str


@dataclass(frozen=True)
class ModeInfo:
    """Brain-layer mode projection: canonical stem + display label + usage metadata.

    Distinct from :class:`nova.core.config.ModeConfig`. See the module
    docstring for the rationale behind the two-type split.

    Story 3.2 reshape (stem / display_name split)
    ---------------------------------------------
    ``stem`` and ``display_name`` are two independent identifiers. They
    exist as separate fields to prevent a silent conflation between the
    canonical mode identifier (filename-derived, stable, safe to use as
    a dict key or a SQL value) and the user-facing label (editable in
    YAML at any time, used purely for rendering).

    Cross-story contract — ``sessions.mode_name`` stores the STEM, not
    the display name. Stories 3.5 (Nerve session lifecycle), 3.6 (mode
    restore), and 3.7 (shutdown flow) are responsible for honoring this
    contract on the write side; Story 3.2 locks it on the read side by
    querying :meth:`BrainPort.get_mode_last_used` with the stem only.
    Renaming ``modes/coding.yaml``'s ``name: "Coding"`` to
    ``name: "Deep Coding"`` therefore does not orphan any prior session
    history — the stem ``coding`` persists in every session row and
    every briefing lookup, while only the rendered label changes.

    Field provenance
    ----------------
    - ``stem``: dict key in :attr:`nova.core.config.NovaConfig.modes`
      (derived from the mode YAML file stem).
    - ``display_name``: :attr:`nova.core.config.ModeConfig.name` (the
      ``name:`` field in the mode YAML).
    - ``app_count``: ``len(ModeConfig.apps)``.
    - ``is_default``: :attr:`nova.core.config.ModeConfig.is_default`.
      Multiple modes may carry this flag simultaneously; tie-break is
      the consumer's concern (Story 3.3 / 3.5).
    - ``last_used_at``: ISO-8601 UTC string from
      :meth:`BrainPort.get_mode_last_used`, or ``None`` if the mode
      has never appeared in a session row.
    """

    stem: str
    display_name: str
    app_count: int
    is_default: bool
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
    """Read aggregate used by Nerve to drive briefing assembly (Story 3.2+).

    Historically returned by ``BrainPort.load_briefing_aggregate`` (Story
    1.9 stub). Story 3.1 removes that port method because epic 3.2 places
    briefing assembly in Nerve, not Brain (Brain provides granular
    persisted-fact queries only). The ``BriefingAggregate`` type itself
    survives — it is the Nerve-assembled shape consumed by Ritual /
    Voice / Skin. All sequence fields are ``tuple[T, ...]`` for true
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
    "SessionSummary",
    "TransparencyModel",
    "WorkspaceSnapshotInput",
]
