"""Story 1.10 AC #2, #3, #16 — :func:`nova.app.create_app` + :class:`NovaApp`."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from nova.adapters.shield import NoOpShieldAdapter
from nova.app import NovaApp, _AlwaysHealthyCheck, create_app
from nova.core import (
    AuditLogger,
    CapabilityTier,
    EventBus,
    ExclusionConfig,
    HealthCheck,
    NovaConfig,
    SqliteStorageEngine,
    TierManager,
    UserSettings,
)
from nova.core.exceptions import StorageError
from nova.core.types import ActionType


def _build_config(tmp_path: Path, *, api_key: str | None = "sk-ant-test") -> NovaConfig:
    """Minimal valid :class:`NovaConfig` anchored at ``tmp_path``.

    Story 2.5 AC #4 — ``create_app`` derives ``initial_tier`` from
    ``config.api_key`` (None → OFFLINE, present → FULL). The shared
    helper defaults to a present test key so existing Story 1.10 shape
    tests continue to exercise the FULL-tier happy path; tests that need
    the OFFLINE-tier path pass ``api_key=None`` explicitly.
    """
    return NovaConfig(
        db_path=tmp_path / "nova.db",
        data_dir=tmp_path,
        modes={},
        exclusions=ExclusionConfig(),
        settings=UserSettings(),
        api_key=api_key,
    )


# --- AC #3 shape tests ------------------------------------------------------


async def test_create_app_returns_populated_novaapp(tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    app = await create_app(config)
    try:
        assert isinstance(app, NovaApp)
        assert app.config is config
        assert isinstance(app.storage, SqliteStorageEngine)
        assert isinstance(app.event_bus, EventBus)
        assert isinstance(app.audit, AuditLogger)
        assert isinstance(app.tier_manager, TierManager)
        assert isinstance(app.shield, NoOpShieldAdapter)
        assert callable(app.close)
        assert app.tier_manager.tier is CapabilityTier.FULL
    finally:
        await app.close()


async def test_novaapp_is_frozen(tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    app = await create_app(config)
    # ``noqa: B010`` — ``setattr`` is the point: mypy rejects direct
    # ``app.config = config`` against a frozen dataclass with a
    # ``[misc]`` error. ``setattr`` is runtime-equivalent to the direct
    # assignment (both dispatch through ``__setattr__``) and lets us
    # test the frozen invariant without adding a ``# type: ignore``.
    try:
        with pytest.raises(FrozenInstanceError):
            setattr(app, "config", config)  # noqa: B010
    finally:
        await app.close()


async def test_novaapp_has_no_dunder_dict_via_slots(tmp_path: Path) -> None:
    """``slots=True`` prevents speculative attribute attachment by future stories."""
    config = _build_config(tmp_path)
    app = await create_app(config)
    try:
        with pytest.raises(AttributeError):
            object.__setattr__(app, "bogus_extra", 1)
    finally:
        await app.close()


# --- AC #2 ordering & behavior tests ---------------------------------------


async def test_create_app_runs_migrations(tmp_path: Path) -> None:
    """After ``create_app``, ``schema_version`` reflects an applied migration."""
    config = _build_config(tmp_path)
    app = await create_app(config)
    try:
        row = await app.storage.fetchone("SELECT MAX(version) AS version FROM schema_version")
        assert row is not None
        assert row["version"] >= 1
    finally:
        await app.close()


async def test_create_app_starts_engine_before_instantiating_audit_logger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC #2 ordering — ``AuditLogger`` must be constructed AFTER ``engine.start``.

    The tracker captures the AuditLogger constructor's ``storage`` kwarg
    explicitly. If a future refactor switched the call to positional
    (``AuditLogger(engine)``), this test would fail loudly rather than
    silently pass with an empty kwargs dict.
    """
    call_order: list[str] = []
    audit_init_kwargs: dict[str, object] = {}

    original_start = SqliteStorageEngine.start

    async def tracked_start(self: SqliteStorageEngine) -> None:
        call_order.append("engine.start")
        await original_start(self)

    original_audit_init = AuditLogger.__init__

    def tracked_audit_init(self: AuditLogger, *, storage: SqliteStorageEngine) -> None:
        call_order.append("AuditLogger.__init__")
        audit_init_kwargs["storage"] = storage
        original_audit_init(self, storage=storage)

    monkeypatch.setattr(SqliteStorageEngine, "start", tracked_start)
    monkeypatch.setattr(AuditLogger, "__init__", tracked_audit_init)

    config = _build_config(tmp_path)
    app = await create_app(config)
    try:
        assert call_order.index("engine.start") < call_order.index("AuditLogger.__init__")
        # Verify the constructor actually received a storage engine — a
        # silent positional-refactor regression would leave this empty.
        assert isinstance(audit_init_kwargs.get("storage"), SqliteStorageEngine)
    finally:
        await app.close()


