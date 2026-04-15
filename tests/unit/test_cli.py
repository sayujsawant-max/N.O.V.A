"""Story 1.10 AC #6, #7, #9 — ``nova.cli`` helper unit tests.

Story 2.1 extension: Step 2.5 validation wiring (``validate_data_dir``).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from nova.cli import (
    _FILE_HANDLER_NAME,
    _STDERR_HANDLER_NAME,
    EXIT_CONFIG_ERROR,
    _async_main,
    _build_formatter,
    _configure_file_logging,
    _configure_stderr_logging,
    _parse_log_level,
    _resolve_data_dir,
)
from nova.core.exceptions import ConfigError

# --- Fixture: tear down cli-installed handlers ------------------------------


@pytest.fixture(autouse=True)
def _cleanup_nova_cli_handlers() -> Iterator[None]:
    """Remove any handlers tagged by ``nova.cli`` after each test."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if handler.name in {_STDERR_HANDLER_NAME, _FILE_HANDLER_NAME}:
            root.removeHandler(handler)
            handler.close()


# --- _resolve_data_dir tests ------------------------------------------------


def test_resolve_data_dir_cli_override_wins(tmp_path: Path) -> None:
    result = _resolve_data_dir(
        cli_override=tmp_path / "cli-override",
        env={"NOVA_DATA_DIR": "/ignored/env", "LOCALAPPDATA": "/ignored/local"},
    )
    assert result == (tmp_path / "cli-override").resolve()


def test_resolve_data_dir_nova_data_dir_env(tmp_path: Path) -> None:
    target = tmp_path / "from-env"
    result = _resolve_data_dir(
        cli_override=None,
        env={"NOVA_DATA_DIR": str(target), "LOCALAPPDATA": "/ignored/local"},
    )
    assert result == target.resolve()


def test_resolve_data_dir_localappdata_fallback(tmp_path: Path) -> None:
    result = _resolve_data_dir(
        cli_override=None,
        env={"NOVA_DATA_DIR": "", "LOCALAPPDATA": str(tmp_path)},
    )
    assert result == (tmp_path / "nova").resolve()


def test_resolve_data_dir_home_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    result = _resolve_data_dir(cli_override=None, env={})
    assert result == (tmp_path / ".nova").resolve()


def test_resolve_data_dir_expands_user_and_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    """``~`` expansion uses the OS user-profile mechanism (USERPROFILE on Windows)."""
    # Pin USERPROFILE (Windows) and HOME (POSIX) to a known absolute path so
    # the expansion is deterministic regardless of platform.
    fake_home = Path("/fake-home").resolve()
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("HOME", str(fake_home))
    result = _resolve_data_dir(cli_override=Path("~/mydir"), env={})
    assert result.is_absolute()
    # The ``~`` must be gone from the resolved path.
    assert "~" not in str(result)
    assert result.name == "mydir"


def test_resolve_data_dir_does_not_mkdir(tmp_path: Path) -> None:
    """Resolver is pure path math — never touches the filesystem."""
    target = tmp_path / "never-created"
    assert not target.exists()
    _resolve_data_dir(cli_override=target, env={})
    assert not target.exists()


