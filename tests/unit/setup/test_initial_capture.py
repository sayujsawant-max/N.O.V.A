"""Unit tests for ``nova.setup.initial_capture.capture_initial_workspace``.

Story 2.4 Group B + G.29. The module under test performs a best-effort
Win32 workspace capture during first-run setup — before Epic 4's
``Win32EyesAdapter`` exists. These tests cover the four
``CaptureResult.status`` corners (``full`` / ``partial`` / ``empty`` /
``unavailable``), the per-window graceful-partial behavior, the
250-window truncation cap, the clock-indirection contract, and the
"no adapter types escape" boundary.

No real Win32 calls happen here — every test injects a fake
``_WorkspaceProbe`` via the module-level seam
``initial_capture._probe_factory`` so tests are deterministic on any
platform.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from nova.core.types import SnapshotType
from nova.setup.initial_capture import (
    CaptureResult,
    WindowRaw,
    _WorkspaceProbe,
    capture_initial_workspace,
)
from nova.systems.eyes.models import WindowContext, WorkspaceSnapshot

# ---------------------------------------------------------------------------
# Fake probe helpers — inject via ``monkeypatch.setattr(initial_capture,
# "_probe_factory", lambda: FakeProbe(...))``. The factory indirection
# (not a global constant) means tests always get a fresh probe instance.
# ---------------------------------------------------------------------------


@dataclass
class _PerWindowFailure:
    """Sentinel placed in the ``windows`` list to ask the fake probe to
    raise for that window instead of returning a real ``WindowRaw``.
    """

    exc: BaseException


class FakeProbe:
    """Test double for ``_WorkspaceProbe``.

    ``windows`` entries can be ``WindowRaw`` instances (success) or
    ``_PerWindowFailure`` sentinels (per-window failure — the probe
    raises the carried exception when that HWND is asked for).
    ``enum_exc`` is raised by ``enumerate_hwnds`` itself when set —
    simulates the outermost ``EnumWindows`` failure.
    ``available`` mirrors the "pywin32/psutil importable?" gate.
    """

    def __init__(
        self,
        *,
        windows: list[WindowRaw | _PerWindowFailure] | None = None,
        focused_hwnd: int | None = None,
        enum_exc: BaseException | None = None,
        available: bool = True,
    ) -> None:
        self.available = available
        self._windows = windows or []
        self._focused_hwnd = focused_hwnd
        self._enum_exc = enum_exc

    def enumerate_hwnds(self) -> list[int]:
        if self._enum_exc is not None:
            raise self._enum_exc
        return [i + 1 for i in range(len(self._windows))]

    def describe_window(self, hwnd: int) -> WindowRaw:
        item = self._windows[hwnd - 1]
        if isinstance(item, _PerWindowFailure):
            raise item.exc
        return item

    def foreground_hwnd(self) -> int | None:
        return self._focused_hwnd


def _install_fake(monkeypatch: pytest.MonkeyPatch, probe: _WorkspaceProbe) -> None:
    from nova.setup import initial_capture

    monkeypatch.setattr(initial_capture, "_probe_factory", lambda: probe)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_returns_unavailable_when_probe_unavailable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="nova.setup.initial_capture")
    _install_fake(monkeypatch, FakeProbe(available=False))

    result = capture_initial_workspace()

    assert result.status == "unavailable"
    assert result.windows_captured == 0
    assert result.windows_dropped == 0
    assert result.snapshot.windows == ()
    assert result.snapshot.snapshot_type is SnapshotType.STARTUP
    assert any("unavailable" in record.getMessage().lower() for record in caplog.records)


def test_returns_unavailable_when_enum_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="nova.setup.initial_capture")
    _install_fake(
        monkeypatch,
        FakeProbe(enum_exc=OSError("simulated enum failure")),
    )

    result = capture_initial_workspace()

    assert result.status == "unavailable"
    assert result.windows_captured == 0
    assert result.windows_dropped == 0
    assert result.snapshot.windows == ()
    # No traceback is surfaced — but a single WARNING is logged.
    assert any("capture" in r.getMessage().lower() for r in caplog.records)


def test_full_capture_populates_windows_and_focused_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raws = [
        WindowRaw(hwnd=1, app_name="code", window_title="main.py", process_name="code"),
        WindowRaw(hwnd=2, app_name="chrome", window_title="docs", process_name="chrome"),
        WindowRaw(hwnd=3, app_name="terminal", window_title="nova", process_name="wt"),
    ]
    _install_fake(
        monkeypatch,
        FakeProbe(windows=list(raws), focused_hwnd=2),
    )

    result = capture_initial_workspace()

    assert result.status == "full"
    assert result.windows_captured == 3
    assert result.windows_dropped == 0
    # WindowContext fields are set with is_opaque=False (exclusion deferred).
    assert all(isinstance(w, WindowContext) for w in result.snapshot.windows)
    assert all(not w.is_opaque for w in result.snapshot.windows)
    app_names = tuple(w.app_name for w in result.snapshot.windows)
    assert app_names == ("code", "chrome", "terminal")
    # Review patch #2 — focused_app reflects the foreground_hwnd match,
    # not the first enumerated app. hwnd=2 → "chrome" (second window).
    assert result.focused_app == "chrome"


def test_focused_app_is_none_when_foreground_hwnd_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review patch #2 — foreground HWND outside the enumerated set → None."""
    raws = [
        WindowRaw(hwnd=1, app_name="code", window_title="main.py", process_name="code"),
    ]
    # Report a focused HWND that is NOT in the enumerated set (e.g. excluded
    # top-level window, or a window that appeared between EnumWindows and
    # GetForegroundWindow).
    _install_fake(monkeypatch, FakeProbe(windows=list(raws), focused_hwnd=999))

    result = capture_initial_workspace()

    assert result.focused_app is None


