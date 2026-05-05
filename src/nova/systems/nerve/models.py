"""Nerve-layer domain models that cross the port boundary.

Per Story 1.9 AC #8, only ``.models`` modules cross system boundaries.
``nova.systems.nerve.briefing`` and ``nova.systems.nerve.system`` are
Nerve-internal; only the types declared here may be referenced from
``nova.ports.nerve`` or from another system.

Story 3.5 ships :class:`CommandOutcome`, the closed two-member vocabulary
that drives the REPL loop's continue/exit decision. Closes
``deferred-work.md:139`` (``NervePort.route_command`` returns ``None`` —
error surface undocumented). With ``-> CommandOutcome``, every handler
declares its outcome explicitly and the REPL inspects the return value
with a clean ``if outcome is CommandOutcome.EXIT`` instead of relying on
control-flow-via-exception.
"""

from __future__ import annotations

from enum import StrEnum


class CommandOutcome(StrEnum):
    """Outcome of a routed Command — drives the REPL loop's continue/exit decision.

    :attr:`CONTINUE` — the REPL loop returns to ``collect_input`` for the
        next turn. Every Layer B routable verb (except ``SHUTDOWN``),
        every Layer C contextual verb (gated or unmapped), and the marker
        verbs ``UNKNOWN`` / ``EMPTY`` resolve to ``CONTINUE`` in Story 3.5.

    :attr:`EXIT` — the REPL loop terminates; the caller
        (:meth:`nova.systems.nerve.system.NerveSystem.startup`) runs
        cleanup and returns. Today only the ``SHUTDOWN`` verb produces
        ``EXIT``; Story 3.7 may add seed-cancel paths that still resolve
        to ``EXIT``.

    The closed two-member vocabulary is locked by
    ``tests/unit/systems/nerve/test_command_outcome_shape.py``. Adding a
    third outcome (e.g., ``ABORT`` for crash recovery, ``RESET`` for a
    re-prompt) is a deliberate update to that test plus the dispatch
    table in :meth:`NerveSystem.route_command`.
    """

    CONTINUE = "continue"
    EXIT = "exit"


__all__: list[str] = ["CommandOutcome"]
