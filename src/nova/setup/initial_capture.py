"""Setup-time best-effort workspace capture + first-run persistence.

Story 2.4 ships two closely related helpers in this module:

* :func:`capture_initial_workspace` enumerates the user's currently open
  top-level windows via ``pywin32``/``psutil`` and returns a
  :class:`CaptureResult` that carries a
  :class:`nova.systems.eyes.models.WorkspaceSnapshot` plus a closed-set
  ``status`` string describing the capture outcome.  The status values
  flow unchanged into the operational status line, the audit
  ``details`` JSON, and every test assertion — it is the single source
  of truth for "what happened during capture."
* :func:`persist_first_run` writes exactly one ``sessions`` row and one
  ``workspace_snapshots`` row inside a single transaction, then logs one
  ``setup_complete`` ``audit_log`` row through the shared
  ``AuditLogger``.

Why this module exists (and not ``nova.adapters.win32.context``)
---------------------------------------------------------------

Story 4.1 ships the real :class:`Win32EyesAdapter` behind
:class:`nova.ports.eyes.EyesPort` with context polling, exclusion
filtering, and change-deduplication.  Story 2.4 needed a one-shot
capture at setup time — before the composition root wires any
adapter — and before Story 3.1's :class:`SqliteBrainAdapter` existed
to own session / snapshot writes.  This module remains the bridge for
Win32 capture.

Story 3.1 migrated the session + snapshot writes from direct
``storage.execute(...)`` calls to :class:`BrainPort` methods
(``create_session`` with ``started_at=capture.snapshot.captured_at`` to
preserve the Story 2.4 row shape; ``store_snapshot`` with a typed
:class:`WorkspaceSnapshotInput`; ``end_session`` stamps ``ended_at``
post-snapshot per AC #12).  The ``setup_complete`` audit row stays as
a direct INSERT via ``storage.execute(_INSERT_AUDIT_SQL, ...)`` so the
three-row atomicity invariant is preserved — ``AuditLogger``'s
observational-swallow contract would break it (see the comment on
:data:`_INSERT_AUDIT_SQL`).

Dependency surface
------------------

Only stdlib, :mod:`nova.core.*`, and the cross-system model types from
:mod:`nova.systems.eyes.models` (portable per Story 1.9 AC #8).
``pywin32`` / ``psutil`` are imported lazily inside a private probe
class — a missing import yields ``CaptureResult.status == "unavailable"``
with a single WARNING and no traceback.

No imports from ``nova.systems.eyes.system``, ``nova.adapters.*``, or
``nova.ports.*`` — locked by
``tests/unit/setup/test_initial_capture_isolation.py``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal, Protocol

from nova.core import events
from nova.core.audit import RESULT_SUCCESS, AuditLogger
from nova.core.storage.engine import SqliteStorageEngine
from nova.core.types import ActionType, SnapshotType
from nova.ports.brain import BrainPort
from nova.systems.brain.models import WorkspaceSnapshotInput
from nova.systems.eyes.models import WindowContext, WorkspaceSnapshot

logger = logging.getLogger("nova.setup.initial_capture")

__all__ = [
    "CaptureResult",
    "CaptureStatus",
    "WindowRaw",
    "capture_initial_workspace",
    "persist_first_run",
]


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


type CaptureStatus = Literal["full", "partial", "empty", "unavailable"]
"""Closed set describing how :func:`capture_initial_workspace` fared.

* ``"full"`` — pywin32 + psutil available, at least one window captured,
  zero per-window failures.
* ``"partial"`` — pywin32 + psutil available, at least one per-window
  failure was gracefully dropped.  Notably, *zero successful windows with
  any drops* also maps to ``"partial"`` — the enumeration worked, the
  desktop was non-empty, but every window failed its probe.
* ``"empty"`` — pywin32 + psutil available, enumeration succeeded, zero
  visible top-level windows and zero drops.
* ``"unavailable"`` — imports failed OR the outermost ``EnumWindows``
  raised before any per-window probe ran.

The string values are persisted into ``audit_log.details`` verbatim and
drive the operational status line in ``nova.setup.__main__``; changing
any value is a user-visible + schema event.
"""


MAX_CAPTURED_WINDOWS = 250
"""Cap on enumerated windows per Story 2.4 AC #24.

