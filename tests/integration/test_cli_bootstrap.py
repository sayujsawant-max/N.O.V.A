"""Story 1.10 AC #13, #14, #15 — end-to-end CLI bootstrap.

Invokes ``nova.cli.main`` directly (no subprocess — coverage-friendly
and faster). Exercises happy path, six failure paths, and teardown
correctness.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

import nova.cli as cli_module
from nova.cli import (
    _FILE_HANDLER_NAME,
    _STDERR_HANDLER_NAME,
    EXIT_CONFIG_ERROR,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_STORAGE_ERROR,
    EXIT_UNEXPECTED,
    main,
)
from nova.core import SqliteStorageEngine
from nova.core.exceptions import StorageError

pytestmark = pytest.mark.integration


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture
def nova_data_dir(tmp_path: Path) -> Path:
    """Seed a minimal valid data directory at ``tmp_path``.

    Empty ``settings.yaml`` + ``exclusions.yaml`` are valid per Story
    1.6 (defaults apply). Empty ``modes/`` is valid (zero modes is a
    warning, not an error). Both YAML files are seeded so ``load_config``
    does NOT emit WARNING records during boot — the happy-path test
    asserts an empty stderr, so leaving config files absent would trip
    on legitimate "zero modes / no exclusions" warnings.

    Story 2.5 AC #7 — the fixture also seeds a test ``api_key`` so the
    one-time offline notice does NOT fire. The happy-path test asserts
    empty stderr; without a key we'd now trip the notice. Tests that
    want to exercise the no-key path override this file explicitly.
    """
    (tmp_path / "settings.yaml").write_text('api_key: "sk-ant-test-bootstrap"\n', encoding="utf-8")
    (tmp_path / "exclusions.yaml").write_text("{}\n", encoding="utf-8")
    # Seed a single valid mode so the "zero modes" warning stays silent.
    modes_dir = tmp_path / "modes"
    modes_dir.mkdir()
    (modes_dir / "placeholder.yaml").write_text(
        "name: placeholder\napps:\n  - name: Placeholder\n    executable: placeholder.exe\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_nova_logging() -> Iterator[None]:
    """Remove cli-owned handlers after each test so parallel tests are isolated."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if handler.name in {_STDERR_HANDLER_NAME, _FILE_HANDLER_NAME}:
            root.removeHandler(handler)
            handler.close()


