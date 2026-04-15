"""Story 1.8 contract tests for ``nova.core.audit``.

Covers the single audit-write boundary: shape (one public ``async``
method, no read/update/delete surface), happy-path inserts, action-type
vocabulary, result validation, JSON-serialization of ``details``, the
load-bearing observational-failure semantics (``StorageError``
swallowed, everything else propagates), append-only invariants enforced
at module-structure level, type-signature opacity contract, and the
deterministic-clock monkeypatch contract.

Tests use a real ``SqliteStorageEngine`` against a per-test ``tmp_path``
scratch DB (Story 1.4/1.5 precedent — the engine sits beneath audit and
mocking it would lose actual ``INSERT`` semantics, NOT NULL enforcement,
and JSON round-trip behavior). Every test that opens an engine
``await engine.close()`` via ``try/finally`` per project-context.md:104.

AST-based static-analysis gates carry forward the Story 1.6/1.7 lesson:
walk ``ast`` nodes, do not regex source text. The append-only guarantee
and the "only ``log_action`` is public" rule are both locked at the
module-structure level so an accidental edit cannot silently regress
them.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

import pytest

import nova.core.audit as audit_module
from nova.core.audit import (
    RESULT_FAILED,
    RESULT_SKIPPED,
    RESULT_SUCCESS,
    ActionResult,
    AuditLogger,
)
from nova.core.exceptions import StorageError
from nova.core.storage.engine import SqliteStorageEngine
from nova.core.types import ActionType, BriefingState

# ---------------------------------------------------------------------------
# Test doubles + factories
# ---------------------------------------------------------------------------


async def _make_logger(tmp_path: Path) -> tuple[AuditLogger, SqliteStorageEngine]:
    """Build a real engine + ``AuditLogger`` for the test.

    Caller is responsible for ``await engine.close()`` (use try/finally).
    Migrations run so the ``audit_log`` table exists.
    """
    engine = SqliteStorageEngine(tmp_path / "test_audit.db")
    await engine.start()
    await engine.run_migrations()
    return AuditLogger(storage=engine), engine


class _FailingExecuteEngine(SqliteStorageEngine):
    """Real engine subclass whose ``execute`` raises after ``arm()``.

    Lets the failure-mode tests exercise the
    ``except StorageError`` swallow path (and the negative cases:
    non-domain exceptions, ``CancelledError``, etc.) while keeping the
    rest of the engine real (``start``/``run_migrations``/``close``
    behave normally so the row-count side assertions can verify the
    failed write produced no row).

    The fail-mode is **off by default** — ``run_migrations`` itself
    calls ``execute`` repeatedly, so unconditionally raising would trip
    setup before the test ever exercises ``log_action``. The factory
    runs migrations, then calls ``arm()`` to flip the switch.
    """

    def __init__(self, db_path: Path, *, raise_with: BaseException) -> None:
        super().__init__(db_path)
        self._raise_with = raise_with
        self._armed = False
        self.execute_call_count = 0

    def arm(self) -> None:
        self._armed = True

    async def execute(
        self,
        sql: str,
        params: Any = (),
    ) -> None:
        if not self._armed:
            await super().execute(sql, params)
            return
        self.execute_call_count += 1
        raise self._raise_with


async def _make_failing_logger(
    tmp_path: Path, *, raise_with: BaseException
) -> tuple[AuditLogger, _FailingExecuteEngine]:
    engine = _FailingExecuteEngine(tmp_path / "test_audit.db", raise_with=raise_with)
    await engine.start()
    await engine.run_migrations()
    engine.arm()
    return AuditLogger(storage=engine), engine


async def _fetch_audit_rows(engine: SqliteStorageEngine) -> list[dict[str, Any]]:
    rows = await engine.fetchall(
        "SELECT id, timestamp, action_type, target, result, details FROM audit_log ORDER BY id"
    )
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Shape tests (~3)
# ---------------------------------------------------------------------------


async def test_audit_logger_constructor_accepts_keyword_only_storage(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "shape.db")
    await engine.start()
    try:
        await engine.run_migrations()
        logger = AuditLogger(storage=engine)
        assert logger is not None
        # Positional should fail — keyword-only enforces explicit naming.
        with pytest.raises(TypeError):
            AuditLogger(engine)  # type: ignore[misc]
    finally:
        await engine.close()


async def test_audit_logger_only_public_method_is_log_action() -> None:
    """Append-only contract: only ``log_action`` is public on the class."""
    public_methods = [
        name
        for name in dir(AuditLogger)
        if not name.startswith("_") and callable(getattr(AuditLogger, name))
    ]
    assert public_methods == ["log_action"], (
        f"AuditLogger must expose exactly one public method (log_action); found {public_methods}"
    )


async def test_module_exposes_canonical_result_constants_and_action_result_alias() -> None:
    assert RESULT_SUCCESS == "success"
    assert RESULT_FAILED == "failed"
    assert RESULT_SKIPPED == "skipped"
    # ``ActionResult`` is a PEP 695 type alias; unwrap to the underlying
    # ``Literal[...]``. The PEP 695 ``type`` keyword wraps the value in a
    # ``TypeAliasType`` whose ``__value__`` carries the actual annotation.
    underlying = ActionResult.__value__
    assert get_origin(underlying) is Literal
    assert set(get_args(underlying)) == {"success", "failed", "skipped"}


# ---------------------------------------------------------------------------
# Happy-path insert tests (~5)
# ---------------------------------------------------------------------------


async def test_log_action_writes_one_row_with_all_fields(tmp_path: Path) -> None:
    logger, engine = await _make_logger(tmp_path)
    try:
        await logger.log_action(ActionType.APP_LAUNCH, "code.exe", RESULT_SUCCESS)
        rows = await _fetch_audit_rows(engine)
        assert len(rows) == 1
        row = rows[0]
        assert row["action_type"] == "app_launch"
        assert row["target"] == "code.exe"
        assert row["result"] == "success"
        assert row["details"] is None
        assert isinstance(row["timestamp"], str) and row["timestamp"]
        # Timestamp shape: ISO 8601 with +00:00 UTC suffix (per Story 1.3 contract).
        assert row["timestamp"].endswith("+00:00")
    finally:
        await engine.close()


async def test_log_action_accepts_none_target(tmp_path: Path) -> None:
    logger, engine = await _make_logger(tmp_path)
    try:
        await logger.log_action(ActionType.SEED_CAPTURE, None, RESULT_SUCCESS)
        rows = await _fetch_audit_rows(engine)
        assert len(rows) == 1
        assert rows[0]["target"] is None
        assert rows[0]["action_type"] == "seed_capture"
    finally:
        await engine.close()


async def test_log_action_serializes_details_as_compact_json(tmp_path: Path) -> None:
    logger, engine = await _make_logger(tmp_path)
    try:
        await logger.log_action(
            ActionType.DELETION,
            "topic_id_42",
            RESULT_SUCCESS,
            {"items_deleted": 7, "tables": ["sessions", "memory_items"]},
        )
        rows = await _fetch_audit_rows(engine)
        assert len(rows) == 1
        details_str = rows[0]["details"]
        assert isinstance(details_str, str)
        # Compact JSON — no whitespace between tokens.
        assert " " not in details_str
        assert json.loads(details_str) == {
            "items_deleted": 7,
            "tables": ["sessions", "memory_items"],
        }
    finally:
        await engine.close()


async def test_log_action_distinguishes_none_from_empty_dict_details(tmp_path: Path) -> None:
    """``None`` writes NULL; ``{}`` writes the literal ``"{}"``."""
    logger, engine = await _make_logger(tmp_path)
    try:
        await logger.log_action(ActionType.MODE_SWITCH, "coding", RESULT_SUCCESS, None)
        await logger.log_action(ActionType.MODE_SWITCH, "study", RESULT_SUCCESS, {})
        rows = await _fetch_audit_rows(engine)
        assert len(rows) == 2
        assert rows[0]["details"] is None
        assert rows[1]["details"] == "{}"
    finally:
        await engine.close()


async def test_log_action_two_calls_produce_two_rows_with_advancing_ids_and_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    timestamps = iter(["2026-04-15T12:00:00+00:00", "2026-04-15T12:00:01+00:00"])
    monkeypatch.setattr("nova.core.events._utc_now_iso", lambda: next(timestamps))
    logger, engine = await _make_logger(tmp_path)
    try:
        await logger.log_action(ActionType.APP_LAUNCH, "a.exe", RESULT_SUCCESS)
        await logger.log_action(ActionType.APP_LAUNCH, "b.exe", RESULT_SUCCESS)
        rows = await _fetch_audit_rows(engine)
        assert len(rows) == 2
        assert rows[0]["id"] < rows[1]["id"]
        assert rows[0]["timestamp"] == "2026-04-15T12:00:00+00:00"
        assert rows[1]["timestamp"] == "2026-04-15T12:00:01+00:00"
    finally:
        await engine.close()


# ---------------------------------------------------------------------------
# Action-type vocabulary tests (~3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("member", list(ActionType), ids=lambda m: m.name)
async def test_log_action_accepts_every_action_type_member(
    tmp_path: Path, member: ActionType
) -> None:
    """Parametrize over ``list(ActionType)`` so adding a 12th enum member auto-extends coverage."""
    logger, engine = await _make_logger(tmp_path)
    try:
        await logger.log_action(member, "target", RESULT_SUCCESS)
        rows = await _fetch_audit_rows(engine)
        assert len(rows) == 1
        assert rows[0]["action_type"] == str(member)
        # Sanity: ``str(StrEnum_member)`` is the canonical wire value.
        assert rows[0]["action_type"] == member.value
    finally:
        await engine.close()


async def test_log_action_signature_pins_action_type_to_enum() -> None:
    """Mypy-strict surrogate: prove the annotation resolves to exactly ``ActionType``.

    A future edit that loosens the annotation to ``str`` would silently
    let raw-string callers slip through; this test fails first.
    Annotations are lazy-evaluated under
    ``from __future__ import annotations``, so resolve via
    ``typing.get_type_hints`` rather than reading raw strings off
    ``inspect.signature``.
    """
    hints = get_type_hints(AuditLogger.log_action)
    assert hints["action_type"] is ActionType


async def test_briefing_state_member_does_not_satisfy_action_type_at_signature_level() -> None:
    """``BriefingState.FIRST_RUN`` is a different StrEnum.

    Ensure the resolved hint on ``action_type`` pins ``ActionType``.
    """
    hints = get_type_hints(AuditLogger.log_action)
    assert hints["action_type"] is not BriefingState
    assert hints["action_type"] is ActionType


# ---------------------------------------------------------------------------
# Result validation tests (~3)
# ---------------------------------------------------------------------------


async def test_log_action_rejects_empty_result(tmp_path: Path) -> None:
    logger, engine = await _make_logger(tmp_path)
    try:
        with pytest.raises(ValueError, match="non-empty"):
            await logger.log_action(ActionType.APP_LAUNCH, "x", "")
        # No row written.
        rows = await _fetch_audit_rows(engine)
        assert rows == []
    finally:
        await engine.close()


async def test_log_action_rejects_whitespace_only_result(tmp_path: Path) -> None:
    logger, engine = await _make_logger(tmp_path)
    try:
        with pytest.raises(ValueError, match="non-empty"):
            await logger.log_action(ActionType.APP_LAUNCH, "x", "   ")
        rows = await _fetch_audit_rows(engine)
        assert rows == []
    finally:
        await engine.close()


async def test_log_action_accepts_custom_non_empty_result(tmp_path: Path) -> None:
    """Non-canonical values like ``"partial"`` are accepted at the API boundary."""
    logger, engine = await _make_logger(tmp_path)
    try:
        await logger.log_action(ActionType.MODE_RESTORE, "coding", "partial")
        rows = await _fetch_audit_rows(engine)
        assert len(rows) == 1
        assert rows[0]["result"] == "partial"
    finally:
        await engine.close()


# ---------------------------------------------------------------------------
# Details serialization tests (~3)
# ---------------------------------------------------------------------------


async def test_log_action_details_none_writes_null(tmp_path: Path) -> None:
    logger, engine = await _make_logger(tmp_path)
    try:
        await logger.log_action(ActionType.APP_FOCUS, "x", RESULT_SUCCESS, None)
        rows = await _fetch_audit_rows(engine)
        assert rows[0]["details"] is None
    finally:
        await engine.close()


async def test_log_action_details_unicode_round_trip_no_escape(tmp_path: Path) -> None:
    """``ensure_ascii=False`` preserves raw UTF-8; no ``\\uXXXX`` sequences."""
    logger, engine = await _make_logger(tmp_path)
    try:
        await logger.log_action(
            ActionType.APP_LAUNCH,
            "protected_app",
            RESULT_SUCCESS,
            {"unicode": "日本語", "nested": {"a": 1}},
        )
        rows = await _fetch_audit_rows(engine)
        details_str = rows[0]["details"]
        assert isinstance(details_str, str)
        assert "日本語" in details_str
        assert "\\u" not in details_str
        assert json.loads(details_str)["unicode"] == "日本語"
    finally:
        await engine.close()


async def test_log_action_details_non_serializable_raises_typeerror_and_writes_no_row(
    tmp_path: Path,
) -> None:
    """``TypeError`` from ``json.dumps`` propagates; row is NOT inserted."""
    logger, engine = await _make_logger(tmp_path)
    try:
        with pytest.raises(TypeError):
            await logger.log_action(
                ActionType.APP_LAUNCH,
                "x",
                RESULT_SUCCESS,
                {"bad": datetime.now(UTC)},
            )
        rows = await _fetch_audit_rows(engine)
        assert rows == []
    finally:
        await engine.close()


# ---------------------------------------------------------------------------
# Observational-failure-mode tests (~4)  — THE most important behavior
# ---------------------------------------------------------------------------


async def test_log_action_swallows_storage_error_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``StorageError`` from engine → swallowed, WARNING logged, returns None.

    Caller's ``await`` does NOT raise. The WARNING record carries ONLY
    ``action_type`` and ``result`` in ``extra`` — neither ``target``
    nor ``details`` (privacy boundary: ``target`` is opaque-by-caller-
    contract that ``AuditLogger`` does not enforce, so dropping it from
    the failure-log path preserves opacity by construction).
    """
    err = StorageError("simulated DB lock contention")
    logger, engine = await _make_failing_logger(tmp_path, raise_with=err)
    try:
        with caplog.at_level(logging.WARNING, logger="nova.core.audit"):
            # ``log_action`` returns None — assigning would be a mypy
            # ``func-returns-value`` violation. The implicit assertion is
            # that the await raises nothing.
            await logger.log_action(
                ActionType.APP_LAUNCH,
                "code.exe",
                RESULT_SUCCESS,
                {"sensitive_payload_field": "value"},
            )
        # Engine was called once (write was attempted).
        assert engine.execute_call_count == 1
        # Exactly one WARNING from this module.
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and r.name == "nova.core.audit"
        ]
        assert len(warnings) == 1
        record = warnings[0]
        assert "audit write failed" in record.getMessage()
        # Structured ``extra`` carries the two opacity-safe canonical fields.
        assert record.action_type == "app_launch"  # type: ignore[attr-defined]
        assert record.result == "success"  # type: ignore[attr-defined]
        # ``target`` and ``details`` are NEVER on the log record.
        assert not hasattr(record, "target")
        assert not hasattr(record, "details")
        # Traceback was attached (exc_info=True).
        assert record.exc_info is not None
    finally:
        await engine.close()


