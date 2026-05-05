"""SkinPort — terminal rendering + input collection + command parsing.

Story 1.9 (AC #4) pins six T1 methods: four render methods
(:meth:`render_briefing_card`, :meth:`render_progress`,
:meth:`render_shutdown_card`, :meth:`render_response`), one input-collection
method (:meth:`collect_input`), and one parser method
(:meth:`parse_command`).

Skin generates no prose (project-context.md:64): every render method
consumes already-rendered content (a view model, a summary, a Voice-
generated string). Voice generates text; Skin renders it. The two roles
never cross.

Story 3.6 reshapes :meth:`render_progress` from
``Sequence[ActionResult] -> None`` to ``ActionResult -> None`` so
:class:`~nova.systems.hands.system.HandsSystem` streams per-app
feedback inline as each launch lands rather than batching at the end.
The Sequence form was speculative (Story 1.9 stub); single-result is
what the epic AC requires (``✓ VS Code`` / ``✗ Postman`` lines render
as each launch lands).

Story 3.7 reshapes :meth:`render_shutdown_card` from
``SessionSummary -> None`` to ``ShutdownViewModel -> None``. The
``SessionSummary`` typing was a Story 1.9 stub assumption;
``SessionSummary`` is the Brain-projection (carries ``is_complete``,
raw ``duration_seconds``, etc.) and lacks the active-mode display
label, the apps-used tuple, the formatted duration string, and the
prompt text. The pre-rendered-labels pattern from Story 3.3 is the
right shape — ``ShutdownViewModel`` has every visible string already
formatted; Skin only chooses the Rich style.

Port rules (architecture.md:948-986, 1464):

- :class:`SkinPort` is a :class:`typing.Protocol` (structural subtyping).
- Every method is ``async def`` with an ellipsis body.
- Adapter types (``rich.panel.Panel``, ``rich.table.Table``,
  ``rich.console.Console``) stay trapped in ``adapters/rich/skin.py`` —
  only domain types and primitives (``str`` for free-form rendered text)
  cross this boundary.
"""

from __future__ import annotations

from typing import Protocol

from nova.systems.hands.models import ActionResult
from nova.systems.ritual.models import BriefingViewModel, ShutdownViewModel
from nova.systems.skin.models import Command


class SkinPort(Protocol):
    """Terminal-rendering + input-collection + parser surface owned by Skin."""

    async def render_briefing_card(self, view_model: BriefingViewModel) -> None: ...

    async def render_progress(self, result: ActionResult) -> None: ...

    async def render_shutdown_card(self, view_model: ShutdownViewModel) -> None: ...

    async def render_response(self, text: str) -> None: ...

    async def collect_input(self, prompt: str) -> str: ...

    async def parse_command(self, raw_input: str) -> Command: ...


__all__: list[str] = [
    "SkinPort",
]