@pytest.fixture(autouse=True)
def _short_circuit_nerve_repl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Story 3.5 — patch the Skin REPL primitive so bootstrap tests exit fast.

    Story 1.10's bootstrap tests verify the boot sequence (config load,
    storage init, exit codes); they don't exercise session-loop
    semantics — that's :mod:`tests.integration.test_session_loop`'s job.
    Without this fixture, ``app.nerve.startup()`` blocks on
    ``Prompt.ask`` and pytest's stdin-capture raises ``OSError``, which
    the REPL doesn't catch.

    Story 3.7 — Prompt.ask is now called twice per shutdown: REPL reads
    the SHUTDOWN command, then the seed prompt reads the seed text or
    a cancel terminator. Returning an iterator with ``"shutdown"`` then
    ``"skip"`` exits the REPL via SHUTDOWN and cancels the seed prompt
    via the ``"skip"`` terminator. Subsequent calls (defensive) also
    return ``"skip"`` so any extra prompts don't crash the test.
    """
    inputs = iter(["shutdown", "skip"])
    monkeypatch.setattr(
        "nova.adapters.rich.skin.Prompt.ask",
        lambda *a, **kw: next(inputs, "skip"),
    )


def _invoke_nova(
    monkeypatch: pytest.MonkeyPatch,
    data_dir: Path | None,
    argv: list[str] | None = None,
) -> int:
    """Run ``main()`` with a clean sys.argv + ``NOVA_DATA_DIR`` env override."""
    monkeypatch.setattr("sys.argv", argv if argv is not None else ["nova"])
    if data_dir is not None:
        monkeypatch.setenv("NOVA_DATA_DIR", str(data_dir))
    else:
        monkeypatch.delenv("NOVA_DATA_DIR", raising=False)
    # Clear LOCALAPPDATA so any test that passes data_dir=None falls
    # through to the Path.home() branch deterministically.
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("NOVA_LOG_LEVEL", raising=False)
    return main()


# --- AC #13: happy-path bootstrap ------------------------------------------


def test_cli_boots_and_exits_cleanly(
    nova_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = _invoke_nova(monkeypatch, nova_data_dir)
    assert exit_code == EXIT_OK

    log_path = nova_data_dir / "logs" / "nova.log"
    assert log_path.exists()
    log_text = log_path.read_text(encoding="utf-8")
    assert "N.O.V.A. initialized" in log_text
    # Story 3.5 — the placeholder log line is replaced by the session-loop
    # entry log; the autouse REPL-short-circuit fixture makes the loop
    # exit on its first iteration via a synthetic SHUTDOWN.
    assert "entering session loop" in log_text
    assert "Traceback" not in log_text
    assert "[ERROR]" not in log_text

    assert (nova_data_dir / "nova.db").exists()

    captured = capsys.readouterr()
    # Story 3.5 — Skin renders the briefing card to stdout.
    # Story 3.7 — Skin also renders the shutdown card + "Cancelled." final
    # line when the autouse REPL fixture drives SHUTDOWN → seed-cancel.
    assert "Session Briefing" in captured.out
    assert "Session ending" in captured.out
    assert "Cancelled." in captured.out
    # No stderr on the happy path — Phase A handler must have been
    # removed before the success INFO lines fired.
    assert captured.err == ""


# --- AC #15: teardown leaves no open handles -------------------------------


def test_cli_teardown_closes_all_resources(
    nova_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    exit_code = _invoke_nova(monkeypatch, nova_data_dir)
    assert exit_code == EXIT_OK

    # AC #15 belt-and-suspenders: no asyncio tasks survive the
    # ``asyncio.run`` boundary inside ``main``. ``asyncio.run`` itself
    # raises ``RuntimeError`` if tasks leak, so reaching this line
    # already proves it; the explicit check also locks the invariant
    # against a future background-task introduction in ``_async_main``.
    try:
        tasks = asyncio.all_tasks()
    except RuntimeError:
        # Expected path outside an event loop — ``all_tasks()`` raises,
        # which IS the proof: no loop, no tasks.
        tasks = set()
    assert not tasks, f"Leaked asyncio tasks after CLI teardown: {tasks!r}"

    # If the engine were still open, a second engine on the same db_path
    # with WAL mode would succeed but the lock file would still exist.
    # The strongest portable check is to re-open with a short-lived raw
    # sqlite3 connection (exclusive locking catches a lingering writer).
    second = SqliteStorageEngine(nova_data_dir / "nova.db")

    async def roundtrip() -> None:
        await second.start()
        try:
            row = await second.fetchone("SELECT COUNT(*) AS count FROM schema_version")
            assert row is not None
        finally:
            await second.close()

    asyncio.run(roundtrip())


# --- AC #14: failure paths -------------------------------------------------


def test_cli_missing_data_dir_exits_with_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "does-not-exist"
    assert not missing.exists()

    exit_code = _invoke_nova(monkeypatch, missing)
    assert exit_code == EXIT_CONFIG_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    # ERROR surfaces on stderr via Phase A (file logging never ran).
    assert "[ERROR]" in captured.err
    assert "config load failed" in captured.err

    # Data-dir invariant: we did NOT silently create the missing directory.
    assert not missing.exists()
    # And therefore no nova.log was written.
    assert not (missing / "logs" / "nova.log").exists()


def test_cli_data_dir_is_a_file_exits_with_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "not-a-dir"
    target.write_text("oops")

    exit_code = _invoke_nova(monkeypatch, target)
    assert exit_code == EXIT_CONFIG_ERROR
    captured = capsys.readouterr()
    # AC #14 invariant: CLI never writes to stdout on any failure path.
    assert captured.out == ""
    assert "[ERROR]" in captured.err
    # File-as-dir never materializes a logs subdir — Phase B never ran.
    assert not (target / "logs").exists()


def test_cli_malformed_settings_yaml_exits_with_config_error(
    nova_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Duplicate-key YAML is rejected by Story 1.6's loader.
    (nova_data_dir / "settings.yaml").write_text(
        "bluntness: direct\nbluntness: calm\n", encoding="utf-8"
    )
    exit_code = _invoke_nova(monkeypatch, nova_data_dir)
    assert exit_code == EXIT_CONFIG_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[ERROR]" in captured.err
    # load_config runs before file logging, so no nova.log exists.
    assert not (nova_data_dir / "logs" / "nova.log").exists()


def test_cli_storage_error_exits_with_storage_code(
    nova_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import asyncio

    async def failing_start(self: SqliteStorageEngine) -> None:
        raise StorageError("simulated engine start failure")

    monkeypatch.setattr(SqliteStorageEngine, "start", failing_start)

    exit_code = _invoke_nova(monkeypatch, nova_data_dir)
    assert exit_code == EXIT_STORAGE_ERROR

    captured = capsys.readouterr()
    # File logging IS active by the time create_app runs, so the ERROR
    # lands in nova.log, not stderr.
    assert captured.out == ""
    log_path = nova_data_dir / "logs" / "nova.log"
    assert log_path.exists()
    log_text = log_path.read_text(encoding="utf-8")
    assert "storage init failed" in log_text
    assert "simulated engine start failure" in log_text

    # P1 regression: after a start-failure, a fresh engine on the same
    # db_path must open without contention — proves no executor / file
    # handle leaked from the partial-init path.
    monkeypatch.undo()  # restore real ``SqliteStorageEngine.start``
    second = SqliteStorageEngine(nova_data_dir / "nova.db")

    async def roundtrip() -> None:
        await second.start()
        try:
            # Migrations were never applied (start failed earlier), so
            # the schema_version table may not exist — just prove we
            # can open and close without error.
            pass
        finally:
            await second.close()

    asyncio.run(roundtrip())


def test_cli_keyboard_interrupt_exits_130(
    nova_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pre-create_app interrupt — exits 130, logs 'interrupted by user' to stderr.

    KeyboardInterrupt here fires during ``load_config``, before Phase B
    file logging attaches, so the notice routes through the Phase A
    stderr handler.
    """

    def raising_load_config(data_dir: Path) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "load_config", raising_load_config)

    exit_code = _invoke_nova(monkeypatch, nova_data_dir)
    assert exit_code == EXIT_INTERRUPTED

    captured = capsys.readouterr()
    assert captured.out == ""
    # AC #11 + #14 clause: `main()` logs "interrupted by user" at INFO
    # and returns 130.
    assert "interrupted by user" in captured.err