async def test_log_action_failure_log_is_warning_not_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Audit-write failure is degraded behavior (WARNING), NOT a system failure (ERROR).

    Locks the choice of ``logger.warning(..., exc_info=True)`` over
    ``logger.exception(...)`` — the latter would silently log at ERROR.
    """
    err = StorageError("disk full")
    logger, engine = await _make_failing_logger(tmp_path, raise_with=err)
    try:
        with caplog.at_level(logging.DEBUG, logger="nova.core.audit"):
            await logger.log_action(ActionType.MODE_SWITCH, "coding", RESULT_FAILED)
        records = [r for r in caplog.records if r.name == "nova.core.audit"]
        # Exactly one record — at WARNING. No ERROR record was emitted.
        levels = [r.levelno for r in records]
        assert logging.ERROR not in levels
        assert logging.WARNING in levels
    finally:
        await engine.close()


async def test_log_action_propagates_non_domain_exception(tmp_path: Path) -> None:
    """A ``RuntimeError`` from the engine is a translation bug — propagates."""
    err = RuntimeError("some unrelated bug")
    logger, engine = await _make_failing_logger(tmp_path, raise_with=err)
    try:
        with pytest.raises(RuntimeError, match="some unrelated bug"):
            await logger.log_action(ActionType.APP_LAUNCH, "x", RESULT_SUCCESS)
    finally:
        await engine.close()


async def test_log_action_propagates_cancelled_error(tmp_path: Path) -> None:
    """``asyncio.CancelledError`` always propagates (project-context.md:49)."""
    err = asyncio.CancelledError()
    logger, engine = await _make_failing_logger(tmp_path, raise_with=err)
    try:
        with pytest.raises(asyncio.CancelledError):
            await logger.log_action(ActionType.APP_LAUNCH, "x", RESULT_SUCCESS)
    finally:
        await engine.close()


# ---------------------------------------------------------------------------
# Append-only contract tests (~2)
# ---------------------------------------------------------------------------


async def test_audit_logger_class_has_no_mutating_methods_beyond_log_action() -> None:
    """AST guard: only public method on ``AuditLogger`` is ``log_action``.

    Walks the class body and asserts nothing public besides ``log_action``
    is defined. Catches a future edit that tries to bolt on
    ``update_action`` / ``delete_action`` / ``clear`` / etc.
    """
    source_path = inspect.getsourcefile(audit_module)
    assert source_path is not None
    tree = ast.parse(Path(source_path).read_text(encoding="utf-8"))

    class_defs = [
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "AuditLogger"
    ]
    assert len(class_defs) == 1
    audit_class = class_defs[0]

    func_types = (ast.FunctionDef, ast.AsyncFunctionDef)
    public_method_names: list[str] = [
        node.name
        for node in audit_class.body
        if isinstance(node, func_types) and not node.name.startswith("_")
    ]

    assert public_method_names == ["log_action"], (
        f"AuditLogger class body defines unexpected public methods: {public_method_names}"
    )


async def test_audit_module_uses_only_insert_sql_no_update_or_delete() -> None:
    """AST guard: no UPDATE/DELETE/REPLACE/TRUNCATE/ALTER/DROP against ``audit_log``.

    Walks the module's AST for ``ast.Constant(str)`` nodes and ensures
    none contain a forbidden mutation verb adjacent to ``audit_log``.
    Carries forward Story 1.6/1.7 lesson: AST > regex on source text
    (regex would false-positive on docstrings; AST scopes to actual
    string-literal values).
    """
    source_path = inspect.getsourcefile(audit_module)
    assert source_path is not None
    tree = ast.parse(Path(source_path).read_text(encoding="utf-8"))

    forbidden = re.compile(
        r"\b(UPDATE|DELETE|REPLACE|TRUNCATE|ALTER|DROP)\b\s+(?:.*?)?audit_log",
        re.IGNORECASE | re.DOTALL,
    )

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not forbidden.search(node.value), (
                f"Forbidden mutation SQL against audit_log found in audit.py: {node.value!r}"
            )

    # Positive sanity: at least one ``INSERT INTO audit_log`` constant exists.
    insert_re = re.compile(r"\bINSERT\s+INTO\s+audit_log\b", re.IGNORECASE)
    found_insert = any(
        isinstance(n, ast.Constant) and isinstance(n.value, str) and insert_re.search(n.value)
        for n in ast.walk(tree)
    )
    assert found_insert, "Expected at least one INSERT INTO audit_log SQL constant in audit.py"


# ---------------------------------------------------------------------------
# Type signature tests (~2)
# ---------------------------------------------------------------------------


async def test_log_action_target_is_typed_as_optional_str_only() -> None:
    """Excluded-context boundary: ``target`` is ``str | None``.

    No struct shape that could smuggle ``WindowContext`` fields.
    """
    hints = get_type_hints(AuditLogger.log_action)
    annotation = hints["target"]
    # PEP 604 ``str | None`` resolves to ``Union[str, None]`` via get_type_hints.
    # PEP 604 ``X | None`` syntax produces a ``types.UnionType`` origin
    # under Python 3.10+; ``typing.Union[...]`` produces ``typing.Union``.
    # Accept either since both are semantically equivalent.
    assert get_origin(annotation) in {Union, UnionType}
    assert set(get_args(annotation)) == {str, type(None)}


async def test_log_action_details_is_typed_as_optional_mapping_str_object_only() -> None:
    """``details`` is ``Mapping[str, object] | None`` — read-only contract, no Any."""
    hints = get_type_hints(AuditLogger.log_action)
    annotation = hints["details"]
    # PEP 604 ``X | None`` syntax produces a ``types.UnionType`` origin
    # under Python 3.10+; ``typing.Union[...]`` produces ``typing.Union``.
    # Accept either since both are semantically equivalent.
    assert get_origin(annotation) in {Union, UnionType}
    args = get_args(annotation)
    assert type(None) in args
    # Find the non-None arg (the Mapping[str, object] form).
    mapping_arg = next(a for a in args if a is not type(None))
    assert get_origin(mapping_arg) is Mapping
    assert get_args(mapping_arg) == (str, object)


# ---------------------------------------------------------------------------
# Concurrency test (~1)
# ---------------------------------------------------------------------------


async def test_log_action_concurrent_writes_all_land(tmp_path: Path) -> None:
    """``asyncio.gather`` of three writes produces three rows; no torn writes."""
    logger, engine = await _make_logger(tmp_path)
    try:
        await asyncio.gather(
            logger.log_action(ActionType.APP_LAUNCH, "a.exe", RESULT_SUCCESS),
            logger.log_action(ActionType.APP_LAUNCH, "b.exe", RESULT_SUCCESS),
            logger.log_action(ActionType.APP_LAUNCH, "c.exe", RESULT_SUCCESS),
        )
        rows = await _fetch_audit_rows(engine)
        assert len(rows) == 3
        targets = sorted(row["target"] for row in rows)
        assert targets == ["a.exe", "b.exe", "c.exe"]
        # Every row has a populated, intact action_type/result.
        for row in rows:
            assert row["action_type"] == "app_launch"
            assert row["result"] == "success"
    finally:
        await engine.close()


# ---------------------------------------------------------------------------
# Monkeypatch-contract regression test (~1) — locks the module-call
# indirection for ``events._utc_now_iso``
# ---------------------------------------------------------------------------


async def test_persisted_timestamp_uses_monkeypatched_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Monkeypatching ``nova.core.events._utc_now_iso`` propagates into audit.py.

    Locks the module-call indirection (``from nova.core import events`` +
    ``events._utc_now_iso()``) against accidental refactors to
    ``from nova.core.events import _utc_now_iso``, which would freeze the
    local binding at import time and silently defeat the patch.
    """
    sentinel = "2099-12-31T23:59:59+00:00"
    monkeypatch.setattr("nova.core.events._utc_now_iso", lambda: sentinel)

    logger, engine = await _make_logger(tmp_path)
    try:
        await logger.log_action(ActionType.APP_LAUNCH, "x", RESULT_SUCCESS)
        rows = await _fetch_audit_rows(engine)
        assert len(rows) == 1
        assert rows[0]["timestamp"] == sentinel
    finally:
        await engine.close()


