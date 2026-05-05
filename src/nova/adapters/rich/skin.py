"""Rich-backed :class:`~nova.ports.skin.SkinPort` implementation (Story 3.3).

Architecture (architecture.md:1377):
``adapters/rich/skin.py — RichSkinAdapter — Panel, Table, Tree, Progress
rendering``. Story 3.3 ships :meth:`render_briefing_card` (the
Briefing Card Panel surface). Story 3.4 ships :meth:`parse_command`
(delegates to :func:`nova.systems.skin.commands.parse`). Story 3.5 ships
:meth:`collect_input` and :meth:`render_response` (the REPL primitives
the :class:`~nova.systems.nerve.system.NerveSystem` loop consumes).
Story 3.6 ships :meth:`render_progress` per-app inline (single
:class:`ActionResult` per call, NOT a batch). Tree (transparency,
Epic 5) and the Shutdown Card (Story 3.7) land in their respective
stories.

The :data:`REASON_NOT_FOUND` import from :mod:`nova.ports.app_launcher`
is the canonical reason-vocabulary owner — importing from the port
file (not from a sibling adapter) preserves the no-cross-adapter-imports
rule (project-context.md:62).

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

import asyncio
import threading
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from nova.ports.app_launcher import REASON_NOT_FOUND
from nova.systems.brain.models import SessionSummary
from nova.systems.hands.models import ActionResult
from nova.systems.ritual.models import BriefingViewModel
from nova.systems.skin.commands import parse
from nova.systems.skin.models import Command


def _safe_set_result(future: asyncio.Future[Any], result: Any) -> None:
    """Set ``future``'s result iff it isn't already done.

    Called from :meth:`asyncio.AbstractEventLoop.call_soon_threadsafe`
    so it runs on the loop thread where future state is consistent.
    The done-check guards against the cancelled-but-thread-finishes
    race (REPL race-pattern teardown cancels the asyncio task; the
    background daemon thread may still complete its blocking ``input``
    call afterward).
    """
    if not future.done():
        future.set_result(result)


def _safe_set_exception(future: asyncio.Future[Any], exc: BaseException) -> None:
    """Set ``future``'s exception iff it isn't already done. See :func:`_safe_set_result`."""
    if not future.done():
        future.set_exception(exc)


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

    async def render_progress(self, result: ActionResult) -> None:
        """Render a single per-app launch result inline (Story 3.6).

        Line shape:
        * Success (including the already-running case — adapter
          returns ``success=True, reason=None`` either way):
          ``"✓ {result.target}"``.
        * Failure with ``reason == REASON_NOT_FOUND``:
          ``"✗ {result.target} (not found — is it installed?)"``
          (extra ``"is it installed?"`` hint per UX spec line 698).
        * Other failure: ``"✗ {result.target} ({result.reason})"``.

        Operational output per project-context.md:66 — direct to Skin,
        no Voice. ``markup=False`` is critical for the same reason as
        :meth:`render_response`: Rich's default markup parsing would
        interpret ``[`` / ``]`` in app names or reasons. The
        :func:`asyncio.to_thread` wrap mirrors :meth:`render_response`
        / :meth:`render_briefing_card` for Rich's blocking I/O.
        """
        if result.success:
            line = f"✓ {result.target}"
        elif result.reason == REASON_NOT_FOUND:
            line = f"✗ {result.target} ({result.reason} — is it installed?)"
        else:
            line = f"✗ {result.target} ({result.reason})"
        await asyncio.to_thread(self._console.print, line, markup=False)

    async def render_shutdown_card(self, summary: SessionSummary) -> None:
        raise NotImplementedError("Story 3.7 scope")

    async def render_response(self, text: str) -> None:
        """Plain-line operational output — no panel, no markup, no Voice (Story 3.5).

        Per project-context.md:66, operational output bypasses Voice and
        renders direct via Skin. ``markup=False`` is critical: Rich's
        ``Console.print`` interprets ``[bold]…[/]``-style square-bracket
        markup by default; passing user-controllable text (or any
        operational template containing ``[`` / ``]``) without the flag
        would let arbitrary Rich markup activate — including potentially
        unintended color / styling effects. Wrapped in
        :func:`asyncio.to_thread` because Rich's ``Console.print`` is
        blocking I/O — same pattern as :meth:`render_briefing_card`.
        """
        await asyncio.to_thread(self._console.print, text, markup=False)

    async def collect_input(self, prompt: str) -> str:
        """Block until the user types a line; return the raw string (Story 3.5).

        Process-exit safety
        -------------------
        Runs :func:`Prompt.ask` on a **daemon** :class:`threading.Thread`
        instead of :func:`asyncio.to_thread` (which uses the loop's
        default executor — a non-daemon thread pool). On signal-driven
        exit, the REPL's race pattern cancels its asyncio await of this
        future, but the underlying blocking ``input()`` call is still
        sitting in the executor thread waiting for stdin. With a
        non-daemon thread, :func:`asyncio.run`'s
        ``shutdown_default_executor`` would block process exit until
        the user types ENTER (or stdin closes). With a daemon thread
        the OS kills it on process exit, so ``asyncio.run`` returns
        cleanly even when the prompt is still blocked. Documented
        invariant; locked by the
        ``test_collect_input_uses_daemon_thread_for_process_exit_safety``
        test in ``tests/unit/adapters/rich/test_skin_adapter.py``.

        Cancellation handling
        ---------------------
        If the asyncio await of ``future`` is cancelled, the daemon
        thread continues to run in the background. When it eventually
        returns (or the process exits), :func:`_safe_set_result` /
        :func:`_safe_set_exception` short-circuit on the already-done
        future — no spurious set-on-cancelled-future error.

        Result handling
        ---------------
        The empty-input case is allowed through to the Story 3.4
        parser (which maps to ``CommandVerb.EMPTY``); no pre-filtering
        happens here. :class:`EOFError` (closed stdin / Ctrl-D)
        propagates so the caller (:meth:`NerveSystem._run_repl`) can
        drive a clean SHUTDOWN. :class:`KeyboardInterrupt` propagates
        similarly.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        def _read_in_daemon_thread() -> None:
            try:
                result = Prompt.ask(prompt, console=self._console)
            except BaseException as exc:  # noqa: BLE001 — propagate via future
                # BaseException catch is intentional: KeyboardInterrupt /
                # EOFError / SystemExit raised by ``Prompt.ask`` (or by
                # signal interruption of the underlying ``input()`` call)
                # all need to surface on the asyncio future. Anything we
                # let escape this thread becomes an "unhandled exception
                # in thread" warning at process exit.
                loop.call_soon_threadsafe(_safe_set_exception, future, exc)
            else:
                loop.call_soon_threadsafe(_safe_set_result, future, result)

        thread = threading.Thread(
            target=_read_in_daemon_thread,
            name="nova-skin-input",
            daemon=True,
        )
        thread.start()
        return await future

    async def parse_command(self, raw_input: str) -> Command:
        return parse(raw_input)


__all__: list[str] = ["RichSkinAdapter"]
