# Cross-Cutting Patterns

**Status:** Operational. Every story that touches a relevant boundary **must** include a line in Dev Notes: `Patterns consulted: <names>`. Patterns are added here when they emerge as cross-cutting (used in ≥2 places). Removing a pattern requires a retro note explaining why it no longer applies.

**Origin:** Epic 1 retrospective (2026-04-15) — `_bmad-output/implementation-artifacts/epic-1-retro-2026-04-15.md`.

---

## 1. Two-function clock indirection

**When to use:**
Any time a module stamps a timestamp, or any domain object auto-populates a `timestamp` / `created_at` field. Use this instead of calling `datetime.now(...)` inline.

**Failure it prevents:**
Non-deterministic tests. A locally imported `_utc_now_iso` (via `from nova.core.events import _utc_now_iso`) binds the reference at import time, so `monkeypatch.setattr(events, "_utc_now_iso", ...)` in a test has no effect on the call site. Tests then either become flaky or start mocking `datetime.now` globally — both are worse than this pattern.

**Canonical implementation:** [src/nova/core/events.py:74-107](../src/nova/core/events.py#L74-L107)

```python
def _utc_now_iso() -> str:
    """Canonical clock function — single source of truth for timestamps."""
    return datetime.now(UTC).isoformat()

def _default_timestamp() -> str:
    """Factory indirection — preserves monkeypatchability of ``_utc_now_iso``."""
    return _utc_now_iso()

@dataclass(frozen=True)
class Event:
    timestamp: str = field(default_factory=_default_timestamp, kw_only=True)
```

**Reuse example (module-attribute call site):** [src/nova/core/audit.py:253](../src/nova/core/audit.py#L253)

```python
from nova.core import events
...
timestamp = events._utc_now_iso()   # NOT: from nova.core.events import _utc_now_iso
```

**Reuse example (determinism hook beyond clocks):** [src/nova/core/paths.py](../src/nova/core/paths.py) — `_get_max_path_length()` exposes the host's Windows long-path limit through the same module-attribute indirection. `validate_data_dir` calls `paths._get_max_path_length()` (not a local binding), so tests monkeypatch via `monkeypatch.setattr(paths, "_get_max_path_length", lambda: 50)`. The pattern generalizes to any determinism-sensitive helper, not just clocks.

**Test reference:** [tests/unit/core/test_events.py:486-500](../tests/unit/core/test_events.py#L486-L500), [tests/unit/core/test_paths.py::test_long_path_rejected_via_module_attribute](../tests/unit/core/test_paths.py)

**Review focus:**
- Search for `from nova.core.events import _utc_now_iso` — **that is the violation**. Always `from nova.core import events` then `events._utc_now_iso()`.
- Confirm any new timestamped object uses `default_factory=_default_timestamp`, not `default_factory=_utc_now_iso` directly (the one-function shortcut breaks monkeypatch in subtle cases).
- Any new clock-dependent module must add a monkeypatch test proving determinism is reachable.

---

## 2. AST-based architectural guardrails

**When to use:**
Any time the architecture depends on a rule that *isn't* expressible in the type system:
- "Module X must not import from module Y"
- "No adapter instantiation at module scope"
- "No dynamic imports (`__import__`, `importlib.import_module`) in core"
- "All domain dataclasses are frozen"

**Failure it prevents:**
Silent architectural drift. A future dev (or agent) adds one "small" import from `adapters/` into `core/`, the test suite still passes, and six stories later the layering is gone. Humans don't enforce layering reliably; AST tests do.

**Canonical implementation (import isolation):** [tests/unit/core/test_core_isolation.py:30-90](../tests/unit/core/test_core_isolation.py#L30-L90)

```python
def test_only_app_and_cli_import_adapters() -> None:
    """AC #4 — non-adapter modules outside app.py / cli.py must not import ``nova.adapters.*``."""
    violators: list[tuple[str, str]] = []
    for py_file in _iter_nova_py_files():
        if _is_app_or_cli(py_file) or _is_under_adapters(py_file):
            continue
        tree = _parse_file(py_file)
        for _, _, full_name in _all_imports(tree):
            if full_name.startswith("nova.adapters"):
                violators.append((_relative_to_nova_src(py_file), full_name))
    assert not violators
```

**Canonical implementation (module-scope call inspection):** [tests/unit/test_composition_root.py:206-285](../tests/unit/test_composition_root.py#L206-L285)

```python
def test_app_module_level_has_no_adapter_instantiation() -> None:
    # Walks ast.Call nodes at module scope; rejects adapter-class instantiation
    # outside create_app's function body.
    ...
```

**Review focus:**
- If a new architectural rule appears in a story's Dev Notes ("X should never do Y"), ask: can this be an AST test? If yes, it must be one before the story ships.
- Never weaken an existing AST guard to "make the test pass" without a retro-grade decision. The guard is the rule; code yields to it.
- When adding a new guard, walk `ast.walk` on the node of interest, not `ast.parse(...).body` alone — catches nested cases.

---

## 3. Frozen dataclass + single-worker executor

**When to use:**
- **Frozen dataclass:** every domain object (Events, config structs, port models, aggregates). Default stance; mutation is the exception that requires justification.
- **Single-worker executor:** whenever the underlying resource has thread-affinity contracts (stdlib `sqlite3`, Win32 GUI handles, COM objects). Use a dedicated `ThreadPoolExecutor(max_workers=1)` — **not** `asyncio.to_thread`, which uses a shared pool and violates thread-affinity.

**Failure it prevents:**
Entire classes of concurrency bugs by construction:
- Frozen objects can't be mutated mid-await-yield by another task.
- Single-worker executor serializes all access to the non-thread-safe resource; there's no "which thread owns the connection" question to answer.

**Canonical (frozen dataclass):** [src/nova/core/events.py:110-155](../src/nova/core/events.py#L110-L155)

```python
@dataclass(frozen=True)
class Event:
    source: str
    timestamp: str = field(default_factory=_default_timestamp, kw_only=True)
```

**Canonical (single-worker executor):** [src/nova/core/storage/engine.py:161-168](../src/nova/core/storage/engine.py#L161-L168)

```python
local_executor = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="nova-sqlite",
)
loop = asyncio.get_running_loop()
local_connection = await loop.run_in_executor(
    local_executor, self._open_and_configure_sync
)
```

**Review focus:**
- Any new domain dataclass **without** `frozen=True` needs written justification in Dev Notes. Default answer is "frozen."
- Any new adapter wrapping a thread-affine library must use a dedicated executor; reject `asyncio.to_thread` for such resources.
- If a pattern appears where the dev "needed" to mutate a frozen object — check if the right answer is a `dataclasses.replace(...)` returning a new instance.

---

## 4. Error-translation-at-boundary

**When to use:**
Every adapter, engine, or loader at the edge of the domain. Stdlib or third-party exceptions (`sqlite3.Error`, `OSError`, `yaml.YAMLError`, `subprocess.CalledProcessError`, Win32 `OSError`) are caught at the boundary and re-raised as a narrow domain exception.

**Failure it prevents:**
- Leaky abstractions: callers handle `sqlite3.OperationalError` directly, coupling the whole codebase to SQLite.
- Information disclosure: stdlib messages often include SQL, paths, row contents. Domain exceptions carry opaque, operator-safe strings.
- Lost tracebacks: catching without `raise ... from err` drops the original stack.

**Rules:**
1. **Catch specific types**, never bare `Exception`.
2. **Translate to one domain exception type** per adapter/engine (e.g., `StorageError`, `ConfigError`).
3. **Opaque message**: no SQL, no file paths (except as explicit parameters), no row content.
4. **Chain via `from err`** — always.

**Canonical (sqlite3):** [src/nova/core/storage/engine.py:169-171](../src/nova/core/storage/engine.py#L169-L171)

```python
except (OSError, sqlite3.Error, sqlite3.Warning, RuntimeError) as err:
    self._cleanup_partial_start(local_connection, local_executor)
    raise StorageError("start failed") from err
```

**Canonical (config loader):** [src/nova/core/config.py:533-540](../src/nova/core/config.py#L533-L540)

```python
except yaml.constructor.ConstructorError as err:
    raise ConfigError(_translate_constructor_error(err)) from err
except yaml.YAMLError as err:
    raise ConfigError("malformed config: parse error") from err
except OSError as err:
    raise ConfigError("malformed config: I/O error") from err
```

**Review focus:**
- Search for `except Exception` in new code — **reject unless justified**.
- Confirm `from err` (or `from None` with written rationale) on every boundary `raise`.
- Verify message contains no SQL statements, no full paths, no row data.
- Verify there's a test that exercises the translation (input → stdlib exception → domain exception).

---

## 5. Per-file skip-on-error vs. singleton hard-fail

**When to use:**
Whenever loading a *set* of configuration-like files. The loader must decide per-input whether a single broken item bricks the whole load.

**Rule:**
- **Singleton configs** (`settings.yaml`, `exclusions.yaml`, schema files): **hard-fail.** Missing or malformed = the app cannot sensibly proceed. Raise `ConfigError`.
- **Collection items** (mode files, future user plugins, future template packs): **skip-on-error.** Log a warning with file identity, continue the loop. One bad file must not deny access to the rest.

**Failure it prevents:**
Operator-hostile behavior. A user who adds a broken sixth mode file shouldn't lose access to the five working ones. A user with a corrupt `settings.yaml` shouldn't see the app start "half-configured" with silent defaults.

**Canonical (skip-on-error, mode files):** [src/nova/core/config.py:596-616](../src/nova/core/config.py#L596-L616)

```python
try:
    parsed = _read_yaml_file(entry)
except yaml.constructor.ConstructorError as err:
    logger.warning(_constructor_error_mode_warning(err), extra={"stem": stem})
    continue
except yaml.YAMLError:
    logger.warning("mode file YAML parse error — skipped", extra={"stem": stem})
    continue
except OSError:
    logger.warning("mode file I/O error — skipped", extra={"stem": stem})
    continue
```

**Canonical (hard-fail, singleton):** [src/nova/core/config.py:531-540](../src/nova/core/config.py#L531-L540)

```python
try:
    parsed = _read_yaml_file(path)
except yaml.constructor.ConstructorError as err:
    raise ConfigError(_translate_constructor_error(err)) from err
except OSError as err:
    raise ConfigError("malformed config: I/O error") from err
```

**Review focus:**
- When a new loader is introduced, the Dev Notes must explicitly classify each input as **singleton** or **collection** and justify.
- Skip-on-error loops must log with enough identity (`extra={"stem": ...}` or equivalent) for an operator to find the broken file.
- Skip-on-error must not silently swallow all exceptions — only the specific classes in the classification table. Unknown exceptions propagate.

---

## 6. `transaction()` async context manager

**When to use:**
Any multi-statement write that must be atomic: migration apply (DDL + version row), multi-table inserts, any read-modify-write sequence that must be isolated from concurrent writers.

**Failure it prevents:**
- **Auto-commit hijacking:** stdlib `sqlite3` auto-commits between statements in isolation level `""`. Without a transaction block, a migration that succeeds on the DDL but fails on the version insert leaves the DB in a half-applied state.
- **Nested-transaction surprise:** nesting silently reuses the outer transaction, producing partial commits. This CM **rejects nesting explicitly** on the owning task.
- **Cancellation mid-ROLLBACK:** `asyncio.CancelledError` during rollback would leak the transaction. ROLLBACK is `asyncio.shield`-wrapped.

**Canonical:** [src/nova/core/storage/engine.py:247-330](../src/nova/core/storage/engine.py#L247-L330)

```python
@asynccontextmanager
async def transaction(self) -> AsyncIterator[None]:
    """Multi-statement transaction context manager.

    Inside the block, execute/executemany called from the same asyncio task
    do NOT auto-commit — COMMIT fires on context exit; ROLLBACK on any
    exception (including CancelledError). Nested transactions on the owning
    task are rejected with StorageError("nested transaction").
    """
    self._require_started()
    current = asyncio.current_task()
    if self._tx_owner is current and current is not None:
        raise StorageError("nested transaction")
    async with self._tx_lock:
        ...  # BEGIN IMMEDIATE, yield, COMMIT/ROLLBACK logic
```

**Review focus:**
- Any new multi-statement write that is NOT wrapped in `transaction()` needs written justification (e.g., "single-statement insert, auto-commit is sufficient").
- Confirm `BEGIN IMMEDIATE` (not `BEGIN DEFERRED`) to avoid SQLITE_BUSY on later upgrade-to-write.
- Confirm ROLLBACK path is reachable and tested (use a test that raises inside the `async with`).

---

## 7. Partial-init cleanup in composition root

**When to use:**
Any function that wires multiple resources with lifecycle dependencies — the composition root, any setup/teardown sequence with external handles (files, sockets, subprocess handles, DB connections).

**Failure it prevents:**
Resource leaks on failed initialization. If `create_app` starts the engine, then fails during audit-logger construction, the engine must still be closed — otherwise the file handle leaks, the WAL stays open, and the next start may see stale lockfiles.

**Canonical:** [src/nova/app.py:130-191](../src/nova/app.py#L130-L191)

```python
storage = SqliteStorageEngine(config.db_path)
await storage.start()

try:
    applied_versions = await storage.run_migrations()
    event_bus = EventBus()
    audit = AuditLogger(storage=storage)
    tier_manager = TierManager(...)
    shield_adapter: ShieldPort = shield if shield is not None else NoOpShieldAdapter()

    async def _close() -> None:
        await storage.close()

    return NovaApp(...)
except BaseException:
    try:
        await storage.close()
    except Exception:
        logger.exception("secondary error closing engine during partial-init teardown")
    raise
```

**Rules:**
1. Resources are acquired in dependency order; cleanup runs in **reverse** order.
2. Cleanup errors are **logged and swallowed**, never allowed to mask the original exception.
3. `BaseException` (not just `Exception`) is caught — partial-init cleanup must run on `KeyboardInterrupt` / `asyncio.CancelledError` too.
4. The original exception is re-raised with `raise` (bare), preserving the traceback.

**Review focus:**
- Any `await X.start()` (or equivalent) followed by more construction must be inside a `try/except BaseException` with teardown.
- Secondary failures in cleanup must be logged with `logger.exception(...)`, not silently swallowed.
- Confirm tests exercise the partial-init path (mock one of the later construction steps to raise, assert the engine was closed).

---

## How to add a new pattern

1. You've written the same discipline in two stories. Before the third, stop and ask: is this cross-cutting?
2. If yes, add a section here with all five parts (when / failure / canonical / review focus / rules if needed).
3. Update `MEMORY.md` or retro notes if this pattern invalidates prior guidance.
4. The story that promotes the pattern to this doc is its "reference story" — cite it.

## How to retire a pattern

1. If a pattern becomes obsolete (e.g., a library version makes it unnecessary), don't silently delete.
2. Add a `## Retired: <name>` section at the bottom with the date, the replacement, and a retro-grade reason.
3. Grep the codebase for citations of the retired pattern and update them in the same PR.
