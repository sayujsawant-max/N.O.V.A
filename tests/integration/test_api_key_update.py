"""Story 2.5 AC #21, #22 — post-setup API-key update & rotten-key safety.

Integration coverage of the restart-picks-up-change contract:

1. A user edits ``settings.yaml`` and re-runs ``nova``; the new key
   value is reflected on the next ``load_config`` call within the same
   Python process (no in-memory caching between invocations).
2. A user removes the ``api_key:`` line; subsequent ``create_app`` calls
   boot into ``CapabilityTier.OFFLINE`` instead of FULL.
3. A present-but-invalid key does NOT crash ``_async_main``; bootstrap
   still returns ``EXIT_OK`` because T1 does not revalidate via a cloud
   ping at startup (Story 3.5 owns the first-cloud-call degradation).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from nova.app import create_app
from nova.cli import (
    _FILE_HANDLER_NAME,
    _STDERR_HANDLER_NAME,
    EXIT_OK,
    _async_main,
)
from nova.core import CapabilityTier
from nova.core.config import load_config

pytestmark = pytest.mark.integration


# --- Fixtures ---------------------------------------------------------------


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
    """Story 3.5 — patch the Skin REPL primitive so api-key tests exit fast.

    These tests verify api-key bootstrap behavior (Story 2.5), not
    session-loop semantics. Without this fixture, ``app.nerve.startup()``
    blocks on ``Prompt.ask`` and pytest's stdin-capture raises ``OSError``.
    """
    monkeypatch.setattr("nova.adapters.rich.skin.Prompt.ask", lambda *a, **kw: "shutdown")


def _atomic_write_settings(path: Path, data: dict[str, object]) -> None:
    """Write ``settings.yaml`` via the Story 2.2 atomic-swap pattern
    (``yaml.safe_dump`` + ``os.replace``).

    Review patch — single helper for every call site so the ``api_key=None``
    fixture path exercises the same YAML-dump codepath as the
    ``api_key=<value>`` path. Prevents a dumper quirk (trailing newline,
    quoting) from landing differently between the two.
    """
    tmp = path.with_suffix(".yaml.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
    os.replace(tmp, path)


def _atomic_write_settings_with_api_key(path: Path, api_key: str) -> None:
    """Write ``settings.yaml`` containing ``api_key`` atomically."""
    _atomic_write_settings(path, {"api_key": api_key, "bluntness": "direct"})


def _atomic_rewrite_settings_without_api_key(path: Path) -> None:
    """Overwrite ``settings.yaml`` so ``api_key`` is absent but the file
    is still a valid non-empty mapping (retains ``bluntness: direct``).
    """
    _atomic_write_settings(path, {"bluntness": "direct"})


def _seed_data_dir(tmp_path: Path, *, api_key: str | None) -> Path:
    """Build a minimal valid data dir with a ``settings.yaml`` containing
    ``api_key`` iff ``api_key is not None``. Also seeds a single valid
    mode so ``load_config`` does NOT emit the "zero modes" WARNING.

    Both branches route through ``_atomic_write_settings`` so the
    ``api_key=None`` fixture exercises the same YAML-dump codepath as
    the ``api_key=<value>`` fixture.
    """
    if api_key is None:
        _atomic_write_settings(tmp_path / "settings.yaml", {"bluntness": "direct"})
    else:
        _atomic_write_settings_with_api_key(tmp_path / "settings.yaml", api_key)
    _atomic_write_settings(tmp_path / "exclusions.yaml", {})
    modes_dir = tmp_path / "modes"
    modes_dir.mkdir()
    (modes_dir / "placeholder.yaml").write_text(
        "name: placeholder\napps:\n  - name: Placeholder\n    executable: placeholder.exe\n",
        encoding="utf-8",
    )
    return tmp_path


def _flush_nova_file_handlers() -> None:
    """Flush + close every ``nova.cli``-tagged ``FileHandler`` so the log
    file can be read safely on Windows without racing the handler.

    Review patch: Windows file-buffering + the still-open ``FileHandler``
    on ``nova.log`` can cause ``read_text`` to see partial or missing
    content until the autouse fixture closes the handler at teardown.
    Tests that read ``nova.log`` inline must call this first.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if handler.name == _FILE_HANDLER_NAME:
            handler.flush()
            handler.close()
            root.removeHandler(handler)


# --- AC #21 — change on restart --------------------------------------------


def test_settings_yaml_edit_is_visible_on_next_load_config(tmp_path: Path) -> None:
    """AC #21 — overwriting ``settings.yaml`` is reflected on the next
    ``load_config`` in the same process.

    Locks the "no in-memory caching" contract: any future refactor that
    memoizes the YAML content would leave users with a stale key after
    editing settings.yaml and re-running nova.
    """
    data_dir = _seed_data_dir(tmp_path, api_key="k1")
    first = load_config(data_dir)
    assert first.api_key == "k1"

    _atomic_write_settings_with_api_key(data_dir / "settings.yaml", "k2")
    second = load_config(data_dir)
    assert second.api_key == "k2"
    # The two returns are distinct objects (no cache leak).
    assert first is not second


def test_settings_yaml_removed_key_is_picked_up_as_none(tmp_path: Path) -> None:
    """AC #21 — deleting the ``api_key:`` line flips the next load to None."""
    data_dir = _seed_data_dir(tmp_path, api_key="k1")
    assert load_config(data_dir).api_key == "k1"

    _atomic_rewrite_settings_without_api_key(data_dir / "settings.yaml")
    reloaded = load_config(data_dir)
    assert reloaded.api_key is None
    # Other settings survive the rewrite.
    assert reloaded.settings.bluntness.value == "direct"


