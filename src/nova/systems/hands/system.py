"""HandsSystem — concrete :class:`~nova.ports.hands.HandsPort` (Story 3.6).

Architecture (project-context.md:65, architecture.md:1338-1339, 1462):

* **Nerve** routes ``mode <stem>`` and delegates to
  :meth:`HandsPort.restore_mode`.
* **HandsSystem** orchestrates the per-mode loop: sequential per-app
  launches, audit→render→event ordering, graceful-partial accounting,
  the aggregate ``ModeRestored`` emission.
* **Win32HandsAdapter** (the :class:`~nova.ports.app_launcher.AppLauncherPort`
  impl) translates per-app OS-level launch outcomes into typed
  :class:`~nova.systems.hands.models.ActionResult`.

The split between system (orchestration / policy) and adapter
(translation) is mandatory per project-context.md:77 *"Adapters may
translate, never decide."*

Per-app ordering (locked by Group K Block 2 ordering test)
----------------------------------------------------------
For each app in ``mode_config.apps``:

1. ``launcher.launch_app(app)`` → ``ActionResult``
2. ``audit.log_action(APP_LAUNCH, ...)`` — durable record FIRST
3. ``skin.render_progress(result)`` — user sees the line
4. ``event_bus.emit(AppLaunched(...))`` — runtime fan-out

THEN the next app's launch begins. Sequential — no
``asyncio.gather``. Reasons documented in the spec § "Why per-app
order is audit → render → event".

Aggregate emission ordering
---------------------------
After the per-app loop drains:

5. ``audit.log_action(MODE_RESTORE, target=mode_stem, ...)`` —
   aggregate durable record.
6. ``event_bus.emit(ModeRestored(mode_name=mode_stem, ...))`` —
   subscribers (Story 4.1's snapshot trigger, Story 6.1's focus
   chain) get the aggregate fact AFTER the audit row exists.
7. ``skin.render_response(_summary_text(...))`` — final user-visible
   line ("Workspace ready." / "Workspace partially ready. ..." /
   "No apps could be launched. ...").

Audit-failure isolation (project-context.md:86)
-----------------------------------------------
:class:`~nova.core.audit.AuditLogger` swallows ``StorageError``
internally (Story 1.8). HandsSystem does NOT wrap ``audit.log_action``
in a try/except — wrapping would also catch programmer errors
(``TypeError`` / ``ValueError`` from AuditLogger's boundary checks)
which MUST surface. The unwrapped ``await self._audit.log_action(...)``
IS the contract; tests use a real ``AuditLogger`` over a failing
storage engine to exercise the swallow path without violating the
contract.

Tier-gating
-----------
Mode restore is purely-local (no cloud surface). HandsSystem does NOT
consult the tier manager — even in OFFLINE tier, ``mode <stem>`` works
normally per architecture.md:809 (Hands runs at full capability across
all three tiers).
"""

from __future__ import annotations

import logging

from nova.core.audit import RESULT_FAILED, RESULT_SUCCESS, AuditLogger
from nova.core.config import ModeConfig
from nova.core.events import AppLaunched, EventBus, ModeRestored
from nova.core.types import ActionType
from nova.ports.app_launcher import AppLauncherPort
from nova.ports.skin import SkinPort
from nova.systems.hands.models import ActionResult

logger = logging.getLogger("nova.systems.hands")


# Aggregate-result vocabulary for the MODE_RESTORE audit row. RESULT_SUCCESS
# and RESULT_FAILED come from nova.core.audit; "partial" is Hands-specific
# (audit's `result` field is loose-typed `str` per Story 1.8's deliberate
# design — see nova/core/audit.py:91-110 — so the loose value is accepted
# without a signature change).
_RESULT_PARTIAL: str = "partial"


