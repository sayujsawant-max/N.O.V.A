"""First-run setup entrypoint — ``python -m nova.setup``.

Two modes:

- ``--validate-only <path>`` — pure validation of a proposed data
  directory via :func:`nova.core.paths.validate_data_dir`. Prints the
  result to stdout and exits 0 (valid) or 1 (``ConfigError``). No
  filesystem state is created. Used by ``setup.bat`` to reject
  pathological paths before any ``mkdir`` so bad paths never produce
  partial state. Argparse-level usage errors (missing flag value,
  unknown flag) exit with code 2 per argparse's default.

- Without flags — renders Briefing Card State A (first-run
  orientation) via Rich, then exits 0. The full interactive wizard
  (API key prompt, mode creation, initial capture) is Stories 2.2–2.4;
  Story 2.1 ships this scaffold only.

State A here is a direct, minimal Rich render — NOT the full
BriefingAggregate / BriefingViewModel pipeline (that assembly lives in
Epic 3, Stories 3.2–3.3). Stories 2.4 and 3.3 will replace this branch
with the bridge-contract flow.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from nova.core.exceptions import ConfigError
from nova.core.paths import validate_data_dir
from nova.setup.api_key import run_api_key_step
from nova.setup.mode_wizard import run_mode_wizard_step


def _force_utf8_stdout() -> None:
    """Reconfigure stdout/stderr to UTF-8 so ``✓``/``✗``/``⚠`` render.

    Windows legacy terminals default to cp1252 which can't encode the
    operational symbols mandated by the UX spec. Python 3.7+ supports
    ``TextIOBase.reconfigure``; ignore failures silently — the caller
    can still run, symbols just fall back to ``?``.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        # Unreconfigurable stream (already closed, non-text, etc.) —
        # degrade silently rather than crash on a cosmetic concern.
        with contextlib.suppress(OSError, ValueError):
            reconfigure(encoding="utf-8")


EXIT_OK: int = 0
EXIT_CONFIG_ERROR: int = 1


def _render_state_a(console: Console) -> None:
    """Render Briefing Card State A — first-run orientation.

    Minimal Rich Panel: bold cyan title, soft white body. Stories 2.4
    and 3.3 will replace this with the full ``BriefingAggregate`` →
    ``BriefingViewModel`` pipeline.
    """
    body = Text()
    body.append("Personal AI Session Companion", style="bright_white")
    body.append("\n\n")
    body.append("First session. No history yet.", style="bright_white")
    body.append("\n")
    body.append("Running setup to create your workspace modes.", style="bright_white")

    panel = Panel(
        body,
        title="[bold cyan]N.O.V.A.[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    )
    console.print(panel)


def _handle_validate_only(path_arg: str, console: Console) -> int:
    """Validate ``path_arg`` without creating any filesystem state.

    Returns :data:`EXIT_OK` on success, :data:`EXIT_CONFIG_ERROR` on
    validation failure. Failure messages follow the UX spec's
    operational pattern: ``✗ <reason>`` + ``Setup stopped.`` — no
    traceback.
    """
    try:
        validate_data_dir(Path(path_arg))
    except ConfigError as err:
        console.print(f"[red]✗[/red] {err}")
        console.print("Setup stopped.")
        return EXIT_CONFIG_ERROR
    console.print("[green]✓[/green] Path is valid.")
    return EXIT_OK


def _resolve_data_dir() -> Path | None:
    """Resolve the user data directory from ``LOCALAPPDATA``.

    Returns ``None`` if ``LOCALAPPDATA`` is not set — the caller must
    handle this gracefully (skip the step, don't crash).  Matches
    ``setup.bat``'s resolution convention.
    """
    localappdata = os.environ.get("LOCALAPPDATA")
    if not localappdata:
        return None
    return Path(localappdata) / "nova"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nova.setup",
        description="N.O.V.A. first-run setup.",
    )
    parser.add_argument(
        "--validate-only",
        metavar="PATH",
        default=None,
        help="Validate a candidate data directory without creating it; exit 0 or 1.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Sync entrypoint for ``python -m nova.setup``.

    ``argv`` is an optional list for testability; defaults to
    :data:`sys.argv[1:]` via argparse when ``None``.
    """
    _force_utf8_stdout()
    parser = _build_parser()
    args = parser.parse_args(argv)
    console = Console()

    if args.validate_only is not None:
        return _handle_validate_only(args.validate_only, console)

    _render_state_a(console)

    # Story 2.2: API key configuration step
    # Story 2.3: guided mode creation wizard
    data_dir = _resolve_data_dir()
    if data_dir is not None:
        run_api_key_step(console, data_dir)
        run_mode_wizard_step(console, data_dir)
    else:
        console.print(
            "[yellow]\u26a0[/yellow] LOCALAPPDATA not set. Skipping API key and mode configuration."
        )

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
