"""Terminal entrypoint — argument parsing, logging setup, session bootstrap.

``main()`` is the synchronous entrypoint registered as the ``nova``
console script in ``pyproject.toml``. It wraps a single ``asyncio.run``
call so the whole process shares one event loop.

Exit codes
----------
- ``0`` — success / clean placeholder exit
- ``1`` — ``ConfigError`` (missing / malformed / invalid-type data dir,
  bad YAML, unknown ``--log-level`` value, ...)
- ``2`` — ``StorageError`` (engine start / migrations failed)
- ``3`` — any other :class:`nova.core.NovaError` subclass
- ``4`` — unexpected :class:`Exception` (full traceback logged at
  CRITICAL)
- ``130`` — ``KeyboardInterrupt`` (POSIX 128 + SIGINT=2)

Environment variables
---------------------
- ``NOVA_DATA_DIR`` — override the user data directory. Takes precedence
  over ``LOCALAPPDATA``.
- ``NOVA_LOG_LEVEL`` — one of ``DEBUG`` / ``INFO`` / ``WARNING`` /
  ``ERROR`` (case-insensitive). Overridden by the ``--log-level`` flag.
- ``LOCALAPPDATA`` — standard Windows user data root; joined with
  ``"nova"`` to resolve the data directory in production.

CLI flags
---------
- ``--data-dir PATH`` — explicit override of the data directory.
- ``--log-level LEVEL`` — explicit override of the log level.
- ``--version`` — print the installed version and exit 0.

Two-phase logging
-----------------
Logging init is split into two phases because the data directory does
not exist during early startup (``setup.bat`` owns its creation):

1. **Phase A** (:func:`_configure_stderr_logging`) attaches a single
   tagged ``StreamHandler(sys.stderr)`` BEFORE any path check. This is
   the one deliberate exception to the "no terminal logging" rule — it
   exists only as a pre-data-dir channel so early failures
   (``ConfigError``, bad ``--log-level``, missing data dir) reach the
   user.
2. **Phase B** (:func:`_configure_file_logging`) attaches the
   ``FileHandler`` at ``<data_dir>/logs/nova.log`` AFTER
   :func:`nova.core.load_config` succeeds (which guarantees the data
   dir exists). Phase B removes the Phase A handler so steady-state
   logging goes only to file.

This two-phase split keeps two invariants simultaneously:

- ``cli.py`` never silently materializes the data directory
  (``_configure_file_logging`` uses ``mkdir(parents=False)`` for
  ``logs/``).
- Every early failure still reaches the user — through stderr, not
  silence.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from nova import __version__
from nova.app import create_app
from nova.core import NovaError, load_config
from nova.core.exceptions import ConfigError, StorageError
from nova.core.paths import validate_data_dir

logger = logging.getLogger("nova.cli")


# --- Exit codes -------------------------------------------------------------

EXIT_OK: Final[int] = 0
EXIT_CONFIG_ERROR: Final[int] = 1
EXIT_STORAGE_ERROR: Final[int] = 2
EXIT_NOVA_ERROR: Final[int] = 3
EXIT_UNEXPECTED: Final[int] = 4
EXIT_INTERRUPTED: Final[int] = 130


# --- Handler tags -----------------------------------------------------------

_STDERR_HANDLER_NAME: Final[str] = "nova-cli-stderr-bootstrap"
_FILE_HANDLER_NAME: Final[str] = "nova-cli-file-handler"


# --- Log level parsing ------------------------------------------------------

_VALID_LOG_LEVELS: Final[Mapping[str, int]] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


# Story 2.5 AC #11 — ``nova --help`` epilog documenting the post-setup
# key-update path. Uses ``argparse.RawDescriptionHelpFormatter`` on the
# parser so indentation and line breaks survive verbatim. Text uses the
# literal ``%LOCALAPPDATA%`` env-var form (not a resolved path) so the
# help output does not leak the username.
_HELP_EPILOG: Final[str] = (
    "API key:\n"
    "  To add or update your Anthropic API key, edit:\n"
    "      %LOCALAPPDATA%/nova/settings.yaml\n"
    "  Change the `api_key:` line, save, and re-run `nova`.\n"
    "  Removing the line starts N.O.V.A. in offline-local-only tier.\n"
)


def _parse_log_level(cli_raw: str | None, env_raw: str | None) -> int:
    """Resolve the log level. CLI flag > env var > default (INFO).

    Raises :class:`ConfigError` on an invalid literal so the top-level
    handler maps to exit code 1 uniformly.
    """
    for source, raw in (("--log-level", cli_raw), ("NOVA_LOG_LEVEL", env_raw)):
        if raw is None or raw.strip() == "":
            continue
        upper = raw.strip().upper()
        if upper not in _VALID_LOG_LEVELS:
            raise ConfigError(
                f"invalid log level from {source}: {raw!r} "
                f"(expected one of {sorted(_VALID_LOG_LEVELS)})"
            )
        return _VALID_LOG_LEVELS[upper]
    return logging.INFO


# --- Data directory resolution ----------------------------------------------


def _resolve_data_dir(cli_override: Path | None, env: Mapping[str, str]) -> Path:
    """Return the user data directory per precedence order.

    Precedence: CLI flag > ``NOVA_DATA_DIR`` > ``LOCALAPPDATA`` > home.
    Does NOT create the directory. Callers (``load_config``, the Phase B
    file-logging initializer) either fail loud on a missing path or
    create only their own subdirectory.
    """
    if cli_override is not None:
        return Path(cli_override).expanduser().resolve()
    nova_data_dir = env.get("NOVA_DATA_DIR", "").strip()
    if nova_data_dir:
        return Path(nova_data_dir).expanduser().resolve()
    local_app_data = env.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return (Path(local_app_data) / "nova").expanduser().resolve()
    return (Path.home() / ".nova").expanduser().resolve()


# --- Formatter --------------------------------------------------------------

_LOG_RECORD_RESERVED_KEYS: Final[frozenset[str]] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class _ExtrasFormatter(logging.Formatter):
    """Formatter that appends ``| extras={k=v, ...}`` for structured ``extra`` fields.

    Filters out :class:`logging.LogRecord` reserved attribute names so
    stdlib metadata (``name``, ``module``, etc.) is never double-logged.
    """

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _LOG_RECORD_RESERVED_KEYS and not key.startswith("_")
        }
        if not extras:
            return base
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(extras.items()))
        return f"{base} | extras={{{rendered}}}"


_FORMAT: Final[str] = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT: Final[str] = "%Y-%m-%dT%H:%M:%S%z"


def _build_formatter() -> _ExtrasFormatter:
    return _ExtrasFormatter(fmt=_FORMAT, datefmt=_DATEFMT)


# --- Logging phases ---------------------------------------------------------


def _remove_handlers_by_name(name: str) -> None:
    """Remove every handler on the root logger tagged with ``name``.

    Tagging via ``handler.name`` lets us identify our own handlers
    deterministically without touching handlers owned by pytest /
    coverage / the user.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if handler.name == name:
            root.removeHandler(handler)
            handler.close()