class HandsSystem:
    """Concrete :class:`~nova.ports.hands.HandsPort` implementation.

    Structural Protocol conformance only — no nominal inheritance, per
    the established convention (cf.
    :class:`~nova.systems.nerve.system.NerveSystem` ↔
    :class:`~nova.ports.nerve.NervePort`,
    :class:`~nova.systems.ritual.system.RitualSystem` ↔
    :class:`~nova.ports.ritual.RitualPort`).

    Constructor stores references only — no I/O, no event subscriptions.
    """

    def __init__(
        self,
        *,
        launcher: AppLauncherPort,
        skin: SkinPort,
        event_bus: EventBus,
        audit: AuditLogger,
    ) -> None:
        self._launcher = launcher
        self._skin = skin
        self._event_bus = event_bus
        self._audit = audit

    async def restore_mode(self, mode_stem: str, mode_config: ModeConfig) -> list[ActionResult]:
        """Sequential per-app launch loop with audit/event/render ordering.

        ``mode_stem`` is the canonical mode identity (kebab-case YAML
        file basename / ``NovaConfig.modes`` dict key) — used for the
        ``MODE_RESTORE`` audit ``target``, the ``ModeRestored.mode_name``
        event field, and the ``mode edit <stem>`` total-failure hint.
        ``mode_config.name`` (display label) is NEVER used as identity.
        """
        # Step 1 — defensive preconditions (Story 1.6 loader guarantees
        # both; the asserts document the reliance).
        assert len(mode_config.apps) >= 1, "loader contract: ModeConfig.apps is non-empty"
        assert mode_stem and not mode_stem.isspace(), (
            "mode_stem is required for canonical mode identity"
        )

        # Step 2 — URL-deferral notice (Story 6.5 will ship URL opening).
        # Log count only; never the URLs themselves (avoids surprise
        # leak of user-typed URLs into the log file).
        if len(mode_config.urls) > 0:
            logger.info(
                "mode urls present but URL opening lands in Story 6.5",
                extra={"mode_stem": mode_stem, "url_count": len(mode_config.urls)},
            )

        # Step 3 — accumulators.
        results: list[ActionResult] = []
        apps_launched: list[str] = []
        apps_failed: list[str] = []

        # Step 4 — sequential per-app loop. Order: launch → audit →
        # render → event, then next app.
        #
        # **Skin / event isolation:** ``render_progress`` and
        # ``event_bus.emit`` are wrapped in narrow try/except so a
        # broken stdout pipe, UnicodeEncodeError on a legacy console,
        # or a misbehaving event subscriber cannot abort the per-app
        # loop mid-mode. Aborting would leave the persisted audit
        # rows out of sync with the user-visible workspace state and
        # would skip the aggregate ``MODE_RESTORE`` audit + the
        # ``ModeRestored`` event Story 4.1's snapshot trigger
        # depends on. Audit is the only path that stays unwrapped —
        # AuditLogger handles its own StorageError swallow per
        # Story 1.8 (and a wrapping try/except would also catch
        # programmer-error TypeError/ValueError that MUST surface).
        for app in mode_config.apps:
            result = await self._launcher.launch_app(app)
            results.append(result)

            # Per-app audit row (durable record FIRST). Audit is
            # observational; AuditLogger swallows StorageError
            # internally — Hands does NOT wrap.
            await self._audit.log_action(
                action_type=ActionType.APP_LAUNCH,
                target=app.name,
                result=RESULT_SUCCESS if result.success else RESULT_FAILED,
                details={"executable": app.executable, "reason": result.reason},
            )

            # Per-app render (user sees the line as it happens).
            # Isolated: a Skin failure on this line does NOT abort the
            # remaining apps in the mode.
            try:
                await self._skin.render_progress(result)
            except Exception:
                logger.exception(
                    "render_progress failed; mode restore continues",
                    extra={"app_name": app.name},
                )

            # Per-app event (runtime fan-out AFTER audit + render).
            # Isolated for the same reason: a misbehaving subscriber
            # cannot abort the per-app loop. EventBus is documented
            # to swallow handler Exception (Story 1.3), so this is
            # belt-and-suspenders, but locks the Hands-side promise
            # that audit/render success → loop continues.
            try:
                await self._event_bus.emit(
                    AppLaunched(
                        app_name=app.name,
                        executable=app.executable,
                        success=result.success,
                        reason=result.reason,
                    )
                )
            except Exception:
                logger.exception(
                    "AppLaunched emit failed; mode restore continues",
                    extra={"app_name": app.name},
                )

            if result.success:
                apps_launched.append(app.name)
            else:
                apps_failed.append(app.name)

        # Step 5 — aggregate audit row (target=mode_stem, the canonical
        # identity, NOT mode_config.name).
        await self._audit.log_action(
            action_type=ActionType.MODE_RESTORE,
            target=mode_stem,
            result=_aggregate_result(apps_launched, apps_failed),
            details={
                "apps_launched": list(apps_launched),
                "apps_failed": list(apps_failed),
            },
        )

        # Step 6 — aggregate ModeRestored event (mode_name=mode_stem).
        # The field is named mode_name for grep-continuity with the
        # Story 1.3 event class declaration; its value is the stem.
        # Isolated for the same reason as the per-app emit: a broken
        # subscriber must not abort the run AFTER audit succeeded.
        try:
            await self._event_bus.emit(
                ModeRestored(
                    mode_name=mode_stem,
                    apps_launched=tuple(apps_launched),
                    apps_failed=tuple(apps_failed),
                )
            )
        except Exception:
            logger.exception(
                "ModeRestored emit failed; mode restore continues",
                extra={"mode_stem": mode_stem},
            )

        # Step 7 — final-line summary (operational copy direct via
        # Skin; Voice-driven prose dressing is Epic 7 scope). Isolated:
        # a Skin failure on the summary line must not propagate up
        # into NerveSystem._handle_mode_switch (which would prevent
        # _active_mode_name from being set even though audit + event
        # already say the mode was restored).
        try:
            await self._skin.render_response(_summary_text(mode_stem, apps_launched, apps_failed))
        except Exception:
            logger.exception(
                "render_response failed; mode restore complete",
                extra={"mode_stem": mode_stem},
            )

        # Step 8 — return results for the caller (NerveSystem currently
        # ignores the return; future stories may consume it).
        return results