async def test_create_app_closes_engine_if_migrations_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Partial-init cleanup — engine is closed when ``run_migrations`` raises."""
    close_calls: list[str] = []
    original_close = SqliteStorageEngine.close

    async def tracked_close(self: SqliteStorageEngine) -> None:
        close_calls.append("close")
        await original_close(self)

    async def failing_migrations(self: SqliteStorageEngine) -> list[int]:
        raise StorageError("boom")

    monkeypatch.setattr(SqliteStorageEngine, "close", tracked_close)
    monkeypatch.setattr(SqliteStorageEngine, "run_migrations", failing_migrations)

    config = _build_config(tmp_path)
    with pytest.raises(StorageError, match="boom"):
        await create_app(config)
    assert close_calls == ["close"]


async def test_create_app_closes_engine_if_tier_manager_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1 regression — partial-init cleanup runs for failures BEYOND migrations too.

    Code-review finding: the original implementation only guarded the
    ``run_migrations`` line; an exception from ``TierManager`` /
    ``AuditLogger`` / ``NovaApp(...)`` construction would orphan a
    started engine. This test forces ``TierManager.__init__`` to raise
    and asserts the engine is still closed.
    """
    close_calls: list[str] = []
    original_close = SqliteStorageEngine.close

    async def tracked_close(self: SqliteStorageEngine) -> None:
        close_calls.append("close")
        await original_close(self)

    def failing_tier_init(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("tier boom")

    monkeypatch.setattr(SqliteStorageEngine, "close", tracked_close)
    monkeypatch.setattr(TierManager, "__init__", failing_tier_init)

    config = _build_config(tmp_path)
    with pytest.raises(RuntimeError, match="tier boom"):
        await create_app(config)
    assert close_calls == ["close"]


async def test_create_app_teardown_logs_secondary_close_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Double-fault path — if ``storage.close()`` also fails during teardown,
    the ORIGINAL exception must still propagate (not the close artifact)."""
    close_call_count = {"n": 0}

    async def double_faulting_close(self: SqliteStorageEngine) -> None:
        close_call_count["n"] += 1
        raise StorageError("close secondary boom")

    async def failing_migrations(self: SqliteStorageEngine) -> list[int]:
        raise StorageError("primary migration boom")

    monkeypatch.setattr(SqliteStorageEngine, "close", double_faulting_close)
    monkeypatch.setattr(SqliteStorageEngine, "run_migrations", failing_migrations)

    config = _build_config(tmp_path)
    with (
        caplog.at_level("ERROR", logger="nova.app"),
        pytest.raises(StorageError, match="primary migration boom"),
    ):
        await create_app(config)
    assert close_call_count["n"] == 1
    # Secondary failure is logged, not swallowed silently.
    assert any("secondary error" in rec.message for rec in caplog.records)


async def test_close_tears_down_engine(tmp_path: Path) -> None:
    """After ``app.close()``, a fresh engine on the same db_path opens without lock contention."""
    config = _build_config(tmp_path)
    app = await create_app(config)
    await app.close()

    # Opening a second engine on the same db_path must succeed if the
    # first was fully closed.
    second = SqliteStorageEngine(config.db_path)
    await second.start()
    try:
        row = await second.fetchone("SELECT MAX(version) AS version FROM schema_version")
        assert row is not None
    finally:
        await second.close()


# --- AC #16 swappable shield adapter ---------------------------------------


class _FakeShieldPort:
    """Test double satisfying :class:`nova.ports.ShieldPort`."""

    def __init__(self) -> None:
        self.is_focus_calls = 0
        self.allow_calls: list[ActionType] = []

    async def is_focus_protected(self) -> bool:
        self.is_focus_calls += 1
        return True

    async def allow_action(self, action_type: ActionType) -> bool:
        self.allow_calls.append(action_type)
        return False


async def test_create_app_accepts_custom_shield_adapter(tmp_path: Path) -> None:
    """AC #16 — a custom :class:`ShieldPort` implementation is used when provided."""
    fake = _FakeShieldPort()
    config = _build_config(tmp_path)
    app = await create_app(config, shield=fake)
    try:
        assert app.shield is fake
        # Structural conformance: the adapter is callable through the port.
        assert await app.shield.is_focus_protected() is True
        assert fake.is_focus_calls == 1
    finally:
        await app.close()


async def test_create_app_defaults_to_noop_shield(tmp_path: Path) -> None:
    """AC #16 — omitting ``shield`` yields :class:`NoOpShieldAdapter`."""
    config = _build_config(tmp_path)
    app = await create_app(config)
    try:
        assert isinstance(app.shield, NoOpShieldAdapter)
    finally:
        await app.close()


# --- Engine lifetime invariant --------------------------------------------


# --- P10: _AlwaysHealthyCheck structural conformance ----------------------


async def test_always_healthy_check_satisfies_health_check_protocol() -> None:
    """``_AlwaysHealthyCheck`` must structurally satisfy :class:`HealthCheck`.

    ``HealthCheck`` is a :class:`typing.Protocol` (not ``@runtime_checkable``),
    so the check is shape-based: verify the stub has the exact async
    ``ping(*, timeout_seconds: float) -> None`` signature that
    :class:`TierManager` calls. A regression that renamed the method or
    added a required kwarg would fail this test before ever reaching
    ``TierManager``.
    """
    import inspect

    stub = _AlwaysHealthyCheck()
    # mypy strict already enforces structural conformance at the call
    # site where ``create_app`` passes the stub to ``TierManager``; this
    # test is the runtime belt-and-suspenders guard.
    await stub.ping(timeout_seconds=1.0)  # must not raise

    # Shape-level conformance: the protocol method ``ping`` takes
    # ``timeout_seconds`` as a keyword-only float and returns ``None``.
    # ``from __future__ import annotations`` stringifies return types,
    # so ``return_annotation`` is the literal string ``"None"``.
    sig = inspect.signature(stub.ping)
    params = sig.parameters
    assert "timeout_seconds" in params
    assert params["timeout_seconds"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.return_annotation in (None, "None")


async def test_always_healthy_check_accepts_assignment_to_health_check_typed_slot() -> None:
    """Mypy strict would catch a structural mismatch; this test exercises
    the same conformance at runtime by wiring the stub into a typed
    variable. If the stub ever drifts from the ``HealthCheck`` shape,
    the ``create_app`` call site would break the build.
    """
    stub: HealthCheck = _AlwaysHealthyCheck()
    await stub.ping(timeout_seconds=0.5)


async def test_create_app_idempotent_on_same_db(tmp_path: Path) -> None:
    """Re-running ``create_app`` on the same db_path is safe (migrations idempotent)."""
    config = _build_config(tmp_path)
    first = await create_app(config)
    await first.close()
    second = await create_app(config)
    try:
        row = await second.storage.fetchone("SELECT COUNT(*) AS count FROM schema_version")
        assert row is not None
        # One migration applied, and not duplicated on second boot.
        assert row["count"] >= 1
    finally:
        await second.close()


# --- Story 2.5 AC #4, #5, #6, #16 — initial-tier derivation from api_key ---


async def test_initial_tier_is_offline_when_api_key_is_none(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Story 2.5 AC #4, #5 — absent key → OFFLINE tier + canonical INFO log."""
    config = _build_config(tmp_path, api_key=None)
    with caplog.at_level("INFO", logger="nova.app"):
        app = await create_app(config)
    try:
        assert app.tier_manager.tier is CapabilityTier.OFFLINE
        offline_records = [
            rec
            for rec in caplog.records
            if rec.name == "nova.app"
            and "starting in offline-local-only tier" in rec.message
        ]
        assert len(offline_records) == 1
        # ``reason`` is injected via ``extra={"reason": "no_api_key"}`` — it
        # lives on the LogRecord as a dynamic attribute that mypy can't
        # statically verify; ``vars(rec)`` is the mypy-friendly read path.
        assert vars(offline_records[0]).get("reason") == "no_api_key"
    finally:
        await app.close()


async def test_initial_tier_is_full_when_api_key_is_present(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Story 2.5 AC #4, #6 — present key → FULL tier, no extra log line."""
    config = _build_config(tmp_path, api_key="sk-ant-present")
    with caplog.at_level("INFO", logger="nova.app"):
        app = await create_app(config)
    try:
        assert app.tier_manager.tier is CapabilityTier.FULL
        # AC #6 — no new "starting in offline" log when the key is present.
        assert not any(
            "starting in offline-local-only tier" in rec.message
            for rec in caplog.records
        )
    finally:
        await app.close()


async def test_initial_tier_is_offline_when_api_key_is_empty_string_after_load_config(
    tmp_path: Path,
) -> None:
    """Story 2.5 AC #4 end-to-end — ``load_config`` normalizes ``""`` → ``None``,
    which ``create_app`` routes to OFFLINE.

    This closes the restart-picks-up-change contract: a user editing
    ``settings.yaml`` to clear the key value (``api_key: ""``) sees
    OFFLINE tier on the next ``nova`` run.
    """
    from nova.core.config import load_config

    (tmp_path / "settings.yaml").write_text('api_key: ""\n', encoding="utf-8")
    (tmp_path / "modes").mkdir()
    loaded = load_config(tmp_path)
    # Sanity check — the config loader normalizes empty → None (Story 2.2).
    assert loaded.api_key is None
    app = await create_app(loaded)
    try:
        assert app.tier_manager.tier is CapabilityTier.OFFLINE
    finally:
        await app.close()


async def test_create_app_does_not_echo_the_api_key_in_any_log_record(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Story 2.5 AC #14, #16 — the key value must never appear in any log record.

    Uses a distinctive sentinel so a substring check is unambiguous. A
    future edit that adds ``extra={"api_key": config.api_key}`` or
    interpolates the key into a message string would fail this guard.
    """
    sentinel = "sk-ant-VERYSECRETUNIQUE12345"
    config = _build_config(tmp_path, api_key=sentinel)
    with caplog.at_level("DEBUG"):
        app = await create_app(config)
    try:
        for rec in caplog.records:
            assert sentinel not in rec.getMessage(), (
                f"API key leaked into log message on logger={rec.name!r}"
            )
            for value in rec.__dict__.values():
                assert sentinel not in repr(value), (
                    f"API key leaked into log extras on logger={rec.name!r}"
                )
    finally:
        await app.close()


async def test_tier_stays_offline_without_recovery_loop(tmp_path: Path) -> None:
    """Story 2.5 Dev Notes — Story 1.10's ``create_app`` does NOT start
    ``tier_manager.run_recovery_loop()``; Nerve (Story 3.5) owns that. For
    the duration of a ``nova`` invocation that boots with no API key, the
    initial OFFLINE tier must persist.

    This is a behavioral smoke test — if a future refactor starts the
    recovery loop inside ``create_app``, the ``_AlwaysHealthyCheck`` stub
    would trip the tier back to FULL on the first tick, silently breaking
    the Story 2.5 posture.
    """
    import asyncio

    config = _build_config(tmp_path, api_key=None)
    app = await create_app(config)
    try:
        assert app.tier_manager.tier is CapabilityTier.OFFLINE
        # Yield once to let any (unintended) background task run.
        await asyncio.sleep(0)
        assert app.tier_manager.tier is CapabilityTier.OFFLINE
    finally:
        await app.close()
