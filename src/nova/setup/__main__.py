"""First-run setup entrypoint — ``python -m nova.setup``.

Two modes:

- ``--validate-only <path>`` — pure validation of a proposed data
  directory via :func:`nova.core.paths.validate_data_dir`. Prints the
  result to stdout and exits 0 (valid) or 1 (``ConfigError``). No
  filesystem state is created. Used by ``setup.bat`` to reject
  pathological paths before any ``mkdir`` so bad paths never produce
  partial state. Argparse-level usage errors (missing flag value,
  unknown flag) exit with code 2 per argparse's default.

- Without flags — runs the full first-run flow:
    1. Already-setup fast path — probe ``nova.db`` for a prior
       ``setup_complete`` audit row (Story 2.4). If present, render a
       short "setup already complete" panel and exit 0 without
       re-running the wizard, re-capturing, or re-persisting.
    2. Render Briefing Card State A (pre-setup orientation).
    3. Run the API key step (Story 2.2) and the mode wizard step
       (Story 2.3).
    4. Run the best-effort initial workspace capture (Story 2.4).
    5. Open the composition root via :func:`nova.app.create_app`,
       write the first ``sessions`` row + ``workspace_snapshots`` row
       in one transaction, log the ``setup_complete`` audit row.
    6. Render the ``Setup complete.`` panel and exit 0.

State A here is a direct, minimal Rich render — NOT the full
``BriefingAggregate`` / ``BriefingViewModel`` pipeline (that assembly
lives in Epic 3, Stories 3.2–3.3).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from nova.app import NovaApp, create_app
from nova.core.config import load_config
from nova.core.exceptions import ConfigError, StorageError
from nova.core.storage.engine import SqliteStorageEngine
from nova.core.types import ActionType
from nova.setup.api_key import run_api_key_step
from nova.setup.completion import render_capture_status, render_completion_panel
from nova.setup.initial_capture import capture_initial_workspace, persist_first_run
from nova.setup.mode_wizard import run_mode_wizard_step

logger = logging.getLogger("nova.setup.__main__")

EXIT_OK: int = 0
EXIT_CONFIG_ERROR: int = 1


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
        with contextlib.suppress(OSError, ValueError):
            reconfigure(encoding="utf-8")


def _render_state_a(console: Console) -> None:
    """Render Briefing Card State A — first-run orientation.

    Copy is locked verbatim by AC #1 — two body lines, in this exact
    order. The UX spec frames State A as "nothing to brief yet"; any
    tagline or "Running setup..." preface widens the contract and is
    explicitly rejected by the Story 2.4 test
    ``test_state_a_body_contains_first_session_line``.

    Title is constructed via :class:`rich.text.Text` (NOT via
    Rich-markup-string concatenation) — matches the bare-``nova``-boot
    renderer at :class:`~nova.adapters.rich.skin.RichSkinAdapter` so
    the byte-for-byte parity test (Story 3.3 AC #20 ANSI variant)
    holds across both code paths.
    """
    body = Text()
    body.append(
        "First session. No history yet — that's expected.",
        style="bright_white",
    )
    body.append("\n")
    body.append(
        "Let's set up your first workspace mode so tomorrow starts warm.",
        style="bright_white",
    )

    panel = Panel(
        body,
        title=Text("N.O.V.A.", style="bold cyan"),
        border_style="cyan",
        padding=(1, 2),
    )
    console.print(panel)


def _render_already_setup_panel(console: Console) -> None:
    """Story 2.4 AC #3 — ``setup.bat`` rerun after completion."""
    body = Text()
    body.append("Setup already complete.", style="bright_white")
    body.append("\n\n")
    body.append("Run ", style="bright_white")
    body.append("uv run nova", style="bold bright_white")
    body.append(" to start a session.", style="bright_white")

    panel = Panel(
        body,
        title="[bold cyan]N.O.V.A.[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    )
    console.print(panel)


def _handle_validate_only(path_arg: str, console: Console) -> int:
    """Validate ``path_arg`` without creating any filesystem state."""
    # Local import avoids dragging the paths module into the fast setup
    # path — no Story 2.4 concern, but keeps --validate-only's surface
    # minimal on import.
    from nova.core.paths import validate_data_dir  # noqa: PLC0415

    try:
        validate_data_dir(Path(path_arg))
    except ConfigError as err:
        console.print(f"[red]✗[/red] {err}")
        console.print("Setup stopped.")
        return EXIT_CONFIG_ERROR
    console.print("[green]✓[/green] Path is valid.")
    return EXIT_OK


def _resolve_data_dir() -> Path | None:
    """Resolve the user data directory from ``LOCALAPPDATA``."""
    localappdata = os.environ.get("LOCALAPPDATA")
    if not localappdata:
        return None
    return Path(localappdata) / "nova"


async def _probe_setup_complete(data_dir: Path) -> bool:
    """Story 2.4 AC #3 fast-path probe — did setup previously complete?

    The canonical completion marker is an ``audit_log`` row with
    ``action_type = 'setup_complete'``. We open the shared
    :class:`SqliteStorageEngine`, run one ``SELECT 1`` against the
    audit table, close. Any engine-level or SQL-level failure is
    treated as "cannot prove complete — proceed with setup" — setup
    is the recovery path for a corrupt or half-baked DB.

    Notably this probe does NOT run migrations. A DB file that exists
    but has no ``audit_log`` table yet will raise an ``OperationalError``
    inside the engine → ``StorageError`` → we return ``False`` and the
    main flow runs migrations on its own ``create_app`` pass.
    """
    db_path = data_dir / "nova.db"
    if not db_path.exists():
        return False

    engine = SqliteStorageEngine(db_path)
    try:
        await engine.start()
    except StorageError:
        logger.warning("fast-path probe could not open nova.db", exc_info=True)
        return False
    try:
        try:
            row = await engine.fetchone(
                "SELECT 1 FROM audit_log WHERE action_type = ? LIMIT 1",
                (str(ActionType.SETUP_COMPLETE),),
            )
        except StorageError:
            logger.warning("fast-path probe query failed", exc_info=True)
            return False
        return row is not None
    finally:
        # A close-time StorageError (e.g., WAL checkpoint failure after a
        # successful SELECT) must NOT override the probe's primary return
        # value — the probe already decided the answer before the close.
        # Swallow and log so the next `create_app` pass sees a clean
        # filesystem handoff.
        try:
            await engine.close()
        except StorageError:
            logger.warning("fast-path probe close failed", exc_info=True)


async def _run_initial_capture_and_persist(console: Console, data_dir: Path) -> int:
    """Story 2.4 AC #10–#17 — capture, persist, render completion.

    Opens :func:`nova.app.create_app`, runs the best-effort capture,
    writes the first session + snapshot + audit inside one transaction,
    renders the completion panel, closes the composition root.

    Returns :data:`EXIT_OK` on success, :data:`EXIT_CONFIG_ERROR` when
    ``load_config`` surfaces a :class:`ConfigError` or when persistence
    surfaces a :class:`StorageError`. Error messages are product-grade
    — no traceback.

    Refuses to write the ``setup_complete`` marker when the wizard
    returned without producing any usable modes. The wizard's AC #11
    exit gate enforces this interactively, but non-interactive (no-TTY)
    and ``KeyboardInterrupt`` paths inside ``run_mode_wizard_step``
    return early BEFORE the gate — persisting then would trap the user
    behind the fast-path marker on every subsequent ``setup.bat`` rerun.
    """
    try:
        config = load_config(data_dir)
    except ConfigError as err:
        console.print(f"[red]✗[/red] {err}")
        console.print(
            "Setup stopped — configuration could not be loaded. Inspect "
            "[bold]%LOCALAPPDATA%/nova/[/bold] and re-run setup."
        )
        return EXIT_CONFIG_ERROR

    # Guard against the "wizard returned without creating any mode" path.
    # Story 2.3 AC #11 is enforced only on the interactive path; writing
    # setup_complete here with zero modes would make the fast-path probe
    # treat a broken setup as complete forever after.
    if not config.modes:
        console.print("[yellow]⚠[/yellow] Setup ended with no workspace modes configured.")
        console.print(
            "Run [bold]setup.bat[/bold] again and create at least one mode "
            "to finish first-run setup."
        )
        return EXIT_CONFIG_ERROR

    capture = capture_initial_workspace()
    render_capture_status(console, capture)

    app: NovaApp
    try:
        app = await create_app(config)
    except StorageError as err:
        console.print(f"[red]✗[/red] {err}")
        console.print("Setup stopped — could not initialize the workspace database.")
        return EXIT_CONFIG_ERROR

    persist_error: StorageError | None = None
    try:
        await persist_first_run(
            app,
            capture,
            api_key_configured=config.api_key is not None and config.api_key != "",
            modes_count=len(config.modes),
        )
    except StorageError as err:
        persist_error = err
    finally:
        # A close-time StorageError must NOT mask the primary persist
        # failure. Log and swallow so the original ``persist_error`` (or
        # a clean success) is what the caller sees.
        try:
            await app.close()
        except StorageError:
            logger.warning("composition-root close failed after persist", exc_info=True)

    if persist_error is not None:
        console.print(f"[red]✗[/red] {persist_error}")
        console.print("Setup stopped — could not record first-run completion.")
        return EXIT_CONFIG_ERROR

    render_completion_panel(console, config)
    return EXIT_OK


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
    """Sync entrypoint for ``python -m nova.setup``."""
    _force_utf8_stdout()
    parser = _build_parser()
    args = parser.parse_args(argv)
    console = Console()

    if args.validate_only is not None:
        return _handle_validate_only(args.validate_only, console)

    data_dir = _resolve_data_dir()

    # Fast path — skip State A + wizard if prior setup already completed.
    # Runs BEFORE State A so a user who already completed setup never
    # sees the first-run orientation again.
    if data_dir is not None and asyncio.run(_probe_setup_complete(data_dir)):
        _render_already_setup_panel(console)
        return EXIT_OK

    _render_state_a(console)

    if data_dir is None:
        console.print(
            "[yellow]\u26a0[/yellow] LOCALAPPDATA not set. Skipping API key and mode configuration."
        )
        return EXIT_OK

    run_api_key_step(console, data_dir)
    run_mode_wizard_step(console, data_dir)

    return asyncio.run(_run_initial_capture_and_persist(console, data_dir))


if __name__ == "__main__":
    raise SystemExit(main())
