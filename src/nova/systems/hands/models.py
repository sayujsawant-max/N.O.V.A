"""Hands-layer domain models consumed through :mod:`nova.ports.hands`.

``ActionRequest`` is the input to a Hands operation (launch, focus,
arrange — T1's safe-only action set per project-context.md:193).
``ActionResult`` is the outcome report: success flag, canonical action
type, opaque target reference, and optional failure reason.

Only ``.models`` crosses system boundaries (Story 1.9 AC #8). Adapter
types (``subprocess.Popen``, ``ShellExecute`` handles) stay trapped in
``adapters/win32/actions.py`` and never leak through this module
(architecture.md:1462).

Note on opacity: the ``target`` field carries an opaque reference (e.g.,
``"code.exe"`` for a launched app, ``"protected_app"`` for an excluded
context). Callers are responsible for passing the opaque form — Hands
does not re-derive identity from the win32 layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from nova.core.types import ActionType


@dataclass(frozen=True)
class ActionRequest:
    """Request carrier for a Hands action.

    ``details`` is a read-only mapping with ``object`` values (not
    ``Any``); callers narrow at their boundary. ``None`` is permitted
    when the action has no caller-supplied metadata.
    """

    action_type: ActionType
    target: str | None
    details: Mapping[str, object] | None


@dataclass(frozen=True)
class ActionResult:
    """Outcome of a single Hands action.

    ``reason`` carries the failure reason when ``success`` is ``False``
    and is ``None`` on success — same shape as
    :class:`nova.core.events.AppLaunched`.
    """

    action_type: ActionType
    target: str
    success: bool
    reason: str | None


__all__: list[str] = [
    "ActionRequest",
    "ActionResult",
]