@pytest.mark.parametrize(
    "env_var",
    ["NOVA_DATA_DIR", "LOCALAPPDATA"],
)
def test_resolve_data_dir_treats_whitespace_env_as_empty(
    tmp_path: Path, env_var: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whitespace-only env values must fall through for BOTH env vars.

    Parametrized across `NOVA_DATA_DIR` and `LOCALAPPDATA` so a future
    regression that only strips one of them fails here.
    """
    # Pin HOME for the POSIX fallback branch (relevant to the LOCALAPPDATA case).
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "from-fallback"

    if env_var == "NOVA_DATA_DIR":
        # Whitespace NOVA_DATA_DIR, valid LOCALAPPDATA → falls through to LOCALAPPDATA.
        result = _resolve_data_dir(
            cli_override=None,
            env={"NOVA_DATA_DIR": "   ", "LOCALAPPDATA": str(target)},
        )
        assert result == (target / "nova").resolve()
    else:
        # Whitespace LOCALAPPDATA, no NOVA_DATA_DIR → falls through to home.
        result = _resolve_data_dir(
            cli_override=None,
            env={"LOCALAPPDATA": "   "},
        )
        assert result == (tmp_path / ".nova").resolve()


# --- _parse_log_level tests -------------------------------------------------


@pytest.mark.parametrize(
    ("cli_raw", "env_raw", "expected"),
    [
        (None, None, logging.INFO),
        (None, "", logging.INFO),
        (None, "DEBUG", logging.DEBUG),
        ("WARNING", None, logging.WARNING),
        ("WARNING", "DEBUG", logging.WARNING),  # CLI wins over env
        ("warning", None, logging.WARNING),  # case-insensitive
        (None, "error", logging.ERROR),
    ],
)
def test_parse_log_level_precedence(
    cli_raw: str | None, env_raw: str | None, expected: int
) -> None:
    assert _parse_log_level(cli_raw, env_raw) == expected


def test_parse_log_level_invalid_cli_raises_configerror() -> None:
    with pytest.raises(ConfigError, match="--log-level"):
        _parse_log_level("TRACE", None)


def test_parse_log_level_invalid_env_raises_configerror() -> None:
    with pytest.raises(ConfigError, match="NOVA_LOG_LEVEL"):
        _parse_log_level(None, "SPAM")


# --- _configure_stderr_logging tests ---------------------------------------


def _stderr_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if h.name == _STDERR_HANDLER_NAME]


def _file_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if h.name == _FILE_HANDLER_NAME]


def test_configure_stderr_logging_attaches_one_handler() -> None:
    _configure_stderr_logging(logging.INFO)
    handlers = _stderr_handlers()
    assert len(handlers) == 1
    assert isinstance(handlers[0], logging.StreamHandler)
    assert handlers[0].stream is sys.stderr


def test_configure_stderr_logging_is_idempotent() -> None:
    _configure_stderr_logging(logging.INFO)
    _configure_stderr_logging(logging.INFO)
    _configure_stderr_logging(logging.DEBUG)
    assert len(_stderr_handlers()) == 1


# --- _configure_file_logging tests -----------------------------------------


def test_configure_file_logging_creates_logs_subdir_only(tmp_path: Path) -> None:
    """``logs/`` is created; ``tmp_path`` itself is untouched."""
    assert tmp_path.exists()
    assert not (tmp_path / "logs").exists()
    _configure_file_logging(tmp_path, logging.INFO)
    assert (tmp_path / "logs").is_dir()
    assert tmp_path.is_dir()


def test_configure_file_logging_fails_loud_if_data_dir_missing(tmp_path: Path) -> None:
    """AC #9 invariant: a missing ``data_dir`` must NOT be silently materialized."""
    missing = tmp_path / "does-not-exist"
    assert not missing.exists()
    with pytest.raises(FileNotFoundError):
        _configure_file_logging(missing, logging.INFO)
    # The data dir must still be absent after the failure.
    assert not missing.exists()


def test_configure_file_logging_removes_stderr_bootstrap(tmp_path: Path) -> None:
    _configure_stderr_logging(logging.INFO)
    assert len(_stderr_handlers()) == 1
    _configure_file_logging(tmp_path, logging.INFO)
    assert len(_stderr_handlers()) == 0
    assert len(_file_handlers()) == 1


def test_configure_file_logging_is_idempotent(tmp_path: Path) -> None:
    _configure_file_logging(tmp_path, logging.INFO)
    _configure_file_logging(tmp_path, logging.INFO)
    _configure_file_logging(tmp_path, logging.DEBUG)
    assert len(_file_handlers()) == 1


def test_configure_file_logging_attaches_no_nova_stream_handler(tmp_path: Path) -> None:
    """Phase B must never leave a nova-tagged StreamHandler attached.

    The earlier `isinstance(h, StreamHandler) and not isinstance(h, FileHandler)`
    filter was tautological — the filter could never surface a nova
    handler because our Phase B only attaches a FileHandler. This test
    instead asserts directly: no handler on the root logger is tagged
    with ``_STDERR_HANDLER_NAME`` after Phase B. If Phase B's stderr
    removal ever silently regressed, this test would catch it.
    """
    _configure_stderr_logging(logging.INFO)
    # Pre-condition: stderr handler is attached.
    assert any(h.name == _STDERR_HANDLER_NAME for h in logging.getLogger().handlers)
    _configure_file_logging(tmp_path, logging.INFO)
    # Post-condition: no handler tagged as ours-for-stderr remains,
    # regardless of whether pytest / coverage attach their own handlers.
    nova_stderr_handlers = [
        h for h in logging.getLogger().handlers if h.name == _STDERR_HANDLER_NAME
    ]
    assert nova_stderr_handlers == []


# --- _ExtrasFormatter tests -------------------------------------------------


def _format_record(
    msg: str, extras: dict[str, object] | None = None, level: int = logging.INFO
) -> str:
    formatter = _build_formatter()
    record = logging.LogRecord(
        name="nova.test",
        level=level,
        pathname=__file__,
        lineno=10,
        msg=msg,
        args=(),
        exc_info=None,
    )
    if extras is not None:
        for key, value in extras.items():
            setattr(record, key, value)
    return formatter.format(record)


def test_extras_formatter_renders_extras() -> None:
    output = _format_record("session stored", extras={"session_id": 42})
    assert "session stored" in output
    assert "extras={session_id=42}" in output


def test_extras_formatter_renders_multiple_extras_sorted() -> None:
    output = _format_record("event", extras={"b": 2, "a": 1})
    assert "extras={a=1, b=2}" in output


def test_extras_formatter_strips_reserved_extras() -> None:
    """Attaching a reserved LogRecord name must NOT appear in the extras block."""
    output = _format_record("event", extras={"module": "fake"})
    assert "module=fake" not in output
    assert "| extras=" not in output


def test_extras_formatter_no_extras_produces_no_trailer() -> None:
    output = _format_record("event")
    assert "| extras=" not in output


def test_extras_formatter_skips_private_underscore_keys() -> None:
    """Implementation-detail ``_private`` keys are filtered out."""
    output = _format_record("event", extras={"_internal": "x", "visible": "y"})
    assert "_internal" not in output
    assert "visible=y" in output


# --- File handler integration: extras appear in the on-disk log ------------


def test_configure_file_logging_writes_extras_to_disk(tmp_path: Path) -> None:
    _configure_file_logging(tmp_path, logging.INFO)
    test_logger = logging.getLogger("nova.test")
    test_logger.info("stored", extra={"id": 7})
    # Close the handler so the file is flushed and released before we
    # read it — avoids Windows shared-read flakiness and proves the
    # record committed durably, not just buffered.
    root = logging.getLogger()
    for handler in list(root.handlers):
        if handler.name == _FILE_HANDLER_NAME:
            root.removeHandler(handler)
            handler.close()
    log_text = (tmp_path / "logs" / "nova.log").read_text(encoding="utf-8")
    assert "stored" in log_text
    assert "extras={id=7}" in log_text


def test_configure_file_logging_raises_if_logs_dir_is_a_file(tmp_path: Path) -> None:
    """P17 — a pre-existing ``logs/`` FILE must raise a clean error, not a cryptic one.

    Code-review finding: ``FileHandler(log_path, ...)`` on a non-
    directory ``logs/`` raises platform-specific errors
    (``[WinError 267]`` on Windows, ``NotADirectoryError`` on POSIX).
    We pre-check and raise a clear ``NotADirectoryError`` with the
    offending path so callers get an actionable message.
    """
    (tmp_path / "logs").write_text("not a dir", encoding="utf-8")
    with pytest.raises(NotADirectoryError, match="logs"):
        _configure_file_logging(tmp_path, logging.INFO)
    # Precondition preserved: the "file logs/" is untouched.
    assert (tmp_path / "logs").read_text(encoding="utf-8") == "not a dir"


# --- Step 2.5 validation wiring (Story 2.1) ---------------------------------


async def test_async_main_validate_data_dir_failure_short_circuits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #38 — a ``ConfigError`` from ``validate_data_dir`` short-circuits bootstrap.

    When Step 2.5 validation fails:
    - ``_async_main`` returns :data:`EXIT_CONFIG_ERROR` (1).
    - ``load_config`` is NOT called (Step 3 skipped).
    - ``_configure_file_logging`` is NOT called (Step 4 skipped).
    - ``create_app`` is NOT called (Step 5 skipped).

    The Phase A stderr handler remains attached — the caller's error
    log reaches the user even though Phase B never initialized.
    """
    monkeypatch.delenv("NOVA_LOG_LEVEL", raising=False)
    args = argparse.Namespace(data_dir=tmp_path, log_level="INFO")

    with (
        patch("nova.cli.validate_data_dir", side_effect=ConfigError("bad path")) as mock_validate,
        patch("nova.cli.load_config") as mock_load,
        patch("nova.cli._configure_file_logging") as mock_phase_b,
        patch("nova.cli.create_app", new_callable=AsyncMock) as mock_create_app,
    ):
        result = await _async_main(args)

    assert result == EXIT_CONFIG_ERROR
    mock_validate.assert_called_once_with(tmp_path.resolve())
    mock_load.assert_not_called()
    mock_phase_b.assert_not_called()
    mock_create_app.assert_not_called()


async def test_async_main_validate_data_dir_success_continues_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation success allows bootstrap to proceed to ``load_config``.

    Companion to the short-circuit test: ensures Step 2.5 is not a
    blocking wall — a valid path lets the flow continue.
    """
    monkeypatch.delenv("NOVA_LOG_LEVEL", raising=False)
    args = argparse.Namespace(data_dir=tmp_path, log_level="INFO")

    # Stop at Step 3 so we don't need to mock the whole app graph —
    # asserting ``load_config`` is reached is sufficient proof that
    # validation passed and did not short-circuit.
    with (
        patch("nova.cli.validate_data_dir") as mock_validate,
        patch("nova.cli.load_config", side_effect=ConfigError("stop here")) as mock_load,
        patch("nova.cli._configure_file_logging") as mock_phase_b,
        patch("nova.cli.create_app", new_callable=AsyncMock) as mock_create_app,
    ):
        result = await _async_main(args)

    assert result == EXIT_CONFIG_ERROR  # from ``load_config`` raising, not validate
    mock_validate.assert_called_once_with(tmp_path.resolve())
    mock_load.assert_called_once_with(tmp_path.resolve())
    mock_phase_b.assert_not_called()
    mock_create_app.assert_not_called()
