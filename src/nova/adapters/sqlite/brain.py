"""T1 SQLite adapter implementing :class:`nova.ports.brain.BrainPort`.

Story 3.1 ships the first concrete ``BrainPort`` implementation and
retires the Story 2.4 direct-SQL setup-time persistence seam
(:func:`nova.setup.initial_capture.persist_first_run` now routes
through this adapter). The ``execute_returning_lastrowid`` helper on
:class:`nova.core.storage.engine.SqliteStorageEngine` — added in Story
2.4 specifically anticipating this reuse — is the single "INSERT and
return id" primitive both setup and Brain now share.

Design invariants
-----------------
* **Statelessness.** The adapter captures the
  :class:`SqliteStorageEngine` reference in its constructor and nothing
  else. No per-call caching, no thread state, no background tasks.
  Lifetime equals composition-root lifetime.
* **No ``sqlite3`` import.** All database interaction goes through the
  engine, which already translates ``sqlite3.Error`` /
  ``sqlite3.Warning`` / ``OSError`` to :class:`StorageError` at its
  boundary. An AST guard test locks this (cross-cutting-patterns.md
  #2).
* **Two-layer error translation.** The engine handles storage-driver
  errors. This adapter translates only the non-storage classes it
  introduces itself — ``json.JSONDecodeError`` (from ``json.loads`` in
  snapshot reads) and ``ValueError`` (from ``SnapshotType(...)`` enum
  coercion of a corrupted persisted value) — via
  ``raise StorageError("brain adapter <op> failed") from err``
  (cross-cutting-patterns.md #4). Engine-translated ``StorageError``
  is **never** re-caught; re-catching would re-chain the exception and
  break the traceback contract.
* **Frozen dataclass domain types + tuple sequences.** All inputs and
  outputs are frozen dataclasses from
  :mod:`nova.systems.brain.models` and
  :mod:`nova.systems.eyes.models`. No raw ``dict`` crosses the port
  boundary — JSON ser/deser happens inside this module
  (cross-cutting-patterns.md #3).
* **Clock indirection (cross-cutting-patterns.md #1).** Timestamp
  stamping (``create_session`` with ``started_at=None``,
  ``end_session``) routes through ``events._utc_now_iso()`` via the
  module-attribute form (``from nova.core import events`` — never
  ``from nova.core.events import _utc_now_iso``). Tests monkeypatch
  ``nova.core.events._utc_now_iso`` to achieve deterministic timing.
* **Workspace JSON shape is LOCKED to Story 2.4's serializer output.**
  ``_serialize_snapshot`` produces
  ``{"apps":[...],"focused_app":...,"mode_name":...}`` with
  ``separators=(",",":"), ensure_ascii=False, allow_nan=False``. Any
  extension beyond the three fields is forbidden in Story 3.1 — Story
  4.3 owns JSON shape evolution. Round-trip tests assert byte-exact
  fidelity against Story 2.4's writer.
* **Lossy deserialization of 2.4-flat JSON.** The persisted flat JSON
  only carries app names; ``get_last_snapshot_for_session`` synthesizes
  :class:`WindowContext` rows with ``app_name`` filled and
  ``window_title`` / ``process_name`` as ``None`` and ``is_opaque`` as
  ``False``. Story 4.3 will unify the richer-capture shape with the
  persisted shape.

Scope fence (Story 3.1)
-----------------------
Epic 5 methods (``query_memory``, ``delete_matching``,
``confirm_deletion``, ``get_transparency_model``) raise
``NotImplementedError("Epic 5 scope")``. Memory-item writes
(``create_memory_item`` etc.) are NOT on the port yet — Story 3.7
(shutdown seed capture) and Epic 4 / 5 own those extensions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from nova.core import events
from nova.core.exceptions import StorageError
from nova.core.storage.engine import SqliteStorageEngine
from nova.core.types import SnapshotType
from nova.systems.brain.models import (
    DeletionPreview,
    DeletionResult,
    MemoryItem,
    SessionSummary,
    TransparencyModel,
    WorkspaceSnapshotInput,
)
from nova.systems.eyes.models import WindowContext, WorkspaceSnapshot

logger = logging.getLogger("nova.adapters.sqlite.brain")

# SQL constants — kept adjacent to the methods that use them so shape changes
# ship atomically with their consuming SQL.
_INSERT_SESSION_SQL = """
INSERT INTO sessions (started_at, ended_at, mode_name, seed_text, summary, is_complete)
VALUES (?, NULL, ?, NULL, NULL, 0)
"""

_UPDATE_SESSION_END_SQL = """
UPDATE sessions
   SET ended_at = ?, seed_text = ?, summary = ?, is_complete = ?
 WHERE id = ?
