"""AppLauncherPort — per-app OS-launch primitive consumed by HandsSystem.

Story 3.6 (Mode Restore & App Launching) introduces this port to keep the
two layers crisp:

* :class:`~nova.systems.hands.system.HandsSystem` owns *orchestration*:
  the per-mode loop, graceful-partial accounting, audit/render/event
  ordering, the aggregate ``ModeRestored`` emission. It depends on
  :class:`AppLauncherPort` (this Protocol).
* :class:`~nova.adapters.win32.actions.Win32HandsAdapter` owns
  *translation*: subprocess.Popen / os.startfile launches, OS error
  mapping into the canonical reason vocabulary, the already-running
  pre-check. It implements :class:`AppLauncherPort`.

Per project-context.md:77 *"Adapters may translate, never decide"* —
this port is the seam that keeps that invariant honest. Without it,
HandsSystem would have to import ``Win32HandsAdapter`` directly
(violates the no-system-imports-adapters rule) or the OS-call logic
would leak into the system layer.

Trust boundary
--------------
Only domain types cross :class:`AppLauncherPort`:

* :class:`~nova.core.config.AppConfig` (in)
* :class:`~nova.systems.hands.models.ActionResult` (out)

``subprocess.Popen``, ``os.startfile``, ``psutil.Process`` handles, and
all pywin32 / pywintypes exception classes stay trapped inside
``nova.adapters.win32.actions`` per architecture.md:1462.

Canonical failure-reason vocabulary
-----------------------------------
The four ``REASON_*`` constants below are the only values
:attr:`ActionResult.reason` carries when ``success`` is ``False``. They
live in this port file (not in the adapter) so that
:class:`~nova.adapters.rich.skin.RichSkinAdapter` can import
``REASON_NOT_FOUND`` for the ``"is it installed?"`` user-facing hint
without violating the no-cross-adapter-imports rule
(project-context.md:62).

**Already-running is NOT a failure reason.** When the adapter detects
that an app is already running (via ``psutil.process_iter``), it
returns ``ActionResult(success=True, reason=None)`` — the workspace
outcome is "ready" regardless of whether N.O.V.A. spawned the process
or just observed it already up. The no-op-launch fact lives in the
adapter's DEBUG log only. See Story 3.6 spec § Group A AC #3 step 2
for the rationale.
"""

from __future__ import annotations

from typing import Final, Protocol

from nova.core.config import AppConfig
from nova.systems.hands.models import ActionResult

# --- Canonical failure-reason vocabulary -----------------------------------
# Closed four-member set. Only emitted when ActionResult.success is False.
# Already-running maps to success=True (see module docstring).

REASON_NOT_FOUND: Final[str] = "not found"
REASON_PERMISSION_DENIED: Final[str] = "permission denied"
REASON_TIMED_OUT: Final[str] = "timed out"
REASON_UNKNOWN_ERROR: Final[str] = "unknown error"


class AppLauncherPort(Protocol):
    """Per-app OS-launch primitive.

    Adapters translate OS-level launch outcomes into typed
    :class:`~nova.systems.hands.models.ActionResult`. Per
    project-context.md:77 adapters do NOT decide policy —
    graceful-partial, audit ordering, and event emission live in
    :class:`~nova.systems.hands.system.HandsSystem`.
    """

    async def launch_app(self, app: AppConfig) -> ActionResult: ...


__all__: list[str] = [
    "AppLauncherPort",
    "REASON_NOT_FOUND",
    "REASON_PERMISSION_DENIED",
    "REASON_TIMED_OUT",
    "REASON_UNKNOWN_ERROR",
]