def test_focused_app_is_none_when_foreground_probe_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review patch #9 — ``foreground_hwnd`` raising does NOT crash capture.

    The foreground probe now uses the full ``psutil_exceptions`` tuple,
    so a simulated ``OSError`` (``pywintypes.error`` superclass) during
    the foreground lookup must degrade gracefully to ``focused_app=None``
    without dropping the already-captured windows.
    """

    class _ForegroundBoom(FakeProbe):
        def foreground_hwnd(self) -> int | None:
            raise OSError("simulated pywintypes foreground failure")

    raws = [
        WindowRaw(hwnd=1, app_name="code", window_title="main.py", process_name="code"),
    ]
    _install_fake(monkeypatch, _ForegroundBoom(windows=list(raws)))

    result = capture_initial_workspace()

    assert result.status == "full"  # windows still captured
    assert result.focused_app is None


def test_per_window_failure_is_graceful_partial(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="nova.setup.initial_capture")
    windows: list[WindowRaw | _PerWindowFailure] = [
        WindowRaw(hwnd=1, app_name="code", window_title="main.py", process_name="code"),
        _PerWindowFailure(exc=OSError("simulated per-window failure")),
        WindowRaw(hwnd=3, app_name="terminal", window_title="nova", process_name="wt"),
    ]
    _install_fake(monkeypatch, FakeProbe(windows=windows, focused_hwnd=None))

    result = capture_initial_workspace()

    assert result.status == "partial"
    assert result.windows_captured == 2
    assert result.windows_dropped == 1
    # Per-window failure logs WARNING but does not identify the app.
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "simulated" not in messages.lower()  # underlying error text never surfaces


def test_empty_desktop_is_distinct_from_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake(monkeypatch, FakeProbe(windows=[], focused_hwnd=None))

    result = capture_initial_workspace()

    assert result.status == "empty"
    assert result.windows_captured == 0
    assert result.windows_dropped == 0
    assert result.snapshot.windows == ()


@pytest.mark.parametrize(
    ("available", "captured", "dropped", "expected"),
    [
        (False, 0, 0, "unavailable"),
        (True, 0, 0, "empty"),
        (True, 3, 0, "full"),
        (True, 2, 1, "partial"),
        # All windows enumerated but every per-window probe failed — per
        # AC #5, "partial" requires captured >= 1 so this routes to
        # "unavailable" (nothing usable came back).
        (True, 0, 2, "unavailable"),
        (True, 1, 0, "full"),
    ],
)
def test_capture_status_decision_table(
    monkeypatch: pytest.MonkeyPatch,
    available: bool,
    captured: int,
    dropped: int,
    expected: str,
) -> None:
    windows: list[WindowRaw | _PerWindowFailure] = []
    for i in range(captured):
        windows.append(
            WindowRaw(
                hwnd=i + 1,
                app_name=f"app{i}",
                window_title=f"title{i}",
                process_name=f"proc{i}",
            )
        )
    for _ in range(dropped):
        windows.append(_PerWindowFailure(exc=OSError("drop me")))
    _install_fake(
        monkeypatch,
        FakeProbe(windows=windows, focused_hwnd=None, available=available),
    )

    result = capture_initial_workspace()

    assert result.status == expected


def test_captured_at_uses_events_module_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed = "2026-04-17T12:34:56+00:00"
    monkeypatch.setattr("nova.core.events._utc_now_iso", lambda: fixed)
    _install_fake(monkeypatch, FakeProbe(windows=[], focused_hwnd=None))

    result = capture_initial_workspace()

    assert result.snapshot.captured_at == fixed


def test_truncates_to_250_windows(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="nova.setup.initial_capture")
    windows: list[WindowRaw | _PerWindowFailure] = [
        WindowRaw(
            hwnd=i + 1,
            app_name=f"app{i}",
            window_title=f"title{i}",
            process_name=f"proc{i}",
        )
        for i in range(300)
    ]
    _install_fake(monkeypatch, FakeProbe(windows=windows, focused_hwnd=None))

    result = capture_initial_workspace()

    assert result.windows_captured == 250
    assert result.status == "full"
    assert any("truncat" in r.getMessage().lower() for r in caplog.records)


def test_snapshot_type_is_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake(monkeypatch, FakeProbe(windows=[], focused_hwnd=None))
    result = capture_initial_workspace()
    assert result.snapshot.snapshot_type is SnapshotType.STARTUP


def test_adapter_types_never_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    raws = [
        WindowRaw(hwnd=1, app_name="code", window_title="t", process_name="code"),
    ]
    _install_fake(monkeypatch, FakeProbe(windows=list(raws), focused_hwnd=1))

    result = capture_initial_workspace()

    assert isinstance(result, CaptureResult)
    assert isinstance(result.snapshot, WorkspaceSnapshot)
    for window in result.snapshot.windows:
        assert isinstance(window, WindowContext)
        # No raw HWND (int) or psutil.Process instances — the domain types
        # carry only string / bool fields.
        assert isinstance(window.app_name, (str, type(None)))
        assert isinstance(window.window_title, (str, type(None)))
        assert isinstance(window.process_name, (str, type(None)))
        assert isinstance(window.is_opaque, bool)


def test_is_opaque_false_for_story_24(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exclusion boundary enforcement is Story 4.2 scope.

    Story 2.4 captures all non-excluded windows with is_opaque=False.
    When Story 4.2 ships, the probe will produce some opaque rows for
    excluded apps — but that is explicitly out of scope for this story.
    """
    raws = [
        WindowRaw(hwnd=1, app_name="code", window_title="t", process_name="code"),
    ]
    _install_fake(monkeypatch, FakeProbe(windows=list(raws), focused_hwnd=1))

    result = capture_initial_workspace()

    assert all(not w.is_opaque for w in result.snapshot.windows)
