"""NervePort — orchestration, tier state, command routing.

Story 1.9 (AC #4) pins two T1 methods: :meth:`NervePort.startup` (called
by :mod:`nova.cli` to boot the continuity loop — Story 1.10) and
:meth:`NervePort.route_command` (called by Skin when a user command is
parsed).

Nerve is an orchestrator and policy layer (project-context.md:65), not a
dumb router. It makes orchestration decisions (skip briefing, degrade to
local-only, suppress actions during offline) but never generates user-
facing prose. Events flow through :class:`nova.core.events.EventBus`
(Story 1.3); :class:`NervePort` does not expose an event-subscription
surface here — subscriptions are wired in the composition root
(Story 1.10).

Port rules (architecture.md:948-986, 1465):

- :class:`NervePort` is a :class:`typing.Protocol` (structural subtyping).
- Every method is ``async def`` with an ellipsis body.
- No adapter types — only :class:`nova.systems.skin.models.Command`.
"""

from __future__ import annotations

from typing import Protocol

from nova.systems.skin.models import Command


class NervePort(Protocol):
    """Orchestration + command-routing surface owned by Nerve."""

    async def startup(self) -> None: ...

    async def route_command(self, command: Command) -> None: ...


__all__: list[str] = [
    "NervePort",
]