# ---------------------------------------------------------------------------
# Sanity: the events module is what audit.py uses (not a frozen local
# binding). Walks the audit.py AST to confirm the required import shape.
# ---------------------------------------------------------------------------


async def test_audit_imports_events_module_not_utc_now_iso_symbol() -> None:
    """Audit must NOT freeze ``_utc_now_iso`` via a direct symbol import.

    A ``from nova.core.events import _utc_now_iso`` form would freeze
    the binding at import time and break the deterministic-clock
    monkeypatch contract (Story 1.3's two-function pattern). Other
    symbols from ``nova.core.events`` (e.g. ``Event``, event dataclasses)
    are NOT subject to the monkeypatch concern and are allowed — this
    guard only fires on the specific clock-function freeze.

    The companion check is that audit imports the ``events`` MODULE so
    its globals are looked up at call time. Either ``from nova.core
    import events`` OR ``import nova.core.events as events`` satisfies
    this; the test accepts both forms.
    """
    source_path = inspect.getsourcefile(audit_module)
    assert source_path is not None
    tree = ast.parse(Path(source_path).read_text(encoding="utf-8"))

    frozen_clock_imports: list[str] = []
    has_module_import = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "nova.core.events":
            # Narrow: only the clock-function freeze breaks the contract.
            for alias in node.names:
                if alias.name == "_utc_now_iso":
                    frozen_clock_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "nova.core":
            for alias in node.names:
                if alias.name == "events":
                    has_module_import = True
        elif isinstance(node, ast.Import):
            # ``import nova.core.events as <name>`` also satisfies the
            # module-call indirection.
            for alias in node.names:
                if alias.name == "nova.core.events":
                    has_module_import = True

    assert has_module_import, (
        "audit.py must import the events MODULE (e.g. "
        "`from nova.core import events`) so the monkeypatch on "
        "`nova.core.events._utc_now_iso` propagates here at call time."
    )
    assert not frozen_clock_imports, (
        "audit.py does `from nova.core.events import _utc_now_iso` — this "
        "freezes the local binding at import time and silently defeats the "
        "deterministic-clock monkeypatch pattern. Call `events._utc_now_iso()` "
        "via the module reference instead."
    )