def _configure_stderr_logging(level: int) -> None:
    """Attach a single tagged stderr handler. Idempotent.

    Phase A of the two-phase logging init — runs before any path check
    so early failures reach the user even when the data directory does
    not exist.
    """
    _remove_handlers_by_name(_STDERR_HANDLER_NAME)
    handler = logging.StreamHandler(sys.stderr)
    handler.name = _STDERR_HANDLER_NAME
    handler.setFormatter(_build_formatter())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level)


def _emit_offline_notice_once(api_key: str | None) -> None:
    """Story 2.5 AC #7, #8, #9, #10 — one-time offline notice on cli startup.

    Emits a single amber stderr line when ``api_key is None`` so the user
    knows they booted without cloud reasoning. Silent when the key is
    present. Called at Step 6.5 of the ``_async_main`` bootstrap (after
    the ``"N.O.V.A. initialized"`` success log, before the session
    placeholder log) — see the module docstring for the full sequence.

    Why direct ``sys.stderr.write`` instead of ``logger.warning``:
    by Step 6.5 the Phase A stderr handler has been torn down
    (:func:`_configure_file_logging` removes it). A ``logger.warning``
    call would reach only the file logger, so the user wouldn't see it
    at the terminal. The direct-write approach matches the
    pre-logger ``ConfigError`` surface in :func:`main` at the top of the
    module, keeps the two-phase logger state invariant unchanged, and
    lets Skin re-home this notice cleanly when it arrives (Story 3.3 /
    Story 5.4).

    Opacity: the notice text is fully static — it never interpolates
    the caller's ``api_key`` value, never echoes a redacted form, never
    embeds a resolved user path (``%LOCALAPPDATA%`` stays in its
    env-var form so we don't leak ``C:\\Users\\<username>\\...``).

    Failure handling (review patch): the notice is best-effort. On a
    Windows console running cp1252/cp437 without ``PYTHONIOENCODING=utf-8``
    the ``\\u26a0`` glyph triggers ``UnicodeEncodeError``; a detached or
    closed stderr raises ``BrokenPipeError`` / ``ValueError``. None of
    those should promote ``EXIT_OK`` to ``EXIT_UNEXPECTED`` — bootstrap
    already succeeded by the time this helper runs. The write is wrapped
    in a narrow exception handler that logs to Phase B's file logger
    (``nova.log``) so the operator still sees something, and the caller
    returns normally.
    """
    if api_key is not None:
        return
    notice = (
        "\u26a0 Cloud reasoning unavailable. Running in "
        "offline-local-only tier. To add or update your API key, "
        "edit %LOCALAPPDATA%/nova/settings.yaml and re-run nova.\n"
    )
    try:
        sys.stderr.write(notice)
        sys.stderr.flush()
    except (UnicodeEncodeError, OSError, ValueError):
        # OSError covers BrokenPipeError (stderr piped to a dead reader)
        # and generic write failures; ValueError covers a closed stream
        # (``I/O operation on closed file``). The file logger is still
        # attached at Step 6.5 so the fallback gets persisted — operator
        # forensics survive even when the terminal can't render the glyph.
        logger.warning(
            "offline notice could not be written to stderr — falling back to file log",
            exc_info=True,
        )