# --- Module-level helpers ---------------------------------------------------


def _aggregate_result(apps_launched: list[str], apps_failed: list[str]) -> str:
    """Return the canonical aggregate-result string for the audit row.

    Three-way decision:
    * no failures → ``RESULT_SUCCESS``
    * mixed → ``_RESULT_PARTIAL``
    * no launches → ``RESULT_FAILED``
    """
    if not apps_failed:
        return RESULT_SUCCESS
    if not apps_launched:
        return RESULT_FAILED
    return _RESULT_PARTIAL


def _summary_text(mode_stem: str, apps_launched: list[str], apps_failed: list[str]) -> str:
    """Build the final-line summary for the user.

    Three distinct buckets (project-context.md:190 — partial restore
    must be distinguishable from full restore):

    * **Full success:** ``"Workspace ready."``
    * **Partial:** ``"Workspace partially ready. {first_failed} was
      skipped."`` plus ``" ({n} more skipped — see status for
      details.)"`` when more than one failed (UX brevity per
      project-context.md:183).
    * **Total failure:** ``"No apps could be launched. Check mode
      config: mode edit {mode_stem}"`` — uses the stem because that's
      what ``mode edit <X>`` resolves against (Story 6.4).

    Operational copy direct to Skin per project-context.md:66. Epic 7
    (Voice prose enrichment) may later prepend personality dressing
    ("Last thread: auth tests."), but the canonical brief form lives
    here. ``mode_stem`` is used only for the ``mode edit`` hint —
    the rest of the copy is mode-name-free.
    """
    if not apps_failed:
        return "Workspace ready."
    if not apps_launched:
        return f"No apps could be launched. Check mode config: mode edit {mode_stem}"
    first_failed = apps_failed[0]
    line = f"Workspace partially ready. {first_failed} was skipped."
    extra = len(apps_failed) - 1
    if extra > 0:
        line += f" ({extra} more skipped — see status for details.)"
    return line


__all__: list[str] = ["HandsSystem"]
