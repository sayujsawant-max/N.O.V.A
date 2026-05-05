"""Win32HandsAdapter — concrete :class:`AppLauncherPort` for Windows.

Story 3.6 owns the launch surface: a per-app :func:`subprocess.Popen`
launch with an :func:`os.startfile` fallback (gated on empty args), an
already-running pre-check via :mod:`psutil`, and OS-error mapping into
the canonical four-member reason vocabulary owned by
:mod:`nova.ports.app_launcher`.

Two-stage launch strategy
-------------------------
1. **`subprocess.Popen([executable, *args], creationflags=...,
   close_fds=True)`** is the primary path. It honors the user's
   ``args`` list cleanly, returns a PID handle (useful for integration
   test cleanup), and fails with :class:`FileNotFoundError` on missing
   executables — easy to map to ``REASON_NOT_FOUND``.

2. **`os.startfile(executable)` fallback ONLY when ``args`` is empty.**
   ShellExecute (which ``startfile`` wraps) handles ``.lnk``
   shortcuts, registered file associations, and App Paths registry
   entries (e.g., ``chrome`` → ``C:\\Program Files\\Google\\Chrome``).
   The args-empty gate is load-bearing: ``startfile``'s
   ``arguments=`` parameter has historically inconsistent behavior
   across Windows versions and shell associations — silently
   dropping the user's configured args would launch the app in a
   misleading state. When ``args`` is non-empty, Popen failure
   returns ``REASON_NOT_FOUND`` directly.

Already-running treated as success
----------------------------------
A matching process found via :func:`psutil.process_iter` returns
``ActionResult(success=True, reason=None)``. The workspace outcome is
"this app is now ready" regardless of whether N.O.V.A. spawned it or
just observed it. Returning failure here would corrupt the
partial / total-failure counters at the HandsSystem layer (a mode
where every app is already running would render
``"No apps could be launched"`` — wrong UX). The no-op-launch fact
lives in this adapter's DEBUG log only; ``ActionResult`` carries no
``already_running`` flag.

Detection is best-effort:

* Different installations of the same exe → only one detected; second
  installation can't be launched separately. Acceptable.
* Renamed processes (game launchers etc.) → missed; we launch a
  duplicate. Acceptable (user closes the duplicate).
* :class:`psutil.AccessDenied` on protected processes → treated as
  "not running" (false-negative is the correct fail-mode — we'd
  rather attempt the launch than skip it).

Error mapping
-------------
Caught exceptions translate to the canonical four-member vocabulary:

* :class:`FileNotFoundError` (after Popen + applicable startfile
  fallback) → ``REASON_NOT_FOUND``
* :class:`PermissionError` OR :class:`OSError` with
  ``winerror == 5`` → ``REASON_PERMISSION_DENIED``
* :class:`asyncio.TimeoutError` (alias of built-in
  :class:`TimeoutError` in 3.11+) from the outer :func:`asyncio.wait_for`
  → ``REASON_TIMED_OUT``
* Any other :class:`OSError` (incl. :class:`subprocess.SubprocessError`
  subclasses) → ``REASON_UNKNOWN_ERROR`` with full traceback at
  WARNING. Never let pywin32 / subprocess exception classes leak
  across the port boundary (project-context.md:40).

Cross-platform import safety
----------------------------
``subprocess.DETACHED_PROCESS`` and ``CREATE_NEW_PROCESS_GROUP`` exist
only on Windows. The module-level ``_CREATIONFLAGS`` constant guards
the lookup so the file is importable on POSIX (for unit tests that
mock the launch primitives). Tests assert the kwarg via
``_CREATIONFLAGS`` directly, NOT via the platform-specific
``subprocess`` attributes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess  # noqa: S404 - launching user-configured apps is the whole point
import time
from pathlib import PurePath

import psutil

from nova.core.config import AppConfig
from nova.core.types import ActionType
from nova.ports.app_launcher import (
    REASON_NOT_FOUND,
    REASON_PERMISSION_DENIED,
    REASON_TIMED_OUT,
    REASON_UNKNOWN_ERROR,
)
from nova.systems.hands.models import ActionResult

logger = logging.getLogger("nova.adapters.win32.actions")


# --- Cross-platform creationflags --------------------------------------------
# DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP are the canonical
# "launch and forget" flags on Windows: the launched child does NOT
# inherit the parent's console (so closing N.O.V.A.'s terminal doesn't
# kill the launched apps) and does NOT receive Ctrl-C / Ctrl-Break
# signals delivered to N.O.V.A.'s process group (so the user pressing
# Ctrl-C in N.O.V.A. doesn't close the apps they just launched).
#
# On POSIX the constants don't exist; the no-op fallback (0) lets the
# module import cleanly so unit tests can mock subprocess.Popen
# without a SkipIf guard. The adapter is documented as Windows-only
# at runtime via the @pytest.mark.windows_only on the integration
# tests; the unit-test surface is platform-neutral via mocking.
# getattr-with-default is cleaner than a sys.platform branch + the
# "type: ignore[attr-defined]" mypy would otherwise require on POSIX
# (where the constants don't exist on the subprocess module). The
# ``int`` cast is structural — on Windows the constants are real ints;
# on POSIX the 0 default is also int.
_CREATIONFLAGS: int = int(
    getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
)


class Win32HandsAdapter:
    """Concrete :class:`~nova.ports.app_launcher.AppLauncherPort` impl.

    ``timeout_seconds`` bounds the per-app launch attempt. Defaults to
    5.0 seconds — typical Popen returns in milliseconds; the timeout
    bounds OS-level stalls (antivirus interference, disk thrash). Tests
    inject smaller values (e.g. 0.05) for the timed-out path.

    Constructor stores ``timeout_seconds`` only — no I/O, no resource
    acquisition. Composition root instantiation is structurally covered
    by the existing partial-init cleanup block in ``nova.app``.
    """

    def __init__(self, *, timeout_seconds: float = 5.0) -> None:
        self._timeout_seconds = timeout_seconds

    async def launch_app(self, app: AppConfig) -> ActionResult:
        """Attempt to launch ``app``; return a typed :class:`ActionResult`.

        See module docstring for the full strategy (Popen primary,
        args-gated startfile fallback, already-running success, error
        mapping into the canonical four-member reason vocabulary).
        """
        start = time.monotonic()

        # Phase 1 — already-running pre-check. Treated as a successful
        # workspace outcome: the user wanted this app available, and
        # it is. The no-op-launch fact lives in the DEBUG log only.
        if await self._is_already_running(app.executable):
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.debug(
                "app already running, skipping launch",
                extra={"executable": app.executable, "duration_ms": elapsed_ms},
            )
            return ActionResult(
                action_type=ActionType.APP_LAUNCH,
                target=app.name,
                success=True,
                reason=None,
            )

        # Phase 2 — primary launch via Popen (bounded by wait_for).
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._popen_launch, app),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            # asyncio.TimeoutError aliases TimeoutError in 3.11+.
            return ActionResult(
                action_type=ActionType.APP_LAUNCH,
                target=app.name,
                success=False,
                reason=REASON_TIMED_OUT,
            )
        except FileNotFoundError:
            # Phase 3 — args-empty fallback to os.startfile. With args
            # present, ShellExecute's arguments handling is too
            # inconsistent to trust; fail-fast so the user sees an
            # honest "not found" rather than a launch with silently
            # dropped args.
            #
            # ``hasattr`` guard: ``os.startfile`` only exists on
            # Windows. The adapter is Windows-only at runtime, but
            # the module is importable on POSIX for unit testing
            # (which monkeypatches ``os.startfile`` via
            # ``raising=False``). The guard makes a hypothetical
            # production POSIX call return REASON_NOT_FOUND cleanly
            # rather than ``AttributeError``.
            if len(app.args) == 0 and hasattr(os, "startfile"):
                try:
                    await asyncio.to_thread(os.startfile, app.executable)  # noqa: S606 - launching user-configured app
                except FileNotFoundError:
                    return ActionResult(
                        action_type=ActionType.APP_LAUNCH,
                        target=app.name,
                        success=False,
                        reason=REASON_NOT_FOUND,
                    )
                except OSError as exc:
                    return _map_os_error(app, exc)
                else:
                    return _success_result(app, start)
            return ActionResult(
                action_type=ActionType.APP_LAUNCH,
                target=app.name,
                success=False,
                reason=REASON_NOT_FOUND,
            )
        except PermissionError:
            return ActionResult(
                action_type=ActionType.APP_LAUNCH,
                target=app.name,
                success=False,
                reason=REASON_PERMISSION_DENIED,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            # subprocess.SubprocessError is NOT a subclass of OSError —
            # catch it explicitly so subprocess-module-specific
            # exceptions (CalledProcessError, TimeoutExpired) translate
            # to the canonical reason vocabulary and never leak across
            # the port boundary (project-context.md:40).
            return _map_os_error(app, exc)

        return _success_result(app, start)

    # --- Internals ---------------------------------------------------------

    @staticmethod
    def _popen_launch(app: AppConfig) -> None:
        """Synchronous Popen call — invoked via ``asyncio.to_thread``.

        Returns ``None`` on success (the spawned ``Popen`` handle is
        intentionally discarded — fire-and-forget per the
        DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP contract). Raises
        :class:`OSError` subclasses on failure for the caller's
        ``except`` chain to translate.
        """
        # bandit S603: launching user-configured apps is the documented
        # purpose of this method. The executable + args come from the
        # validated ModeConfig (Story 1.6 loader checks); shell=False
        # by default avoids shell-injection risk.
        subprocess.Popen(  # noqa: S603
            [app.executable, *app.args],
            creationflags=_CREATIONFLAGS,
            close_fds=True,
        )

    @staticmethod
    async def _is_already_running(executable: str) -> bool:
        """Best-effort already-running detection via :mod:`psutil`.

        Matches case-insensitively on the executable basename. Returns
        ``False`` on :class:`psutil.AccessDenied` (the false-negative
        fail-mode — we'd rather attempt the launch than skip it).
        Wrapped in :func:`asyncio.to_thread` because
        :func:`psutil.process_iter` is a blocking syscall.
        """
        return await asyncio.to_thread(_iter_processes_for_match, executable)


def _iter_processes_for_match(executable: str) -> bool:
    """Best-effort already-running detection by basename.

    Normalizes BOTH sides by stripping a trailing ``.exe`` and
    lowercasing — so a YAML config with ``executable: chrome`` matches
    a running ``chrome.exe`` process (and vice versa). This is the
    common Windows case: users write the short name ("chrome",
    "code", "notepad"), Windows reports the full name ("chrome.exe").

    Returns ``False`` (treat as not-running, fail-safe) on any of:
    * Outer ``psutil.AccessDenied`` (the iter itself refused)
    * Other ``psutil.Error`` subclasses (resource exhaustion, etc.)
    * Generic ``OSError`` from underlying syscalls
    """
    target = _normalize_exe_basename(executable)
    # Defense-in-depth: never match against an empty target. A misconfigured
    # mode YAML with ``executable=""`` (loader gap) OR a normalize input that
    # collapses to empty (e.g. ``".exe"``) would otherwise match every
    # process whose ``proc.info["name"]`` is None / empty after the
    # ``or ""`` fallback below — silently no-op the entire mode while
    # reporting all-success to the user.
    if not target:
        return False
    try:
        for proc in psutil.process_iter(["name"]):
            try:
                proc_name = proc.info.get("name") or ""
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                # Per-process failure must NOT abort the whole iter.
                # Zombie processes and transient handle-close OSErrors
                # are common in steady-state Windows; outer-except would
                # incorrectly mark every remaining app as not-running.
                continue
            if _normalize_exe_basename(proc_name) == target:
                return True
    except (psutil.Error, OSError):
        # Whole iter refused or syscall failed; false-negative is the
        # correct fail-mode (we'd rather attempt the launch than skip).
        return False
    return False


def _normalize_exe_basename(value: str) -> str:
    """Strip directory, lowercase, drop trailing ``.exe`` for symmetric matching."""
    base = PurePath(value).name.lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base


def _map_os_error(app: AppConfig, exc: OSError | subprocess.SubprocessError) -> ActionResult:
    """Translate a non-FileNotFoundError / non-PermissionError adapter exception.

    Accepts ``OSError`` (the common case for Win32 launch failures)
    AND ``subprocess.SubprocessError`` (which is NOT an OSError
    subclass — needed so subprocess-specific exceptions like
    :class:`subprocess.CalledProcessError` translate cleanly).

    ``winerror == 5`` (ERROR_ACCESS_DENIED) maps to permission-denied
    even when the exception class is plain ``OSError`` (some Win32
    paths surface that). Anything else logs at WARNING with the
    executable + winerror context and maps to ``REASON_UNKNOWN_ERROR``.
    """
    winerror = getattr(exc, "winerror", None)
    if winerror == 5:
        return ActionResult(
            action_type=ActionType.APP_LAUNCH,
            target=app.name,
            success=False,
            reason=REASON_PERMISSION_DENIED,
        )
    logger.warning(
        "launch_app failed",
        extra={"executable": app.executable, "winerror": winerror},
        exc_info=True,
    )
    return ActionResult(
        action_type=ActionType.APP_LAUNCH,
        target=app.name,
        success=False,
        reason=REASON_UNKNOWN_ERROR,
    )


def _success_result(app: AppConfig, start: float) -> ActionResult:
    elapsed_ms = int((time.monotonic() - start) * 1000)
    logger.debug(
        "app launched",
        extra={"executable": app.executable, "duration_ms": elapsed_ms},
    )
    return ActionResult(
        action_type=ActionType.APP_LAUNCH,
        target=app.name,
        success=True,
        reason=None,
    )


__all__: list[str] = ["Win32HandsAdapter"]
