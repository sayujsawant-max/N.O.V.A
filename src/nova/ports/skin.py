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

Port rules (architecture.md:948-986, 1464):

- :class:`SkinPort` is a :class:`typing.Protocol` (structural subtyping).
- Every method is ``async def`` with an ellipsis body.
- Adapter types (``rich.panel.Panel``, ``rich.table.Table``,
  ``rich.console.Console``) stay trapped in ``adapters/rich/skin.py`` —
  only domain types and primitives (``str`` for free-form rendered text)
  cross this boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from nova.systems.brain.models import SessionSummary
from nova.systems.hands.models import ActionResult
from nova.systems.ritual.models import BriefingViewModel
from nova.systems.skin.models import Command


class SkinPort(Protocol):
    """Terminal-rendering + input-collection + parser surface owned by Skin."""

    async def render_briefing_card(self, view_model: BriefingViewModel) -> None: ...

    async def render_progress(self, results: Sequence[ActionResult]) -> None: ...

    async def render_shutdown_card(self, summary: SessionSummary) -> None: ...

    async def render_response(self, text: str) -> None: ...

    async def collect_input(self, prompt: str) -> str: ...

    async def parse_command(self, raw_input: str) -> Command: ...


__all__: list[str] = [
    "SkinPort",
]
