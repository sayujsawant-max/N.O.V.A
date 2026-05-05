"""Unit tests for :class:`nova.systems.hands.system.HandsSystem` (Story 3.6).

Eight blocks per AC #29:

1. Constructor — reference-storage only, keyword-only signature.
2. Happy path — all apps succeed (audit→render→event ordering, stem in
   audit target + ModeRestored.mode_name, "Workspace ready." final line).
3. Partial path — graceful-partial pattern with split tuples + count
   suffix when multiple failures.
4. Total-failure path — distinct "No apps could be launched" line with
   stem in the ``mode edit`` hint.
5. Audit-failure isolation — uses real AuditLogger over a failing
   storage engine + AST guard that locks "no try/except around
   log_action" + propagates AuditLogger's own ValueError on bad result.
6. URL-deferral notice — log count only, never the URLs themselves.
7. Defensive preconditions — empty apps tuple + empty/whitespace stem.
8. Single-app boundary — works for a 1-app mode in both success and
   failure cases.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nova.core.audit import RESULT_FAILED, RESULT_SUCCESS, AuditLogger
from nova.core.config import AppConfig, ModeConfig
from nova.core.events import EventBus, ModeRestored
from nova.core.exceptions import StorageError
from nova.core.types import ActionType
from nova.ports.app_launcher import (
    REASON_NOT_FOUND,
    AppLauncherPort,
)
from nova.ports.skin import SkinPort
from nova.systems.hands.models import ActionResult
from nova.systems.hands.system import HandsSystem

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _app(name: str, executable: str | None = None) -> AppConfig:
    return AppConfig(name=name, executable=executable or f"{name.lower()}.exe", args=())


def _mode(
    stem_display_name: str = "Coding",
    apps: tuple[AppConfig, ...] | None = None,
) -> ModeConfig:
    return ModeConfig(
        name=stem_display_name,
        apps=apps if apps is not None else (_app("VS Code"), _app("Chrome"), _app("Postman")),
        is_default=False,
    )


def _success_result(target: str) -> ActionResult:
    return ActionResult(
        action_type=ActionType.APP_LAUNCH,
        target=target,
        success=True,
        reason=None,
    )


def _failure_result(target: str, reason: str = REASON_NOT_FOUND) -> ActionResult:
    return ActionResult(
        action_type=ActionType.APP_LAUNCH,
        target=target,
        success=False,
        reason=reason,
    )


def _make_launcher_mock(results: list[ActionResult]) -> MagicMock:
    """Build a launcher mock that returns ``results[i]`` for the i-th call."""
    launcher = MagicMock(spec=AppLauncherPort)
    iter_results = iter(results)
    launcher.launch_app = AsyncMock(side_effect=lambda app: next(iter_results))
    return launcher


def _make_skin_mock() -> MagicMock:
    skin = MagicMock(spec=SkinPort)
    skin.render_progress = AsyncMock(return_value=None)
    skin.render_response = AsyncMock(return_value=None)
    return skin


def _make_event_bus_mock() -> MagicMock:
    bus = MagicMock(spec=EventBus)
    bus.emit = AsyncMock(return_value=None)
    return bus


def _make_audit_mock() -> MagicMock:
    audit = MagicMock(spec=AuditLogger)
    audit.log_action = AsyncMock(return_value=None)
    return audit


def _build_hands(
    *,
    launcher: MagicMock | None = None,
    skin: MagicMock | None = None,
    event_bus: MagicMock | None = None,
    audit: MagicMock | None = None,
) -> tuple[HandsSystem, MagicMock, MagicMock, MagicMock, MagicMock]:
    launcher = (
        launcher
        if launcher is not None
        else _make_launcher_mock(
            [_success_result("VS Code"), _success_result("Chrome"), _success_result("Postman")]
        )
    )
    skin = skin if skin is not None else _make_skin_mock()
    event_bus = event_bus if event_bus is not None else _make_event_bus_mock()
    audit = audit if audit is not None else _make_audit_mock()
    hands = HandsSystem(launcher=launcher, skin=skin, event_bus=event_bus, audit=audit)
    return hands, launcher, skin, event_bus, audit


# ===========================================================================
# Block 1 — Constructor (AC #6)
# ===========================================================================


def test_constructor_is_reference_storage_only() -> None:
    """Constructor must not call any method on its dependencies."""
    launcher = _make_launcher_mock([])
    skin = _make_skin_mock()
    event_bus = _make_event_bus_mock()
    audit = _make_audit_mock()
    HandsSystem(launcher=launcher, skin=skin, event_bus=event_bus, audit=audit)
    assert launcher.launch_app.call_count == 0
    assert skin.render_progress.call_count == 0
    assert skin.render_response.call_count == 0
    assert event_bus.emit.call_count == 0
    assert audit.log_action.call_count == 0


def test_constructor_keyword_only_signature() -> None:
    """All four constructor params are keyword-only — positional construction fails."""
    with pytest.raises(TypeError):
        HandsSystem(  # type: ignore[misc]
            _make_launcher_mock([]),
            _make_skin_mock(),
            _make_event_bus_mock(),
            _make_audit_mock(),
        )


# ===========================================================================
# Block 2 — Happy path: all apps succeed (AC #7)
# ===========================================================================


@pytest.mark.asyncio
async def test_restore_mode_launches_each_app_in_order() -> None:
    apps = (_app("VS Code"), _app("Chrome"), _app("Postman"))
    mode = _mode(apps=apps)
    launcher = _make_launcher_mock(
        [_success_result("VS Code"), _success_result("Chrome"), _success_result("Postman")]
    )
    hands, _launcher, _skin, _bus, _audit = _build_hands(launcher=launcher)

    results = await hands.restore_mode("coding", mode)

    assert launcher.launch_app.call_count == 3
    assert [call.args[0].name for call in launcher.launch_app.call_args_list] == [
        "VS Code",
        "Chrome",
        "Postman",
    ]
    assert len(results) == 3
    assert all(r.success for r in results)


@pytest.mark.asyncio
async def test_restore_mode_per_app_audit_then_render_then_event_ordering() -> None:
    """For each app: audit row → render → event, before the next app's launch.

    Uses ``parent.attach_mock`` to capture chronological order across
    the three mocks (AC #17). A bare per-mock ``call_args_list`` only
    proves per-mock counts; the cross-mock sequence is what locks the
    AC, so a future regression that swapped, e.g., audit and render
    would not slip past this guard.
    """
    audit = _make_audit_mock()
    skin = _make_skin_mock()
    event_bus = _make_event_bus_mock()
    launcher = _make_launcher_mock([_success_result("App1"), _success_result("App2")])
    mode = _mode(apps=(_app("App1"), _app("App2")))
    hands, *_ = _build_hands(launcher=launcher, skin=skin, event_bus=event_bus, audit=audit)

    parent = MagicMock()
    parent.attach_mock(launcher.launch_app, "launch")
    parent.attach_mock(audit.log_action, "audit")
    parent.attach_mock(skin.render_progress, "render")
    parent.attach_mock(event_bus.emit, "emit")

    await hands.restore_mode("coding", mode)

    # Extract just the method NAMES from the chronological sequence.
    sequence = [call[0] for call in parent.mock_calls]
    # Per-app pattern (twice, once per app), then aggregate audit + emit.
    # Render_response is on a different mock (skin.render_response is a
    # separate AsyncMock attribute, but we attached the parent skin's
    # render_progress only). The expected order:
    expected = [
        "launch",
        "audit",
        "render",
        "emit",  # AppLaunched #1
        "launch",
        "audit",
        "render",
        "emit",  # AppLaunched #2
        "audit",
        "emit",  # MODE_RESTORE + ModeRestored
    ]
    assert sequence == expected, (
        f"Per-app + aggregate ordering drift. Expected:\n  {expected}\nGot:\n  {sequence}"
    )


@pytest.mark.asyncio
async def test_restore_mode_aggregate_audit_after_per_app_loop_before_mode_restored_event() -> None:
    """Aggregate ordering: MODE_RESTORE audit → ModeRestored emit → final render_response."""
    audit = _make_audit_mock()
    skin = _make_skin_mock()
    event_bus = _make_event_bus_mock()
    launcher = _make_launcher_mock([_success_result("App1"), _success_result("App2")])
    mode = _mode(apps=(_app("App1"), _app("App2")))
    hands, *_ = _build_hands(launcher=launcher, skin=skin, event_bus=event_bus, audit=audit)

    parent = MagicMock()
    parent.attach_mock(audit.log_action, "audit")
    parent.attach_mock(event_bus.emit, "emit")
    parent.attach_mock(skin.render_response, "summary")

    await hands.restore_mode("coding", mode)

    # Find aggregate-stage indices: last audit (MODE_RESTORE), the
    # ModeRestored emit (last emit), and the single summary render.
    sequence = parent.mock_calls
    aggregate_audit_idx = max(
        i
        for i, call in enumerate(sequence)
        if call[0] == "audit" and call.kwargs.get("action_type") is ActionType.MODE_RESTORE
    )
    mode_restored_emit_idx = max(
        i
        for i, call in enumerate(sequence)
        if call[0] == "emit" and isinstance(call.args[0], ModeRestored)
    )
    summary_idx = next(i for i, call in enumerate(sequence) if call[0] == "summary")

    assert aggregate_audit_idx < mode_restored_emit_idx < summary_idx, (
        f"Aggregate ordering drift: aggregate_audit={aggregate_audit_idx} "
        f"mode_restored_emit={mode_restored_emit_idx} summary={summary_idx}. "
        f"Expected aggregate_audit < mode_restored_emit < summary."
    )


@pytest.mark.asyncio
async def test_restore_mode_final_render_response_is_workspace_ready() -> None:
    skin = _make_skin_mock()
    launcher = _make_launcher_mock([_success_result("App1"), _success_result("App2")])
    mode = _mode(apps=(_app("App1"), _app("App2")))
    hands, *_ = _build_hands(launcher=launcher, skin=skin)

    await hands.restore_mode("coding", mode)

    skin.render_response.assert_called_once_with("Workspace ready.")


@pytest.mark.asyncio
async def test_restore_mode_aggregate_audit_result_is_success_when_all_launched() -> None:
    audit = _make_audit_mock()
    launcher = _make_launcher_mock([_success_result("App1"), _success_result("App2")])
    mode = _mode(apps=(_app("App1"), _app("App2")))
    hands, *_ = _build_hands(launcher=launcher, audit=audit)

    await hands.restore_mode("coding", mode)

    aggregate_call = next(
        c
        for c in audit.log_action.call_args_list
        if c.kwargs.get("action_type") is ActionType.MODE_RESTORE
    )
    assert aggregate_call.kwargs["result"] == RESULT_SUCCESS


@pytest.mark.asyncio
async def test_restore_mode_aggregate_audit_target_is_mode_stem_not_display_name() -> None:
    """``MODE_RESTORE`` audit row uses the stem, NOT ModeConfig.name display label."""
    audit = _make_audit_mock()
    launcher = _make_launcher_mock([_success_result("App1")])
    mode = _mode(stem_display_name="Study Group", apps=(_app("App1"),))
    hands, *_ = _build_hands(launcher=launcher, audit=audit)

    await hands.restore_mode("study-group", mode)

    aggregate_call = next(
        c
        for c in audit.log_action.call_args_list
        if c.kwargs.get("action_type") is ActionType.MODE_RESTORE
    )
    assert aggregate_call.kwargs["target"] == "study-group"
    assert aggregate_call.kwargs["target"] != "Study Group"


@pytest.mark.asyncio
async def test_restore_mode_emits_mode_restored_with_stem_as_mode_name() -> None:
    event_bus = _make_event_bus_mock()
    launcher = _make_launcher_mock([_success_result("App1")])
    mode = _mode(stem_display_name="Study Group", apps=(_app("App1"),))
    hands, *_ = _build_hands(launcher=launcher, event_bus=event_bus)

    await hands.restore_mode("study-group", mode)

    mode_restored = next(
        c.args[0] for c in event_bus.emit.call_args_list if isinstance(c.args[0], ModeRestored)
    )
    assert mode_restored.mode_name == "study-group"


@pytest.mark.asyncio
async def test_restore_mode_emits_mode_restored_with_full_apps_launched_tuple() -> None:
    event_bus = _make_event_bus_mock()
    launcher = _make_launcher_mock(
        [_success_result("VS Code"), _success_result("Chrome"), _success_result("Postman")]
    )
    mode = _mode(apps=(_app("VS Code"), _app("Chrome"), _app("Postman")))
    hands, *_ = _build_hands(launcher=launcher, event_bus=event_bus)

    await hands.restore_mode("coding", mode)

    mode_restored = next(
        c.args[0] for c in event_bus.emit.call_args_list if isinstance(c.args[0], ModeRestored)
    )
    assert mode_restored.apps_launched == ("VS Code", "Chrome", "Postman")
    assert mode_restored.apps_failed == ()


@pytest.mark.asyncio
async def test_restore_mode_treats_already_running_as_success() -> None:
    """Already-running outcomes (success=True at the launcher) flow cleanly through.

    HandsSystem can't distinguish "fresh launch" from "already running"
    at the ``ActionResult`` boundary — both are ``success=True,
    reason=None``. The end-to-end assertion is that an all-success
    restore renders ``"Workspace ready."`` regardless of which path
    the launcher took.
    """
    skin = _make_skin_mock()
    audit = _make_audit_mock()
    event_bus = _make_event_bus_mock()
    launcher = _make_launcher_mock(
        [_success_result("App1"), _success_result("App2"), _success_result("App3")]
    )
    mode = _mode(apps=(_app("App1"), _app("App2"), _app("App3")))
    hands, *_ = _build_hands(launcher=launcher, skin=skin, event_bus=event_bus, audit=audit)

    await hands.restore_mode("coding", mode)

    skin.render_response.assert_called_once_with("Workspace ready.")
    aggregate_call = next(
        c
        for c in audit.log_action.call_args_list
        if c.kwargs.get("action_type") is ActionType.MODE_RESTORE
    )
    assert aggregate_call.kwargs["result"] == RESULT_SUCCESS
    mode_restored = next(
        c.args[0] for c in event_bus.emit.call_args_list if isinstance(c.args[0], ModeRestored)
    )
    assert mode_restored.apps_failed == ()


# ===========================================================================
# Block 3 — Partial path: some apps fail (AC #7)
# ===========================================================================


@pytest.mark.asyncio
async def test_restore_mode_partial_2_of_3_succeed_continues() -> None:
    launcher = _make_launcher_mock(
        [_success_result("VS Code"), _failure_result("Postman"), _success_result("Chrome")]
    )
    mode = _mode(apps=(_app("VS Code"), _app("Postman"), _app("Chrome")))
    hands, *_ = _build_hands(launcher=launcher)

    results = await hands.restore_mode("coding", mode)

    assert launcher.launch_app.call_count == 3
    assert results[0].success
    assert not results[1].success
    assert results[2].success


@pytest.mark.asyncio
async def test_restore_mode_partial_final_line_names_first_failure() -> None:
    skin = _make_skin_mock()
    launcher = _make_launcher_mock([_success_result("VS Code"), _failure_result("Postman")])
    mode = _mode(apps=(_app("VS Code"), _app("Postman")))
    hands, *_ = _build_hands(launcher=launcher, skin=skin)

    await hands.restore_mode("coding", mode)

    skin.render_response.assert_called_once_with("Workspace partially ready. Postman was skipped.")


@pytest.mark.asyncio
async def test_restore_mode_partial_final_line_appends_count_when_multiple_fail() -> None:
    skin = _make_skin_mock()
    launcher = _make_launcher_mock(
        [
            _success_result("VS Code"),
            _failure_result("Postman"),
            _failure_result("Slack"),
            _failure_result("Notion"),
        ]
    )
    mode = _mode(apps=(_app("VS Code"), _app("Postman"), _app("Slack"), _app("Notion")))
    hands, *_ = _build_hands(launcher=launcher, skin=skin)

    await hands.restore_mode("coding", mode)

    skin.render_response.assert_called_once_with(
        "Workspace partially ready. Postman was skipped. (2 more skipped — see status for details.)"
    )


@pytest.mark.asyncio
async def test_restore_mode_partial_aggregate_audit_result_is_partial() -> None:
    audit = _make_audit_mock()
    launcher = _make_launcher_mock([_success_result("VS Code"), _failure_result("Postman")])
    mode = _mode(apps=(_app("VS Code"), _app("Postman")))
    hands, *_ = _build_hands(launcher=launcher, audit=audit)

    await hands.restore_mode("coding", mode)

    aggregate_call = next(
        c
        for c in audit.log_action.call_args_list
        if c.kwargs.get("action_type") is ActionType.MODE_RESTORE
    )
    assert aggregate_call.kwargs["result"] == "partial"


@pytest.mark.asyncio
async def test_restore_mode_partial_emits_mode_restored_with_split_tuples() -> None:
    event_bus = _make_event_bus_mock()
    launcher = _make_launcher_mock(
        [_success_result("VS Code"), _failure_result("Postman"), _success_result("Chrome")]
    )
    mode = _mode(apps=(_app("VS Code"), _app("Postman"), _app("Chrome")))
    hands, *_ = _build_hands(launcher=launcher, event_bus=event_bus)

    await hands.restore_mode("coding", mode)

    mode_restored = next(
        c.args[0] for c in event_bus.emit.call_args_list if isinstance(c.args[0], ModeRestored)
    )
    assert mode_restored.apps_launched == ("VS Code", "Chrome")
    assert mode_restored.apps_failed == ("Postman",)


# ===========================================================================
# Block 4 — Total-failure path: every app fails (AC #7)
# ===========================================================================


@pytest.mark.asyncio
async def test_restore_mode_total_failure_final_line_includes_mode_edit_stem_hint() -> None:
    """Final line uses the STEM (lowercased), NOT the display label."""
    skin = _make_skin_mock()
    launcher = _make_launcher_mock([_failure_result("App1"), _failure_result("App2")])
    mode = _mode(stem_display_name="Coding", apps=(_app("App1"), _app("App2")))
    hands, *_ = _build_hands(launcher=launcher, skin=skin)

    await hands.restore_mode("coding", mode)

    skin.render_response.assert_called_once_with(
        "No apps could be launched. Check mode config: mode edit coding"
    )


@pytest.mark.asyncio
async def test_restore_mode_total_failure_aggregate_audit_result_is_failed() -> None:
    audit = _make_audit_mock()
    launcher = _make_launcher_mock([_failure_result("App1"), _failure_result("App2")])
    mode = _mode(apps=(_app("App1"), _app("App2")))
    hands, *_ = _build_hands(launcher=launcher, audit=audit)

    await hands.restore_mode("coding", mode)

    aggregate_call = next(
        c
        for c in audit.log_action.call_args_list
        if c.kwargs.get("action_type") is ActionType.MODE_RESTORE
    )
    assert aggregate_call.kwargs["result"] == RESULT_FAILED


@pytest.mark.asyncio
async def test_restore_mode_total_failure_still_emits_mode_restored_event() -> None:
    event_bus = _make_event_bus_mock()
    launcher = _make_launcher_mock([_failure_result("App1"), _failure_result("App2")])
    mode = _mode(apps=(_app("App1"), _app("App2")))
    hands, *_ = _build_hands(launcher=launcher, event_bus=event_bus)

    await hands.restore_mode("coding", mode)

    mode_restored = next(
        c.args[0] for c in event_bus.emit.call_args_list if isinstance(c.args[0], ModeRestored)
    )
    assert mode_restored.mode_name == "coding"
    assert mode_restored.apps_launched == ()
    assert mode_restored.apps_failed == ("App1", "App2")


# ===========================================================================
# Block 5 — Audit-failure isolation (AC #19) — real AuditLogger over failing storage
# ===========================================================================


class _FailingStorageEngine:
    """Minimal storage stand-in: every ``execute`` raises ``StorageError``.

    Mirrors only the surface ``AuditLogger`` touches. Used to exercise
    ``AuditLogger``'s internal ``StorageError``-swallow path without
    patching ``audit.log_action`` directly (which would contradict the
    AC #19 "Hands does not wrap" contract).
    """

    async def execute(self, sql: str, params: tuple[object, ...]) -> None:
        del sql, params
        raise StorageError("simulated storage failure")


@pytest.mark.asyncio
async def test_restore_mode_continues_when_audit_storage_fails_for_every_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """All Hands flow continues when AuditLogger swallows StorageError internally."""
    real_audit = AuditLogger(storage=_FailingStorageEngine())  # type: ignore[arg-type]
    skin = _make_skin_mock()
    event_bus = _make_event_bus_mock()
    launcher = _make_launcher_mock(
        [_success_result("App1"), _success_result("App2"), _success_result("App3")]
    )
    mode = _mode(apps=(_app("App1"), _app("App2"), _app("App3")))
    hands = HandsSystem(launcher=launcher, skin=skin, event_bus=event_bus, audit=real_audit)

    with caplog.at_level("WARNING", logger="nova.core.audit"):
        results = await hands.restore_mode("coding", mode)

    # All 3 launches happened.
    assert launcher.launch_app.call_count == 3
    # All 3 per-app render lines fired.
    assert skin.render_progress.call_count == 3
    # 3 AppLaunched events + 1 ModeRestored aggregate = 4 emissions.
    assert event_bus.emit.call_count == 4
    # Final line still rendered.
    skin.render_response.assert_called_once_with("Workspace ready.")
    # Returned the full results list.
    assert len(results) == 3
    # AuditLogger logged its swallow at WARNING (per-app + aggregate = 4 rows).
    audit_warnings = [r for r in caplog.records if r.name == "nova.core.audit"]
    assert len(audit_warnings) >= 4


def test_restore_mode_does_not_wrap_audit_log_action_in_try_except() -> None:
    """AST guard — no ``try/except`` block in ``restore_mode`` may contain a ``log_action`` call.

    Locks the AC #19 unwrapped-audit contract: a future maintainer who
    "wraps for safety" would also catch programmer errors AuditLogger
    raises by design (TypeError / ValueError from boundary checks).

    Walks ``body`` AND ``handlers`` AND ``orelse`` AND ``finalbody`` of
    every ``ast.Try`` node — closes /bmad-code-review patch #5 (BH+EC).
    The previous walker only covered ``body`` so a ``log_action`` call
    inside an ``except`` arm or a ``finally:`` clause would silently
    slip past.
    """
    source = textwrap.dedent(inspect.getsource(HandsSystem.restore_mode))
    tree = ast.parse(source)

    log_action_in_try: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        # Cover every code-bearing slot of a try statement:
        # body, orelse (the else-clause), finalbody, AND each handler's body.
        slots: list[tuple[str, list[ast.stmt]]] = [
            ("body", node.body),
            ("orelse", node.orelse),
            ("finalbody", node.finalbody),
        ]
        for handler in node.handlers:
            slots.append((f"handler({getattr(handler.type, 'id', '?')})", handler.body))
        for slot_name, stmts in slots:
            for stmt in ast.walk(ast.Module(body=stmts, type_ignores=[])):
                if (
                    isinstance(stmt, ast.Call)
                    and isinstance(stmt.func, ast.Attribute)
                    and stmt.func.attr == "log_action"
                ):
                    log_action_in_try.append((node.lineno, slot_name))

    assert not log_action_in_try, (
        f"audit.log_action found inside try/{{body,handler,else,finally}} "
        f"in restore_mode at: {log_action_in_try}. The AC #19 contract "
        f"requires the audit call to be unwrapped — AuditLogger swallows "
        f"StorageError internally; a wrapping except (in any slot) would "
        f"also catch programmer errors that MUST surface."
    )


@pytest.mark.asyncio
async def test_restore_mode_propagates_audit_value_error_from_bad_result_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If AuditLogger raises ValueError (boundary check), it propagates out of restore_mode."""
    audit = MagicMock(spec=AuditLogger)
    audit.log_action = AsyncMock(side_effect=ValueError("result must be a non-empty string"))
    skin = _make_skin_mock()
    event_bus = _make_event_bus_mock()
    launcher = _make_launcher_mock([_success_result("App1")])
    mode = _mode(apps=(_app("App1"),))
    hands = HandsSystem(launcher=launcher, skin=skin, event_bus=event_bus, audit=audit)

    with pytest.raises(ValueError, match="non-empty string"):
        await hands.restore_mode("coding", mode)


# ===========================================================================
# Block 6 — URL deferral notice (AC #7 step 2)
# ===========================================================================


@pytest.mark.asyncio
async def test_restore_mode_logs_url_count_when_mode_has_urls(
    caplog: pytest.LogCaptureFixture,
) -> None:
    launcher = _make_launcher_mock([_success_result("App1")])
    mode = ModeConfig(
        name="Coding",
        apps=(_app("App1"),),
        urls=("https://example.com/secret", "https://example.com/internal"),
    )
    hands, *_ = _build_hands(launcher=launcher)

    with caplog.at_level("INFO", logger="nova.systems.hands"):
        await hands.restore_mode("coding", mode)

    url_logs = [
        r
        for r in caplog.records
        if r.name == "nova.systems.hands" and "URL opening lands in Story 6.5" in r.message
    ]
    assert len(url_logs) == 1
    record = url_logs[0]
    extras: dict[str, Any] = {k: getattr(record, k) for k in ("mode_stem", "url_count")}
    assert extras == {"mode_stem": "coding", "url_count": 2}
    # URLs themselves NEVER appear in the message or extras.
    assert "https://example.com/secret" not in record.message
    assert "https://example.com/internal" not in record.message


@pytest.mark.asyncio
async def test_restore_mode_no_url_log_when_zero_urls(
    caplog: pytest.LogCaptureFixture,
) -> None:
    launcher = _make_launcher_mock([_success_result("App1")])
    mode = _mode(apps=(_app("App1"),))  # default empty urls
    hands, *_ = _build_hands(launcher=launcher)

    with caplog.at_level("INFO", logger="nova.systems.hands"):
        await hands.restore_mode("coding", mode)

    url_logs = [r for r in caplog.records if "URL opening lands" in r.message]
    assert len(url_logs) == 0


# ===========================================================================
# Block 7 — Defensive preconditions (AC #7 step 1)
# ===========================================================================


@pytest.mark.asyncio
async def test_restore_mode_raises_assertion_error_on_empty_apps_tuple() -> None:
    invalid_mode = ModeConfig(name="Coding", apps=())
    hands, *_ = _build_hands()

    with pytest.raises(AssertionError, match="loader contract"):
        await hands.restore_mode("coding", invalid_mode)


@pytest.mark.asyncio
async def test_restore_mode_raises_assertion_error_on_empty_mode_stem() -> None:
    valid_mode = _mode(apps=(_app("App1"),))
    hands, *_ = _build_hands()

    with pytest.raises(AssertionError, match="mode_stem"):
        await hands.restore_mode("", valid_mode)


@pytest.mark.asyncio
async def test_restore_mode_raises_assertion_error_on_whitespace_mode_stem() -> None:
    valid_mode = _mode(apps=(_app("App1"),))
    hands, *_ = _build_hands()

    with pytest.raises(AssertionError, match="mode_stem"):
        await hands.restore_mode("   ", valid_mode)


# ===========================================================================
# Block 8 — Single-app boundary
# ===========================================================================


@pytest.mark.asyncio
async def test_restore_mode_single_app_success_renders_workspace_ready() -> None:
    skin = _make_skin_mock()
    launcher = _make_launcher_mock([_success_result("Notepad")])
    mode = _mode(apps=(_app("Notepad"),))
    hands, *_ = _build_hands(launcher=launcher, skin=skin)

    await hands.restore_mode("notes", mode)

    skin.render_response.assert_called_once_with("Workspace ready.")


@pytest.mark.asyncio
async def test_restore_mode_single_app_failure_renders_total_failure_line() -> None:
    skin = _make_skin_mock()
    launcher = _make_launcher_mock([_failure_result("Bogus")])
    mode = _mode(apps=(_app("Bogus"),))
    hands, *_ = _build_hands(launcher=launcher, skin=skin)

    await hands.restore_mode("bogus-mode", mode)

    skin.render_response.assert_called_once_with(
        "No apps could be launched. Check mode config: mode edit bogus-mode"
    )


# ===========================================================================
# Block 9 — Per-app audit details shape (acceptance auditor follow-up)
# ===========================================================================


@pytest.mark.asyncio
async def test_restore_mode_per_app_audit_details_carry_executable_and_reason() -> None:
    """Per-app audit ``details`` must carry ``executable`` AND ``reason``.

    AC #7 step 4 spells the per-app audit ``details`` shape exactly:
    ``{"executable": app.executable, "reason": result.reason}``.
    Without this lock a regression that dropped ``executable`` (or
    swapped to a different key name) would slip past unit tests.
    """
    audit = _make_audit_mock()
    launcher = _make_launcher_mock(
        [_success_result("VS Code"), _failure_result("Postman", reason="not found")]
    )
    mode = _mode(apps=(_app("VS Code", "code.exe"), _app("Postman", "postman.exe")))
    hands, *_ = _build_hands(launcher=launcher, audit=audit)

    await hands.restore_mode("coding", mode)

    per_app_calls = [
        c
        for c in audit.log_action.call_args_list
        if c.kwargs.get("action_type") is ActionType.APP_LAUNCH
    ]
    assert len(per_app_calls) == 2
    # Success row: executable carried, reason is None.
    assert per_app_calls[0].kwargs["details"] == {
        "executable": "code.exe",
        "reason": None,
    }
    # Failure row: executable carried, reason is the failure string.
    assert per_app_calls[1].kwargs["details"] == {
        "executable": "postman.exe",
        "reason": "not found",
    }


# ===========================================================================
# Block 10 — Skin / event isolation (closes review HIGH findings)
# ===========================================================================


@pytest.mark.asyncio
async def test_restore_mode_continues_when_render_progress_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A Skin failure on a per-app render line must not abort the mode loop.

    Closes Edge Case Hunter findings #3 (UnicodeEncodeError on legacy
    consoles) and #4 (broken stdout / Rich internal error). Without
    isolation, the per-app loop aborts mid-mode, the aggregate
    MODE_RESTORE audit + ModeRestored event never fire, and persisted
    state diverges from runtime fan-out.
    """
    skin = _make_skin_mock()
    skin.render_progress = AsyncMock(side_effect=UnicodeEncodeError("ascii", "✓", 0, 1, "bad"))
    audit = _make_audit_mock()
    event_bus = _make_event_bus_mock()
    launcher = _make_launcher_mock([_success_result("App1"), _success_result("App2")])
    mode = _mode(apps=(_app("App1"), _app("App2")))
    hands, *_ = _build_hands(launcher=launcher, skin=skin, event_bus=event_bus, audit=audit)

    with caplog.at_level("ERROR", logger="nova.systems.hands"):
        results = await hands.restore_mode("coding", mode)

    # All 3 per-app surfaces still ran.
    assert launcher.launch_app.call_count == 2
    assert (
        sum(
            1
            for c in audit.log_action.call_args_list
            if c.kwargs.get("action_type") is ActionType.APP_LAUNCH
        )
        == 2
    )
    # Aggregate MODE_RESTORE + ModeRestored emitted despite the render failures.
    aggregate_audit = [
        c
        for c in audit.log_action.call_args_list
        if c.kwargs.get("action_type") is ActionType.MODE_RESTORE
    ]
    assert len(aggregate_audit) == 1
    mode_restored_emits = [
        c for c in event_bus.emit.call_args_list if isinstance(c.args[0], ModeRestored)
    ]
    assert len(mode_restored_emits) == 1
    # Final summary fires (skin.render_response is a separate mock — not failing).
    skin.render_response.assert_called_once()
    assert len(results) == 2
    # Render failures logged at ERROR.
    assert sum(1 for r in caplog.records if "render_progress failed" in r.message) == 2


@pytest.mark.asyncio
async def test_restore_mode_continues_when_app_launched_emit_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A subscriber-raised exception on per-app emit must not abort the loop."""
    event_bus = _make_event_bus_mock()
    event_bus.emit = AsyncMock(side_effect=RuntimeError("subscriber blew up"))
    audit = _make_audit_mock()
    launcher = _make_launcher_mock([_success_result("App1"), _success_result("App2")])
    mode = _mode(apps=(_app("App1"), _app("App2")))
    hands, *_ = _build_hands(launcher=launcher, event_bus=event_bus, audit=audit)

    with caplog.at_level("ERROR", logger="nova.systems.hands"):
        results = await hands.restore_mode("coding", mode)

    assert launcher.launch_app.call_count == 2
    # Aggregate audit row still fired.
    aggregate_audit = [
        c
        for c in audit.log_action.call_args_list
        if c.kwargs.get("action_type") is ActionType.MODE_RESTORE
    ]
    assert len(aggregate_audit) == 1
    assert len(results) == 2
    # Per-app + aggregate emit failures all logged.
    emit_failures = [r for r in caplog.records if "emit failed" in r.message]
    assert len(emit_failures) >= 2  # 2 per-app; aggregate also fails


@pytest.mark.asyncio
async def test_restore_mode_continues_when_render_response_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A Skin failure on the final-line summary must not propagate.

    Without isolation NerveSystem._handle_mode_switch never sets
    ``_active_mode_name`` even though audit + event already say the
    mode was restored.
    """
    skin = _make_skin_mock()
    skin.render_response = AsyncMock(side_effect=BrokenPipeError("stdout closed"))
    launcher = _make_launcher_mock([_success_result("App1")])
    mode = _mode(apps=(_app("App1"),))
    hands, *_ = _build_hands(launcher=launcher, skin=skin)

    with caplog.at_level("ERROR", logger="nova.systems.hands"):
        # Must NOT raise.
        results = await hands.restore_mode("coding", mode)

    assert len(results) == 1
    assert any("render_response failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raising_surface",
    ["render_progress", "per_app_emit", "render_response"],
)
async def test_restore_mode_propagates_cancelled_error_from_isolated_surfaces(
    raising_surface: str,
) -> None:
    """The skin/event isolation try/except must catch ``Exception`` ONLY — never ``BaseException``.

    Closes /bmad-code-review patch #6 (BH#6 + EC#10). A future
    maintainer who "tightens" the handler to ``except BaseException``
    would silently swallow ``CancelledError`` mid-mode-restore, which
    breaks the asyncio cancellation contract (project-context.md:49 —
    "Never swallow asyncio.CancelledError. Cleanup is allowed, but
    cancellation must always be re-raised."). Conversely, a sloppy
    ``except:`` would do the same. Lock the choice with a regression
    test that asserts CancelledError propagates out of every isolated
    surface.
    """
    skin = _make_skin_mock()
    event_bus = _make_event_bus_mock()
    launcher = _make_launcher_mock([_success_result("App1")])

    if raising_surface == "render_progress":
        skin.render_progress = AsyncMock(side_effect=asyncio.CancelledError())
    elif raising_surface == "per_app_emit":
        event_bus.emit = AsyncMock(side_effect=asyncio.CancelledError())
    else:  # render_response
        skin.render_response = AsyncMock(side_effect=asyncio.CancelledError())

    mode = _mode(apps=(_app("App1"),))
    hands, *_ = _build_hands(launcher=launcher, skin=skin, event_bus=event_bus)

    with pytest.raises(asyncio.CancelledError):
        await hands.restore_mode("coding", mode)
