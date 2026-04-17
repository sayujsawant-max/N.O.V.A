"""Completion-UX renderers for first-run setup (Story 2.4).

Owns two Rich renderers used by :mod:`nova.setup.__main__` after the
wizard completes and the transactional session + snapshot write lands:

* :func:`render_capture_status` — one operational status line per
  :class:`nova.setup.initial_capture.CaptureResult` — green ``✓`` for
  "full", amber ``⚠`` for "partial" / "empty" / "unavailable".
* :func:`render_completion_panel` — the setup-complete Rich ``Panel``
  derived from the already-loaded :class:`nova.core.config.NovaConfig`
  (no filesystem scan — single source of truth).

Both renderers follow the Story 2.3 UX-voice contract: no emoji beyond
the whitelist ``{"✓", "✗", "⚠", "—"}``, no sycophantic framing, no
trailing celebration copy. The banned-phrase list is exposed as
:data:`BANNED_PHRASES` so the test suite can assert it everywhere.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from nova.core.config import NovaConfig
from nova.setup.initial_capture import CaptureResult

__all__ = [
    "BANNED_PHRASES",
    "render_capture_status",
    "render_completion_panel",
]


BANNED_PHRASES: tuple[str, ...] = (
    "How can I help you today",
    "I'd be happy to",
    "Great question",
    "Great!",
    "Welcome to N.O.V.A.",
    "All set!",
)
"""Phrases that may NEVER appear in any Story 2.4 output.

Asserted by ``test_completion.py`` against the rendered Rich output
exported via ``Console.export_text``. Adding a phrase here is a
contract-widening event — update the test matrix at the same time.
"""


def render_capture_status(console: Console, capture: CaptureResult) -> None:
    """Print one operational status line for the capture outcome.

    Dispatches on ``capture.status`` via ``match`` with a default arm
    that raises ``AssertionError`` — combined with the ``Literal`` alias
    on :class:`CaptureResult`, this locks the four-status closed set at
    both type-check and run-time.
    """
    match capture.status:
        case "full":
            console.print(
                f"[green]✓[/green] Captured initial workspace snapshot "
                f"({capture.windows_captured} apps)."
            )
        case "partial":
            total = capture.windows_captured + capture.windows_dropped
            console.print(
                f"[yellow]⚠[/yellow] Captured {capture.windows_captured} "
                f"of {total} apps; setup will continue."
            )
        case "empty":
            console.print("[yellow]⚠[/yellow] Workspace capture is empty. Setup will continue.")
        case "unavailable":
            console.print(
                "[yellow]⚠[/yellow] Workspace capture unavailable right now. Setup will continue."
            )
        case _ as other:
            # Exhaustiveness guard — ``CaptureStatus`` is a ``Literal`` so
            # type-checking should already prevent this, but the runtime
            # arm is test-covered by
            # ``test_capture_status_exhaustive_dispatch`` and locks the
            # four-state closed set at runtime too.
            raise AssertionError(f"unhandled CaptureResult.status: {other!r}")


def render_completion_panel(console: Console, config: NovaConfig) -> None:
    """Render the ``Setup complete.`` panel derived from ``config.modes``.

    Sort order is case-insensitive by ``ModeConfig.name`` so the list
    matches what runtime loading will present on the next ``uv run
    nova`` invocation. Singular / plural copy branches on
    ``len(config.modes)``.
    """
    modes = sorted(config.modes.values(), key=lambda m: m.name.casefold())

    if not modes:
        body = Text(
            "You have no modes ready. Re-run setup.bat to complete setup.",
            style="bright_white",
        )
    else:
        noun = "mode" if len(modes) == 1 else "modes"
        names = ", ".join(m.name for m in modes)
        body = Text()
        body.append(f"You have {len(modes)} {noun} ready: {names}", style="bright_white")
        body.append("\n")
        body.append("Run ", style="bright_white")
        body.append("uv run nova", style="bold bright_white")
        body.append(" to start your next session.", style="bright_white")

    console.print(
        Panel(
            body,
            title="[bold cyan]Setup complete.[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )
