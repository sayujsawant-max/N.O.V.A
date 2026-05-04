"""Nerve-owned briefing assembly and state determination (Story 3.2).

Two public callables make up this module's T1 surface:

- :func:`load_briefing_aggregate` — async; merges
  :class:`~nova.ports.brain.BrainPort` persisted facts with
  :class:`~nova.core.config.NovaConfig` mode metadata into a
  :class:`~nova.systems.brain.models.BriefingAggregate`.
- :func:`determine_briefing_state` — pure sync function mapping a
  :class:`BriefingAggregate` to a :class:`~nova.core.types.BriefingState`
  (``FIRST_RUN`` / ``POST_SETUP`` / ``WARM_RESUME``) via the epic 3.2
  state machine (first match wins).

Architecture notes (Decision 3b)
--------------------------------
- Brain owns persisted-fact queries ONLY. It does NOT read mode YAML —
  that would cross the config-ownership boundary (project-context.md
  §Architecture: "Config module is the single YAML reader").
- Nerve is the sole assembly site for ``BriefingAggregate``. Ritual
  (Story 3.3) consumes the aggregate + the determined state to build
  a ``BriefingViewModel``; Nerve does not reach into the ViewModel
  layer.
- ``ModeInfo`` enrichment asks Brain by STEM (the canonical identifier
  that ``sessions.mode_name`` stores). Stories 3.5 / 3.6 / 3.7 are
  responsible for writing stems into ``sessions.mode_name`` on session
  creation / update — the write-side half of this cross-story
  contract.

State machine (epics.md §Story 3.2, architecture.md Decision 3b)
----------------------------------------------------------------
- ``FIRST_RUN``: ``available_modes`` empty AND ``last_session is None``.
- ``POST_SETUP``: ``last_seed is None`` AND
  (``last_session is None`` OR ``last_session.is_complete is False``).
- ``WARM_RESUME``: else — catches "completed session without seed"
  (including the Story 2.4 setup row), "any seed present", and
  "interrupted session with seed".

State B is NOT the normal post-setup path in T1. A brand-new user who
just finished ``setup.bat`` and runs ``nova`` again sees State C with
progressive omission (Story 3.3) — the setup row is ``is_complete=True``
so the B guard short-circuits to the else branch. State B is reached
only when (a) the user hand-edited ``modes/*.yaml`` before ever running
``nova``, or (b) the prior session was interrupted before seed capture.

Scope fence (Story 3.2)
-----------------------
- ``recent_memory`` is always ``()`` in T1. Memory-item reads are Epic
  4 / 5 scope; populating a real value requires wiring that does not
  exist yet.
- No ``BriefingViewModel`` construction, no rendering. Story 3.3 owns
  those concerns.
- Neither callable wires itself into the composition root. Story 3.5
  (Nerve session lifecycle) calls :func:`load_briefing_aggregate` on
  bare-``nova`` boot; this module is a pure producer.
"""

from __future__ import annotations

from nova.core.config import NovaConfig
from nova.core.types import BriefingState
from nova.ports.brain import BrainPort
from nova.systems.brain.models import BriefingAggregate, ModeInfo


async def load_briefing_aggregate(brain: BrainPort, config: NovaConfig) -> BriefingAggregate:
    """Merge Brain persisted facts with ``NovaConfig.modes`` into a BriefingAggregate.

    Call order is deterministic:

    1. :meth:`BrainPort.get_last_session` → ``last_session``.
    2. :meth:`BrainPort.get_last_seed` → ``last_seed``.
    3. If ``last_session is not None``,
       :meth:`BrainPort.get_last_snapshot_for_session` with that session
       id → ``last_snapshot``. Empty-DB fast path: skipped entirely so no
       spurious read lands on an empty ``workspace_snapshots`` table.
    4. One :meth:`BrainPort.get_mode_last_used` per configured mode,
       iterated in stem-ascending order (``sorted(config.modes.items())``).
       Each call passes the stem (the dict key) — the canonical
       identifier matching ``sessions.mode_name`` on the write side.

    ``recent_memory`` is populated as an empty tuple — T1 does not ship
    real memory reads; Story 3.3 / Epic 4 / Epic 5 will populate it
    once memory-item queries exist on ``BrainPort``.

    This function is a pure producer: no logging, no side effects
    beyond the awaited port calls, no mutation of ``config``.
    """
    last_session = await brain.get_last_session()
    last_seed = await brain.get_last_seed()
    last_snapshot = (
        None
        if last_session is None
        else await brain.get_last_snapshot_for_session(last_session.session_id)
    )
    mode_infos: list[ModeInfo] = []
    for stem, mode_config in sorted(config.modes.items()):
        last_used_at = await brain.get_mode_last_used(stem)
        mode_infos.append(
            ModeInfo(
                stem=stem,
                display_name=mode_config.name,
                app_count=len(mode_config.apps),
                is_default=mode_config.is_default,
                last_used_at=last_used_at,
            )
        )
    return BriefingAggregate(
        last_session=last_session,
        last_snapshot=last_snapshot,
        last_seed=last_seed,
        available_modes=tuple(mode_infos),
        recent_memory=(),
    )


def determine_briefing_state(aggregate: BriefingAggregate) -> BriefingState:
    """Return the briefing state for ``aggregate`` via the first-match-wins machine.

    Pure function. No async, no DB, no clock, no logging. Deterministic:
    identical inputs produce identical outputs with zero side effects.

    See the module docstring for the state-machine table and the
    explanation of why State B is NOT the normal post-setup path in T1.
    """
    if not aggregate.available_modes and aggregate.last_session is None:
        return BriefingState.FIRST_RUN
    if aggregate.last_seed is None and (
        aggregate.last_session is None or aggregate.last_session.is_complete is False
    ):
        return BriefingState.POST_SETUP
    return BriefingState.WARM_RESUME


__all__: list[str] = ["determine_briefing_state", "load_briefing_aggregate"]
