"""Composition root — wires ports to adapters, boots the monolith.

This module is the ONE place in the codebase that instantiates concrete
adapter classes. Every other module depends on port Protocols
(``nova.ports.*``) or core infrastructure (``nova.core.*``); only
``app.py`` and ``cli.py`` may import from ``nova.adapters.*``. This
invariant is locked by ``tests/unit/test_composition_root.py``.

Scope (Story 1.10)
------------------
T1's composition root wires only the infrastructure that already ships:

    1. :class:`nova.core.SqliteStorageEngine` (Story 1.4)
    2. Migration runner (Story 1.5) — invoked via ``engine.run_migrations``
    3. :class:`nova.core.EventBus` (Story 1.3)
    4. :class:`nova.core.AuditLogger` (Story 1.8) — constructed AFTER the
       engine is started so writes never vanish silently (closes a
       deferred-work item from Story 1.8 code review).
    5. :class:`nova.core.TierManager` (Story 1.7) with a no-op
       :class:`nova.core.HealthCheck` that never fails (real Claude-backed
       probe arrives with :class:`nova.adapters.claude.*`).
    6. :class:`nova.adapters.shield.NoOpShieldAdapter` (Story 1.9) — the
       only port-implementing adapter that exists today.

Brain / Eyes / Hands / Voice / Skin / Nerve / Ritual adapters and system
classes are NOT wired here — those arrive with their own stories.
Speculative stubs would violate the "adapters may translate, never
decide" rule (project-context.md:77) and would force a rewrite when real
adapters land.

Lifecycle
---------
``create_app`` must be called inside an active asyncio loop;
``nova.cli.main`` provides that via ``asyncio.run``, which gives the
engine a single loop for the entire process lifetime. This closes the
Story 1.4 deferred-work item about cross-loop engine drift: there is no
second loop.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from rich.console import Console

from nova.adapters.rich import RichSkinAdapter
from nova.adapters.shield import NoOpShieldAdapter
from nova.adapters.sqlite.brain import SqliteBrainAdapter
from nova.core import (
    AuditLogger,
    CapabilityTier,
    EventBus,
    NovaConfig,
    SqliteStorageEngine,
    TierManager,
)
from nova.ports import ShieldPort
from nova.ports.brain import BrainPort
from nova.ports.nerve import NervePort
from nova.ports.ritual import RitualPort
from nova.ports.skin import SkinPort
from nova.systems.nerve.system import NerveSystem
from nova.systems.ritual import RitualSystem

logger = logging.getLogger("nova.app")


class _AlwaysHealthyCheck:
    """T1 no-op :class:`nova.core.HealthCheck`.

    Satisfies the Protocol structurally: ``ping`` returns ``None`` on
    every invocation, never raises. The real Claude-backed probe arrives
    with :class:`nova.adapters.claude.ClaudeReasoningAdapter` — at that
    point the composition root will swap this instance out.

    Keeps :class:`TierManager` in :attr:`CapabilityTier.FULL` indefinitely
    because the recovery loop sees no failure signal.

    Story 3.5 does NOT swap this stub or start the recovery loop —
    swapping requires a real Claude-backed health probe, which lands
    with the Claude adapter. The stub plus the no-recovery-loop posture
    together preserve the OFFLINE-tier-when-no-api-key promise locked
    by Story 2.5's smoke test
    (``tests/unit/test_app.py::test_tier_stays_offline_without_recovery_loop``).
    """

    async def ping(self, *, timeout_seconds: float) -> None:
        # timeout_seconds is part of the structural HealthCheck contract
        # but irrelevant for a stub that never performs I/O.
        del timeout_seconds


@dataclass(frozen=True, slots=True)
class NovaApp:
    """Wired infrastructure graph returned by :func:`create_app`.

    Downstream stories extend this dataclass by adding fields (Brain,
    Eyes, Hands, Voice, Skin, Nerve, Ritual). They never replace it.

    The ``close`` callable performs teardown — Story 1.10 closes only the
    storage engine; future stories chain adapter-specific shutdowns in
    reverse-of-construction order.
    """

    config: NovaConfig
    storage: SqliteStorageEngine
    brain: BrainPort
    event_bus: EventBus
    audit: AuditLogger
    tier_manager: TierManager
    shield: ShieldPort
    ritual: RitualPort
    skin: SkinPort
    nerve: NervePort
    close: Callable[[], Awaitable[None]] = field(repr=False)


async def create_app(
    config: NovaConfig,
    *,
    shield: ShieldPort | None = None,
) -> NovaApp:
    """Construct the wired :class:`NovaApp` graph.

    Parameters
    ----------
    config
        Immutable :class:`NovaConfig` loaded by
        :func:`nova.core.config.load_config`.
    shield
        Optional :class:`nova.ports.ShieldPort` implementation. Defaults
        to :class:`NoOpShieldAdapter`. This knob exists to prove
        structural Protocol conformance works end-to-end; it is NOT the
        template for future adapters (brain / eyes / hands / etc. land
        as positional dataclass fields of :class:`NovaApp`, not as
        keyword arguments here).

    Construction order matters
    --------------------------
    1. ``SqliteStorageEngine`` is constructed and started FIRST.
    2. Migrations run SECOND.
    3. ``AuditLogger`` is constructed THIRD, AFTER the engine is
       started — otherwise every audit write would silently no-op with a
       WARNING (closes Story 1.8 deferred-work item).

    If migrations fail, the partially-started engine is closed before
    the exception propagates so no handle leaks.
    """
    storage = SqliteStorageEngine(config.db_path)
    await storage.start()
    logger.info(
        "storage engine started",
        extra={"db_path": str(config.db_path)},
    )

    # Every construction step after ``engine.start`` is guarded so a failure
    # anywhere — migrations, AuditLogger, TierManager, NoOpShieldAdapter, or
    # the final NovaApp() constructor — still closes the engine. The inner
    # ``storage.close()`` is itself shielded because it can raise its own
    # StorageError; we log that secondary failure but let the original
    # exception propagate (chained via ``from None``-style context).
    try:
        applied_versions = await storage.run_migrations()
        logger.info(
            "migrations applied",
            extra={"applied_count": len(applied_versions), "versions": applied_versions},
        )

        # Story 3.1 — Brain adapter constructed after migrations are applied so
        # the ``sessions`` / ``workspace_snapshots`` tables exist. Constructor
        # is side-effect free (captures the engine reference only), so this
        # adds zero new failure modes to the partial-init cleanup path.
        brain: BrainPort = SqliteBrainAdapter(storage)
        logger.info("brain adapter wired", extra={"adapter": type(brain).__name__})

        event_bus = EventBus()
        logger.info("event bus constructed")

        audit = AuditLogger(storage=storage)
        logger.info("audit logger wired")

        # Story 2.5 AC #4 — initial tier is derived from ``config.api_key``.
        # Absent key (None, empty, whitespace-only, non-string — all
        # normalized to ``None`` by ``_normalize_api_key``) → OFFLINE.
        # Present key → FULL (optimistic; the first real cloud call is what
        # would proof-test validity — Story 3.5 owns that degradation).
        initial_tier = CapabilityTier.OFFLINE if config.api_key is None else CapabilityTier.FULL
        if initial_tier is CapabilityTier.OFFLINE:
            # Opacity: ``reason`` is a closed-set string; the key value is
            # never logged — only its absence as a category label.
            logger.info(
                "starting in offline-local-only tier (no API key configured)",
                extra={"reason": "no_api_key"},
            )

        tier_manager = TierManager(
            health_check=_AlwaysHealthyCheck(),
            event_bus=event_bus,
            initial_tier=initial_tier,
        )
        logger.info("tier manager constructed", extra={"initial_tier": str(tier_manager.tier)})

        shield_adapter: ShieldPort = shield if shield is not None else NoOpShieldAdapter()
        logger.info(
            "shield adapter wired",
            extra={"adapter": type(shield_adapter).__name__},
        )

        # Story 3.3 — Ritual system + Rich skin adapter. Both are stateless
        # (Ritual is parameterless, RichSkinAdapter holds one Console
        # reference); neither acquires external resources, so the existing
        # partial-init cleanup block already covers them.
        ritual: RitualPort = RitualSystem()
        logger.info("ritual system wired", extra={"system": type(ritual).__name__})

        skin: SkinPort = RichSkinAdapter(console=Console())
        logger.info("skin adapter wired", extra={"adapter": type(skin).__name__})

        # Story 3.5 — Nerve orchestrator. Constructor is reference-storage
        # only (no I/O, no clock reads, no asyncio.Event creation), so it
        # adds zero new failure modes to the partial-init cleanup path.
        # The recovery loop is NOT started here — see _AlwaysHealthyCheck
        # docstring for the deferral rationale.
        nerve: NervePort = NerveSystem(
            brain=brain,
            ritual=ritual,
            skin=skin,
            event_bus=event_bus,
            tier_manager=tier_manager,
            config=config,
        )
        logger.info("nerve system wired", extra={"system": type(nerve).__name__})

        async def _close() -> None:
            await storage.close()
            logger.info("storage engine closed")

        return NovaApp(
            config=config,
            storage=storage,
            brain=brain,
            event_bus=event_bus,
            audit=audit,
            tier_manager=tier_manager,
            shield=shield_adapter,
            ritual=ritual,
            skin=skin,
            nerve=nerve,
            close=_close,
        )
    except BaseException:
        # Close the engine best-effort so the file handle does not leak.
        # Secondary failure during close is logged but never replaces the
        # in-flight exception — operators need the original cause, not the
        # teardown artifact.
        try:
            await storage.close()
        except Exception:
            logger.exception("secondary error closing engine during partial-init teardown")
        raise


__all__: list[str] = [
    "NovaApp",
    "create_app",
]
