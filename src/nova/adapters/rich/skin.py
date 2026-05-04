"""Rich-backed :class:`~nova.ports.skin.SkinPort` implementation (Story 3.3).

Architecture (architecture.md:1377):
``adapters/rich/skin.py — RichSkinAdapter — Panel, Table, Tree, Progress
rendering``. Story 3.3 ships only :meth:`render_briefing_card` (the
Briefing Card Panel surface). Tree (transparency, Epic 5) and Progress
(mode restore, Story 3.6) land in their epics. Command parsing
(Story 3.4) and the shutdown / response / input methods (Story 3.7)
land in their respective stories.

Port-trapping invariant (project-context.md §62):
Rich-specific types (:class:`rich.panel.Panel`, :class:`rich.text.Text`,
:class:`rich.console.Console`) stay inside this file. The
:class:`~nova.ports.skin.SkinPort` Protocol surface uses domain types
only.

Skin makes ZERO content decisions
---------------------------------
The renderer reads :class:`~nova.systems.ritual.models.BriefingViewModel`
fields in a fixed order, applies a fixed Rich style per field, and
omits the corresponding line when the field is ``None`` /
empty-tuple. There is NO ``if view_model.state is …`` branch — every
visible character originates in
:meth:`~nova.systems.ritual.system.RitualSystem.build_briefing`. State A
produces a ViewModel with ``intro_lines`` populated and everything else
None / empty; State B populates ``intro_lines`` + ``available_modes_label``
+ ``prompt_text``; State C populates ``seed_quote`` + ``last_session_label``
+ ``last_apps_label`` + ``prompt_text`` (and optionally ``prose_enrichment``).
The renderer's omission logic + the ViewModel's field presence drive
everything.

Spacing model — block transitions
---------------------------------
Body lines are grouped into "blocks." A blank line separates adjacent
blocks; lines within the same block are tight (single newline only).
Block assignments:

* ``intro`` — :attr:`BriefingViewModel.intro_lines` (locked-copy preface)
* ``seed`` — :attr:`BriefingViewModel.seed_quote`
* ``metadata`` — :attr:`BriefingViewModel.last_session_label` and
  :attr:`BriefingViewModel.last_apps_label` (tight grouping; one
  newline between, no blank line)
* ``available_modes`` — :attr:`BriefingViewModel.available_modes_label`
* ``prose`` — :attr:`BriefingViewModel.prose_enrichment`
* ``prompt`` — :attr:`BriefingViewModel.prompt_text`
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from nova.systems.brain.models import SessionSummary
from nova.systems.hands.models import ActionResult
from nova.systems.ritual.models import BriefingViewModel
from nova.systems.skin.models import Command


class RichSkinAdapter:
    """Concrete :class:`~nova.ports.skin.SkinPort` implementation backed by Rich.

    Holds one mutable reference (``self._console``); otherwise stateless.
    The :class:`Console` is dependency-injected so tests can pass
    ``Console(record=True, file=StringIO(), width=80)`` without
    monkeypatching globals.
    """

    def __init__(self, console: Console) -> None:
        self._console = console

    async def render_briefing_card(self, view_model: BriefingViewModel) -> None:
        """Render a State A/B/C Briefing Card as a cyan Rich Panel.

        Pre-rendered labels from the ViewModel are mapped to fixed Rich
        styles and emitted in a fixed order. ``None`` / empty-tuple /
        empty-string fields are omitted; block transitions get a blank
        line of separation, in-block transitions are single newlines.

        The panel title is constructed via :class:`rich.text.Text` (NOT
        via Rich-markup-string concatenation) so that any future
        user-controlled title source cannot inject Rich markup tags
        (review finding P2). Body lines use the same markup-safe
        :meth:`Text.append` pattern.
        """
        body = Text()
        previous_block: str | None = None

        def _emit(text: str, style: str, block: str) -> None:
            nonlocal previous_block
            if previous_block is not None:
                body.append("\n\n" if previous_block != block else "\n")
            body.append(text, style=style)
            previous_block = block

        # Filter empty-string entries — review finding P14. An empty
        # ``intro_lines`` member would render as a visible double-newline
        # via the block-transition rule.
        for line in view_model.intro_lines:
            if line:
                _emit(line, "bright_white", "intro")

        if view_model.seed_quote is not None:
            _emit(view_model.seed_quote, "bold bright_white", "seed")

        if view_model.last_session_label is not None:
            _emit(view_model.last_session_label, "dim", "metadata")

        if view_model.last_apps_label is not None:
            _emit(view_model.last_apps_label, "dim", "metadata")

        if view_model.available_modes_label is not None:
            _emit(view_model.available_modes_label, "", "available_modes")

        if view_model.prose_enrichment is not None:
            _emit(view_model.prose_enrichment, "", "prose")

        if view_model.prompt_text is not None:
            _emit(view_model.prompt_text, "bold bright_white", "prompt")

        # Build the title as a Text object — markup-injection-safe even
        # if a future story sources `view_model.title` from user input.
        title = Text(view_model.title, style="bold cyan")

        panel = Panel(
            body,
            title=title,
            border_style="cyan",
            padding=(1, 2),
        )
        self._console.print(panel)

    async def render_progress(self, results: Sequence[ActionResult]) -> None:
        raise NotImplementedError("Story 3.6 scope")

    async def render_shutdown_card(self, summary: SessionSummary) -> None:
        raise NotImplementedError("Story 3.7 scope")

    async def render_response(self, text: str) -> None:
        raise NotImplementedError("Story 3.7 scope")

    async def collect_input(self, prompt: str) -> str:
        raise NotImplementedError("Story 3.7 scope")

    async def parse_command(self, raw_input: str) -> Command:
        raise NotImplementedError("Story 3.4 scope")


__all__: list[str] = ["RichSkinAdapter"]
