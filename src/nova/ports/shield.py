"""ShieldPort — focus protection, DND, distraction detection (stubbed in T1).

Story 1.9 (AC #4, #6) pins two T1 methods:
:meth:`ShieldPort.is_focus_protected` and :meth:`ShieldPort.allow_action`.
The T1 shipping adapter is :class:`nova.adapters.shield.noop.NoOpShieldAdapter`
— inert returns (``False`` / ``True``). The real policy engine lives in
``systems/shield/system.py`` + ``adapters/win32/shield.py`` in v0.15.

:class:`ShieldPort` is the **only** port in Story 1.9 decorated with
:func:`typing.runtime_checkable`. Rationale (Story 1.9 AC #11): the
Shield adapter is the only port implementation verified via runtime
``isinstance`` checks in this story's test suite. Other ports rely on
mypy strict for structural conformance at call sites; opting them into
``@runtime_checkable`` speculatively would add ``__instancecheck__``
overhead for no current benefit.

Port rules (architecture.md:948-986):

- :class:`ShieldPort` is a :class:`typing.Protocol` (structural subtyping)
  decorated with :func:`typing.runtime_checkable`.
- Every method is ``async def`` with an ellipsis body.
- No adapter-specific types in signatures — only domain enums
  (:class:`nova.core.types.ActionType`) and primitives.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from nova.core.types import ActionType


@runtime_checkable
class ShieldPort(Protocol):
    """Focus-protection / action-gating surface owned by Shield.

    T1 implementation is the no-op adapter. v0.15 ships the real policy
    engine behind this same port.
    """

    async def is_focus_protected(self) -> bool: ...

    async def allow_action(self, action_type: ActionType) -> bool: ...


__all__: list[str] = [
    "ShieldPort",
]
