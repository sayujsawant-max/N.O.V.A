"""Unit tests for :class:`nova.adapters.win32.actions.Win32HandsAdapter` (Story 3.6).

Platform-neutral via mocking: every Windows-specific surface
(:func:`subprocess.Popen`, :func:`os.startfile`,
:func:`psutil.process_iter`) is patched at the module-attribute level
so the tests run on any platform. The 100% coverage gate at AC #37
depends on this — ``@pytest.mark.windows_only`` would skip the tests
on non-Windows runners and break coverage. Real-Win32 coverage lives
in the integration tests at ``tests/integration/test_session_loop.py``.

Five blocks per AC #30:

A. Happy launch — Popen success path with the right creationflags.
B. Already-running pre-check — psutil match returns success=True.
C. Error mapping — canonical four-member reason vocabulary.
D. Adapter is one-app-at-a-time — single ActionResult return shape.
E. Domain-exception boundary — subprocess-specific exceptions don't leak.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import psutil
import pytest

from nova.adapters.win32 import actions as win32_actions
from nova.adapters.win32.actions import Win32HandsAdapter
from nova.core.config import AppConfig
from nova.core.types import ActionType
from nova.ports.app_launcher import (
    REASON_NOT_FOUND,
    REASON_PERMISSION_DENIED,
    REASON_TIMED_OUT,
    REASON_UNKNOWN_ERROR,
)
from nova.systems.hands.models import ActionResult


def _app(executable: str = "code", args: tuple[str, ...] = ()) -> AppConfig:
    return AppConfig(name=executable.capitalize(), executable=executable, args=args)


def _no_psutil_match(_executable: str) -> bool:
    """Default already-running stub — nothing matches."""
    return False


@pytest.fixture
def mock_popen(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``subprocess.Popen`` (via the actions module reference) with a MagicMock."""
    popen = MagicMock(name="Popen", return_value=MagicMock(pid=1234))
    monkeypatch.setattr("nova.adapters.win32.actions.subprocess.Popen", popen)
    return popen