def _configure_file_logging(data_dir: Path, level: int) -> None:
    """Attach the file handler and remove the Phase A stderr handler.

    Uses ``mkdir(parents=False, exist_ok=True)`` for ``<data_dir>/logs/``
    so a missing ``data_dir`` fails loud with :class:`FileNotFoundError`
    rather than silently materializing the user data root
    (``setup.bat``'s exclusive responsibility).
    """
    logs_dir = data_dir / "logs"
    # ``parents=False`` is the default, but we spell it explicitly because
    # the invariant is load-bearing: we only create ``logs/`` — never the
    # enclosing ``data_dir`` (``setup.bat`` owns that creation). A missing
    # ``data_dir`` must surface as ``FileNotFoundError``, not be silently
    # materialized.
    if logs_dir.exists() and not logs_dir.is_dir():
        # ``mkdir(exist_ok=True)`` on an existing FILE raises
        # ``FileExistsError`` with an OS-dependent message; pre-check here
        # so callers get a clean, specific failure.
        raise NotADirectoryError(f"{logs_dir} exists and is not a directory")
    logs_dir.mkdir(parents=False, exist_ok=True)
    log_path = logs_dir / "nova.log"

    _remove_handlers_by_name(_FILE_HANDLER_NAME)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.name = _FILE_HANDLER_NAME
    handler.setFormatter(_build_formatter())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level)

    _remove_handlers_by_name(_STDERR_HANDLER_NAME)


# --- Argument parser --------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nova",
        description="N.O.V.A. — local-first Windows workspace assistant.",
        # Story 2.5 AC #11 — RawDescriptionHelpFormatter preserves the
        # indentation and line breaks in ``_HELP_EPILOG``. The default
        # ``HelpFormatter`` rewraps the epilog into a single block,
        # which collapses the two-space indent under "API key:" and
        # renders the file-path line unreadable at 80 columns.
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_HELP_EPILOG,
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override the user data directory (default: $NOVA_DATA_DIR or %%LOCALAPPDATA%%/nova).",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        # No ``choices=`` — validation lives in ``_parse_log_level`` so the
        # case-insensitive behavior is honored end-to-end. Using argparse's
        # ``choices`` would reject lowercase at parse time with exit code 2,
        # contradicting the documented ``EXIT_CONFIG_ERROR`` contract.
        help=(
            f"Log level (default: $NOVA_LOG_LEVEL or INFO). "
            f"One of: {', '.join(sorted(_VALID_LOG_LEVELS))} (case-insensitive)."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"nova {__version__}",
    )
    return parser


# --- Async main -------------------------------------------------------------