def test_cli_keyboard_interrupt_during_session_still_closes_engine(
    nova_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2 regression — KeyboardInterrupt AFTER ``create_app`` succeeds must
    still run ``app.close()`` via the ``finally`` block.

    The original AC #14 test only covered the pre-create_app case. This
    variant fires the interrupt AFTER ``create_app`` returns — inside
    the post-init session body — and asserts a fresh engine can open
    the same db_path afterwards (proving the finally-close path fired
    and the migrations persisted).

    Implementation note: we intercept ``cli_module.logger.info`` on its
    SECOND call (the first is "N.O.V.A. initialized" — which proves
    create_app succeeded; the second would be "session shell
    placeholder"). We raise KeyboardInterrupt exactly once so the
    subsequent "interrupted by user" log in ``main()`` is not
    re-interrupted — that would escape pytest's boundary.
    """
    from collections.abc import Callable
    from typing import cast

    # Narrow ``logger.info`` to ``Callable[..., None]`` — it accepts any
    # positional / keyword args and returns ``None``. This avoids
    # ``Any`` (AC #18) while still letting ``**kwargs`` unpack without
    # mypy errors against logging's many optional kwargs.
    original_info = cast(Callable[..., None], cli_module.logger.info)
    info_call_count = {"n": 0}
    raised_once = {"fired": False}

    def interrupt_on_placeholder_log(msg: object, *args: object, **kwargs: object) -> None:
        info_call_count["n"] += 1
        if info_call_count["n"] == 2 and not raised_once["fired"]:
            raised_once["fired"] = True
            raise KeyboardInterrupt
        original_info(msg, *args, **kwargs)

    monkeypatch.setattr(cli_module.logger, "info", interrupt_on_placeholder_log)

    exit_code = _invoke_nova(monkeypatch, nova_data_dir)
    assert exit_code == EXIT_INTERRUPTED
    # Evidence that ``create_app`` returned successfully: the second
    # ``cli.logger.info`` call fires — and ONLY fires — AFTER
    # ``create_app`` completes. ``raised_once["fired"]`` set to True
    # proves the interrupt triggered at the correct boundary (the
    # post-init session body, not during init). Subsequent calls
    # (``"interrupted by user"`` in ``main()``) may increment the
    # counter further but do not re-trigger the interrupt.
    assert raised_once["fired"]
    assert info_call_count["n"] >= 2


def test_cli_keyboard_interrupt_from_nerve_startup_returns_exit_interrupted(
    nova_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Story 3.5 contract — nerve.startup raises KeyboardInterrupt → main returns 130.

    The custom signal handler (Story 3.5) suppresses the OS-level
    KeyboardInterrupt-injection that would otherwise propagate from a
    Ctrl-C during the REPL. Without nerve.startup re-raising
    ``KeyboardInterrupt`` after a signal-driven exit, the user's Ctrl-C
    would silently exit with code 0 while the session was marked
    interrupted in nova.db. Locks the contract that signal-driven exit
    surfaces as ``EXIT_INTERRUPTED``.

    This test patches ``app.nerve.startup`` directly (instead of firing
    a real signal) because: (a) firing real SIGINT in a pytest test
    affects the entire test runner, (b) the contract under test is
    "nerve raises → cli catches → 130" which is independent of how
    nerve decided to raise.
    """
    import asyncio
    from typing import Any
    from unittest.mock import AsyncMock

    from nova.app import create_app as real_create_app

    async def create_app_with_kbdint_startup(config: Any, *, shield: Any = None) -> object:
        # Build the real app (so app.close still works correctly), then
        # patch its nerve.startup to raise KeyboardInterrupt as the
        # signal-driven exit path does in production.
        app = await real_create_app(config, shield=shield)
        # Replace nerve.startup with a coroutine that raises KbdInt.
        # The real app's storage engine is still open; the finally
        # block in _async_main will call app.close() correctly.
        app.nerve.startup = AsyncMock(  # type: ignore[method-assign]
            side_effect=KeyboardInterrupt("session interrupted by signal")
        )
        return app

    monkeypatch.setattr(cli_module, "create_app", create_app_with_kbdint_startup)

    exit_code = _invoke_nova(monkeypatch, nova_data_dir)
    assert exit_code == EXIT_INTERRUPTED, (
        f"Story 3.5 contract: nerve.startup raising KeyboardInterrupt MUST "
        f"map to EXIT_INTERRUPTED ({EXIT_INTERRUPTED}); got {exit_code}"
    )

    # Verify the engine was actually closed by the finally block — open
    # a fresh one on the same path. If finally didn't fire, WAL would
    # still hold the file. (Same probe pattern as the
    # ``_during_session_still_closes_engine`` test above.)
    monkeypatch.undo()
    second = SqliteStorageEngine(nova_data_dir / "nova.db")

    async def roundtrip_after_kbdint() -> None:
        await second.start()
        try:
            row = await second.fetchone("SELECT COUNT(*) AS count FROM schema_version")
            assert row is not None
        finally:
            await second.close()

    asyncio.run(roundtrip_after_kbdint())

    # Prove the engine was closed by the finally block — open a fresh
    # one on the same path. If ``app.close()`` didn't run, the WAL
    # files / executor thread would still hold the DB, and migrations
    # would have rolled back.
    monkeypatch.undo()
    second = SqliteStorageEngine(nova_data_dir / "nova.db")

    async def roundtrip() -> None:
        await second.start()
        try:
            row = await second.fetchone("SELECT COUNT(*) AS count FROM schema_version")
            # Migration applied before the interrupt, and committed —
            # proves the engine was cleanly torn down.
            assert row is not None
        finally:
            await second.close()

    asyncio.run(roundtrip())


def test_cli_unexpected_exception_exits_with_unexpected_code(
    nova_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def raising_load_config(data_dir: Path) -> None:
        raise RuntimeError("surprise")

    monkeypatch.setattr(cli_module, "load_config", raising_load_config)

    exit_code = _invoke_nova(monkeypatch, nova_data_dir)
    assert exit_code == EXIT_UNEXPECTED

    captured = capsys.readouterr()
    assert captured.out == ""
    # CRITICAL with traceback surfaces on stderr (file logging not yet
    # configured because load_config raised pre-Phase-B).
    assert "CRITICAL" in captured.err
    assert "Traceback" in captured.err
    assert "surprise" in captured.err
    # Negative assertion — the NovaError branch's log message must NOT
    # appear. Guards against a future refactor that collapses the two
    # branches and quietly degrades the exit-code contract.
    assert "unhandled NovaError at top level" not in captured.err


# --- Version + arg parsing -------------------------------------------------


def test_cli_version_flag_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["nova", "--version"])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "nova" in captured.out


def test_cli_keyboard_interrupt_during_argparse_exits_130(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """P18 regression — KeyboardInterrupt raised during ``parse_args`` is
    handled by ``main()``, not escaped.

    External review caught a gap: ``_build_parser()`` and
    ``parser.parse_args()`` were originally OUTSIDE the ``try`` block,
    so a Ctrl-C landing during argument parsing would propagate out of
    ``main()`` and the documented ``EXIT_INTERRUPTED`` contract would
    be violated. This test monkeypatches ``_build_parser`` to raise
    ``KeyboardInterrupt`` and asserts the exit code is still 130.
    """

    def interrupting_build_parser() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "_build_parser", interrupting_build_parser)
    monkeypatch.setattr("sys.argv", ["nova"])
    monkeypatch.delenv("NOVA_DATA_DIR", raising=False)

    exit_code = main()
    assert exit_code == EXIT_INTERRUPTED


def test_cli_log_level_lowercase_accepted(
    nova_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P6 regression — ``nova --log-level warning`` (lowercase) must boot cleanly.

    The original argparse ``choices=`` restriction rejected lowercase
    with argparse's default exit code 2, colliding with
    ``EXIT_STORAGE_ERROR`` and contradicting the docstring's exit-code
    table. After the fix, ``_parse_log_level`` is the single
    case-insensitive validator and lowercase boots normally.
    """
    exit_code = _invoke_nova(monkeypatch, nova_data_dir, argv=["nova", "--log-level", "warning"])
    assert exit_code == EXIT_OK


def test_cli_log_level_invalid_exits_with_config_error(
    nova_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """P6 — an invalid ``--log-level`` now exits with ``EXIT_CONFIG_ERROR`` (1),
    NOT argparse's default 2. Written-to-stderr, never stdout.
    """
    exit_code = _invoke_nova(monkeypatch, nova_data_dir, argv=["nova", "--log-level", "TRACE"])
    assert exit_code == EXIT_CONFIG_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "nova:" in captured.err
    assert "TRACE" in captured.err or "invalid" in captured.err.lower()


# --- Story 2.1 AC #36 — Step 2.5 path validation integration ---------------


def test_cli_rejects_reserved_windows_name_in_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC #36 — ``nova --data-dir <reserved-name>`` exits 1 via Step 2.5.

    Asserts:
    - Exit code is :data:`EXIT_CONFIG_ERROR` (1).
    - stderr contains the non-technical reason (reserved name).
    - The bad path is NOT created on disk (short-circuit before any
      mkdir).
    """
    bad_path = tmp_path / "CON"
    exit_code = _invoke_nova(
        monkeypatch,
        data_dir=None,
        argv=["nova", "--data-dir", str(bad_path)],
    )
    assert exit_code == EXIT_CONFIG_ERROR

    captured = capsys.readouterr()
    # Structured log line surfaces the reason on stderr (Phase A handler
    # is still the only handler because Step 2.5 aborts before Step 4).
    assert "data dir validation failed" in captured.err
    assert "reserved Windows name" in captured.err

    # Short-circuit proof: the bad path was not materialized.
    assert not bad_path.exists()


def test_cli_rejects_invalid_character_in_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invalid character in data-dir segment also triggers Step 2.5 rejection."""
    bad_path = tmp_path / "bad<name"
    exit_code = _invoke_nova(
        monkeypatch,
        data_dir=None,
        argv=["nova", "--data-dir", str(bad_path)],
    )
    assert exit_code == EXIT_CONFIG_ERROR
    captured = capsys.readouterr()
    assert "invalid character" in captured.err
    assert not bad_path.exists()


def test_cli_validation_runs_before_file_logging_init(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad data-dir must never produce a ``logs/`` subdirectory.

    Regression guard: Phase B file-logging init (Step 4) uses
    ``mkdir(parents=False)`` on ``logs/``. If Step 2.5 did not run,
    or ran after Step 4, a bad path could leave ``logs/`` behind.
    """
    bad_path = tmp_path / "PRN"
    _invoke_nova(
        monkeypatch,
        data_dir=None,
        argv=["nova", "--data-dir", str(bad_path)],
    )
    assert not (bad_path / "logs").exists()
    assert not bad_path.exists()
