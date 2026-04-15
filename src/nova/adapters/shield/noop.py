"""T1 no-op Shield adapter — structurally satisfies :class:`nova.ports.shield.ShieldPort`.

Story 1.9 (AC #6) pins the contract: T1 ships an inert adapter that
answers every :class:`nova.ports.shield.ShieldPort` method with an
always-safe default. Focus protection is a v0.15 feature; until then the
no-op returns keep the port wired through the composition root without
ever blocking an action or reporting protection.

Contract:

- :meth:`NoOpShieldAdapter.is_focus_protected` returns :data:`False`
  (focus is never protected in T1).
- :meth:`NoOpShieldAdapter.allow_action` returns :data:`True` for every
  :class:`nova.core.types.ActionType` (no action is ever gated in T1).

No instance state, no logging, no audit hooks, no event emissions. The
adapter is genuinely silent — it is not an observability surface
(observability would drift the contract; v0.15 decides whether Shield
decisions are auditable at that time).

Facade / adapter split (Story 1.9 AC #6): the port and facade boundary
live in ``systems/shield/`` (currently a docstring-only package marker
until v0.15 ships the real policy engine). This file is the adapter
implementation — concrete behavior is an adapter concern, not a system
concern.
"""

from __future__ import annotations

from nova.core.types import ActionType


class NoOpShieldAdapter:
    """Inert :class:`nova.ports.shield.ShieldPort` implementation for T1.

    Structurally conforms to :class:`nova.ports.shield.ShieldPort` without
    nominal inheritance — the port is a :class:`typing.Protocol`, so mypy
    strict checks shape at the call site and ``isinstance`` works at
    runtime because :class:`nova.ports.shield.ShieldPort` is decorated
    with :func:`typing.runtime_checkable`.
    """

    async def is_focus_protected(self) -> bool:
        return False

    async def allow_action(self, action_type: ActionType) -> bool:
        # ``action_type`` is part of the structural contract with ShieldPort
        # but unused by the T1 no-op; v0.15's real adapter will consult policy.
        return True


__all__: list[str] = [
    "NoOpShieldAdapter",
]
