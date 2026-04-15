"""HandsPort — desktop actions (launch, focus, arrange).

Story 1.9 (AC #4) pins a single T1 method: :meth:`HandsPort.restore_mode`.
Per-app :meth:`launch_app` / :meth:`focus_window` / :meth:`arrange_windows`
specializations are internal to :class:`nova.adapters.win32.actions.Win32HandsAdapter`
in Story 3.6; Story 6.1 may widen the port if a direct per-action surface
is needed by Nerve.

T1 safe-only action set (project-context.md:193): launch, focus, arrange
— nothing destructive.

Port rules (architecture.md:948-986, 1462):

- :class:`HandsPort` is a :class:`typing.Protocol` (structural subtyping).
- Every method is ``async def`` with an ellipsis body.
- Adapter types (``subprocess.Popen``, ``ShellExecute`` handles) stay
  trapped in ``adapters/win32/actions.py`` — only domain types
  (:class:`nova.core.config.ModeConfig`,
  :class:`nova.systems.hands.models.ActionResult`) cross this boundary.
"""

from __future__ import annotations

from typing import Protocol

from nova.core.config import ModeConfig
from nova.systems.hands.models import ActionResult


class HandsPort(Protocol):
    """Desktop-action surface owned by Hands."""

    async def restore_mode(self, mode_config: ModeConfig) -> list[ActionResult]: ...


__all__: list[str] = [
    "HandsPort",
]