@pytest.fixture
def mock_startfile(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``os.startfile`` with a MagicMock returning None on success."""
    startfile = MagicMock(name="startfile", return_value=None)
    monkeypatch.setattr("nova.adapters.win32.actions.os.startfile", startfile, raising=False)
    return startfile


@pytest.fixture
def stub_already_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the psutil pre-check to find nothing.

    Opt-in (NOT autouse) so tests that exercise the real
    ``_iter_processes_for_match`` function still see the live
    implementation.
    """
    monkeypatch.setattr("nova.adapters.win32.actions._iter_processes_for_match", _no_psutil_match)


# ===========================================================================
# Block A — Happy launch
# ===========================================================================


@pytest.mark.asyncio
async def test_launch_app_subprocess_popen_success(
    mock_popen: MagicMock, stub_already_running: None
) -> None:
    adapter = Win32HandsAdapter()
    result = await adapter.launch_app(_app("code"))
    assert isinstance(result, ActionResult)
    assert result.success is True
    assert result.reason is None
    assert result.target == "Code"
    assert result.action_type is ActionType.APP_LAUNCH
    mock_popen.assert_called_once()


@pytest.mark.asyncio
async def test_launch_app_uses_detached_creationflags_constant(
    mock_popen: MagicMock, stub_already_running: None
) -> None:
    """Assert against the module-level _CREATIONFLAGS constant (cross-platform safe)."""
    adapter = Win32HandsAdapter()
    await adapter.launch_app(_app("code"))
    assert mock_popen.call_args.kwargs["creationflags"] == win32_actions._CREATIONFLAGS


@pytest.mark.asyncio
async def test_launch_app_uses_close_fds_true(
    mock_popen: MagicMock, stub_already_running: None
) -> None:
    adapter = Win32HandsAdapter()
    await adapter.launch_app(_app("code"))
    assert mock_popen.call_args.kwargs["close_fds"] is True


@pytest.mark.asyncio
async def test_launch_app_passes_args_in_argv(
    mock_popen: MagicMock, stub_already_running: None
) -> None:
    adapter = Win32HandsAdapter()
    await adapter.launch_app(_app("chrome", args=("--new-window",)))
    assert mock_popen.call_args.args[0] == ["chrome", "--new-window"]


# ===========================================================================
# Block B — Already-running pre-check (success-returning)
# ===========================================================================


@pytest.mark.asyncio
async def test_launch_app_returns_success_when_already_running(
    monkeypatch: pytest.MonkeyPatch,
    mock_popen: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Already-running is treated as a successful workspace outcome (AC #3 step 2)."""
    monkeypatch.setattr(
        "nova.adapters.win32.actions._iter_processes_for_match",
        lambda exe: True,
    )
    adapter = Win32HandsAdapter()
    with caplog.at_level("DEBUG", logger="nova.adapters.win32.actions"):
        result = await adapter.launch_app(_app("chrome"))

    assert result.success is True
    assert result.reason is None
    assert mock_popen.call_count == 0
    debug_msgs = [r for r in caplog.records if "already running, skipping launch" in r.message]
    assert len(debug_msgs) == 1


@pytest.mark.asyncio
async def test_launch_app_case_insensitive_already_running_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``executable="Code.exe"`` matches a running ``"code.exe"`` process."""
    captured: dict[str, str] = {}

    def fake_iter_processes_for_match(executable: str) -> bool:
        captured["executable"] = executable
        # Real impl basenames + lowercases. Verify the input flows in correctly;
        # return True so the success branch fires.
        return True

    monkeypatch.setattr(
        "nova.adapters.win32.actions._iter_processes_for_match",
        fake_iter_processes_for_match,
    )
    adapter = Win32HandsAdapter()
    result = await adapter.launch_app(_app("Code.exe"))
    assert result.success is True
    assert captured["executable"] == "Code.exe"


@pytest.mark.asyncio
async def test_iter_processes_for_match_basename_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real ``_iter_processes_for_match`` lowercases + basenames."""
    fake_proc = MagicMock()
    fake_proc.info = {"name": "code.exe"}
    monkeypatch.setattr("psutil.process_iter", lambda fields: [fake_proc])
    assert win32_actions._iter_processes_for_match("Code.exe") is True
    assert win32_actions._iter_processes_for_match("notepad.exe") is False


@pytest.mark.asyncio
async def test_iter_processes_for_match_skips_inaccessible_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-proc AccessDenied is caught silently (best-effort detection)."""
    bad_proc = MagicMock()
    type(bad_proc).info = property(_raise_no_such_process)
    good_proc = MagicMock()
    good_proc.info = {"name": "code.exe"}
    monkeypatch.setattr("psutil.process_iter", lambda fields: [bad_proc, good_proc])
    assert win32_actions._iter_processes_for_match("code.exe") is True


def _raise_no_such_process(_self: object) -> dict[str, str]:
    raise psutil.NoSuchProcess(pid=42)


@pytest.mark.asyncio
async def test_iter_processes_for_match_returns_false_on_outer_access_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the whole iter refuses, treat as 'not running' (false-negative fail-mode)."""

    def raising_iter(_fields: list[str]) -> object:
        raise psutil.AccessDenied()

    monkeypatch.setattr("psutil.process_iter", raising_iter)
    assert win32_actions._iter_processes_for_match("anything.exe") is False


# ===========================================================================
# Block C — Error mapping (canonical four-reason vocabulary)
# ===========================================================================


@pytest.mark.asyncio
async def test_launch_app_file_not_found_with_empty_args_falls_back_to_startfile(
    mock_popen: MagicMock,
    mock_startfile: MagicMock,
    stub_already_running: None,
) -> None:
    mock_popen.side_effect = FileNotFoundError(2, "no such file")
    adapter = Win32HandsAdapter()
    result = await adapter.launch_app(_app("chrome"))
    assert result.success is True
    mock_startfile.assert_called_once_with("chrome")


@pytest.mark.asyncio
async def test_launch_app_file_not_found_with_args_skips_startfile_fallback(
    mock_popen: MagicMock,
    mock_startfile: MagicMock,
    stub_already_running: None,
) -> None:
    """args present → no startfile fallback; return REASON_NOT_FOUND directly."""
    mock_popen.side_effect = FileNotFoundError(2, "no such file")
    adapter = Win32HandsAdapter()
    result = await adapter.launch_app(_app("chrome", args=("--new-window",)))
    assert result.success is False
    assert result.reason == REASON_NOT_FOUND
    assert mock_startfile.call_count == 0


@pytest.mark.asyncio
async def test_launch_app_returns_not_found_when_both_popen_and_startfile_fail(
    mock_popen: MagicMock,
    mock_startfile: MagicMock,
    stub_already_running: None,
) -> None:
    mock_popen.side_effect = FileNotFoundError(2, "no such file")
    mock_startfile.side_effect = FileNotFoundError(2, "no such file")
    adapter = Win32HandsAdapter()
    result = await adapter.launch_app(_app("chrome"))
    assert result.success is False
    assert result.reason == REASON_NOT_FOUND


@pytest.mark.asyncio
async def test_launch_app_startfile_fallback_maps_other_oserror_via_map_os_error(
    mock_popen: MagicMock,
    mock_startfile: MagicMock,
    stub_already_running: None,
) -> None:
    """When startfile raises a non-FileNotFoundError OSError, _map_os_error translates it.

    Covers the ``except OSError as exc: return _map_os_error(app, exc)``
    branch inside the args-empty fallback path.
    """
    mock_popen.side_effect = FileNotFoundError(2, "no such file")
    err = OSError(5, "Access is denied")
    err.winerror = 5
    mock_startfile.side_effect = err
    adapter = Win32HandsAdapter()
    result = await adapter.launch_app(_app("chrome"))
    assert result.success is False
    # winerror 5 maps to PERMISSION_DENIED via _map_os_error.
    assert result.reason == REASON_PERMISSION_DENIED


@pytest.mark.asyncio
async def test_launch_app_returns_permission_denied_on_permission_error(
    mock_popen: MagicMock,
    stub_already_running: None,
) -> None:
    mock_popen.side_effect = PermissionError(13, "access denied")
    adapter = Win32HandsAdapter()
    result = await adapter.launch_app(_app("locked"))
    assert result.success is False
    assert result.reason == REASON_PERMISSION_DENIED


@pytest.mark.asyncio
async def test_launch_app_returns_permission_denied_on_os_error_winerror_5(
    mock_popen: MagicMock,
    stub_already_running: None,
) -> None:
    """Plain OSError with .winerror == 5 also maps to permission-denied."""
    err = OSError(5, "Access is denied")
    err.winerror = 5
    mock_popen.side_effect = err
    adapter = Win32HandsAdapter()
    result = await adapter.launch_app(_app("locked"))
    assert result.success is False
    assert result.reason == REASON_PERMISSION_DENIED


@pytest.mark.asyncio
async def test_launch_app_returns_timed_out_when_wait_for_exceeds_timeout(
    monkeypatch: pytest.MonkeyPatch,
    stub_already_running: None,
) -> None:
    """A blocking Popen that never returns within the timeout maps to REASON_TIMED_OUT."""

    async def slow_to_thread(func: object, /, *args: object, **kwargs: object) -> None:
        del func, args, kwargs
        await asyncio.sleep(10)

    monkeypatch.setattr("nova.adapters.win32.actions.asyncio.to_thread", slow_to_thread)
    adapter = Win32HandsAdapter(timeout_seconds=0.05)
    result = await adapter.launch_app(_app("hangs"))
    assert result.success is False
    assert result.reason == REASON_TIMED_OUT


@pytest.mark.asyncio
async def test_launch_app_returns_unknown_error_for_other_os_errors(
    mock_popen: MagicMock,
    caplog: pytest.LogCaptureFixture,
    stub_already_running: None,
) -> None:
    err = OSError(99, "weird unknown OS error")
    err.winerror = 99
    mock_popen.side_effect = err
    adapter = Win32HandsAdapter()

    with caplog.at_level("WARNING", logger="nova.adapters.win32.actions"):
        result = await adapter.launch_app(_app("weird"))

    assert result.success is False
    assert result.reason == REASON_UNKNOWN_ERROR
    warnings = [r for r in caplog.records if "launch_app failed" in r.message]
    assert len(warnings) == 1
    # ``winerror`` was passed via ``extra=`` so it's an attribute on the
    # LogRecord; getattr-with-default keeps mypy strict happy.
    assert getattr(warnings[0], "winerror", None) == 99


# ===========================================================================
# Block D — Adapter is one-app-at-a-time (AC #5)
# ===========================================================================


@pytest.mark.asyncio
async def test_launch_app_returns_single_action_result_not_a_list(
    mock_popen: MagicMock,
    stub_already_running: None,
) -> None:
    adapter = Win32HandsAdapter()
    result = await adapter.launch_app(_app("code"))
    assert isinstance(result, ActionResult)
    assert not isinstance(result, list)


# ===========================================================================
# Block E — Domain-exception boundary (AC #3 step 5)
# ===========================================================================


@pytest.mark.asyncio
async def test_launch_app_does_not_leak_subprocess_specific_exceptions(
    mock_popen: MagicMock,
    caplog: pytest.LogCaptureFixture,
    stub_already_running: None,
) -> None:
    """``subprocess.SubprocessError`` (NOT an OSError subclass) is caught and translated."""
    import subprocess as _subprocess

    mock_popen.side_effect = _subprocess.SubprocessError("subprocess-specific")
    adapter = Win32HandsAdapter()

    with caplog.at_level("WARNING", logger="nova.adapters.win32.actions"):
        result = await adapter.launch_app(_app("weird"))

    assert isinstance(result, ActionResult)
    assert result.success is False
    assert result.reason == REASON_UNKNOWN_ERROR


@pytest.mark.asyncio
async def test_iter_processes_for_match_skips_per_proc_access_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-proc AccessDenied (NOT outer iter) is caught silently."""

    def raising_proc_info(_self: object) -> dict[str, str]:
        raise psutil.AccessDenied(pid=42)

    bad = MagicMock()
    type(bad).info = property(raising_proc_info)
    good = MagicMock()
    good.info = {"name": "match.exe"}
    monkeypatch.setattr("psutil.process_iter", lambda fields: [bad, good])
    assert win32_actions._iter_processes_for_match("match.exe") is True


# ---------------------------------------------------------------------------
# Block B' — Story 3.6 review fix: basename normalization (.exe stripping)
# ---------------------------------------------------------------------------


def test_normalize_exe_basename_strips_directory_and_lowercases() -> None:
    assert win32_actions._normalize_exe_basename("C:\\Program Files\\Foo\\Foo.exe") == "foo"
    assert win32_actions._normalize_exe_basename("CHROME.EXE") == "chrome"
    assert win32_actions._normalize_exe_basename("notepad") == "notepad"


@pytest.mark.asyncio
async def test_iter_processes_for_match_normalizes_chrome_vs_chrome_exe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Config ``executable: chrome`` matches a running ``chrome.exe`` process.

    Closes Blind Hunter finding #5 / Edge Case finding #12 — common
    Windows pattern: user writes the short name, OS reports the
    full-name with extension.
    """
    proc = MagicMock()
    proc.info = {"name": "chrome.exe"}
    monkeypatch.setattr("psutil.process_iter", lambda fields: [proc])
    assert win32_actions._iter_processes_for_match("chrome") is True


@pytest.mark.asyncio
async def test_iter_processes_for_match_returns_false_on_outer_psutil_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """psutil.Error (subclass other than AccessDenied) treated as not-running."""

    def raising_iter(_fields: list[str]) -> object:
        # psutil.Error is the base class — picking a generic subclass.
        raise psutil.Error("simulated psutil failure")

    monkeypatch.setattr("psutil.process_iter", raising_iter)
    assert win32_actions._iter_processes_for_match("anything") is False


@pytest.mark.asyncio
async def test_iter_processes_for_match_returns_false_on_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Underlying OSError from syscall treated as not-running (fail-safe)."""

    def raising_iter(_fields: list[str]) -> object:
        raise OSError("simulated kernel error")

    monkeypatch.setattr("psutil.process_iter", raising_iter)
    assert win32_actions._iter_processes_for_match("anything") is False


# ---------------------------------------------------------------------------
# /bmad-code-review patches: per-proc exception widening + empty-target guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iter_processes_for_match_skips_zombie_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single ZombieProcess must be skipped per-iter, not abort the whole scan.

    Closes /bmad-code-review patch #1 (BH+EC). Without per-proc handling
    of ``psutil.ZombieProcess``, the outer ``except (psutil.Error,
    OSError)`` would catch the zombie and abort the entire iteration,
    silently returning False even if the desired process exists later in
    the iterator. Defeats the dedup pre-check whenever any zombie is
    present anywhere on the system.
    """

    def raising_proc_info(_self: object) -> dict[str, str]:
        raise psutil.ZombieProcess(pid=42)

    bad = MagicMock()
    type(bad).info = property(raising_proc_info)
    good = MagicMock()
    good.info = {"name": "match.exe"}
    monkeypatch.setattr("psutil.process_iter", lambda fields: [bad, good])
    assert win32_actions._iter_processes_for_match("match.exe") is True


@pytest.mark.asyncio
async def test_iter_processes_for_match_skips_per_proc_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-process OSError (handle-close race) is skipped, scan continues.

    Closes /bmad-code-review patch #1 — psutil normally translates
    OSError into its own exceptions, but on some Windows builds a
    process whose handle was just closed mid-iter raises raw OSError.
    Without this skip, the outer except would abort the whole scan.
    """

    def raising_proc_info(_self: object) -> dict[str, str]:
        raise OSError("handle closed")

    bad = MagicMock()
    type(bad).info = property(raising_proc_info)
    good = MagicMock()
    good.info = {"name": "match.exe"}
    monkeypatch.setattr("psutil.process_iter", lambda fields: [bad, good])
    assert win32_actions._iter_processes_for_match("match.exe") is True


@pytest.mark.asyncio
async def test_iter_processes_for_match_returns_false_for_empty_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty normalized target must not match any process.

    Closes /bmad-code-review patch #2 (BH#4 + EC#2). A misconfigured
    mode YAML with ``executable=""`` OR an input that normalizes to
    empty (``".exe"``) would otherwise match every process whose
    ``proc.info["name"]`` is None / empty — silently no-op the
    entire mode while reporting all-success to the user.
    """
    proc_with_no_name = MagicMock()
    proc_with_no_name.info = {"name": None}
    monkeypatch.setattr("psutil.process_iter", lambda fields: [proc_with_no_name])
    # Direct empty input
    assert win32_actions._iter_processes_for_match("") is False
    # Input that normalizes to empty
    assert win32_actions._iter_processes_for_match(".exe") is False
