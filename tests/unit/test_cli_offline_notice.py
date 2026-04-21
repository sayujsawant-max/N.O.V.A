"""Story 2.5 AC #7–#10, #17 — one-time offline notice on ``nova`` startup."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova.app import NovaApp
from nova.cli import (
    _FILE_HANDLER_NAME,
    _STDERR_HANDLER_NAME,
    EXIT_OK,
    EXIT_STORAGE_ERROR,
    _async_main,
    _emit_offline_notice_once,
)
from nova.core import CapabilityTier, NovaConfig
from nova.core.exceptions import StorageError

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


# --- AC #7 wording contract -------------------------------------------------


_EXPECTED_NOTICE = (
    "\u26a0 Cloud reasoning unavailable. Running in "
    "offline-local-only tier. To add or update your API key, "
    "edit %LOCALAPPDATA%/nova/settings.yaml and re-run nova.\n"
)


def test_notice_prints_to_stderr_when_api_key_is_none(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC #7 — notice text matches the spec verbatim (codepoint, path, period)."""
    _emit_offline_notice_once(None)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == _EXPECTED_NOTICE
    # Explicit sub-assertions so a regression pinpoints the broken field.
    assert "\u26a0" in captured.err  # amber warning glyph
    assert "%LOCALAPPDATA%/nova/settings.yaml" in captured.err
    assert captured.err.endswith("re-run nova.\n")