# ---------------------------------------------------------------------------
# Patches from code review (2026-04-15) — runtime guards + serialization
# normalization
# ---------------------------------------------------------------------------


async def test_log_action_rejects_non_actiontype_at_runtime(tmp_path: Path) -> None:
    """``# type: ignore`` callers passing a different StrEnum (or raw str) must be rejected.

    Locks the audit-vocabulary boundary against ``str(other_enum)``
    silently widening ``audit_log.action_type``. Mypy is the primary
    defense; this runtime guard closes the ``# type: ignore`` path.
    """
    logger, engine = await _make_logger(tmp_path)
    try:
        with pytest.raises(TypeError, match="ActionType member"):
            await logger.log_action(BriefingState.FIRST_RUN, "x", RESULT_SUCCESS)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="ActionType member"):
            await logger.log_action("app_launch", "x", RESULT_SUCCESS)  # type: ignore[arg-type]
        # No row written by either rejected call.
        rows = await _fetch_audit_rows(engine)
        assert rows == []
    finally:
        await engine.close()


async def test_log_action_rejects_non_str_result_with_value_error(tmp_path: Path) -> None:
    """Non-``str`` ``result`` raises ``ValueError`` (not ``AttributeError``).

    Covers ``None`` / ``True`` / ``int`` and any other non-``str`` shape.
    The ``isinstance(result, str)`` check fires BEFORE ``.strip()`` so
    non-``str`` callers (via ``# type: ignore`` or an adapter at the
    type boundary) get the documented ``ValueError`` instead of a raw
    ``AttributeError`` from ``None.strip()``.
    """
    logger, engine = await _make_logger(tmp_path)
    try:
        with pytest.raises(ValueError, match="non-empty string"):
            await logger.log_action(ActionType.APP_LAUNCH, "x", None)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="non-empty string"):
            await logger.log_action(ActionType.APP_LAUNCH, "x", True)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="non-empty string"):
            await logger.log_action(ActionType.APP_LAUNCH, "x", 42)  # type: ignore[arg-type]
        rows = await _fetch_audit_rows(engine)
        assert rows == []
    finally:
        await engine.close()


