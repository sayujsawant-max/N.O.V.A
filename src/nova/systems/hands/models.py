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

Story 3.6 invariants (``__post_init__``)
----------------------------------------
* :class:`ActionResult` enforces the tri-state pairing:
  ``success=True`` ⇔ ``reason is None``;
  ``success=False`` ⇔ ``reason`` is a non-empty string.
  Closes deferred-work.md:146.
* :class:`ActionRequest` wraps caller-supplied ``details`` mappings in
  ``types.MappingProxyType`` (after a defensive ``dict(...)`` copy) so
  the frozen-dataclass promise holds at runtime — caller-side mutation
  of the original dict cannot reach back into the request.
  Closes deferred-work.md:137.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from nova.core.types import ActionType


@dataclass(frozen=True)
class ActionRequest:
    """Request carrier for a Hands action.

    ``details`` is a read-only mapping with ``object`` values (not
    ``Any``); callers narrow at their boundary. ``None`` is permitted
    when the action has no caller-supplied metadata.

    The ``__post_init__`` defensively copies and wraps any non-``None``
    ``details`` argument in :class:`types.MappingProxyType`. Without
    the copy, a caller mutating its original dict would mutate the
    request's view too — defeating the frozen-dataclass promise. The
    ``object.__setattr__`` rewrite is the documented escape hatch for
    frozen dataclasses (see Python dataclasses reference).
    """

    action_type: ActionType
    target: str | None
    details: Mapping[str, object] | None

    def __post_init__(self) -> None:
        if self.details is None:
            return
        # Defensive copy + freeze ALWAYS — even when the caller passes a
        # ``MappingProxyType``, the proxy wraps a mutable underlying dict
        # the caller may still hold a reference to. Without re-wrapping,
        # caller-side mutation of that source dict shows through
        # ``req.details`` and silently breaks the frozen-dataclass
        # promise. The ``dict(self.details)`` copy isolates the new proxy
        # from any caller-retained reference.
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


@dataclass(frozen=True)
class ActionResult:
    """Outcome of a single Hands action.

    ``reason`` carries the failure reason when ``success`` is ``False``
    and is ``None`` on success — same shape as
    :class:`nova.core.events.AppLaunched`.

    The ``__post_init__`` validator enforces the tri-state invariant at
    construction time so a programmer error (``ActionResult(success=True,
    reason="failed")``) fails immediately rather than at the consumer.
    """

    action_type: ActionType
    target: str
    success: bool
    reason: str | None

    def __post_init__(self) -> None:
        if self.success and self.reason is not None:
            raise ValueError("ActionResult: success=True requires reason=None")
        if not self.success and (self.reason is None or not self.reason):
            raise ValueError("ActionResult: success=False requires non-empty reason")


__all__: list[str] = [
    "ActionRequest",
    "ActionResult",
]
