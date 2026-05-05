"""Nerve orchestrator — concrete :class:`~nova.ports.nerve.NervePort` (Story 3.5).

Architecture (project-context.md:65, architecture.md:1104-1133):

* **Skin** parses user input into a :class:`~nova.systems.skin.models.Command`.
* **Nerve** routes the command, manages the session lifecycle, gates
  cloud-dependent operations on tier state, and registers the signal
  handler for unexpected termination. **Nerve never generates user-facing
  prose** — operational output is sent to Skin verbatim; personality-bearing
  prose is Voice's job (Epic 7).
* **Brain** owns persisted facts; Nerve reads via :class:`BrainPort` and
  writes session lifecycle markers (``create_session`` / ``end_session``).
* **Ritual** owns ceremony logic (briefing assembly, shutdown flow); Nerve
  decides when ceremonies run.

Eleven-step startup sequence
----------------------------
The ordering below is **load-bearing** — every Brain READ
(``get_last_session`` / ``get_last_seed`` / ``get_last_snapshot_for_session``
/ ``get_mode_last_used`` inside :func:`load_briefing_aggregate`) MUST run
BEFORE :meth:`BrainPort.create_session`'s WRITE. Otherwise the freshly-
created open session row pollutes the prior-state read, breaking State
A/B/C determination (a true first-run DB no longer produces ``FIRST_RUN``;
the setup-row-only case gets shadowed by the new open row; the recency
check compares against the just-created row).

1. **Initialize lifecycle state.** Create ``_shutdown_event`` LAZILY here
   (NOT in ``__init__``) so its loop binding matches the running loop.
   Reset ``_session_id`` / ``_session_active`` / ``_prompt_context``.
2. **Register the signal handler** FIRST so a Ctrl-C during steps 4–8
   still gets best-effort capture. The handler short-circuits when
   ``_session_active is False`` so a Ctrl-C BEFORE step 9 is a clean no-op.
3. **Wrap the body in ``try / finally``.** The ``finally`` runs the
   defense-in-depth ``end_session`` (if ``_session_active`` is still True)
   plus ``_uninstall_signal_handler``.
4. **Assemble the briefing aggregate** via :func:`load_briefing_aggregate`.
   All four prior-state Brain reads run here; no write yet exists.
5. **Determine briefing state** (``FIRST_RUN`` / ``POST_SETUP`` / ``WARM_RESUME``).
6. **State A early return** — render the State A briefing and return.
   No session is created (no orphan row), no REPL is entered (no commands
   are useful without modes).
7. **Skip-briefing policy** — pure decision based on the prior session's
   ``ended_at`` and the ``UserSettings`` recency knobs.
8. **Conditional briefing render** for State B / C.
9. **Create the runtime session** via ``brain.create_session``. Set
   ``_session_active = True`` only AFTER the write returns the id.
10. **Persist-before-emit** — emit ``SessionStarted`` only after step 9.
11. **Enter the REPL loop**. Three exit paths converge in the ``finally``:
    SHUTDOWN command, signal handler set ``_shutdown_event``, EOF/KbdInt
    at input. The defense-in-depth cleanup writes the interrupted-session
    marker if the REPL exited with ``_session_active=True``.

Signal-handler safety
---------------------
Once :func:`signal.signal` (Windows) or
:meth:`asyncio.AbstractEventLoop.add_signal_handler` (POSIX) installs a
custom callback, the default ``KeyboardInterrupt`` propagation is
suppressed. Without an explicit shutdown mechanism the handler would do
its Brain write and then control would return to the blocked
``Prompt.ask`` thread — the REPL would stay alive. Setting
``_shutdown_event`` is the explicit signal that drives the REPL's
race-pattern exit (see :meth:`_run_repl`). Workspace-snapshot capture in
the handler is Story 3.10 scope (Eyes integration); Story 3.5 ships only
the session-end-with-``is_complete=False`` marker.

Scope fences (Story 3.5 owns):
* **Hands integration** — Story 3.6 replaces :meth:`_handle_mode_switch`'s
  body with a delegation to ``HandsPort.restore_mode``.
* **Seed-capture ceremony** — Story 3.7 replaces :meth:`_handle_shutdown`'s
  body with a delegation to ``RitualPort.begin_shutdown``.
* **State C resume hero-path** — Story 3.8 sets
  ``_prompt_context = "briefing_resume"`` after the State C briefing
  renders and adds the resume-routing branch.
* **Full status / help table** — Story 3.9 replaces
  :meth:`_handle_status` and :meth:`_handle_help` placeholder bodies.
* **Workspace-snapshot capture in signal handler** — Story 3.10 extends
  :meth:`_signal_handler_callback`.
* **Memory / forget real implementations** — Epic 5.
* **Mode create / edit wizards** — Epic 6.
* **Voice prose enrichment** — Epic 7. The
  :meth:`_tier_check_or_offline_response` helper is the structural seam
  Epic 7 will consume; Story 3.5 has zero call sites for cloud ops.

Recovery-loop deferral (Story 3.5 explicit)
-------------------------------------------
``tier_manager.run_recovery_loop()`` is NOT started by this story.
Wiring the loop against the current
:class:`nova.app._AlwaysHealthyCheck` stub would flip OFFLINE → FULL on
the first 60-second tick, silently breaking Story 2.5's
``test_tier_stays_offline_without_recovery_loop`` smoke test. The
recovery loop lands with the future Claude adapter (Epic 7+); see
``_bmad-output/implementation-artifacts/3-5-nerve-command-routing-and-session-lifecycle.md``
§ Group I for the full reconciliation.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Callable
from datetime import UTC, datetime

from nova.core.config import NovaConfig, UserSettings
from nova.core.events import EventBus, SessionEnded, SessionStarted
from nova.core.tiers import TierManager
from nova.core.types import BriefingState, CapabilityTier
from nova.ports.brain import BrainPort
from nova.ports.hands import HandsPort
from nova.ports.ritual import RitualPort
from nova.ports.skin import SkinPort
from nova.systems.brain.models import SessionSummary
from nova.systems.nerve.briefing import determine_briefing_state, load_briefing_aggregate
from nova.systems.nerve.models import CommandOutcome
from nova.systems.skin.models import Command, CommandVerb

logger = logging.getLogger("nova.systems.nerve")


# --- Clock helper (two-function pattern, project-context.md:46) -------------


def _utc_now() -> datetime:
    """Canonical clock function — single source of truth for current UTC time.

    Returns a timezone-aware ``datetime`` (NOT the deprecated
    :func:`datetime.utcnow` which returns naive). The default for
    :class:`NerveSystem`'s ``clock`` parameter; tests inject a fixed-point
    callable to make recency decisions deterministic.

    Distinct from :func:`nova.core.events._utc_now_iso` (which returns an
    ISO string for event timestamps); this returns a ``datetime`` for
    arithmetic in :func:`_should_skip_briefing`.
    """
    return datetime.now(UTC)  # pragma: no cover - production default; tests inject


# --- Skip-briefing pure helper (Story 3.5 AC #7) ----------------------------


def _should_skip_briefing(
    prior_session: SessionSummary | None,
    settings: UserSettings,
    clock: Callable[[], datetime],
) -> bool:
    """Return True iff the prior session ended recently enough to skip the briefing.

    Decision table (first match wins; every branch returns):

    1. ``settings.skip_briefing_if_recent is False`` → ``False`` (always render).
    2. ``prior_session is None`` → ``False`` (no prior session to be recent-against).
    3. ``prior_session.ended_at is None`` → ``False`` (interrupted session,
       no defined end timestamp).
    4. ``settings.briefing_recency_threshold_minutes == 0`` → ``False``
       (recency disabled — no time interval is "recent enough").
    5. ``now - parsed(prior_session.ended_at) < threshold`` → ``True``.
    6. Else → ``False``.

    Defense-in-depth: a malformed ISO string in ``prior_session.ended_at``
    returns ``False`` (fail-open to rendering the briefing rather than
    fail-closed to skipping). The validator on
    :attr:`UserSettings.briefing_recency_threshold_minutes` already
    guarantees ``>= 0``; this helper does not re-validate.

    Pure function: no DB, no logging, no event emission. The clock is
    injected so tests can pin ``now`` deterministically.
    """
    if not settings.skip_briefing_if_recent:
        return False
    if prior_session is None:
        return False
    if prior_session.ended_at is None:
        return False
    if settings.briefing_recency_threshold_minutes == 0:
        return False
    try:
        ended_at = datetime.fromisoformat(prior_session.ended_at)
        now = clock()
        delta = now - ended_at
    except (ValueError, TypeError):
        # ValueError — malformed ISO string.
        # TypeError — naive vs aware datetime arithmetic
        # (``fromisoformat("2026-04-01T10:00:00")`` parses cleanly to a
        # naive datetime; subtracting an aware ``clock()`` raises).
        # Treat both as "not recent" to fall back to rendering the
        # briefing. Logging is the caller's job (this helper stays pure).
        return False
    threshold_seconds = settings.briefing_recency_threshold_minutes * 60
    return delta.total_seconds() < threshold_seconds


# --- NerveSystem ------------------------------------------------------------


class NerveSystem:
    """Concrete :class:`~nova.ports.nerve.NervePort` implementation.

    Structural Protocol conformance only — no nominal inheritance, per
    the established convention (cf.
    :class:`~nova.adapters.sqlite.brain.SqliteBrainAdapter` ↔
    :class:`~nova.ports.brain.BrainPort`,
    :class:`~nova.systems.ritual.system.RitualSystem` ↔
    :class:`~nova.ports.ritual.RitualPort`).

    Constructor stores references only — no I/O, no clock reads, no event
    subscriptions, no :class:`asyncio.Event` instantiation. The
    ``_shutdown_event`` is created lazily inside :meth:`startup` so its
    loop binding matches the running loop.
    """

    def __init__(
        self,
        *,
        brain: BrainPort,
        ritual: RitualPort,
        skin: SkinPort,
        event_bus: EventBus,
        tier_manager: TierManager,
        config: NovaConfig,
        hands: HandsPort,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._brain = brain
        self._ritual = ritual
        self._skin = skin
        self._event_bus = event_bus
        self._tier_manager = tier_manager
        self._config = config
        self._hands = hands
        self._clock = clock
        # Lifecycle state — initialized at the start of every startup() call
        # so a re-invocation (e.g., a future story that supports re-entry)
        # starts from a known baseline. Story 3.5's contract is one
        # startup() call per process invocation; cli.py enforces.
        self._shutdown_event: asyncio.Event | None = None
        self._session_id: int | None = None
        self._session_active: bool = False
        self._prompt_context: str | None = None
        self._signal_handlers_installed: bool = False
        # Story 3.6 — active mode tracker (the canonical stem, set by
        # _handle_mode_switch on successful restore, read by Story 3.7's
        # shutdown summary and Story 3.9's status command). The field is
        # named _active_mode_name for grep-continuity with the existing
        # mode_name parameter on BrainPort.create_session and the
        # ModeRestored.mode_name event field; its value is always the
        # stem (kebab-case YAML basename), NOT the display label.
        self._active_mode_name: str | None = None
        # Track which sync handler we replaced on Windows so uninstall
        # restores the prior behavior instead of silently dropping it.
        self._previous_sigint_handler: object | None = None
        self._previous_sigbreak_handler: object | None = None
        # Reference to the in-flight signal-handler task. Captured by
        # ``_on_signal`` (the single sync entrypoint shared by POSIX and
        # Windows install paths). The cleanup path
        # (:meth:`_cleanup_after_repl`) checks this reference and awaits
        # the task BEFORE deciding whether to run its own end_session
        # write — guarantees the handler is the SINGLE OWNER of
        # interrupted-session cleanup. Without this single-owner contract,
        # the REPL could observe ``_shutdown_event`` and exit while the
        # handler's ``end_session`` was still in flight; cleanup would
        # then race the handler with a duplicate write + emit.
        self._signal_handler_task: asyncio.Task[None] | None = None

    # --- Public surface (NervePort Protocol) -------------------------------

    async def startup(self) -> None:
        """Eleven-step boot path; see module docstring for the full ordering rule."""
        # Step 1 — initialize lifecycle state. asyncio.Event() must be
        # created on the running loop; constructing it in __init__ would
        # bind to whatever loop happened to be current at that time.
        self._shutdown_event = asyncio.Event()
        self._session_id = None
        self._session_active = False
        self._prompt_context = None
        # Reset the handler-task reference so a re-invoked startup()
        # doesn't see a stale task from a previous run.
        self._signal_handler_task = None

        # Step 2 — register the signal handler FIRST so a Ctrl-C during
        # the prior-state Brain reads still hits a registered handler.
        # The handler's _session_active=False guard makes that case a
        # clean no-op (no Brain write attempted).
        self._install_signal_handler()

        # Step 3 — wrap the body so cleanup always runs.
        try:
            # Step 4 — assemble the briefing aggregate. Issues all four
            # prior-state Brain reads (get_last_session / get_last_seed /
            # get_last_snapshot_for_session / get_mode_last_used).
            aggregate = await load_briefing_aggregate(self._brain, self._config)

            # Step 5 — determine briefing state.
            state = determine_briefing_state(aggregate)
            current_tier = self._tier_manager.tier

            # Step 6 — State A early return. NO session is created on this
            # path (no orphan row), NO REPL is entered (no commands are
            # useful without modes). The finally block still runs the
            # signal-handler uninstall.
            if state is BriefingState.FIRST_RUN:
                view_model = await self._ritual.build_briefing(aggregate, state, current_tier)
                await self._skin.render_briefing_card(view_model)
                logger.info(
                    "State A briefing rendered — setup wizard auto-start "
                    "deferred to setup.bat first-run gate"
                )
                return

            # Step 7 — skip-briefing policy decision. Reuses
            # aggregate.last_session (already loaded in step 4); does NOT
            # issue a second get_last_session call.
            should_skip = _should_skip_briefing(
                aggregate.last_session, self._config.settings, self._clock
            )

            # Step 8 — conditional briefing render (State B / C only).
            if should_skip:
                ended_at = (
                    aggregate.last_session.ended_at if aggregate.last_session is not None else None
                )
                logger.info(
                    "briefing skipped (recent prior session)",
                    extra={"prior_session_ended_at": ended_at},
                )
            else:
                view_model = await self._ritual.build_briefing(aggregate, state, current_tier)
                await self._skin.render_briefing_card(view_model)

            # Step 9 — create the runtime session. NOW — only after the
            # prior-state reads are done and the briefing has rendered (or
            # been skipped). Set _session_active AFTER create_session
            # returns the id so a Brain failure leaves _session_active
            # False and the finally cleanup is a clean no-op.
            self._session_id = await self._brain.create_session(mode_name=None, started_at=None)
            self._session_active = True

            # Step 10 — persist-before-emit. SessionStarted only AFTER the
            # write returns. architecture.md:1037.
            await self._event_bus.emit(SessionStarted(session_id=self._session_id, mode_name=None))

            # Step 11 — enter the REPL loop. Returns when any of the three
            # exit paths fires; the finally block handles cleanup.
            await self._run_repl()
        finally:
            await self._cleanup_after_repl()

        # Step 12 — surface signal-driven exit as ``KeyboardInterrupt`` so
        # ``cli.py``'s top-level ``except KeyboardInterrupt: return
        # EXIT_INTERRUPTED`` handler maps to exit code 130. Without this,
        # the custom signal handler suppresses the OS-level KbdInt
        # propagation (that's how it captured cleanup ownership in the
        # first place), and Ctrl-C would silently exit with 0 — the
        # process would report success while the user's session was
        # marked interrupted in nova.db.
        #
        # Asymmetry: SHUTDOWN command path leaves ``_signal_handler_task``
        # as None (no signal arrived), so the if-guard fires only on the
        # signal-driven exit. EOF / KeyboardInterrupt-at-input path also
        # leaves it None (no signal handler ran on that path), correctly
        # mapping stdin-closed to EXIT_OK.
        #
        # Placement after the finally is deliberate: cleanup completes
        # successfully first, THEN the interrupt-signal surfaces. If
        # something inside the try raised, that exception propagates
        # through the finally and supersedes this raise — a real bug
        # wins over the interrupt indicator.
        if self._signal_handler_task is not None:
            raise KeyboardInterrupt("session interrupted by signal")

    async def route_command(self, command: Command) -> CommandOutcome:
        """Dispatch ``command`` to the appropriate handler via match-on-verb.

        The match statement is exhaustive over :class:`CommandVerb` (16
        members). The default arm raises :class:`RuntimeError` for an
        unhandled member — programmer-error guard. Locked by
        :func:`tests.unit.systems.nerve.test_nerve_system.test_route_command_dispatch_table_covers_every_command_verb`.
        """
        match command.verb:
            # Layer B — routable verbs
            case CommandVerb.MODE:
                if command.target is None:
                    return await self._handle_modes_list(command)
                return await self._handle_mode_switch(command)
            case CommandVerb.MODE_CREATE:
                return await self._handle_mode_create(command)
            case CommandVerb.MODE_EDIT:
                return await self._handle_mode_edit(command)
            case CommandVerb.STATUS:
                return await self._handle_status(command)
            case CommandVerb.MEMORY:
                return await self._handle_memory(command)
            case CommandVerb.FORGET:
                return await self._handle_forget(command)
            case CommandVerb.HELP:
                return await self._handle_help(command)
            case CommandVerb.SHUTDOWN:
                return await self._handle_shutdown(command)
            # Layer C — contextual replies (gated on _prompt_context)
            case (
                CommandVerb.RESUME
                | CommandVerb.YES
                | CommandVerb.NO
                | CommandVerb.SKIP
                | CommandVerb.CANCEL
                | CommandVerb.CONFIRM
            ):
                return await self._handle_contextual(command)
            # Marker verbs from the Story 3.4 parser
            case CommandVerb.UNKNOWN:
                return await self._handle_unknown(command)
            case CommandVerb.EMPTY:
                return await self._handle_empty(command)
            case _:  # pragma: no cover — exhaustiveness guard
                raise RuntimeError(f"unhandled CommandVerb: {command.verb!r}")

    # --- Internal: cleanup after REPL --------------------------------------

    async def _cleanup_after_repl(self) -> None:
        """Defense-in-depth cleanup that runs in :meth:`startup`'s finally.

        Single-owner contract for interrupted cleanup
        ----------------------------------------------
        If the signal handler ran (``_signal_handler_task is not None``),
        it OWNS interrupted-session cleanup — this method awaits the
        handler to completion and then SKIPS its own ``end_session``
        write. Without this contract the REPL could observe
        ``_shutdown_event``, return immediately, and let cleanup race
        the handler's ``end_session`` (double write + double
        ``SessionEnded`` emission).

        When no handler ran (``_signal_handler_task is None``), this
        method is the only writer for ``is_complete=False`` markers —
        fires for paths where the REPL exited via uncaught exception
        despite the guards, or any other abnormal exit path that
        leaves ``_session_active=True``.

        Write-then-emit ordering applies here too — a failed Brain
        write logs and SKIPS the SessionEnded emission. The signal
        handler always uninstalls last regardless of Brain-write
        outcome.
        """
        handler_owned_cleanup = self._signal_handler_task is not None
        try:
            # Phase 1 — if the handler is in flight, await its completion
            # so the loop teardown doesn't tear it down mid-write. Bounded
            # by a 3-second ``wait_for`` so a hung handler can't hang
            # cleanup itself. The handler's own internal 2s ``wait_for``
            # on the Brain write keeps total handler time bounded; the 3s
            # cap here is defense-in-depth (covers any future addition to
            # the handler that adds awaits after the Brain write).
            # ``Exception`` suppression because the handler's own
            # try/except chain already logged any failure;
            # ``CancelledError`` is BaseException and propagates.
            if self._signal_handler_task is not None:
                try:
                    await asyncio.wait_for(self._signal_handler_task, timeout=3.0)
                except TimeoutError:
                    logger.warning(
                        "startup cleanup: signal-handler task did not complete "
                        "within 3s; cancelled and proceeding"
                    )
                except Exception:
                    # Handler logged its own failure inside its body.
                    pass

            # Phase 2 — write the interrupted-session marker only when the
            # handler did NOT own cleanup. This is the race fix: the
            # single-owner contract guarantees at most one
            # ``end_session(is_complete=False)`` call per session.
            if not handler_owned_cleanup and self._session_active and self._session_id is not None:
                try:
                    await self._brain.end_session(
                        self._session_id,
                        seed_text=None,
                        summary=None,
                        is_complete=False,
                    )
                except Exception:
                    logger.exception("startup cleanup: brain.end_session failed")
                else:
                    self._session_active = False
                    try:
                        await self._event_bus.emit(
                            SessionEnded(
                                session_id=self._session_id,
                                seed_text=None,
                                is_complete=False,
                            )
                        )
                    except Exception:
                        logger.exception("startup cleanup: SessionEnded emission failed")
        finally:
            self._uninstall_signal_handler()

    # --- Internal: REPL loop -----------------------------------------------

    async def _run_repl(self) -> None:
        """Race ``collect_input`` against ``_shutdown_event`` per turn.

        Three exit paths:

        * (a) :class:`CommandOutcome.EXIT` from a routed command (typically
          ``SHUTDOWN``). ``_handle_shutdown`` already wrote+emitted.
        * (b) ``_shutdown_event.set()`` by the signal handler. The handler
          attempted the Brain write itself; the startup() finally is a
          no-op when ``_session_active`` is False after that.
        * (c) :class:`EOFError` / :class:`KeyboardInterrupt` raised inside
          ``collect_input`` (closed stdin, or an in-input KeyboardInterrupt
          that landed before the signal handler ran). REPL invokes
          ``_handle_shutdown`` — idempotent via the ``_session_active``
          guard.

        The cancelled "loser" task on each iteration MUST be drained so
        its CancelledError doesn't surface as an "unawaited" warning at
        process exit (project-context.md:105).

        ``Prompt.ask`` is wrapped in :func:`asyncio.to_thread` by the
        adapter — cancelling the asyncio task does NOT unblock the
        underlying OS-level ``input()`` call. The orphan thread becomes
        a daemon that the process kills at exit; acceptable because the
        process closes within seconds of REPL return.
        """
        assert self._shutdown_event is not None  # set in startup() step 1
        while not self._shutdown_event.is_set():
            input_task: asyncio.Task[str] = asyncio.create_task(
                self._skin.collect_input(prompt="> ")
            )
            shutdown_task: asyncio.Task[bool] = asyncio.create_task(self._shutdown_event_wait())
            try:
                done, pending = await asyncio.wait(
                    {input_task, shutdown_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                # External cancellation (e.g., asyncio.run cleanup) — drain
                # both tasks then re-raise per project-context.md:49. The
                # drain awaits are critical: an unawaited cancelled task
                # surfaces as "Task was destroyed but it is pending!" at
                # process exit (project-context.md:105). The inner
                # pending-drain block below honors this for the FIRST_COMPLETED
                # path; this outer handler must do the same for the
                # external-cancellation path.
                input_task.cancel()
                shutdown_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await input_task
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await shutdown_task
                raise

            for task in pending:
                task.cancel()
                # Drain the cancelled task — the loser's exception (if any)
                # is irrelevant to the REPL exit path. Without the drain,
                # an unawaited cancelled task surfaces as a RuntimeWarning
                # at process exit (project-context.md:105 — no silent
                # warnings). ``contextlib.suppress`` covers both
                # ``CancelledError`` (the expected case) and any tail
                # ``Exception`` from the cancelled body.
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

            if shutdown_task in done:
                # Path (b) — signal-driven exit.
                logger.info("repl exiting via shutdown event (signal-driven)")
                return

            try:
                raw_input_text = input_task.result()
            except (EOFError, KeyboardInterrupt) as exc:
                # Path (c) — drive a clean SHUTDOWN. Idempotent.
                # Use a sentinel ``raw_input`` so audit-log subscribers
                # inspecting ``Command.raw_input`` can disambiguate
                # synthesized-from-EOF / synthesized-from-KbdInt from a
                # user-typed empty string. The Story 3.4 parser would
                # never produce these angle-bracket strings (its lookup
                # tables are all lowercase identifiers).
                sentinel = "<eof>" if isinstance(exc, EOFError) else "<keyboard-interrupt>"
                logger.info(
                    "repl input terminated — invoking clean shutdown",
                    extra={"sentinel": sentinel},
                )
                await self._handle_shutdown(
                    Command(
                        verb=CommandVerb.SHUTDOWN,
                        target=None,
                        raw_input=sentinel,
                        is_contextual=False,
                    )
                )
                return

            command = await self._skin.parse_command(raw_input_text)
            outcome = await self.route_command(command)
            if outcome is CommandOutcome.EXIT:
                # Path (a) — SHUTDOWN routed; nothing more to do.
                return

    async def _shutdown_event_wait(self) -> bool:
        """Async wrapper around ``_shutdown_event.wait()`` for the REPL race.

        Returns True unconditionally so the race-pattern result
        differentiates "shutdown won" from "input won" cleanly. Mypy-
        friendlier than typing a ``Coroutine[Any, Any, None]`` slot.
        """
        assert self._shutdown_event is not None
        await self._shutdown_event.wait()
        return True

    # --- Internal: signal handler ------------------------------------------

    def _install_signal_handler(self) -> None:
        """Install SIGINT (and SIGTERM/SIGBREAK on platform) handlers.

        POSIX: :meth:`asyncio.AbstractEventLoop.add_signal_handler` for
        SIGINT + SIGTERM. The callback is sync (asyncio requires); it
        schedules the async handler via :func:`asyncio.create_task`.

        Windows: ``add_signal_handler`` raises ``NotImplementedError`` on
        the standard event-loop policies, so we fall back to
        :func:`signal.signal` for SIGINT + SIGBREAK. The sync handler
        dispatches via :func:`asyncio.AbstractEventLoop.call_soon_threadsafe`
        so the actual Brain write happens on the loop.

        Idempotent — calling twice is a no-op (guarded by
        ``_signal_handlers_installed``).
        """
        if self._signal_handlers_installed:
            return
        loop = asyncio.get_running_loop()
        if sys.platform == "win32":
            # Windows fallback. Save the prior handlers so uninstall can
            # restore them — silently dropping a previously-installed
            # handler would surprise an embedding caller (Story 3.5
            # itself runs as a top-level entrypoint via cli.py, but
            # future tooling may import and re-use NerveSystem).
            self._previous_sigint_handler = signal.signal(
                signal.SIGINT, self._sync_signal_handler_factory(loop)
            )
            sigbreak = getattr(signal, "SIGBREAK", None)
            if sigbreak is not None:
                self._previous_sigbreak_handler = signal.signal(
                    sigbreak, self._sync_signal_handler_factory(loop)
                )
        else:  # pragma: no cover - POSIX-only branch; CI runs on Windows
            loop.add_signal_handler(signal.SIGINT, self._on_signal)
            loop.add_signal_handler(signal.SIGTERM, self._on_signal)
        self._signal_handlers_installed = True

    def _uninstall_signal_handler(self) -> None:
        """Reverse of :meth:`_install_signal_handler`. Best-effort.

        Failures are logged but never raise — the cleanup path must run
        to completion even if loop teardown is in progress. Idempotent.
        """
        if not self._signal_handlers_installed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (process is tearing down) — nothing to
            # remove. Sync signal.signal handlers will reset on process
            # exit anyway.
            self._signal_handlers_installed = False
            return
        if sys.platform == "win32":
            try:
                if self._previous_sigint_handler is not None:
                    signal.signal(signal.SIGINT, self._previous_sigint_handler)  # type: ignore[arg-type]
                sigbreak = getattr(signal, "SIGBREAK", None)
                if sigbreak is not None and self._previous_sigbreak_handler is not None:
                    signal.signal(sigbreak, self._previous_sigbreak_handler)  # type: ignore[arg-type]
            except (ValueError, OSError):
                logger.warning("failed to restore previous signal handler", exc_info=True)
        else:  # pragma: no cover - POSIX-only branch; CI runs on Windows
            try:
                loop.remove_signal_handler(signal.SIGINT)
                loop.remove_signal_handler(signal.SIGTERM)
            except (NotImplementedError, RuntimeError):
                # Loop may not support remove_signal_handler in some test
                # contexts; not fatal.
                logger.debug("loop.remove_signal_handler not available")
        self._signal_handlers_installed = False

    def _on_signal(self) -> None:
        """Single sync entrypoint — creates and tracks the handler task on the loop.

        Both POSIX (via :meth:`asyncio.AbstractEventLoop.add_signal_handler`)
        and Windows (via :func:`signal.signal` → ``call_soon_threadsafe``)
        route through this method so the cleanup race fix has ONE place
        to capture ``_signal_handler_task``. Without this single
        entrypoint, the Windows path would create the task via the
        ``call_soon_threadsafe(asyncio.create_task, ...)`` shortcut and
        the task reference would be lost — cleanup couldn't await it
        and the race in :meth:`_cleanup_after_repl` would re-open.

        Idempotent across multiple signal deliveries: if a handler task
        is already in flight (a second SIGINT arrives before the first
        completed), we do NOT replace it. The first handler's
        ``_session_active`` guard makes any second-call path a clean
        no-op anyway, but tracking only the first task keeps the
        cleanup contract crisp.
        """
        if self._signal_handler_task is not None and not self._signal_handler_task.done():
            return
        self._signal_handler_task = asyncio.create_task(
            self._signal_handler_callback(),
            name="nova-signal-handler",
        )

    def _sync_signal_handler_factory(
        self, loop: asyncio.AbstractEventLoop
    ) -> Callable[[int, object], None]:
        """Build a sync ``signal.signal``-compatible callback that hops to the loop.

        Windows requires ``signal.signal`` (loop.add_signal_handler is not
        supported). The sync callback receives ``(signum, frame)`` and
        must complete quickly; we hop to the loop via
        ``call_soon_threadsafe`` so the actual ``asyncio.create_task``
        runs on the loop thread (and the captured ``_signal_handler_task``
        reference lives on the loop's task registry where cleanup can
        await it).
        """

        def _sync_handler(signum: int, frame: object) -> None:
            # signum / frame are part of the signal.signal contract but
            # not used here — the async handler doesn't differentiate
            # signals (every signal triggers the same shutdown path).
            del signum, frame
            loop.call_soon_threadsafe(self._on_signal)

        return _sync_handler

    async def _signal_handler_callback(self) -> None:
        """Best-effort interrupted-session capture; sets ``_shutdown_event``.

        **Must NOT raise** — every step is guarded.

        Ordering:
        1. Set ``_shutdown_event`` FIRST. Even on the no-Brain-write path
           (no active session, or a second-Ctrl-C while the first handler's
           write is still in flight) the REPL must exit. ``Event.set()``
           is idempotent.
        2. Guard on ``_session_active``. False → return; the handler is
           one-shot.
        3. Brain write with 2-second timeout (epic 3.10 AC).
        4. **Write-then-emit:** ``SessionEnded`` ONLY after the Brain
           write succeeds. A failed/timed-out write returns early WITHOUT
           emitting — emitting after a failed write would lie about
           persistence to downstream consumers (audit log readers, future
           replay mechanisms). Per architecture.md:1037.

        Workspace-snapshot capture is Story 3.10 scope (Eyes integration).
        """
        # Phase 1 — always set the shutdown event first.
        if self._shutdown_event is not None:
            self._shutdown_event.set()

        # Phase 2 — one-shot guard.
        if not self._session_active:
            return
        # _session_active=True implies _session_id was set in startup() step 9.
        assert self._session_id is not None, "_session_active=True implies _session_id is set"

        # Phase 3 — Brain write, bounded by 2 seconds (epic 3.10 AC).
        try:
            await asyncio.wait_for(
                self._brain.end_session(
                    self._session_id,
                    seed_text=None,
                    summary=None,
                    is_complete=False,
                ),
                timeout=2.0,
            )
        except TimeoutError:
            # asyncio.TimeoutError aliases TimeoutError in 3.11+. Log,
            # STOP. Do NOT emit SessionEnded.
            logger.warning("signal handler: brain.end_session timed out (>2s)")
            return
        except Exception:
            logger.exception("signal handler: brain.end_session failed")
            return

        # Phase 4 — flag flip then emit. Write-then-emit holds.
        self._session_active = False
        try:
            await self._event_bus.emit(
                SessionEnded(
                    session_id=self._session_id,
                    seed_text=None,
                    is_complete=False,
                )
            )
        except Exception:
            logger.exception("signal handler: SessionEnded emission failed")

    # --- Internal: tier-gate helper ----------------------------------------

    def _tier_check_or_offline_response(self, op_name: str) -> bool:
        """Structural seam for cloud-dependent ops; Story 3.5 has zero call sites.

        Returns ``True`` when ``tier_manager.tier is FULL`` (op may
        proceed); ``False`` otherwise (caller short-circuits to a local
        fallback). When False, emits a structured INFO log with the op
        name (closed-set string) and the tier (closed enum) — never user
        data.

        Epic 7 (Voice prose enrichment) is the first real consumer.
        Story 3.5 ships this helper purely to lock the contract so Epic 7
        wires through unchanged.
        """
        if self._tier_manager.tier is CapabilityTier.FULL:
            return True
        logger.info(
            "op skipped due to tier",
            extra={"op": op_name, "tier": str(self._tier_manager.tier)},
        )
        return False

    # --- Internal: per-verb handlers ---------------------------------------

    async def _handle_modes_list(self, command: Command) -> CommandOutcome:
        """Render a compact one-line list of configured modes."""
        del command
        modes = self._config.modes
        if not modes:
            await self._skin.render_response(
                "No modes configured. Edit %LOCALAPPDATA%/nova/modes/ to add one."
            )
        else:
            stems = sorted(modes.keys())
            await self._skin.render_response(f"Modes: {', '.join(stems)}")
        return CommandOutcome.CONTINUE

    async def _handle_mode_switch(self, command: Command) -> CommandOutcome:
        """Delegate to :meth:`HandsPort.restore_mode` for the user-named mode.

        Mode restore is purely-local (no cloud surface) — does NOT
        consult :meth:`_tier_check_or_offline_response`. Even in
        OFFLINE tier, ``mode <stem>`` works. Documented scope fence;
        locked by ``test_mode_switch_does_not_consult_tier_manager``.

        ``command.target`` is user-typed and **preserves casing** per
        the Story 3.4 parser contract (``mode Coding`` →
        ``target="Coding"``; ``Switch to Deep Work mode`` →
        ``target="Deep Work"``). The lookup against
        ``NovaConfig.modes`` is **case-insensitive at Nerve's level**
        (Story 3.4 spec line 406): kebab-case stems in
        ``%LOCALAPPDATA%/nova/modes/<stem>.yaml`` are validated
        lowercase by Story 1.6's loader, so ``command.target.lower()``
        is the canonical lookup key. The user-facing error message
        echoes the ORIGINAL casing so the user recognizes what they
        typed; downstream identity (``hands.restore_mode``,
        ``_active_mode_name``, ``ModeRestored.mode_name``, audit
        ``target``, ``mode edit <stem>`` hints) all use the
        lowercased canonical stem.

        Tracks the active mode in :attr:`_active_mode_name` (the
        canonical stem) for Story 3.7's shutdown summary and Story
        3.9's status command. Set on successful restore (even
        partial — partial is still "active"); cleared on total
        failure so the status command and shutdown summary don't
        claim a previously-restored mode is still active.
        """
        assert command.target is not None  # parser guarantees for MODE/<target>
        # Case-insensitive lookup per Story 3.4 contract — kebab-case
        # stems are always lowercase, but the parser preserves the
        # user's original casing in target.
        mode_stem = command.target.lower()
        mode_config = self._config.modes.get(mode_stem)
        if mode_config is None:
            # Echo the user's original casing in the error so they
            # recognize what they typed (NOT the lowercased lookup key).
            await self._skin.render_response(
                f"No mode named '{command.target}'. Try mode to see available modes."
            )
            return CommandOutcome.CONTINUE
        results = await self._hands.restore_mode(mode_stem, mode_config)
        # Reflect the LATEST restore outcome — both first-time and
        # overwrite. Set to ``mode_stem`` on any-success (full or
        # partial — partial is still "active"); clear to None on total
        # failure so the status command and shutdown summary don't
        # claim a previously-restored mode is still active after the
        # user just saw "No apps could be launched". Without the
        # else-clear branch a sequence like ``mode coding`` (succeeds)
        # → ``mode coding`` (all apps now uninstalled, total failure)
        # would leave ``_active_mode_name == "coding"`` — lying to
        # Story 3.7's shutdown summary and Story 3.9's status.
        if any(r.success for r in results):
            self._active_mode_name = mode_stem
        else:
            self._active_mode_name = None
        return CommandOutcome.CONTINUE

    async def _handle_mode_create(self, command: Command) -> CommandOutcome:
        """Placeholder — Epic 6 wizard replaces this body."""
        del command
        await self._skin.render_response(
            "Create mode lands in Epic 6 — for now, hand-edit "
            "%LOCALAPPDATA%/nova/modes/<stem>.yaml."
        )
        return CommandOutcome.CONTINUE

    async def _handle_mode_edit(self, command: Command) -> CommandOutcome:
        """Partial form (target=None) → guidance; with-target → Epic 6 placeholder."""
        if command.target is None:
            await self._skin.render_response("Need one more detail. Try mode edit coding.")
        else:
            await self._skin.render_response(
                f"Edit mode lands in Epic 6 — for now, hand-edit "
                f"%LOCALAPPDATA%/nova/modes/{command.target}.yaml."
            )
        return CommandOutcome.CONTINUE

    async def _handle_status(self, command: Command) -> CommandOutcome:
        """Placeholder — Story 3.9 replaces this body with the full status table."""
        del command
        tier = self._tier_manager.tier
        await self._skin.render_response(f"Status: tier={tier}, mode=(none)")
        return CommandOutcome.CONTINUE

    async def _handle_memory(self, command: Command) -> CommandOutcome:
        """Placeholder — Epic 5 (Knowledge Display) replaces this body."""
        del command
        await self._skin.render_response(
            "Transparency coming soon. Your data is stored locally in %LOCALAPPDATA%/nova/nova.db."
        )
        return CommandOutcome.CONTINUE

    async def _handle_forget(self, command: Command) -> CommandOutcome:
        """Partial form → guidance; with-target → Epic 5 placeholder."""
        if command.target is None:
            await self._skin.render_response("Tell me what to forget. Example: forget Meridian")
        else:
            await self._skin.render_response("Forget capability coming soon.")
        return CommandOutcome.CONTINUE

    async def _handle_help(self, command: Command) -> CommandOutcome:
        """Placeholder — Story 3.9 replaces this body with the full help table."""
        del command
        await self._skin.render_response(
            "Commands: mode <name>, mode/modes, status, help, shutdown. (Full table in Story 3.9.)"
        )
        return CommandOutcome.CONTINUE

    async def _handle_shutdown(self, command: Command) -> CommandOutcome:
        """Idempotent clean shutdown.

        Story 3.7 will replace this body with a delegation to
        :meth:`RitualPort.begin_shutdown` (the seed-prompt ceremony).
        Until then, end the session cleanly via Brain with
        ``is_complete=True`` and emit ``SessionEnded`` write-then-emit.

        Idempotency: the ``_session_active`` guard makes a second call
        a clean no-op (returns ``EXIT`` without re-writing or
        re-emitting). This handles the signal-handler-then-SHUTDOWN race
        and the two-paths-to-shutdown race (EOF triggers
        ``_handle_shutdown`` AND the user typed ``shutdown``).

        Best-effort = ONE attempt at the user's intended outcome
        ----------------------------------------------------------
        ``_session_active`` is flipped to ``False`` BEFORE the Brain
        ``end_session`` await. This is deliberate: if the Brain write
        raises, we do NOT want :meth:`_cleanup_after_repl` to retry
        with ``is_complete=False`` — that would silently overwrite the
        user's clean-shutdown intent with an interrupted-marker. On
        Brain failure here we log + return EXIT cleanly; the row stays
        in whatever state Brain last saw, and Story 3.10's next-startup
        interrupted-session detection picks it up. Same posture as the
        signal handler.

        Why no timeout (vs the signal handler's 2s)
        --------------------------------------------
        The signal handler bounds its Brain write to 2 seconds because
        it runs in a constrained context — no human is available to
        intervene if the write hangs. ``_handle_shutdown`` runs because
        the user typed ``shutdown`` interactively; the user CAN press
        Ctrl-C if it hangs, which falls back to the signal-handler path
        that IS bounded. Adding a timeout here would risk masking real
        Brain bugs as "interrupted session" markers and surprise the
        user. The user retains agency: hung Brain → Ctrl-C → bounded
        fallback.
        """
        del command
        if not self._session_active:
            return CommandOutcome.EXIT
        # _session_active=True implies _session_id was set in startup() step 9.
        assert self._session_id is not None
        # Flip BEFORE the await so a Brain failure doesn't trigger a cleanup
        # retry. See "Best-effort = ONE attempt" docstring section above.
        self._session_active = False
        try:
            await self._brain.end_session(
                self._session_id,
                seed_text=None,
                summary=None,
                is_complete=True,
            )
        except Exception:
            logger.exception("user-typed shutdown: brain.end_session failed")
            # Don't render confirmation — user should know something went
            # wrong; the missing "Session ended." line surfaces that.
            # Don't emit SessionEnded — write didn't confirm.
            return CommandOutcome.EXIT
        try:
            await self._event_bus.emit(
                SessionEnded(
                    session_id=self._session_id,
                    seed_text=None,
                    is_complete=True,
                )
            )
        except Exception:
            # Brain write succeeded; emission is observability only.
            # Log and continue to render the confirmation.
            logger.exception("user-typed shutdown: SessionEnded emission failed")
        await self._skin.render_response("Session ended.")
        return CommandOutcome.EXIT

    async def _handle_contextual(self, command: Command) -> CommandOutcome:
        """Layer C contextual reply gating.

        Story 3.5 ships ``_prompt_context = None`` for the bare REPL;
        every Layer C verb maps to "nothing to resume / confirm right
        now" guidance. Story 3.8 will set ``_prompt_context`` after the
        State C briefing renders and add the resume-routing branch here.
        """
        if self._prompt_context is None:
            if command.verb is CommandVerb.RESUME:
                await self._skin.render_response(
                    "Nothing to resume right now. Try mode <name> or mode to view available modes."
                )
            else:
                await self._skin.render_response(
                    "Nothing to confirm right now. Try help to see available commands."
                )
            return CommandOutcome.CONTINUE
        # Story 3.8 territory — when _prompt_context is set, dispatch
        # based on the active context. Story 3.5 ships only the
        # _prompt_context=None branch; the active-context path is left
        # for the next story's tests to drive.
        await self._skin.render_response(
            "Nothing to confirm right now. Try help to see available commands."
        )  # pragma: no cover — Story 3.8 scope
        return CommandOutcome.CONTINUE  # pragma: no cover — Story 3.8 scope

    async def _handle_unknown(self, command: Command) -> CommandOutcome:
        """Echo the original input back per the parser's UNKNOWN contract.

        The Story 3.4 parser preserves the user's original text in
        ``Command.target`` for UNKNOWN inputs so this response template
        can echo it. The repr quoting (``!r``) makes whitespace and
        control characters visually unambiguous.
        """
        await self._skin.render_response(
            f"Didn't catch that: {command.target!r}. Try help to see available commands."
        )
        return CommandOutcome.CONTINUE

    async def _handle_empty(self, command: Command) -> CommandOutcome:
        """Silent no-op per Story 3.4 § "Why the parser never raises".

        MUST NOT log even at DEBUG — a per-keystroke log line for empty
        input would flood the file logger during a long REPL session.
        """
        del command
        return CommandOutcome.CONTINUE


__all__: list[str] = ["NerveSystem"]