"""

_SELECT_LAST_SESSION_SQL = """
SELECT id, started_at, ended_at, mode_name, summary, is_complete
  FROM sessions
 ORDER BY id DESC
 LIMIT 1
"""

_SELECT_LAST_SEED_SQL = """
SELECT seed_text
  FROM sessions
 WHERE is_complete = 1 AND seed_text IS NOT NULL
 ORDER BY id DESC
 LIMIT 1
"""

_SELECT_LAST_MODE_USAGE_SQL = """
SELECT started_at
  FROM sessions
 WHERE mode_name = ?
 ORDER BY id DESC
 LIMIT 1
"""

_INSERT_SNAPSHOT_SQL = """
INSERT INTO workspace_snapshots (session_id, captured_at, snapshot_type, workspace_data)
VALUES (?, ?, ?, ?)
"""

_SELECT_LAST_SNAPSHOT_FOR_SESSION_SQL = """
SELECT captured_at, snapshot_type, workspace_data
  FROM workspace_snapshots
 WHERE session_id = ?
 ORDER BY id DESC
 LIMIT 1
"""


def _compute_duration_seconds(started_at: str, ended_at: str | None) -> int:
    """Return the session duration in whole seconds.

    ``ended_at is None`` → returns ``0`` (the interrupted-session
    convention: callers detect "no duration" by checking
    ``SessionSummary.ended_at is None``, not by the ``duration_seconds``
    value). On any parse error, also returns ``0`` — timestamp strings
    are written by the adapter's own clock indirection and the setup
    writer, both of which emit valid ISO-8601 with ``+00:00``. A
    runtime parse failure therefore signals a corrupt row rather than a
    typical operation, and the "interrupted" fallback keeps the adapter
    robust.
    """
    if ended_at is None:
        return 0
    try:
        started = datetime.fromisoformat(started_at)
        ended = datetime.fromisoformat(ended_at)
        # P12: subtraction across aware/naive stamps raises ``TypeError``,
        # which the old narrower ``except ValueError`` let escape. Keep
        # the subtraction inside the try so both parse AND subtraction
        # failures surface as duration=0 + WARNING.
        delta = (ended - started).total_seconds()
    except (ValueError, TypeError):
        logger.warning("could not parse session timestamps; returning duration=0")
        return 0
    if delta < 0:
        # Clock skew between the two stamps (shouldn't happen with the
        # canonical clock indirection, but a corrupt row may violate the
        # invariant). Treat as zero rather than surfacing a negative.
        return 0
    return int(delta)


def _serialize_snapshot(snapshot: WorkspaceSnapshotInput) -> str:
    """Serialize a :class:`WorkspaceSnapshotInput` to the locked JSON shape.

    Shape contract (LOCKED to Story 2.4's writer for round-trip
    fidelity):

    ``{"apps":[...list-form of tuple...],"focused_app":"<or null>","mode_name":"<or null>"}``

    with ``separators=(",",":"), ensure_ascii=False, allow_nan=False``.

    Extending this shape breaks 2.4-vs-3.1 round-trip equality — Story
    4.3 owns any shape evolution.
    """
    payload: dict[str, object] = {
        "apps": list(snapshot.apps),
        "focused_app": snapshot.focused_app,
        "mode_name": snapshot.mode_name,
    }
    return json.dumps(
        payload,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


class SqliteBrainAdapter:
    """Concrete :class:`nova.ports.brain.BrainPort` implementation for T1.

    Structurally conforms to ``BrainPort`` without nominal inheritance
    — the port is a :class:`typing.Protocol`, so mypy strict checks
    shape at the call site. Composition root
    (:func:`nova.app.create_app`) constructs exactly one instance per
    process, injected into downstream systems via constructor
    injection in Stories 3.5 / 3.7.
    """

    def __init__(self, storage: SqliteStorageEngine) -> None:
        self._storage = storage

    # ------------------------------------------------------------------
    # Story 3.1 surface — session lifecycle, seed retrieval, snapshots
    # ------------------------------------------------------------------

    async def create_session(self, mode_name: str | None, *, started_at: str | None) -> int:
        """Insert a new session row with ``is_complete=0`` and return its id.

        ``started_at`` resolution:

        - ``None`` — adapter stamps via ``events._utc_now_iso()``
          (module-attribute form). Normal path for Story 3.5 (Nerve
          bare-boot) and Story 3.7 (shutdown tests).
        - ISO-8601 string — adapter uses it verbatim. Used by Story
          2.4's migrated ``persist_first_run`` to preserve
          ``capture.snapshot.captured_at`` as the setup session's
          ``started_at``.

        No default per Story 1.9 port rule; callers always pass the
        argument explicitly.
        """
        logger.debug(
            "brain.create_session start",
            extra={
                "has_mode": mode_name is not None,
                "caller_supplied_started_at": started_at is not None,
            },
        )
        # Contract (see docstring): ``None`` → adapter stamps the clock;
        # any non-``None`` string is used verbatim. An empty string or
        # otherwise-malformed value is the caller's bug and surfaces
        # downstream (``_compute_duration_seconds`` logs WARNING and
        # returns 0 on a parse failure). Silent fallback to the clock
        # on ``""`` would mask the bug — do NOT widen this guard.
        resolved_started_at = started_at if started_at is not None else events._utc_now_iso()
        session_id = await self._storage.execute_returning_lastrowid(
            _INSERT_SESSION_SQL,
            (resolved_started_at, mode_name),
        )
        return session_id

    async def end_session(
        self,
        session_id: int,
        *,
        seed_text: str | None,
        summary: str | None,
        is_complete: bool,
    ) -> str:
        """Stamp ``ended_at`` and finalize the session row.

        Returns the stamped ``ended_at`` ISO-8601 string so callers can
        reuse it for a companion audit write without re-sampling the
        clock (the setup seam in
        :func:`nova.setup.initial_capture.persist_first_run` uses this
        to keep ``sessions.ended_at`` byte-equal to
        ``audit_log.timestamp`` under the one-transaction invariant).

        ``ended_at`` is always adapter-stamped via
        ``events._utc_now_iso()`` at call time — callers never pass
        their own timestamp. This matches Story 2.4 AC #12: inside the
        setup transaction, ``end_session`` is called AFTER
        ``store_snapshot``, so ``ended_at`` naturally lands after the
        snapshot INSERT.

        A zero-row UPDATE is treated as a programmer error. Before the
        UPDATE, the adapter issues a SELECT to verify the session
        exists; if it does not, a WARNING is logged with the offending
        id. The UPDATE still runs (and is a no-op in that case) — the
        adapter does not raise. Story 3.5's Nerve never calls
        ``end_session`` with an unknown id under normal flow, so this
        is pure observability for programmer errors.
        """
        logger.debug("brain.end_session start", extra={"session_id_opaque": "<int>"})
        ended_at = events._utc_now_iso()
        # Pre-UPDATE existence probe: AC #8 promises a WARNING log for
        # zero-row updates. Single extra fetchone per end_session call
        # is cheap next to the transaction cost.
        existing = await self._storage.fetchone(
            "SELECT id FROM sessions WHERE id = ?", (session_id,)
        )
        if existing is None:
            logger.warning(
                "brain.end_session matched zero rows; UPDATE will be a no-op",
                extra={"session_id": session_id},
            )
        is_complete_int = 1 if is_complete else 0
        await self._storage.execute(
            _UPDATE_SESSION_END_SQL,
            (ended_at, seed_text, summary, is_complete_int, session_id),
        )
        return ended_at

    async def get_last_session(self) -> SessionSummary | None:
        """Return the most recent session's summary projection or None."""
        logger.debug("brain.get_last_session start")
        row = await self._storage.fetchone(_SELECT_LAST_SESSION_SQL)
        if row is None:
            return None
        return SessionSummary(
            session_id=int(row["id"]),
            started_at=str(row["started_at"]),
            ended_at=None if row["ended_at"] is None else str(row["ended_at"]),
            duration_seconds=_compute_duration_seconds(
                str(row["started_at"]),
                None if row["ended_at"] is None else str(row["ended_at"]),
            ),
            mode_name=None if row["mode_name"] is None else str(row["mode_name"]),
            summary=None if row["summary"] is None else str(row["summary"]),
            is_complete=bool(row["is_complete"]),
        )

    async def get_last_seed(self) -> str | None:
        """Return the seed from the most recent completed session with a seed."""
        logger.debug("brain.get_last_seed start")
        row = await self._storage.fetchone(_SELECT_LAST_SEED_SQL)
        if row is None:
            return None
        seed_value = row["seed_text"]
        return None if seed_value is None else str(seed_value)

    async def store_snapshot(self, session_id: int, snapshot: WorkspaceSnapshotInput) -> None:
        """Insert a workspace snapshot row tied to ``session_id``.

        The adapter takes ``captured_at`` from the input (caller-
        supplied, never resampled) to preserve Story 2.4's exact row
        shape when the setup migration runs. ``snapshot_type`` is
        serialized via ``str(snapshot_type)`` (StrEnum gives the
        canonical string value). The three payload fields go into
        compact JSON per ``_serialize_snapshot``.
        """
        logger.debug("brain.store_snapshot start")
        workspace_data = _serialize_snapshot(snapshot)
        await self._storage.execute(
            _INSERT_SNAPSHOT_SQL,
            (
                session_id,
                snapshot.captured_at,
                str(snapshot.snapshot_type),
                workspace_data,
            ),
        )

    async def get_last_snapshot_for_session(self, session_id: int) -> WorkspaceSnapshot | None:
        """Return the latest snapshot for ``session_id`` or None.

        Deserializes the locked-shape workspace JSON into a
        :class:`WorkspaceSnapshot` with synthesized ``WindowContext``
        rows (one per app name, with ``window_title`` and
        ``process_name`` both ``None``). Lossy by design — the
        persisted JSON does not carry the richer eyes-capture fields;
        Story 4.3 unifies this.

        Translates ``json.JSONDecodeError`` and ``ValueError`` (from
        ``SnapshotType(corrupt_persisted_value)``) into
        :class:`StorageError` — these are ADAPTER-boundary translation
        errors. Engine-raised ``StorageError`` propagates untouched
        (never double-caught).
        """
        logger.debug("brain.get_last_snapshot_for_session start")
        row = await self._storage.fetchone(_SELECT_LAST_SNAPSHOT_FOR_SESSION_SQL, (session_id,))
        if row is None:
            return None
        try:
            payload = json.loads(str(row["workspace_data"]))
            snapshot_type = SnapshotType(str(row["snapshot_type"]))
        except (json.JSONDecodeError, ValueError) as err:
            raise StorageError("brain adapter get_last_snapshot_for_session failed") from err
        # P2 guard: valid JSON that decodes to a non-object (``null``,
        # ``42``, ``[]``, etc.) would crash ``.get`` with AttributeError
        # on the next line, escaping the adapter's StorageError boundary.
        if not isinstance(payload, dict):
            raise StorageError("brain adapter get_last_snapshot_for_session failed") from TypeError(
                "workspace_data payload is not a JSON object"
            )
        apps_raw = payload.get("apps", [])
        if not isinstance(apps_raw, list):
            # Corrupt shape — treat like a JSON error.
            raise StorageError("brain adapter get_last_snapshot_for_session failed") from TypeError(
                "apps field is not a list"
            )
        # P6: non-string entries are corruption, not optional-drop. Surface
        # them the same way as a non-list ``apps`` shape — via StorageError.
        for app in apps_raw:
            if not isinstance(app, str):
                raise StorageError(
                    "brain adapter get_last_snapshot_for_session failed"
                ) from TypeError("apps entry is not a string")
        windows: tuple[WindowContext, ...] = tuple(
            WindowContext(
                app_name=app,
                window_title=None,
                process_name=None,
                is_opaque=False,
            )
            for app in apps_raw
        )
        return WorkspaceSnapshot(
            captured_at=str(row["captured_at"]),
            snapshot_type=snapshot_type,
            windows=windows,
        )

    async def get_mode_last_used(self, mode_name: str) -> str | None:
        """Return the ``started_at`` of the most recent session with ``mode_name`` or None.

        Consumed by Nerve's briefing-assembly layer (Story 3.2) to enrich
        ``ModeInfo.last_used_at`` per configured mode. Returns the raw
        ISO-8601 string from ``sessions.started_at``; callers needing a
        ``datetime`` can parse it at the render layer via
        ``datetime.fromisoformat``.

        ``mode_name`` is the canonical **stem** (dict key in
        ``NovaConfig.modes``) — matching the write-side contract that
        ``sessions.mode_name`` stores the stem, not the display name.
        Stories 3.5 / 3.6 / 3.7 own the write side of that contract;
        this read-side method simply trusts equality against the stored
        column value.

        A mode that has never been used returns ``None``. Sessions with
        ``mode_name IS NULL`` (e.g., the Story 2.4 setup row) never
        match any stem — SQL NULL inequality semantics handle that
        naturally. Empty ``mode_name`` input is accepted but returns
        ``None`` because no session carries an empty string as a mode
        (setup writes NULL; runtime writes populated stems).

        Logging is DEBUG-only with no ``mode_name`` payload — mode names
        can legitimately carry user-chosen text and the opacity rule
        (project-context.md §Trust) keeps them out of log messages.
        """
        logger.debug("brain.get_mode_last_used start")
        row = await self._storage.fetchone(_SELECT_LAST_MODE_USAGE_SQL, (mode_name,))
        if row is None:
            return None
        started_at = row["started_at"]
        return None if started_at is None else str(started_at)

    # ------------------------------------------------------------------
    # Epic 5 scope — declared to satisfy Protocol shape; NotImplementedError
    # ------------------------------------------------------------------

    async def query_memory(self, query: str) -> list[MemoryItem]:
        """Placeholder for Epic 5's memory-query surface.

        ``query`` is part of the structural ``BrainPort`` contract but
        unused here; the Epic 5 adapter will wire it to a real search.
        """
        del query
        raise NotImplementedError("Epic 5 scope")

    async def delete_matching(self, target: str) -> DeletionPreview:
        """Placeholder for Epic 5's selective-forget preview."""
        del target
        raise NotImplementedError("Epic 5 scope")

    async def confirm_deletion(self, preview: DeletionPreview) -> DeletionResult:
        """Placeholder for Epic 5's selective-forget confirmation."""
        del preview
        raise NotImplementedError("Epic 5 scope")

    async def get_transparency_model(self) -> TransparencyModel:
        """Placeholder for Epic 5's transparency aggregate."""
        raise NotImplementedError("Epic 5 scope")


__all__: list[str] = [
    "SqliteBrainAdapter",
]