async def _async_main(args: argparse.Namespace) -> int:
    """Per AC #10: 8-step ordered bootstrap.

    Ordering is load-bearing — see the module docstring's "Two-phase
    logging" section.
    """
    # Step 1: Phase A stderr logging (runs before anything that can fail
    # user-visibly).
    try:
        level = _parse_log_level(args.log_level, os.environ.get("NOVA_LOG_LEVEL"))
    except ConfigError as err:
        # Logging is not yet configured; write directly to stderr.
        sys.stderr.write(f"nova: {err}\n")
        return EXIT_CONFIG_ERROR
    _configure_stderr_logging(level)

    # Step 2: resolve data dir (pure path math; no exceptions expected).
    data_dir = _resolve_data_dir(args.data_dir, os.environ)

    # Step 2.5: validate data_dir. Rejects reserved Windows names,
    # invalid characters, trailing dots/spaces, and over-long paths.
    # Runs BEFORE any directory creation (Phase B logging at Step 4)
    # and BEFORE engine start (``create_app`` at Step 5). Closes the
    # Windows-path validation item deferred from Story 1.10.
    try:
        validate_data_dir(data_dir)
    except ConfigError as err:
        logger.error("data dir validation failed", extra={"reason": str(err)})
        return EXIT_CONFIG_ERROR

    # Step 3: load config. Missing / malformed data dir surfaces here.
    try:
        config = load_config(data_dir)
    except ConfigError as err:
        logger.error("config load failed", extra={"data_dir": str(data_dir), "reason": str(err)})
        return EXIT_CONFIG_ERROR

    # Step 4: Phase B file logging. Data dir is known to exist now.
    try:
        _configure_file_logging(data_dir, level)
    except OSError as err:
        logger.error(
            "file logging init failed", extra={"data_dir": str(data_dir), "reason": str(err)}
        )
        return EXIT_CONFIG_ERROR

    # Step 5: create the app graph.
    try:
        app = await create_app(config)
    except StorageError as err:
        logger.error("storage init failed", extra={"reason": str(err)})
        return EXIT_STORAGE_ERROR

    try:
        # Step 6: success log line — intentionally structured for later
        # observability consumers.
        logger.info(
            "N.O.V.A. initialized",
            extra={
                "data_dir": str(config.data_dir),
                "db_path": str(config.db_path),
                "mode_count": len(config.modes),
                "api_key_present": config.api_key is not None,
                "tier": str(app.tier_manager.tier),
            },
        )
        # Step 6.5 (Story 2.5 AC #7, #10): one-time offline notice. Fires
        # only when ``config.api_key is None``; silent otherwise. Must land
        # AFTER the success log (so the file log shows bootstrap succeeded
        # before the consequence is surfaced) and BEFORE the placeholder
        # log so a human scanning stderr sees the notice without having to
        # tail nova.log.
        _emit_offline_notice_once(config.api_key)
        # Step 7: enter the session loop (Story 3.5). Nerve.startup runs
        # the eleven-step boot path: prior-state aggregate read → state
        # determine → State A early-return / B-or-C briefing render →
        # session create → REPL → cleanup. Returns when the REPL exits
        # via SHUTDOWN command, signal handler, or EOF/KbdInt.
        logger.info("entering session loop")
        await app.nerve.startup()
        return EXIT_OK
    finally:
        # Step 8: teardown always runs if create_app succeeded.
        await app.close()


# --- Sync entrypoint --------------------------------------------------------


def main() -> int:
    """Sync entrypoint registered as ``nova`` in ``pyproject.toml``.

    Catches the top-level exception boundary per project-context.md:53.
    """
    # ``_build_parser`` and ``parse_args`` run inside the outer ``try``
    # so a Ctrl-C landing during argument parsing (or parser
    # construction) still maps to ``EXIT_INTERRUPTED`` — otherwise the
    # KeyboardInterrupt escapes the documented main() contract.
    try:
        parser = _build_parser()
        args = parser.parse_args()
        return asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        # Logging handler may or may not be installed depending on
        # where the interrupt landed; ``logger.info`` is safe either
        # way (falls through to the root logger's default handler-of-
        # last-resort if nothing is attached).
        logger.info("interrupted by user")
        return EXIT_INTERRUPTED
    except SystemExit:
        # ``parser.parse_args`` raises ``SystemExit`` on ``--help`` /
        # ``--version`` / bad CLI args. Propagate — these are user-
        # intended exits; converting them to ``EXIT_UNEXPECTED`` would
        # mask the documented argparse behavior.
        raise
    except NovaError as err:
        logger.error("unhandled NovaError at top level", extra={"reason": str(err)})
        return EXIT_NOVA_ERROR
    except Exception:
        # Log with full traceback for operator forensics; return the
        # unexpected-error exit code.
        logger.critical("unexpected exception at top level", exc_info=True)
        return EXIT_UNEXPECTED


if __name__ == "__main__":
    raise SystemExit(main())
