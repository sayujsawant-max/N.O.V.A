"""EyesPort — context capture, workspace snapshots.

Story 1.9 (AC #4) pins a single T1 method: :meth:`EyesPort.capture_current_workspace`.
The context-polling loop that emits :class:`nova.core.events.ContextChanged`
events is EyesSystem-internal (Story 4.1+) and not exposed on the port.

Port rules (architecture.md:948-986, 1461):

- :class:`EyesPort` is a :class:`typing.Protocol` (structural subtyping).
- Every method is ``async def`` with an ellipsis body.
- Adapter types (``win32gui`` handles, ``psutil.Process`` instances) stay
  trapped in ``adapters/win32/context.py`` — only domain types
  (:class:`nova.systems.eyes.models.WorkspaceSnapshot`,
  :class:`nova.systems.eyes.models.WindowContext`) cross this boundary.
"""

from __future__ import annotations

from typing import Protocol

from nova.systems.eyes.models import WorkspaceSnapshot


class EyesPort(Protocol):
    """Workspace capture surface owned by Eyes."""

    async def capture_current_workspace(self) -> WorkspaceSnapshot: ...


__all__: list[str] = [
    "EyesPort",
]