async def test_log_action_rejects_nan_and_infinity_in_details(tmp_path: Path) -> None:
    """``allow_nan=False`` + normalize-to-``TypeError`` for non-finite floats.

    Without ``allow_nan=False``, ``json.dumps`` would silently emit
    non-standard ``NaN`` / ``Infinity`` tokens that any strict downstream
    JSON parser (Story 5.3 transparency model) would reject when reading
    the row. The audit boundary normalizes the failure to ``TypeError``
    so callers see a single documented exception class for any
    serialization failure.
    """
    logger, engine = await _make_logger(tmp_path)
    try:
        with pytest.raises(TypeError, match="JSON-serializable"):
            await logger.log_action(ActionType.APP_LAUNCH, "x", RESULT_SUCCESS, {"v": float("nan")})
        with pytest.raises(TypeError, match="JSON-serializable"):
            await logger.log_action(ActionType.APP_LAUNCH, "x", RESULT_SUCCESS, {"v": float("inf")})
        with pytest.raises(TypeError, match="JSON-serializable"):
            await logger.log_action(
                ActionType.APP_LAUNCH, "x", RESULT_SUCCESS, {"v": float("-inf")}
            )
        rows = await _fetch_audit_rows(engine)
        assert rows == []
    finally:
        await engine.close()


async def test_log_action_rejects_circular_reference_in_details_as_typeerror(
    tmp_path: Path,
) -> None:
    """``json.dumps`` raises ``ValueError`` on circular refs; audit normalizes to ``TypeError``.

    Single documented exception class for any caller-supplied bad
    payload. The chained ``__cause__`` carries the original
    ``ValueError`` for diagnostics.
    """
    logger, engine = await _make_logger(tmp_path)
    try:
        bad: dict[str, object] = {"x": 1}
        bad["self"] = bad
        with pytest.raises(TypeError, match="JSON-serializable") as excinfo:
            await logger.log_action(ActionType.APP_LAUNCH, "x", RESULT_SUCCESS, bad)
        # The original ValueError is chained for diagnostic purposes.
        assert isinstance(excinfo.value.__cause__, ValueError)
        rows = await _fetch_audit_rows(engine)
        assert rows == []
    finally:
        await engine.close()