async def test_removed_key_degrades_initial_tier_to_offline_on_next_create_app(
    tmp_path: Path,
) -> None:
    """AC #21 end-to-end — load → create → FULL, edit file, load → create → OFFLINE.

    Closes the restart-picks-up-change contract at the ``create_app``
    boundary: a user whose key was valid yesterday but who deletes it
    today boots into OFFLINE on the next ``nova`` run.
    """
    data_dir = _seed_data_dir(tmp_path, api_key="sk-ant-first-session")

    first_config = load_config(data_dir)
    first_app = await create_app(first_config)
    try:
        assert first_app.tier_manager.tier is CapabilityTier.FULL
    finally:
        await first_app.close()

    _atomic_rewrite_settings_without_api_key(data_dir / "settings.yaml")

    second_config = load_config(data_dir)
    assert second_config.api_key is None
    second_app = await create_app(second_config)
    try:
        assert second_app.tier_manager.tier is CapabilityTier.OFFLINE
    finally:
        await second_app.close()


# --- AC #22 — rotten key does not crash ------------------------------------


async def test_stale_or_invalid_key_completes_bootstrap_without_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC #22 — a fake/revoked key still boots ``_async_main`` with ``EXIT_OK``.

    T1 does not revalidate the key via a cloud ping at startup. The
    ``_AlwaysHealthyCheck`` stub (Story 1.10) never fails, so ``FULL``
    tier sticks at bootstrap even when the key is nonsense.

    An invalid-but-present key degrades to OFFLINE at first
    cloud-call failure via ``TierManager.report_failure`` (Story 1.7,
    Story 3.5). Story 2.5's contract is only: bootstrap succeeds, the
    one-time offline notice is NOT shown (because the key IS present),
    and the existing tier machinery handles the eventual failure. The
    first-cloud-call degradation path is Story 3.5's test surface.
    """
    data_dir = _seed_data_dir(tmp_path, api_key="sk-ant-obviously-invalid-rotten")
    monkeypatch.setenv("NOVA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("NOVA_LOG_LEVEL", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    args = argparse.Namespace(data_dir=None, log_level=None)
    exit_code = await _async_main(args)
    assert exit_code == EXIT_OK

    # Review patch (Patch 9): flush the FileHandler before reading nova.log
    # so Windows file-buffering can't race the handler's still-open stream.
    _flush_nova_file_handlers()

    # Log artifact assertions — bootstrap SUCCEEDED, which is the AC.
    log_path = data_dir / "logs" / "nova.log"
    assert log_path.exists()
    log_text = log_path.read_text(encoding="utf-8")
    assert "N.O.V.A. initialized" in log_text
    # Present key → no offline notice, no "starting in offline-local-only" log.
    assert "starting in offline-local-only" not in log_text

    # Review patch (Patch 5): AC #22 specifies "the one-time notice is NOT
    # shown (because the key IS present)". The notice lands on stderr, not
    # in nova.log, so we must check capsys.err directly — the file-log
    # assertion above is only coincidentally related.
    captured = capsys.readouterr()
    assert "Cloud reasoning unavailable" not in captured.err
    assert "offline-local-only tier" not in captured.err

    # Yield once to release any pending asyncio housekeeping.
    await asyncio.sleep(0)


# --- Review patch (Patch 1): Phase-A-gone invariant for the notice --------


async def test_offline_notice_is_only_stderr_output_on_no_key_boot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Proves the notice fires at Step 6.5 AFTER Phase A's stderr handler
    has been torn down by Phase B. If Phase A were still attached, both
    the success INFO log AND the notice would hit stderr; the notice
    would no longer be the ONLY stderr content.

    The unit-level ``test_notice_integration_in_async_main_when_api_key_none``
    patches ``_configure_file_logging`` to a no-op, which leaves Phase A
    alive throughout the test — so it cannot verify this invariant.
    This integration test runs the REAL Phase B transition against a
    real data dir, then asserts the offline notice is the only line on
    stderr (modulo trailing whitespace).
    """
    data_dir = _seed_data_dir(tmp_path, api_key=None)
    monkeypatch.setenv("NOVA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("NOVA_LOG_LEVEL", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    args = argparse.Namespace(data_dir=None, log_level=None)
    exit_code = await _async_main(args)
    assert exit_code == EXIT_OK

    _flush_nova_file_handlers()
    captured = capsys.readouterr()

    # The notice is the sole stderr payload — proves Phase A was removed
    # before Step 6.5 (otherwise the "N.O.V.A. initialized" INFO record
    # would also have landed on stderr via Phase A's StreamHandler).
    stderr_lines = [line for line in captured.err.splitlines() if line.strip()]
    assert len(stderr_lines) == 1, (
        f"expected exactly one stderr line (the offline notice); "
        f"Phase A may still be attached. Got:\n{captured.err!r}"
    )
    notice_line = stderr_lines[0]
    assert "Cloud reasoning unavailable" in notice_line
    assert "%LOCALAPPDATA%/nova/settings.yaml" in notice_line
    assert "\u26a0" in notice_line

    # And the file log DID land in nova.log — confirming the success
    # INFO record was routed to Phase B (file), not stderr.
    log_path = data_dir / "logs" / "nova.log"
    assert log_path.exists()
    log_text = log_path.read_text(encoding="utf-8")
    assert "N.O.V.A. initialized" in log_text
    assert "starting in offline-local-only tier" in log_text

    await asyncio.sleep(0)