Prevents a pathological desktop (dev machine with ~400 Electron
processes) from stalling setup completion.  Story 4.1's polling adapter
owns its own cap independently.
"""


@dataclass(frozen=True, slots=True)
class WindowRaw:
    """Intermediate probe result — the raw tuple per visible HWND.

    Kept module-private in spirit but exposed so the test suite can
    assemble fixtures without round-tripping through Win32.  Adapter
    types (HWND ``int`` handles, ``psutil.Process`` instances) stay
    trapped inside the probe — this carrier is pure data.
    """

    hwnd: int
    app_name: str
    window_title: str
    process_name: str


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """Outcome of :func:`capture_initial_workspace`.

    Carries both the structured :class:`WorkspaceSnapshot` (for
    persistence) and the ``status`` vocabulary (for audit/UX).  ``status``
    is derived from ``windows_captured`` / ``windows_dropped`` /
    availability per the :data:`CaptureStatus` docstring — it is never
    re-derived downstream.

    ``focused_app`` is the process name of the window that held keyboard
    focus at capture time, resolved via ``GetForegroundWindow`` and
    matched against the enumerated HWND set. ``None`` when no foreground
    window is identifiable or when the foreground probe itself fails.
    """

    snapshot: WorkspaceSnapshot
    status: CaptureStatus
    windows_captured: int
    windows_dropped: int
    focused_app: str | None = None


# ---------------------------------------------------------------------------
# Probe protocol + factory seam — tests monkeypatch ``_probe_factory``.
# ---------------------------------------------------------------------------


class _WorkspaceProbe(Protocol):
    """Narrow boundary between :func:`capture_initial_workspace` and the
    real Win32 / psutil calls.

    The real implementation (:class:`_Win32Probe`) performs every
    platform-specific call here; the capture function orchestrates the
    probe's outputs into domain types.  Tests inject ``FakeProbe``
    instances via :data:`_probe_factory`.
    """

    available: bool

    def enumerate_hwnds(self) -> list[int]: ...

    def describe_window(self, hwnd: int) -> WindowRaw: ...

    def foreground_hwnd(self) -> int | None: ...


class _Win32Probe:
    """Real probe — talks to ``win32gui`` / ``win32process`` / ``psutil``.

    Instance construction is where the imports happen.  An ``ImportError``
    during construction flips :attr:`available` to ``False`` so the
    capture function can route to the ``"unavailable"`` status without
    crashing on any code path that assumes the libraries are present.
    """

    __slots__ = ("available", "_win32gui", "_win32process", "_psutil")

    def __init__(self) -> None:
        try:
            import psutil  # noqa: PLC0415
            import win32gui  # noqa: PLC0415
            import win32process  # noqa: PLC0415
        except ImportError:
            self.available = False
            self._win32gui = None
            self._win32process = None
            self._psutil = None
            return

        self.available = True
        self._win32gui = win32gui
        self._win32process = win32process
        self._psutil = psutil

    def enumerate_hwnds(self) -> list[int]:
        """Collect visible top-level HWNDs via ``EnumWindows``.

        Re-raises the underlying ``pywintypes.error`` / ``OSError`` so
        the caller can catch it at the outer boundary and route to the
        ``"unavailable"`` status.
        """
        assert self._win32gui is not None
        hwnds: list[int] = []

        def _callback(hwnd: int, _extra: object) -> bool:
            assert self._win32gui is not None
            if self._win32gui.IsWindowVisible(hwnd):
                # An empty title is how Windows represents "system shell"
                # and transient shim windows.  Skip them here — they
                # produce zero user-visible value in a first-run capture.
                title = self._win32gui.GetWindowText(hwnd)
                if title:
                    hwnds.append(hwnd)
            return True

        self._win32gui.EnumWindows(_callback, None)
        return hwnds

    def describe_window(self, hwnd: int) -> WindowRaw:
        assert self._win32gui is not None
        assert self._win32process is not None
        assert self._psutil is not None
        title = self._win32gui.GetWindowText(hwnd)
        _tid, pid = self._win32process.GetWindowThreadProcessId(hwnd)
        process_name = self._psutil.Process(pid).name()
        return WindowRaw(
            hwnd=hwnd,
            app_name=process_name,
            window_title=title,
            process_name=process_name,
        )

    def foreground_hwnd(self) -> int | None:
        assert self._win32gui is not None
        hwnd = self._win32gui.GetForegroundWindow()
        return int(hwnd) if hwnd else None


def _default_probe_factory() -> _WorkspaceProbe:
    """Factory indirection — tests replace this, not ``_Win32Probe``.

    Follows the two-function clock pattern (cross-cutting-patterns.md
    #1): the factory's body does a module-global lookup of
    :class:`_Win32Probe`, so a test that patches
    ``initial_capture._probe_factory`` gets a fresh probe each call
    without fighting the ``_Win32Probe.__init__`` imports.
    """
    return _Win32Probe()


_probe_factory: type = _default_probe_factory  # type: ignore[assignment]
"""Module-level seam — overridden in tests via ``monkeypatch.setattr``.

Typed as a bare callable alias (not ``Callable[..., _WorkspaceProbe]``)
so tests can assign lambdas without mypy strict complaining; the return
type is structurally checked at the capture-function boundary.
"""


# ---------------------------------------------------------------------------
# capture_initial_workspace
# ---------------------------------------------------------------------------


_EXPECTED_PROBE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    OSError,
    ValueError,
    RuntimeError,
)
"""Exception classes we expect at the probe boundary.

``pywintypes.error`` is an ``OSError`` subclass in modern pywin32.
``psutil.Error`` (``NoSuchProcess`` / ``AccessDenied`` / ``ZombieProcess``)
subclasses :class:`Exception` but is effectively an ``OSError`` for our
purposes; we add it below once psutil is importable.
"""


def _resolve_psutil_exceptions() -> tuple[type[BaseException], ...]:
    """Lazy lookup — psutil may be missing on non-Windows CI.

    When psutil is unavailable we return the base tuple only; the
    enumeration path would have already taken the ``"unavailable"``
    route before reaching this helper.
    """
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        return _EXPECTED_PROBE_EXCEPTIONS
    return (*_EXPECTED_PROBE_EXCEPTIONS, psutil.Error)


def capture_initial_workspace() -> CaptureResult:
    """Best-effort workspace capture for Story 2.4 AC #5–#9.

    Builds a :class:`WorkspaceSnapshot` with
    ``snapshot_type=SnapshotType.STARTUP``.  Every failure path (import
    failure, enumeration exception, per-window exception) degrades
    gracefully to a valid ``CaptureResult`` — capture is **non-blocking**
    for setup completion.

    Timestamps flow through the canonical two-function clock pattern:
    this function calls :func:`nova.core.events._utc_now_iso` via the
    imported :mod:`nova.core.events` module attribute so
    ``monkeypatch.setattr("nova.core.events._utc_now_iso", ...)`` in
    tests takes effect on every call.
    """
    captured_at = events._utc_now_iso()

    probe = _probe_factory()
    if not probe.available:
        logger.warning("workspace capture unavailable: pywin32 or psutil not importable")
        return _empty_unavailable(captured_at)

    try:
        hwnds = probe.enumerate_hwnds()
    except _EXPECTED_PROBE_EXCEPTIONS:
        # EnumWindows itself failed — no per-window data to salvage.
        logger.warning("workspace capture failed during enumeration", exc_info=True)
        return _empty_unavailable(captured_at)

    truncated = False
    if len(hwnds) > MAX_CAPTURED_WINDOWS:
        hwnds = hwnds[:MAX_CAPTURED_WINDOWS]
        truncated = True

    psutil_exceptions = _resolve_psutil_exceptions()

    contexts: list[WindowContext] = []
    hwnd_to_app: dict[int, str] = {}
    dropped = 0
    for hwnd in hwnds:
        try:
            raw = probe.describe_window(hwnd)
        except psutil_exceptions:
            dropped += 1
            # Log with HWND only — never the app/title/process (the per-
            # window failure could itself be caused by the excluded-app
            # boundary we are deferring to Story 4.2).
            logger.warning(
                "workspace capture dropped one window during probe",
                extra={"hwnd": hwnd},
            )
            continue
        hwnd_to_app[raw.hwnd] = raw.app_name
        contexts.append(
            WindowContext(
                app_name=raw.app_name,
                window_title=raw.window_title,
                process_name=raw.process_name,
                is_opaque=False,
            )
        )

    if truncated:
        logger.warning(
            "workspace capture truncated to the first %d windows",
            MAX_CAPTURED_WINDOWS,
        )

    # Resolve the foreground window AFTER per-window describe completes so
    # the lookup table ``hwnd_to_app`` is populated. The foreground probe
    # uses ``psutil_exceptions`` (not the narrower base tuple) because the
    # underlying call can raise the same ``psutil.Error`` classes as
    # ``describe_window`` — a ``pywintypes.error`` or ``psutil.AccessDenied``
    # must not crash an otherwise-successful capture.
    focused_app: str | None = None
    try:
        focused_hwnd = probe.foreground_hwnd()
    except psutil_exceptions:
        logger.debug("foreground window probe failed; ignoring", exc_info=True)
        focused_hwnd = None
    if focused_hwnd is not None:
        focused_app = hwnd_to_app.get(focused_hwnd)

    snapshot = WorkspaceSnapshot(
        captured_at=captured_at,
        snapshot_type=SnapshotType.STARTUP,
        windows=tuple(contexts),
    )

    status = _derive_status(
        available=True,
        windows_captured=len(contexts),
        windows_dropped=dropped,
    )

    return CaptureResult(
        snapshot=snapshot,
        status=status,
        windows_captured=len(contexts),
        windows_dropped=dropped,
        focused_app=focused_app,
    )


def _empty_unavailable(captured_at: str) -> CaptureResult:
    return CaptureResult(
        snapshot=WorkspaceSnapshot(
            captured_at=captured_at,
            snapshot_type=SnapshotType.STARTUP,
            windows=(),
        ),
        status="unavailable",
        windows_captured=0,
        windows_dropped=0,
    )


def _derive_status(
    *, available: bool, windows_captured: int, windows_dropped: int
) -> CaptureStatus:
    """Pure function — lockable by ``test_capture_status_decision_table``.

    Status routing per AC #5:

    - ``"unavailable"`` — probe imports failed OR enumeration returned
      but every per-window probe failed (``captured == 0 AND dropped > 0``).
      Enumeration succeeding with zero readable probes produces nothing
      useful downstream, so the honest status is "unavailable" rather
      than "partial" (AC #5 defines "partial" as requiring at least one
      captured window).
    - ``"empty"`` — enumeration succeeded, zero visible windows, zero
      drops (genuine empty desktop).
    - ``"partial"`` — at least one window captured AND at least one
      per-window drop (graceful-partial).
    - ``"full"`` — at least one window captured, zero drops.
    """
    if not available:
        return "unavailable"
    if windows_captured == 0 and windows_dropped > 0:
        return "unavailable"
    if windows_captured >= 1 and windows_dropped > 0:
        return "partial"
    if windows_captured == 0:
        return "empty"
    return "full"


# ---------------------------------------------------------------------------
# persist_first_run — AC #10–#17 (Story 3.1 migrated session/snapshot to Brain)
# ---------------------------------------------------------------------------

# Story 2.4 writes the setup_complete audit row directly inside the
# session+snapshot transaction rather than through ``AuditLogger.log_action``
# because the fast-path probe (``nova.setup.__main__._probe_setup_complete``)
# treats this row as the canonical "setup done" marker. ``AuditLogger``'s
# observational-swallow semantic (``StorageError`` logged and dropped) would
# break that marker contract — a swallowed audit write leaves session+snapshot
# orphaned and the next ``setup.bat`` run would produce duplicate rows. By
# inlining the INSERT we bind all three rows into one atomic transaction:
# ``audit_log`` lands iff the session and snapshot land, and the fast path
# and the DB state never disagree. ``AuditLogger``'s observational contract
# stays intact for every OTHER caller.
#
# Story 3.1 migrated the session + snapshot writes to ``BrainPort``. The
# audit row stays as direct SQL — routing it through ``AuditLogger.log_action``
# would re-introduce the swallow-breaks-atomicity bug this seam deliberately
# avoids.
_INSERT_AUDIT_SQL = (
    "INSERT INTO audit_log (timestamp, action_type, target, result, details) VALUES (?, ?, ?, ?, ?)"
)


def _serialize_audit_details(
    *, modes_count: int, api_key_configured: bool, capture_status: CaptureStatus
) -> str:
    """Build the ``audit_log.details`` JSON for the inline setup-complete row.

    Mirrors ``AuditLogger.log_action``'s serializer: compact separators,
    ``ensure_ascii=False``, ``allow_nan=False``. Kept adjacent to the INSERT
    SQL so any future shape change ships atomically with its persistence
    contract — not bolted onto a string far away.
    """
    return json.dumps(
        {
            "modes_count": modes_count,
            "api_key_configured": api_key_configured,
            "capture_status": capture_status,
        },
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


# ``NovaApp`` is the narrow shape we need from the composition root —
# declaring it via Protocol with read-only properties means both the
# real frozen :class:`nova.app.NovaApp` dataclass and the plain-attribute
# test harness satisfy it without mypy-strict noise.
class _NovaAppLike(Protocol):
    @property
    def storage(self) -> SqliteStorageEngine: ...

    @property
    def brain(self) -> BrainPort: ...

    @property
    def audit(self) -> AuditLogger: ...


async def persist_first_run(
    app: _NovaAppLike,
    capture: CaptureResult,
    *,
    api_key_configured: bool,
    modes_count: int,
) -> None:
    """Story 2.4 AC #10–#17 — write session + snapshot + audit atomically.

    Story 3.1 migration: session + snapshot writes now route through
    :class:`BrainPort`; the ``setup_complete`` audit row keeps its
    direct-SQL inline INSERT so the three-row atomicity invariant is
    preserved (see :data:`_INSERT_AUDIT_SQL`'s comment).

    Sequence (all inside one ``storage.transaction()``):

    1. ``brain.create_session(mode_name=None, started_at=capture.snapshot.captured_at)``
       — caller-override of ``started_at`` preserves Story 2.4's exact
       row shape (session's ``started_at`` equals the capture timestamp,
       not a fresh clock stamp).
    2. ``brain.store_snapshot(session_id, WorkspaceSnapshotInput(...))``
       — captured_at is preserved from ``capture.snapshot.captured_at``.
    3. ``brain.end_session(session_id, ...)`` stamps ``ended_at`` at
       call time, which is AFTER the snapshot insert — satisfies AC #12.
    4. Direct INSERT of the ``setup_complete`` audit row (not through
       :class:`AuditLogger` per the atomicity comment on
       :data:`_INSERT_AUDIT_SQL`).

    Any failure inside the transaction rolls back all three rows. A
    rolled-back ``sessions.id`` autoincrement slot is released by SQLite
    on commit failure, so a successful next run starts clean.
    """
    storage = app.storage
    async with storage.transaction():
        session_id = await app.brain.create_session(
            mode_name=None,
            started_at=capture.snapshot.captured_at,
        )
        await app.brain.store_snapshot(
            session_id,
            WorkspaceSnapshotInput(
                captured_at=capture.snapshot.captured_at,
                snapshot_type=SnapshotType.STARTUP,
                apps=tuple(
                    sorted(w.app_name for w in capture.snapshot.windows if w.app_name is not None)
                ),
                focused_app=capture.focused_app,
                mode_name=None,
            ),
        )
        # AC #12 — ``end_session`` stamps ``ended_at`` at call time,
        # after the snapshot insert. Brain uses ``events._utc_now_iso()``
        # via module-attribute form so the monkeypatch contract holds.
        # Story 3.1 code-review patch P0: ``end_session`` now returns the
        # stamped ``ended_at`` so this seam can reuse it for the audit
        # row without a second clock sample. Keeps ``sessions.ended_at``
        # and ``audit_log.timestamp`` byte-equal under the one-
        # transaction three-row atomicity invariant.
        audit_timestamp = await app.brain.end_session(
            session_id,
            seed_text=None,
            summary=None,
            is_complete=True,
        )
        audit_details = _serialize_audit_details(
            modes_count=modes_count,
            api_key_configured=api_key_configured,
            capture_status=capture.status,
        )
        await storage.execute(
            _INSERT_AUDIT_SQL,
            (
                audit_timestamp,
                str(ActionType.SETUP_COMPLETE),
                None,
                RESULT_SUCCESS,
                audit_details,
            ),
        )