def test_notice_is_silent_when_api_key_is_present(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC #8 — present key ⇒ helper is a no-op."""
    _emit_offline_notice_once("sk-ant-test")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_notice_contains_no_user_home_path_substring(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC #7 opacity — the notice never embeds a resolved username path."""
    _emit_offline_notice_once(None)
    captured = capsys.readouterr()
    # Resolved Windows user-home leakage guards.
    assert "C:\\Users\\" not in captured.err
    assert "C:/Users/" not in captured.err
    # Unix user-home leakage guards (defense-in-depth for cross-platform tests).
    assert "/home/" not in captured.err


def test_notice_does_not_echo_the_key_even_if_somehow_passed_nonstandard(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC #14, #15 opacity — the emitted stderr payload must be the spec
    text verbatim, with zero variance from the caller's ``api_key`` value.

    Review patch (Patch 4): the original test passed the sentinel as the
    ``api_key`` argument, which short-circuits the helper (present key →
    silent return) before any write — so the assertion was tautological.
    This version fires the notice (``api_key=None``) and asserts the
    stderr payload is byte-for-byte equal to the static template, so any
    future edit that interpolates caller-provided or derived content
    would fail the exact-equality check.
    """
    _emit_offline_notice_once(None)
    captured = capsys.readouterr()
    assert captured.err == _EXPECTED_NOTICE
    # Defense-in-depth: no substring that could leak from a widened
    # signature (e.g., ``key``, ``api_key``, raw path fragments).
    for forbidden in ("sk-ant-", "C:\\Users\\", "C:/Users/", "/Users/", "/home/"):
        assert forbidden not in captured.err


# --- Review patch (Patch 2): stderr write exception safety ------------------


def test_notice_swallows_unicode_encode_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Patch 2 — a cp1252/cp437 console can't encode ``\\u26a0``.

    The notice should degrade to a WARNING on the file logger, not
    raise ``UnicodeEncodeError`` out to ``_async_main``.
    """
    import sys as _sys

    def raise_unicode_error(_: str) -> int:
        raise UnicodeEncodeError("cp1252", "\u26a0", 0, 1, "bogus")

    monkeypatch.setattr(_sys.stderr, "write", raise_unicode_error)
    with caplog.at_level(logging.WARNING, logger="nova.cli"):
        _emit_offline_notice_once(None)  # must not raise

    captured = capsys.readouterr()
    # stderr capture is empty — the monkeypatched write raised before
    # anything landed.
    assert captured.err == ""
    # File-log fallback landed the WARNING.
    assert any(
        "offline notice could not be written to stderr" in rec.getMessage()
        for rec in caplog.records
    )


def test_notice_swallows_broken_pipe_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Patch 2 — a closed/detached stderr raises ``BrokenPipeError`` /
    ``OSError``. The helper must swallow and log, not propagate.
    """
    import sys as _sys

    def raise_broken_pipe(_: str) -> int:
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(_sys.stderr, "write", raise_broken_pipe)
    with caplog.at_level(logging.WARNING, logger="nova.cli"):
        _emit_offline_notice_once(None)  # must not raise

    assert any(
        "offline notice could not be written to stderr" in rec.getMessage()
        for rec in caplog.records
    )


def test_notice_swallows_value_error_on_closed_stream(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Patch 2 — writing to an already-closed file raises ``ValueError``
    (``I/O operation on closed file``). Must be swallowed.
    """
    import sys as _sys

    def raise_value_error(_: str) -> int:
        raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(_sys.stderr, "write", raise_value_error)
    with caplog.at_level(logging.WARNING, logger="nova.cli"):
        _emit_offline_notice_once(None)  # must not raise

    assert any(
        "offline notice could not be written to stderr" in rec.getMessage()
        for rec in caplog.records
    )


# --- AC #17 integration: placement at Step 6.5 ------------------------------


def _fake_config_with_api_key(api_key: str | None, tmp_path: Path) -> MagicMock:
    """Build a ``NovaConfig``-spec'd MagicMock with ``api_key`` set.

    Review patch (Patch 3) — ``spec=NovaConfig`` forces attribute access
    to go through the frozen dataclass's declared fields. A future
    success-log addition that reads e.g. ``config.settings.bluntness``
    would raise ``AttributeError`` at test time instead of silently
    returning another ``MagicMock``.
    """
    config = MagicMock(spec=NovaConfig)
    config.api_key = api_key
    config.data_dir = tmp_path
    config.db_path = tmp_path / "nova.db"
    config.modes = {}
    return config


def _fake_app_with_api_key(api_key: str | None, tmp_path: Path) -> MagicMock:
    """Build a ``NovaApp``-spec'd MagicMock wired to the fake config."""
    app = MagicMock(spec=NovaApp)
    app.config = _fake_config_with_api_key(api_key, tmp_path)
    app.tier_manager = MagicMock()
    app.tier_manager.tier = CapabilityTier.OFFLINE if api_key is None else CapabilityTier.FULL
    app.close = AsyncMock()
    return app


async def test_notice_integration_in_async_main_when_api_key_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC #17 — notice lands at Step 6.5: after "N.O.V.A. initialized" INFO,
    before the session-placeholder INFO.
    """
    monkeypatch.delenv("NOVA_LOG_LEVEL", raising=False)
    args = argparse.Namespace(data_dir=tmp_path, log_level="INFO")

    fake_config = _fake_config_with_api_key(api_key=None, tmp_path=tmp_path)
    fake_app = _fake_app_with_api_key(api_key=None, tmp_path=tmp_path)

    with (
        caplog.at_level(logging.INFO, logger="nova.cli"),
        patch("nova.cli.validate_data_dir"),
        patch("nova.cli.load_config", return_value=fake_config),
        patch("nova.cli._configure_file_logging"),
        patch("nova.cli.create_app", new_callable=AsyncMock, return_value=fake_app),
    ):
        result = await _async_main(args)

    assert result == EXIT_OK
    captured = capsys.readouterr()
    # Notice appears exactly once on stderr.
    assert captured.err.count(_EXPECTED_NOTICE) == 1

    # Ordering: INFO "N.O.V.A. initialized" landed before the placeholder
    # INFO record, and the notice was emitted between them.
    cli_records = [r for r in caplog.records if r.name == "nova.cli"]
    init_idx = next(
        i for i, r in enumerate(cli_records) if "N.O.V.A. initialized" in r.getMessage()
    )
    placeholder_idx = next(
        i for i, r in enumerate(cli_records) if "session shell placeholder" in r.getMessage()
    )
    assert init_idx < placeholder_idx, "INFO log order regressed"


async def test_notice_does_not_fire_when_api_key_is_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC #8 end-to-end — a present key means stderr stays clean."""
    monkeypatch.delenv("NOVA_LOG_LEVEL", raising=False)
    args = argparse.Namespace(data_dir=tmp_path, log_level="INFO")

    fake_config = _fake_config_with_api_key(api_key="sk-ant-test", tmp_path=tmp_path)
    fake_app = _fake_app_with_api_key(api_key="sk-ant-test", tmp_path=tmp_path)

    with (
        patch("nova.cli.validate_data_dir"),
        patch("nova.cli.load_config", return_value=fake_config),
        patch("nova.cli._configure_file_logging"),
        patch("nova.cli.create_app", new_callable=AsyncMock, return_value=fake_app),
    ):
        result = await _async_main(args)

    assert result == EXIT_OK
    captured = capsys.readouterr()
    assert "Cloud reasoning unavailable" not in captured.err


async def test_notice_does_not_fire_when_create_app_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC #9 — a StorageError from ``create_app`` must not be masked by a
    spurious offline notice.
    """
    monkeypatch.delenv("NOVA_LOG_LEVEL", raising=False)
    args = argparse.Namespace(data_dir=tmp_path, log_level="INFO")

    # Patch 3: spec-guarded config so a future field read fails loud.
    fake_config = _fake_config_with_api_key(api_key=None, tmp_path=tmp_path)

    async def exploding_create_app(_: object) -> object:
        raise StorageError("migration boom")

    with (
        patch("nova.cli.validate_data_dir"),
        patch("nova.cli.load_config", return_value=fake_config),
        patch("nova.cli._configure_file_logging"),
        patch("nova.cli.create_app", side_effect=exploding_create_app),
    ):
        result = await _async_main(args)

    assert result == EXIT_STORAGE_ERROR
    captured = capsys.readouterr()
    # The storage-error path logs an ERROR, not the offline notice.
    assert "Cloud reasoning unavailable" not in captured.err
