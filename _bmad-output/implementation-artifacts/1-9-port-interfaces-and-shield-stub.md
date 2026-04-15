# Story 1.9: Port Interfaces & Shield Stub

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer implementing any system,
I want all 8 port interfaces defined as `Protocol` classes under `src/nova/ports/` and the Shield no-op adapter living under `src/nova/adapters/shield/`,
so that Story 1.10's composition root can wire every system through injected ports without any system module ever importing a concrete adapter, and so that future adapters (SqliteBrainAdapter, Win32EyesAdapter, ClaudeReasoningAdapter, real ShieldAdapter in v0.15, etc.) can slot in behind these contracts with zero churn in systems/.

## Acceptance Criteria

1. **Eight Protocol-class port files ship under `src/nova/ports/`.** Each file defines **exactly one** `Protocol` class named `{System}Port`, decorated with `@runtime_checkable` only where explicitly pinned below (default: no decorator — structural subtyping via mypy is sufficient; `runtime_checkable` is opt-in per port, not a blanket rule). Files and classes:
   - [src/nova/ports/brain.py](src/nova/ports/brain.py) → `class BrainPort(Protocol)`
   - [src/nova/ports/eyes.py](src/nova/ports/eyes.py) → `class EyesPort(Protocol)`
   - [src/nova/ports/hands.py](src/nova/ports/hands.py) → `class HandsPort(Protocol)`
   - [src/nova/ports/shield.py](src/nova/ports/shield.py) → `class ShieldPort(Protocol)`
   - [src/nova/ports/voice.py](src/nova/ports/voice.py) → `class VoicePort(Protocol)`
   - [src/nova/ports/ritual.py](src/nova/ports/ritual.py) → `class RitualPort(Protocol)`
   - [src/nova/ports/skin.py](src/nova/ports/skin.py) → `class SkinPort(Protocol)`
   - [src/nova/ports/nerve.py](src/nova/ports/nerve.py) → `class NervePort(Protocol)`

   Each file opens with `from __future__ import annotations` (Story 1.1 convention) and ends with `__all__ = ["{System}Port"]`. **No other public symbols** per port file — helper enums / dataclasses / type aliases live in `core/types.py` or `systems/{system}/models.py`, never inside a port file.

2. **All port methods are `async def`** — even for methods that current concrete adapters will implement synchronously under the hood (e.g. Win32 context capture wraps in `asyncio.to_thread`). This is architecture.md:957's rule: "Port methods are all `async` — even if the current adapter is synchronous, the port is async to allow future async adapters." Every method body is a single-line `...` ellipsis (Protocol bodies never contain logic). No `@abstractmethod`, no default-method bodies, no `raise NotImplementedError`. Locked by `test_all_port_methods_are_async`.

3. **Port method signatures use domain types only — never adapter-specific types.** Explicit forbidden types in any port method signature (parameters or return) per architecture.md:959 + 1460-1465:
   - `sqlite3.Row`, `sqlite3.Connection`, `sqlite3.Cursor`, `sqlite3.*` → **forbidden** (Brain adapter translates rows to domain types)
   - `rich.panel.Panel`, `rich.table.Table`, `rich.tree.Tree`, `rich.console.Console`, `rich.*` → **forbidden** (Skin adapter owns Rich component construction)
   - `anthropic.Message`, `anthropic.types.*`, `anthropic.*` → **forbidden** (Claude adapter owns SDK types)
   - `win32gui.HWND`, `win32con.*`, `pywin32.*`, `psutil.Process`, `psutil.*` → **forbidden** (Win32 adapter owns handle types)
   - `yaml.*`, `dict[str, object]` as a "raw YAML blob" — **forbidden** at port boundaries (Config module has already parsed YAML into typed `ModeConfig` / `ExclusionConfig` / `UserSettings` / `NovaConfig` from `core/config.py`; ports consume those).
   - **Allowed**: stdlib primitives (`str`, `int`, `bool`, `float`, `bytes`, `None`), `list[T]`, `tuple[T, ...]`, `Mapping[K, V]` / `Sequence[T]` from `collections.abc`, `X | None` unions, enum members from `core/types.py`, frozen dataclasses from `core/config.py`, frozen dataclasses from `systems/{system}/models.py`, `Event` / `Command` domain types. Locked by `test_port_signatures_use_only_allowed_types`.

4. **Per-port method contract — minimum T1 method set.** The architecture (architecture.md:343–400 T1 Continuity Loop, architecture.md:966–972 BrainPort sketch, architecture.md:1458–1465 port boundary table) pins the **minimum** set below. Each port may expose exactly these methods in Story 1.9 — nothing more, nothing less. Later stories (3.x, 4.x, 5.x, 6.x) add methods when they ship the consuming logic; this story does NOT pre-add speculative methods.

   | Port | Method | Signature |
   |---|---|---|
   | **BrainPort** | `load_last_session` | `async def load_last_session(self) -> Session \| None: ...` |
   |  | `store_session` | `async def store_session(self, session: SessionData) -> None: ...` |
   |  | `load_briefing_aggregate` | `async def load_briefing_aggregate(self) -> BriefingAggregate: ...` |
   |  | `query_memory` | `async def query_memory(self, query: str) -> list[MemoryItem]: ...` |
   |  | `delete_matching` | `async def delete_matching(self, target: str) -> DeletionPreview: ...` |
   |  | `confirm_deletion` | `async def confirm_deletion(self, preview: DeletionPreview) -> DeletionResult: ...` |
   |  | `get_transparency_model` | `async def get_transparency_model(self) -> TransparencyModel: ...` |
   | **EyesPort** | `capture_current_workspace` | `async def capture_current_workspace(self) -> WorkspaceSnapshot: ...` |
   | **HandsPort** | `restore_mode` | `async def restore_mode(self, mode_config: ModeConfig) -> list[ActionResult]: ...` |
   | **ShieldPort** | `is_focus_protected` | `async def is_focus_protected(self) -> bool: ...` |
   |  | `allow_action` | `async def allow_action(self, action_type: ActionType) -> bool: ...` |
   | **VoicePort** | `generate_prose_enrichment` | `async def generate_prose_enrichment(self, aggregate: BriefingAggregate) -> str \| None: ...` |
   |  | `generate_restore_summary` | `async def generate_restore_summary(self, results: Sequence[ActionResult], context: str) -> str: ...` |
   |  | `generate_shutdown_confirmation` | `async def generate_shutdown_confirmation(self, seed: str) -> str: ...` |
   | **RitualPort** | `build_briefing` | `async def build_briefing(self, aggregate: BriefingAggregate, state: BriefingState, tier: CapabilityTier) -> BriefingViewModel: ...` |
   |  | `begin_shutdown` | `async def begin_shutdown(self) -> ShutdownData: ...` |
   | **SkinPort** | `render_briefing_card` | `async def render_briefing_card(self, view_model: BriefingViewModel) -> None: ...` |
   |  | `render_progress` | `async def render_progress(self, results: Sequence[ActionResult]) -> None: ...` |
   |  | `render_shutdown_card` | `async def render_shutdown_card(self, summary: SessionSummary) -> None: ...` |
   |  | `render_response` | `async def render_response(self, text: str) -> None: ...` |
   |  | `collect_input` | `async def collect_input(self, prompt: str) -> str: ...` |
   |  | `parse_command` | `async def parse_command(self, raw_input: str) -> Command: ...` |
   | **NervePort** | `startup` | `async def startup(self) -> None: ...` |
   |  | `route_command` | `async def route_command(self, command: Command) -> None: ...` |

   **Rationale for minimalism:** Adding a method to a port is cheap (one-line change). Removing one after adapters have implemented it is expensive (touches every adapter + every stub + every mock). Story 1.8's precedent applies: the public surface is intentionally tiny; widening it is a deliberate future-story decision, not an in-line addition here. If a downstream story (3.x/4.x/etc.) discovers it needs a new method, that story adds it to the port + adapter + any stub in the same commit.

   **Method ordering convention:** within each Protocol class, methods appear in the order listed in the table above (load/read before store/write, lifecycle methods last). Alphabetical is rejected because it scrambles semantic groupings (`confirm_deletion` would land between `capture_*` and `delete_*`, which is confusing). Locked by `test_port_method_ordering_matches_contract`.

5. **Domain types — where they live.** Every type referenced in a port signature MUST be importable from one of these locations. If a type doesn't yet exist, this story creates a minimal frozen dataclass in `systems/{system}/models.py`. **No placeholder types in `core/types.py`** — `core/types.py` is reserved for cross-system enums + primitives (Story 1.2 precedent). Domain models are system-scoped.

   | Type | Lives in | Fields for Story 1.9 |
   |---|---|---|
   | `Session` | `systems/brain/models.py` | `id: int, started_at: str, ended_at: str \| None, mode_name: str \| None, is_complete: bool` |
   | `SessionData` | `systems/brain/models.py` | `seed_text: str \| None, mode_name: str \| None, duration_seconds: int, ended_at: str` |
   | `SessionSummary` | `systems/brain/models.py` | `session_id: int, started_at: str, ended_at: str \| None, duration_seconds: int, mode_name: str \| None, is_complete: bool` |
   | `MemoryItem` | `systems/brain/models.py` | `id: int, category: MemoryCategory, content: str, created_at: str` |
   | `BriefingAggregate` | `systems/brain/models.py` | `last_session: SessionSummary \| None, last_snapshot: WorkspaceSnapshot \| None, last_seed: str \| None, available_modes: tuple[ModeInfo, ...], recent_memory: tuple[MemoryItem, ...]` |
   | `ModeInfo` | `systems/brain/models.py` | `name: str, last_used_at: str \| None` (thin projection of `ModeConfig` plus usage metadata — distinct from the file-backed `ModeConfig` in `core/config.py`) |
   | `DeletionPreview` | `systems/brain/models.py` | `target: str, affected_tables: tuple[str, ...], items_to_delete: int` |
   | `DeletionResult` | `systems/brain/models.py` | `target: str, items_deleted: int, success: bool` |
   | `TransparencyModel` | `systems/brain/models.py` | `sessions_count: int, memory_items_count: int, snapshots_count: int, audit_log_count: int` (minimal T1 shape; Story 5.1 will extend) |
   | `WindowContext` | `systems/eyes/models.py` | `app_name: str \| None, window_title: str \| None, process_name: str \| None, is_opaque: bool` |
   | `WorkspaceSnapshot` | `systems/eyes/models.py` | `captured_at: str, snapshot_type: SnapshotType, windows: tuple[WindowContext, ...]` |
   | `ActionRequest` | `systems/hands/models.py` | `action_type: ActionType, target: str \| None, details: Mapping[str, object] \| None` |
   | `ActionResult` | `systems/hands/models.py` | `action_type: ActionType, target: str, success: bool, reason: str \| None` |
   | `BriefingViewModel` | `systems/ritual/models.py` | `state: BriefingState, tier: CapabilityTier, title: str, prompt_text: str \| None, auto_start_setup: bool, seed_text: str \| None, last_mode: str \| None, last_duration_seconds: int \| None, last_apps: tuple[str, ...], available_modes: tuple[ModeInfo, ...], suggested_mode: ModeInfo \| None, prose_enrichment: str \| None` |
   | `ShutdownData` | `systems/ritual/models.py` | `session_id: int, prompt_text: str, last_context: str \| None` |
   | `Command` | `systems/skin/models.py` | `verb: str, target: str \| None, raw_input: str, is_contextual: bool = False` |

   **All** model classes are `@dataclass(frozen=True)` with absolute imports (`from nova.core.types import MemoryCategory` etc.). **Sequence-valued fields use `tuple[T, ...]`, not `list[T]`** — matches `ModeRestored.apps_launched` precedent in [src/nova/core/events.py:245](src/nova/core/events.py#L245) for genuine immutability. Frozen-dataclass decoration only freezes attribute rebinding; the container itself must be immutable for true frozenness (Story 1.3 carry-forward). Locked by `test_domain_models_are_frozen_dataclasses_with_tuple_fields`.

   **ModeInfo vs ModeConfig distinction (important):** `ModeConfig` in [src/nova/core/config.py:206](src/nova/core/config.py#L206) is the file-backed schema (name, apps, folders, URLs). `ModeInfo` is a **Brain-layer projection** — mode name + usage metadata (last_used_at) — returned by `BrainPort` for briefing assembly. They are distinct types. `BriefingAggregate.available_modes: tuple[ModeInfo, ...]` is NOT `tuple[ModeConfig, ...]`. Rationale: the briefing cares about "which modes does the user have and when did they last use them," not the full app-launch config. `HandsPort.restore_mode` consumes the full `ModeConfig` (from `core/config.py`) because it IS launching apps. Both types co-exist. Locked by the test `test_mode_info_is_distinct_from_mode_config`.

6. **Shield no-op adapter — location, class name, contract.** Per epic AC: "Shield no-op adapter lives in `adapters/` (not `systems/shield/`) — it is a concrete adapter implementing ShieldPort that returns inert/empty responses for all methods. `systems/shield/` defines the port/facade boundary; the no-op implementation is an adapter concern."
   - **File:** [`src/nova/adapters/shield/__init__.py`](src/nova/adapters/shield/__init__.py) (create new sub-package) and [`src/nova/adapters/shield/noop.py`](src/nova/adapters/shield/noop.py) (the adapter module).
   - **Class:** `class NoOpShieldAdapter` — a plain class (not a dataclass, no `__init__` parameters required). Its entire job is to satisfy `ShieldPort` structurally with inert returns.
   - **Method behavior:**
     - `async def is_focus_protected(self) -> bool: return False` — T1 never protects focus; v0.15's real adapter will compute this.
     - `async def allow_action(self, action_type: ActionType) -> bool: return True` — inert adapter allows everything; v0.15's real adapter consults focus-protection rules. The `action_type` parameter MUST be annotated (not `_action_type`) to match the port signature exactly — mypy strict with `--strict` catches parameter-shape drift via structural Protocol matching, but an explicit-typed parameter with a discarding body (`del action_type` or simply referencing it in a docstring-level comment) makes the intent clear. **Recommendation:** keep the parameter named `action_type` identically to the port, with no underscore prefix; mypy/ruff do not flag unused parameters on Protocol-conforming methods because the parameter is part of the public interface contract.
   - **No state.** `NoOpShieldAdapter` has no instance attributes, no `__init__` override (uses the default `object.__init__`). A composition root (Story 1.10) writes `shield_adapter = NoOpShieldAdapter()` with zero arguments.
   - **No audit hooks, no logging, no event emissions.** The no-op adapter is genuinely silent — it is not an observability surface. Story 1.8's `AuditLogger` does NOT get wired to the Shield adapter here; if future v0.15 work decides to audit Shield decisions, that is a v0.15 design decision.
   - **Imports:** only `from nova.core.types import ActionType` (for the parameter type). No other imports. Does NOT import `ShieldPort` (structural subtyping via mypy covers conformance without a nominal `ShieldPort` base; adding the import would create an unnecessary dependency and no runtime benefit). Conformance is verified by a `test_noop_shield_adapter_satisfies_shield_port` test that does `assert isinstance_protocol_match(NoOpShieldAdapter(), ShieldPort)` style checks (see AC #13).
   - **Module docstring:** one-paragraph summary pinning the v0.15 deferral, the "inert returns" contract, and the `systems/shield/` facade/adapter split.
   - Locked by tests `test_noop_shield_adapter_is_focus_protected_returns_false`, `test_noop_shield_adapter_allow_action_returns_true_for_all_action_types` (parametrized over `list(ActionType)` so a 12th member auto-extends coverage), `test_noop_shield_adapter_has_no_instance_state`.

7. **Port files MUST NOT import from `nova.adapters.*` or from any `nova.systems.{X}.*` module with a non-`.models` suffix.** Exact allowlist per port:
   - Stdlib: `__future__`, `collections`, `typing`
   - First-party: `nova.core.types`, `nova.core.config`, `nova.core.events`, `nova.systems.{owning_system}.models` (the port's own system's models), `nova.systems.{other_system}.models` **only when the signature demands it** (e.g., `RitualPort.build_briefing` takes `BriefingAggregate` from `systems/brain/models.py`; `NervePort.route_command` takes `Command` from `systems/skin/models.py` — both imports are allowed).
   - **Forbidden absolutely:**
     - Any `from nova.adapters.*` or `import nova.adapters.*`
     - Any `from nova.systems.{X}.<suffix>` where `<suffix>` is anything other than `models` — `.system`, `.adapter`, `.commands`, `.components`, `.internals`, bare `.__init__` re-exports, etc. are all forbidden. Only `.models` crosses system boundaries (AC #8).
     - Any module in `FORBIDDEN_TOPLEVEL_MODULES` from [tests/unit/core/test_core_isolation.py:73](tests/unit/core/test_core_isolation.py#L73) (`sqlite3`, `anthropic`, `yaml`, `rich`, pywin32 modules, `psutil`)
   - Dynamic imports (`importlib.import_module`, `__import__`) are forbidden by the same denylist. Locked by AST-based static-analysis tests in `tests/unit/ports/test_port_isolation.py` (AC #13).

8. **Cross-system model imports are allowed from ports — `.models` is the one portable cross-system module.** Clarification of AC #7: `ports/ritual.py` legitimately imports `BriefingAggregate` from `systems/brain/models.py` (because `RitualPort.build_briefing` consumes it) and `BriefingViewModel` from `systems/ritual/models.py` (because it returns it). Similarly, `ports/nerve.py` imports `Command` from `systems/skin/models.py` because `NervePort.route_command` consumes it. This is explicitly allowed.

   **The discriminator is the dotted suffix: `.models` is the ONLY portable cross-system module.** Any other suffix (`.system`, `.adapter`, `.commands`, `.components`, `.internals`, etc.) is forbidden for cross-system imports, regardless of whether the target file exists yet. Rationale: keeping a single well-known suffix as the cross-system surface makes the isolation test a one-rule check (`.models` only) and prevents a future story from introducing a second portable suffix (e.g. a `systems/skin/commands.py` parser module) that would then have to be whitelisted twice — once in the prose, once in the test. The `Command` dataclass lives in `systems/skin/models.py` (AC #5) specifically to avoid this contradiction — Architecture.md:1355's speculative `systems/skin/commands.py` split is deferred until a Skin-internal parser actually needs a dedicated module (Story 3.4's scope, not Story 1.9's). When that happens, the parser module stays Skin-internal; the `Command` type stays in `models.py`.

   Locked by `test_ports_only_import_from_system_models_modules` — walks every `ast.ImportFrom` / `ast.Import` in every port file and fails if any `nova.systems.{X}` import targets anything other than the `.models` suffix (e.g. fails on `from nova.systems.skin.commands import Command`, passes on `from nova.systems.skin.models import Command`).

9. **`systems/shield/` remains interface-only in T1.** Per architecture.md:1343 + epic AC, `systems/shield/system.py` is NOT in scope for this story. The port file `src/nova/ports/shield.py` IS the facade for T1. `src/nova/systems/shield/__init__.py` already exists ([current content](src/nova/systems/shield/__init__.py): `"""Shield system - focus protection (stubbed in T1). Implementation deferred to v0.15+."""`) — leave it as-is. **Do NOT create `src/nova/systems/shield/system.py`** in this story. When v0.15 lands, that story will introduce `systems/shield/system.py` (the policy engine) and `adapters/shield/win32.py` (the real adapter); the no-op adapter created here will continue to exist for tests and offline scenarios.

10. **`src/nova/ports/__init__.py` re-export update.** Match the pattern Stories 1.2 / 1.3 / 1.4 / 1.5 / 1.6 / 1.7 / 1.8 set for `core/__init__.py`. The current `ports/__init__.py` contains only a single-line docstring ([`src/nova/ports/__init__.py:1`](src/nova/ports/__init__.py#L1)). Replace it with:
    ```python
    """Port interfaces - Protocol classes authored in Story 1.9."""

    from nova.ports.brain import BrainPort
    from nova.ports.eyes import EyesPort
    from nova.ports.hands import HandsPort
    from nova.ports.nerve import NervePort
    from nova.ports.ritual import RitualPort
    from nova.ports.shield import ShieldPort
    from nova.ports.skin import SkinPort
    from nova.ports.voice import VoicePort

    __all__: list[str] = [
        "BrainPort",
        "EyesPort",
        "HandsPort",
        "NervePort",
        "RitualPort",
        "ShieldPort",
        "SkinPort",
        "VoicePort",
    ]
    ```
    Alphabetical ordering locked by a new `test_ports_init_exports_alphabetical_and_complete` test mirroring Story 1.2's monotonic-ordering check.

11. **No wiring in this story.** Specifically — mirror Story 1.8's AC #14:
    - Do NOT modify `src/nova/app.py` — wiring ports to adapters is Story 1.10's job.
    - Do NOT modify `src/nova/cli.py` — cli startup is Story 1.10.
    - Do NOT modify `src/nova/core/__init__.py` — no new re-exports (all new types live in `systems/*/models.py`, not `core/`).
    - Do NOT create any concrete system adapter beyond `NoOpShieldAdapter` (Story 1.10+ wires stubs/real adapters; Stories 3.x/4.x ship real ones).
    - Do NOT create `adapters/sqlite/brain.py`, `adapters/win32/context.py`, `adapters/claude/reasoning.py`, `adapters/rich/skin.py`, or any other concrete adapter. The adapter sub-packages (`adapters/sqlite/`, `adapters/win32/`, `adapters/claude/`, `adapters/rich/`) already exist as empty packages (see [src/nova/adapters/](src/nova/adapters/)); leave them alone.
    - Do NOT create `systems/{system}/system.py` for any system — those are owned by Stories 3.x/4.x/5.x. This story only creates `models.py` files. The `Command` dataclass lives in `systems/skin/models.py` alongside any other Skin-layer value types (T1 has only `Command`; future stories may add `RenderInstruction` / `PromptState` etc.). **A dedicated `systems/skin/commands.py` module is deliberately NOT created** — that would violate the "only `.models` is a portable cross-system module" rule established in AC #7/#8, since `NervePort.route_command` imports `Command` across system boundaries. Architecture.md:1355 lists a `commands.py` alongside `models.py` for the Skin system; that is an aspirational split anticipating a future deterministic-parser module in Story 3.4 — **Story 1.9 collapses it into `models.py` for the cross-system-portability reason above**. If Story 3.4 needs a private parser module, it can introduce `systems/skin/commands.py` as a Skin-internal module (consumed only by `systems/skin/system.py`, never imported across system boundaries) — at that point the `Command` dataclass stays in `models.py` and the parser logic lives in `commands.py`.
    - Do NOT subscribe port implementations to the event bus from this story. Ports are passive contracts; subscribers are wired in Story 1.10 (composition root) and in the system stories that implement them.
    - Do NOT add `@runtime_checkable` to every port. Only add it to ports where a test specifically uses `isinstance(obj, SomePort)` at runtime — for Story 1.9 that is **only `ShieldPort`** (to verify `NoOpShieldAdapter` structurally satisfies it via `isinstance`). All other ports remain non-runtime-checkable `Protocol`s, which keeps their mypy contract strict and avoids the `@runtime_checkable` overhead of ABC registration at import time.

12. **`pyproject.toml` is NOT modified** — no new dependencies. All needed imports are stdlib (`typing`, `collections.abc`) or already-first-party (`nova.core.*`). No `pytest-protocols`, no `typing_extensions`, no third-party Protocol tooling.

13. **New test file `tests/unit/ports/test_port_isolation.py` — AST-based isolation + shape guardrails.** Mirror the Story 1.2 + 1.6 + 1.8 `test_core_isolation.py` pattern. New directory: `tests/unit/ports/` (no `__init__.py` — matches `tests/unit/core/` layout). Required tests:
    - **`test_no_relative_imports` (parametrized over all 8 port modules)** — same AST walk as `test_core_isolation.py::test_no_relative_imports`, asserts `node.level == 0` on every `ImportFrom`. Locks AC #7 against `from ..core.types import X`.
    - **`test_no_forbidden_imports` (parametrized)** — uses the `FORBIDDEN_TOPLEVEL_MODULES` frozenset exported from `tests/unit/core/test_core_isolation.py` (import it — do not re-declare). Asserts no port file imports `sqlite3`, `rich`, `anthropic`, `yaml`, `pywin32`, `psutil`, or any Win32 submodule.
    - **`test_no_import_of_nova_adapters_or_other_systems_internals` (parametrized)** — walks every `ast.ImportFrom` / `ast.Import` and fails if any dotted path starts with `nova.adapters` OR matches `nova.systems.{X}.<suffix>` where `<suffix>` is anything other than `models` (the ONE permitted cross-system suffix per AC #8). Implementation: for each `nova.systems.*` import, split on `.`, assert the 4th segment (0-indexed: `["nova", "systems", "{X}", "{suffix}"]`) equals `"models"`. Bare `from nova.systems.{X} import ...` (no 4th segment) is also forbidden — ports must target `.models` explicitly. Uses `_has_forbidden_prefix` helper from `test_core_isolation.py` for the `nova.adapters` check (import it — do not re-implement); the `nova.systems.*` suffix check is new logic in this test file.
    - **`test_no_dynamic_imports_of_forbidden_modules` (parametrized)** — uses `_dynamic_import_targets` + `_dynamic_import_full_targets` helpers from `test_core_isolation.py`. Asserts no `importlib.import_module("nova.adapters.*")` or `__import__("sqlite3")` anywhere in any port file.
    - **`test_each_port_defines_exactly_one_protocol_class`** — parametrized over the 8 ports. AST-walks the module, finds all `ast.ClassDef` nodes, asserts exactly one, asserts its name matches the expected `{System}Port`, asserts one of its base-class names is `Protocol`.
    - **`test_all_port_methods_are_async`** — parametrized over the 8 ports. Walks the `ClassDef` for the port, finds every `ast.FunctionDef` / `ast.AsyncFunctionDef` inside, asserts every method is `AsyncFunctionDef` (not plain `FunctionDef`). The body of every method is a single `ast.Expr(value=ast.Constant(value=Ellipsis))` (locks AC #2's "bodies are `...` only" rule — no logic, no `raise NotImplementedError`).
    - **`test_port_method_ordering_matches_contract`** — parametrized over the 8 ports. Asserts the order of method defs inside each `ClassDef` matches the order in AC #4's table, detected by extracting method names in source order and comparing to the pinned tuple. A future method added out-of-order trips this test and forces the author to place it explicitly.
    - **`test_port_signatures_use_only_allowed_types`** — parametrized over the 8 ports. Walks every `ast.AsyncFunctionDef.args.args` + `returns` and collects every referenced name (via `ast.Name` / `ast.Attribute`). Fails if any name references a forbidden prefix (`sqlite3`, `rich`, `anthropic`, `yaml`, `win32*`, `pywin*`, `psutil`). Rationale: mypy strict would catch most of this at type-check time, but the AST check is faster feedback + fails independently of mypy's cache state.
    - **`test_ports_init_exports_alphabetical_and_complete`** — imports `nova.ports` and asserts `nova.ports.__all__` is alphabetically sorted AND contains exactly `{"BrainPort", "EyesPort", "HandsPort", "NervePort", "RitualPort", "ShieldPort", "SkinPort", "VoicePort"}`. Mirror of Story 1.2's monotonic-ordering test.
    - **`test_domain_models_are_frozen_dataclasses_with_tuple_fields`** — imports every model class listed in AC #5, asserts each is a frozen dataclass (`dataclasses.is_dataclass(cls) and cls.__dataclass_params__.frozen is True`), and asserts every sequence-valued field is typed as `tuple[...]` (not `list[...]`) via `typing.get_type_hints`.
    - **`test_mode_info_is_distinct_from_mode_config`** — imports `ModeInfo` from `systems.brain.models` and `ModeConfig` from `core.config`, asserts they are distinct classes (no aliasing) and have non-overlapping field sets (ModeInfo has `name, last_used_at`; ModeConfig has `name, apps, folders, urls`).

14. **New test file `tests/unit/adapters/test_noop_shield_adapter.py` — Shield no-op adapter behavior.** New directory: `tests/unit/adapters/` (no `__init__.py`). Required tests:
    - **`test_noop_shield_adapter_satisfies_shield_port`** — constructs `NoOpShieldAdapter()` and asserts it structurally satisfies `ShieldPort`. Implementation: since `ShieldPort` is opted into `@runtime_checkable` per AC #11, use `isinstance(NoOpShieldAdapter(), ShieldPort)` directly. Adds a regression guard that the adapter actually implements every port method with the correct async shape.
    - **`test_noop_shield_adapter_is_focus_protected_returns_false`** — calls `await NoOpShieldAdapter().is_focus_protected()` and asserts the return is exactly `False` (not falsy — identity check: `result is False`).
    - **`test_noop_shield_adapter_allow_action_returns_true_for_all_action_types`** — parametrized over `list(ActionType)` (so adding a 12th member auto-extends). Calls `await adapter.allow_action(member)` and asserts `result is True`.
    - **`test_noop_shield_adapter_has_no_instance_state`** — constructs an adapter, asserts `vars(adapter) == {}` (no instance attributes), asserts `type(adapter).__slots__` is either absent or empty (no reserved slots).
    - **`test_noop_shield_adapter_construction_takes_no_arguments`** — asserts `inspect.signature(NoOpShieldAdapter).parameters` contains only `self` (or is empty, depending on how inspect reports dataclass vs plain class), so the composition root can always write `NoOpShieldAdapter()` with zero args.
    - **Do NOT add a test that imports the adapter into core** — the `nova.adapters.*` denylist in `test_core_isolation.py` already prevents that. This new test file just verifies the adapter's observable behavior.

15. **`test_core_isolation.py` — NOT modified.** Story 1.9 does NOT register any new `core/*` module. The port files live in `src/nova/ports/`, not `src/nova/core/`, so the existing `test_core_isolation.py` allowlist/denylist scheme does NOT extend here. Instead, the new `test_port_isolation.py` (AC #13) provides the port-layer equivalent. **One import line added** to `test_port_isolation.py`: `from tests.unit.core.test_core_isolation import FORBIDDEN_TOPLEVEL_MODULES, FORBIDDEN_NOVA_PREFIXES, _all_imports, _dynamic_import_targets, _dynamic_import_full_targets, _has_forbidden_prefix` — this keeps the forbidden set single-sourced and avoids drift between the two isolation test files. If that cross-module import needs `tests/unit/__init__.py` to resolve, add a minimal `__init__.py` under `tests/unit/` and only there — do NOT add `__init__.py` under `tests/unit/core/` or `tests/unit/ports/` or `tests/unit/adapters/` (Story 1.4+ precedent: flat test-dir layout).

    **Alternative (preferred) if the cross-module import is awkward:** duplicate the frozensets `FORBIDDEN_TOPLEVEL_MODULES` and `FORBIDDEN_NOVA_PREFIXES` as verbatim copies inside `test_port_isolation.py` with a one-line comment `# Mirror of tests/unit/core/test_core_isolation.py — keep in sync.` Accept the minor duplication for independence of the two isolation-test files. **Developer chooses between these two approaches based on what pytest + the current test layout tolerates** — both satisfy AC #13's behavior.

16. **mypy strict passes on every port file.** Explicit requirement per epic AC #5. Run `uv run mypy src/` and the eight port files + adapter + models files type-check clean with zero `# type: ignore` in production code. `Protocol` method bodies are `...` — mypy accepts these as abstract implicitly; no `@abstractmethod` decorator is needed (and adding it would conflict with Protocol's structural semantics).

17. **Quality gate passes clean (Story 1.8 carry-forward):** `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` returns exit code 0.
    - Ruff rules active: `E`, `F`, `I` (import ordering), `UP` (modern syntax — `list[T]` not `List[T]`, PEP 695 `type` aliases), `B` (bugbear), `SIM` (simplify), `T20` (no print). None of these rules trigger on a well-formed Protocol class.
    - mypy strict succeeds on every new `.py` file.
    - Repo tree stays clean after the verify run — no `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `*.db`, `*.db-wal`, `*.db-shm`.
    - **Expected test count delta:** `tests/unit/ports/test_port_isolation.py` adds ~24–32 tests (parametrized across 8 ports → most tests produce 8+ rows each). `tests/unit/adapters/test_noop_shield_adapter.py` adds ~14–18 tests (11 parametrized over `ActionType` + 5 behavioral). Firm number is whatever the run produces — don't over-fit a target. Prior total: **514 passed, 1 skipped** at end of Story 1.8.

18. **Consumer wiring deferred — cross-story notes.** The following stories will consume the ports created here. Document (don't implement) the expected consumer pattern:

    | Downstream story | Consumes | Pattern |
    |---|---|---|
    | 1.10 Composition root | All 8 ports | `app.py` instantiates concrete adapters, passes them as `brain=SqliteBrainAdapter(storage), eyes=Win32EyesAdapter(...), ..., shield=NoOpShieldAdapter()` constructor args to each system. |
    | 3.1 Brain session & seed persistence | `BrainPort` | Ships `SqliteBrainAdapter` in `adapters/sqlite/brain.py` implementing every `BrainPort` method against the storage engine. |
    | 3.2 BriefingAggregate | `BrainPort.load_briefing_aggregate` | Brain's adapter method materializes the aggregate from SQLite + file-backed mode config. |
    | 3.3 BriefingViewModel rendering | `RitualPort.build_briefing`, `SkinPort.render_briefing_card`, `VoicePort.generate_prose_enrichment` | RitualSystem implements `RitualPort`; SkinSystem implements `SkinPort`; VoiceSystem implements `VoicePort` via Claude adapter. |
    | 3.4 Command parser | `SkinPort.parse_command` | SkinSystem ships deterministic parser (may introduce a Skin-internal `systems/skin/commands.py` for the parser logic — Skin-internal only, never cross-system-imported); the `Command` dataclass already lives in `systems/skin/models.py` from this story. |
    | 3.5 Nerve routing | `NervePort` | NerveSystem implements `NervePort.startup` + `NervePort.route_command`. |
    | 3.6 Mode restore | `HandsPort.restore_mode` | Win32HandsAdapter ships in `adapters/win32/actions.py`; calls into `AuditLogger` per-action (Story 1.8 carry-forward). |
    | 3.7 Shutdown flow | `RitualPort.begin_shutdown`, `BrainPort.store_session`, `VoicePort.generate_shutdown_confirmation` | RitualSystem orchestrates; Brain persists; Voice confirms. |
    | 4.1 Eyes Win32 context capture | `EyesPort.capture_current_workspace` | Win32EyesAdapter ships in `adapters/win32/context.py`. |
    | 5.1 Transparency command | `BrainPort.get_transparency_model` | Brain adapter extends `TransparencyModel` (schema-expanded from Story 1.9's minimal T1 shape). |
    | 5.2 Selective forget | `BrainPort.delete_matching`, `BrainPort.confirm_deletion` | Brain adapter's deletion flow. |
    | v0.15 Shield activation | `ShieldPort` | Ships `Win32ShieldAdapter` in `adapters/win32/shield.py`. `NoOpShieldAdapter` from this story remains available for tests + offline tier. |

    **Nine downstream stories** directly consume ports authored here. The biggest risk vector is **method-set drift**: if Story 3.2 (BriefingAggregate) needs a field that `BriefingAggregate` in this story doesn't expose, Story 3.2 extends `systems/brain/models.py` — not `ports/brain.py`. Port method signatures are the stable contract.

## Tasks / Subtasks

- [x] Create domain model files under `systems/*/models.py` (AC: #5)
  - [x] `systems/brain/models.py` — Session, SessionData, SessionSummary, MemoryItem, BriefingAggregate, ModeInfo, DeletionPreview, DeletionResult, TransparencyModel (9 frozen dataclasses)
  - [x] `systems/eyes/models.py` — WindowContext, WorkspaceSnapshot (2 frozen dataclasses)
  - [x] `systems/hands/models.py` — ActionRequest, ActionResult (2 frozen dataclasses)
  - [x] `systems/ritual/models.py` — BriefingViewModel, ShutdownData (2 frozen dataclasses)
  - [x] `systems/skin/models.py` — Command (1 frozen dataclass with default field)
  - [x] No `voice/models.py` created — `VoicePort` returns plain `str` in T1 (AC #4).
  - [x] No `nerve/models.py` created — `NervePort` consumes `Command` from `systems/skin/models.py`.

- [x] Create 8 port files under `ports/*.py` (AC: #1, #2, #3, #4, #7, #8)
  - [x] `ports/brain.py` — BrainPort Protocol with 7 async methods
  - [x] `ports/eyes.py` — EyesPort Protocol with 1 async method
  - [x] `ports/hands.py` — HandsPort Protocol with 1 async method
  - [x] `ports/shield.py` — ShieldPort Protocol with 2 async methods, decorated `@runtime_checkable`
  - [x] `ports/voice.py` — VoicePort Protocol with 3 async methods
  - [x] `ports/ritual.py` — RitualPort Protocol with 2 async methods
  - [x] `ports/skin.py` — SkinPort Protocol with 6 async methods
  - [x] `ports/nerve.py` — NervePort Protocol with 2 async methods

- [x] Update `ports/__init__.py` with alphabetized re-exports (AC: #10)
  - [x] Import all 8 Port classes
  - [x] Export via `__all__` alphabetically

- [x] Create Shield no-op adapter (AC: #6)
  - [x] `adapters/shield/__init__.py` — new sub-package with docstring
  - [x] `adapters/shield/noop.py` — `NoOpShieldAdapter`, two inert async methods

- [x] Create isolation + shape tests (AC: #13)
  - [x] `tests/unit/ports/test_port_isolation.py` — 92 parametrized rows
  - [x] Chose AC #15's duplicate-verbatim option (forbidden-sets + AST helpers mirrored with "keep in sync" comment — keeps the two isolation-test files independent)

- [x] Create Shield adapter behavior tests (AC: #14)
  - [x] `tests/unit/adapters/test_noop_shield_adapter.py` — 15 rows (11 `ActionType` parametrize + 4 behavioral)

- [x] Verify quality gate (AC: #16, #17)
  - [x] `uv run ruff check src/ tests/` — clean
  - [x] `uv run ruff format --check src/ tests/` — 60 files already formatted
  - [x] `uv run mypy src/ tests/` — strict mode, zero `# type: ignore`, 60 source files clean
  - [x] `uv run pytest` — 621 passed, 1 skipped (delta from Story 1.8: +107 tests)
  - [x] Repo tree clean — no cache dirs, no DB files

- [ ] Commit with conventional message (deferred to user)
  - [ ] Commit message: `"Story 1.9: port interfaces + shield no-op adapter (ports/, adapters/shield/)"`

### Review Findings (2026-04-15)

Parallel adversarial review ran Blind Hunter + Edge Case Hunter + Acceptance Auditor. Acceptance Auditor reports all 18 ACs satisfied. Findings below are code-quality + test-robustness improvements — no AC violations.

**Patch (unambiguous fixes, 11):**

- [x] [Review][Patch] Remove `del action_type` from `NoOpShieldAdapter.allow_action` — non-idiomatic dead code; if lint complains, use `# noqa: ARG002` [src/nova/adapters/shield/noop.py:64-66]
- [x] [Review][Patch] Rewrite clumsy docstring sentence ("facade / this-file-here adapter split") in `shield/noop.py` for readability [src/nova/adapters/shield/noop.py:33-37]
- [x] [Review][Patch] Collapse redundant outer `if method.args.defaults or method.args.kw_defaults` + inner re-check in `test_port_method_parameters_have_no_defaults` to a single clear condition [tests/unit/ports/test_port_isolation.py]
- [x] [Review][Patch] `_collect_annotation_names` double-counts dotted names (appends both `["typing", "Protocol"]` AND `"typing.Protocol"`) — either skip inner `ast.Name` inside `ast.Attribute` walks or deduplicate output [tests/unit/ports/test_port_isolation.py]
- [x] [Review][Patch] `test_domain_models_use_tuple_not_list_for_sequence_fields` misses `list[T] | None` unions (`typing.get_origin(list[str] | None)` returns `UnionType`, not `list`). Recurse through `Union`/`UnionType` args and also reject nested `list` inside `tuple[list[...], ...]` [tests/unit/ports/test_port_isolation.py]
- [x] [Review][Patch] `test_port_signatures_use_only_allowed_types` has two blind spots: (a) string-form annotations like `-> "sqlite3.Row"` (walk `ast.Constant(str)` via `ast.parse(val, mode="eval")`); (b) `posonlyargs`, `vararg`, `kwarg` annotations never inspected — extend iteration [tests/unit/ports/test_port_isolation.py]
- [x] [Review][Patch] `test_all_port_methods_are_async_with_ellipsis_body` doesn't reject decorated methods — `@property`/`@staticmethod`/`@classmethod` slip through. Assert `method.decorator_list == []` per method [tests/unit/ports/test_port_isolation.py]
- [x] [Review][Patch] Add `test_shield_port_is_only_runtime_checkable_port` — assert `getattr(ShieldPort, '_is_runtime_protocol', False) is True` AND every other port's flag is `False`. Locks AC #11's design invariant against silent drift if a future story adds `@runtime_checkable` elsewhere [tests/unit/ports/test_port_isolation.py]
- [x] [Review][Patch] Add `test_noop_shield_adapter_methods_are_coroutines` using `inspect.iscoroutinefunction` — `@runtime_checkable` `isinstance` only checks method NAMES, so a regression that removes `async` from the adapter's methods would still pass `isinstance(adapter, ShieldPort)` but crash at `await` time [tests/unit/adapters/test_noop_shield_adapter.py]
- [x] [Review][Patch] `test_domain_models_are_frozen_dataclasses` iterates `module.__all__` only — silently skips private non-frozen dataclasses. Walk all classes in the module via `inspect.getmembers(module, inspect.isclass)` with a same-module filter [tests/unit/ports/test_port_isolation.py]
- [x] [Review][Patch] `FORBIDDEN_SIGNATURE_PREFIXES` manually mirrors a subset of `FORBIDDEN_TOPLEVEL_MODULES` — derive one from the other to prevent drift (e.g., `FORBIDDEN_SIGNATURE_PREFIXES = tuple(sorted(FORBIDDEN_TOPLEVEL_MODULES))`) [tests/unit/ports/test_port_isolation.py]

**Deferred (pre-existing or out-of-scope for Story 1.9, 13):**

- [x] [Review][Defer] `Mapping[str, object] | None` on `ActionRequest.details` permits runtime `dict` mutation — freeze via `MappingProxyType` in `__post_init__` [src/nova/systems/hands/models.py] — target Story 3.6 (Hands implementation owns enforcement)
- [x] [Review][Defer] `DeletionPreview` has no nonce/id binding to originating `delete_matching` call — two concurrent preview flows could confirm the wrong one [src/nova/systems/brain/models.py] — target Story 5.2 (Selective Forget)
- [x] [Review][Defer] `NervePort.route_command` returns `None` — error surface for unknown verbs / invalid targets / tier-gated commands undocumented [src/nova/ports/nerve.py] — target Story 3.5 (Nerve routing)
- [x] [Review][Defer] `VoicePort.generate_prose_enrichment` returns `str | None` but no cross-module test enforces Ritual's null-branch rendering against `BriefingViewModel.prose_enrichment` [src/nova/ports/voice.py] — target Story 3.3 (BriefingViewModel rendering)
- [x] [Review][Defer] Domain models use stringly-typed ISO timestamps (`started_at: str`, `created_at: str`, `captured_at: str`, etc.) throughout — callers must reparse to sort/diff [src/nova/systems/*/models.py] — project-wide refactor, revisit when a caller's ergonomics demand typed `datetime`
- [x] [Review][Defer] `Command.verb: str` could be `Literal["mode","shutdown","status","forget","memory","help"]` or an enum for typed dispatch safety [src/nova/systems/skin/models.py] — target Story 3.4 (Command parser owns verb vocabulary)
- [x] [Review][Defer] `BrainPort.delete_matching(target: str)` — "opaque reference" privacy invariant lives in docstring only; string type cannot carry the "never raw app name" constraint [src/nova/ports/brain.py] — target Story 5.2 (introduce `OpaqueTarget` NewType)
- [x] [Review][Defer] `BriefingAggregate` is triple-nullable (`last_session / last_snapshot / last_seed` all `| None`) with 2³=8 possible states — no valid-state matrix documented [src/nova/systems/brain/models.py] — target Story 3.2 (BriefingAggregate population) to document or tighten
- [x] [Review][Defer] `WindowContext.is_opaque` invariant ("if `True`, all identity fields are `None`") enforced in docstring only; `__post_init__` validator absent [src/nova/systems/eyes/models.py] — target Story 4.2 (exclusion boundary at capture)
- [x] [Review][Defer] `ActionResult` has tri-state `success`/`reason` with no invariant check — `ActionResult(success=True, reason="failed")` type-checks but is semantic nonsense [src/nova/systems/hands/models.py] — target Story 3.6 (Hands actions owns the contract)
- [x] [Review][Defer] No smoke / integration test imports the full port + model graph to catch circular-import regressions across systems [tests/] — target Story 1.10 (composition root naturally instantiates the full graph)
- [x] [Review][Defer] Port docstrings cite `architecture.md:NNN` line numbers that will drift when architecture.md is edited [src/nova/ports/*.py] — low-value cleanup; revisit if cites actually stale
- [x] [Review][Defer] `test_ports_only_import_from_system_models_modules` accepts `segments[3] == "models"` but allows deeper nesting (`nova.systems.brain.models.internal`) — speculative, no current caller [tests/unit/ports/test_port_isolation.py]

Dismissed (handled elsewhere or noise): ~18 findings — e.g., AC #15 verbatim-duplication decision, established project conventions (flat test layout, `list[str]` `__all__`), structural-subtyping intentional extras behavior, test-name renames that preserve semantic coverage.

## Dev Notes

### Critical Architecture Rules (pinned from project-context.md + architecture.md)

Carry-forward with rationale specific to this story:

- **"Ports use Protocols/ABCs."** (project-context.md:54) — this story materializes the boundary. System logic depends on `ports/*.py` Protocol classes, never on concrete adapter classes. Structural subtyping (no `class SqliteBrainAdapter(BrainPort):` nominal inheritance required) is the T1 convention — adapters satisfy the port by shape.
- **"Ports-and-adapters is load-bearing."** (project-context.md:62) — Story 1.10's composition root is the single wiring point; this story is the contracts layer that makes that wiring possible.
- **"No system imports another system's adapter."** (project-context.md:63) — port files explicitly never import from `adapters/`. Adapters import their owning port (for structural conformance via mypy, not runtime ABC).
- **"Voice generates text; Skin renders it."** (project-context.md:64) — reflected in `VoicePort.generate_*` returning `str` and `SkinPort.render_*` consuming `str` / view-models, never each other's data shapes.
- **"Nerve is an orchestrator, not a router."** (project-context.md:65) — `NervePort` has `startup` + `route_command` only. No per-system proxying methods; Nerve's orchestration logic is internal to `NerveSystem` (Story 3.5), not a port surface.
- **"Operational output bypasses Voice."** (project-context.md:66) — reflected in `SkinPort.render_progress` and `SkinPort.render_shutdown_card` existing as direct-to-skin calls (no prose generation layer), distinct from `SkinPort.render_response` which receives Voice-generated text.
- **"Brain owns all SQLite tables."** (project-context.md:67) — reflected in `BrainPort` being the sole surface for session / memory / transparency / deletion operations. Other ports do NOT expose session-read / memory-read / deletion methods.
- **"Ritual owns ceremony logic; Nerve decides when ceremonies run."** (project-context.md:68) — reflected in `RitualPort.build_briefing` (ceremony) vs `NervePort` NOT having a `should_run_briefing` method (policy is internal to NerveSystem).
- **"Config module is the single YAML reader."** (project-context.md:69) — reflected in no port method taking a `dict[str, object]` YAML blob. `HandsPort.restore_mode` consumes the already-parsed `ModeConfig` from `core/config.py`, not raw YAML.
- **"PromptBuilder is a trust boundary, not part of Brain."** (project-context.md:70) — reflected in `BrainPort` NOT having a `build_prompt` method. `VoicePort.generate_prose_enrichment` is where PromptBuilder's output will be consumed (Story 3.3).
- **"Three capability tiers are real product behavior."** (project-context.md:71) — reflected in `RitualPort.build_briefing` taking `tier: CapabilityTier` as an input parameter. Systems don't guess the tier; they are told.
- **"Exclusion filtering happens at capture, not rendering."** (project-context.md:72) — reflected in `WindowContext.is_opaque: bool` field (AC #5). The opacity flag is set by Eyes at the capture boundary; downstream consumers check the flag and drop raw app name / window title / process name from derived output.
- **"Audit trail is append-only and cross-cutting."** (project-context.md:73) — NOT in any port surface for this story. `AuditLogger` from Story 1.8 is consumed directly by systems that perform auditable actions (Hands, Nerve for tier changes, Brain for deletions), not routed through a port. This is deliberate — the audit boundary is `AuditLogger` itself (architecture.md:1187), not a port method.
- **"Event bus for inter-system communication."** (project-context.md:74) — reflected in no port method having an `Event` parameter. Events flow through `EventBus` (Story 1.3), not through port methods. `NervePort` subscribes to events via the bus in Story 3.5; that subscription is an implementation detail, not a port method.
- **"Dependency direction is one-way."** (project-context.md:76) — ports depend on `core/` (types, config, events, exceptions) and on `systems/{X}/models` (pure data classes, no logic). Ports do NOT depend on `app.py`, `cli.py`, `adapters/*`, or `systems/{X}/system`.
- **"Adapters may translate, never decide."** (project-context.md:77) — reflected in `NoOpShieldAdapter` having no policy at all (inert returns). Future `Win32ShieldAdapter` (v0.15) will translate Win32 focus state into bool; any policy decisions about "is this app a distraction" belong in a `systems/shield/system.py` module (v0.15 scope), not the adapter.
- **"Persist before emit."** (project-context.md:78) — NOT a port-level concern in this story. Write-then-emit enforcement lives in each concrete adapter (Brain's `store_session` will await the SQLite insert before Ritual emits `SessionEnded`).
- **"One public entrypoint per system."** (project-context.md:83) — reflected in `ports/__init__.py`'s `__all__` containing exactly one Port class per system.
- **"Tier evaluation is centralized."** (project-context.md:84) — reflected in `RitualPort.build_briefing` taking `tier` as input (not calling `TierManager.current` internally). Systems receive tier state from Nerve's orchestration, never independently query it via a port.
- **"Type annotations on everything."** (project-context.md:35) — every parameter + return type annotated. `X | None` syntax throughout. `list[T]` not `List[T]`. No `typing.Optional`, no `typing.List`.
- **"Dataclasses for all domain types."** (project-context.md:38) — every model in `systems/*/models.py` is `@dataclass(frozen=True)`.
- **"No mutable default values."** (project-context.md:52) — reflected in `Command.is_contextual: bool = False` (bool default, not container). `BriefingAggregate.available_modes: tuple[...]` (immutable container), not `list[...] = field(default_factory=list)`.
- **"Enum for constrained values."** (project-context.md:42) — reflected in `ActionRequest.action_type: ActionType`, `BriefingViewModel.state: BriefingState`, `BriefingViewModel.tier: CapabilityTier`, etc. Never `str`.
- **"Absolute imports only."** (project-context.md:43) — every port and model file uses `from nova.core.types import ...`, never `from ..core.types import ...`. AST-locked via `test_no_relative_imports`.
- **"No raw string event types."** (project-context.md:39) — ports never carry raw event type strings; they consume typed `Event` subclasses from `core/events.py` when needed.
- **"No wildcard imports."** (project-context.md:130) — every port/model imports specific names.

### Previous Story Intelligence — Story 1.8 (done 2026-04-15)

Story 1.8 landed the audit logger. Key carry-forwards for Story 1.9:

- **Test file placement mirrors systems under test, flat directory layout.** `tests/unit/ports/test_port_isolation.py` lives flat under `tests/unit/ports/`, no subdirectory, no `__init__.py`. `tests/unit/adapters/test_noop_shield_adapter.py` lives flat under `tests/unit/adapters/`. Matches `test_audit.py`, `test_tiers.py`, `test_config.py` precedent.
- **AST-based static-analysis tests, not text regex.** Story 1.6 / 1.7 / 1.8 precedent, called out in [memory/feedback_ast_static_analysis_tests.md](../memory/feedback_ast_static_analysis_tests.md). Port method-shape, ordering, and type-reference checks must use `ast.walk` + `ast.ClassDef` / `ast.AsyncFunctionDef` inspection — not text regex on the source. Rationale from memory: regex trips on docstrings and comments that mention forbidden names innocently. AST walks only visit actual code constructs.
- **Parametrize over sets to catch future additions.** `test_noop_shield_adapter_allow_action_returns_true_for_all_action_types` parametrizes over `list(ActionType)` — adding a 12th `ActionType` member auto-extends the test without a manual update. Same pattern was used in Story 1.8's `test_log_action_accepts_all_action_type_members`.
- **Method-name / ordering / alphabetization tests.** Story 1.2 + 1.8 precedent: alphabetical ordering of `__all__` exports is a locked test, not a convention to hope for. `test_ports_init_exports_alphabetical_and_complete` extends this pattern to `ports/__init__.py`.
- **Structured-logging `extra={...}` pattern.** N/A for this story — ports are pure type definitions, no logging. Shield no-op adapter is explicitly silent (AC #6).
- **Ruff rules active:** `E`, `F`, `I`, `UP`, `B`, `SIM`, `T20`. None trigger on well-formed Protocol classes or frozen dataclasses.
- **mypy strict, zero `# type: ignore` in production code.** Protocol method bodies are `...` — mypy accepts them as abstract-by-convention. No `cast`, no `Any`. `Mapping[K, V]` from `collections.abc` for read-only dict-shaped parameters; `Sequence[T]` for read-only list-shaped parameters.
- **Commit convention (Story 1.4/1.5/1.6/1.7/1.8 carry-forward):** terse, imperative, story ID prefix + brief scope in parens. Expected: `"Story 1.9: port interfaces + shield no-op adapter (ports/, adapters/shield/)"`.
- **"New layer" pattern established by Stories 1.3 / 1.4 / 1.5 / 1.6 / 1.7 / 1.8 for `core/`.** Story 1.9 extends the pattern to `ports/` and `adapters/` for the first time. Every new file ships with: a module docstring, typed imports, alphabetized `__all__`, and at least one dedicated test file. The only module-level difference is that ports are type-only (no runtime logic), so test coverage focuses on AST-level shape + isolation rather than behavioral verification.
- **"No carve-out" forbidden-imports pattern.** `ports/*.py` has NO forbidden-module carve-out (no Protocol file imports `sqlite3`, `yaml`, `rich`, `anthropic`, or any Win32 module). The full `FORBIDDEN_TOPLEVEL_MODULES` denylist applies. Same posture as Story 1.7 (`tiers.py`) and Story 1.8 (`audit.py`).
- **Two-function clock pattern (Story 1.3).** N/A here — ports are type definitions, no timestamps generated.
- **`Mapping[str, object]` over `dict[str, Any]`** — applied in `ActionRequest.details: Mapping[str, object] | None` (AC #5). Same posture as Story 1.8's `AuditLogger.log_action(details: Mapping[str, object] | None)`.

### Git Intelligence — last 5 commits

```
f2ef02b Story 1.8: audit logger (core/audit.py)
ab2f676 Story 1.7: capability tier state machine (core/tiers.py)
ba24622 Story 1.6: YAML config loader + immutable NovaConfig (core/config.py)
c64849c Story 1.5: migration runner + 001_initial_schema (core/storage/migrations)
4ae06ee Story 1.4: SQLite storage engine (core/storage/engine.py)
```

- **Commit style:** terse, imperative, story ID prefix + brief scope in parens. Follow exactly.
- **Scope pattern:** `"Story 1.N: {what} ({where})"`. Story 1.9 scope spans two directories (`ports/` and `adapters/`) — list both in the parens.
- **No prior `ports/*.py` port files in the tree.** Greenfield for this story — only [`src/nova/ports/__init__.py`](src/nova/ports/__init__.py) exists (one-line placeholder docstring).
- **No prior `adapters/shield/` directory in the tree.** `src/nova/adapters/__init__.py`, `src/nova/adapters/claude/__init__.py`, `src/nova/adapters/rich/__init__.py`, `src/nova/adapters/sqlite/__init__.py`, `src/nova/adapters/win32/__init__.py` all exist (empty package markers). This story creates the `src/nova/adapters/shield/` sub-package as a new peer.
- **No prior `systems/*/models.py` files.** Each `systems/{brain,eyes,hands,nerve,ritual,shield,skin,voice}/__init__.py` exists as a one-line docstring placeholder. This story adds `models.py` to brain, eyes, hands, ritual, skin (5 of 8).

### Latest Tech Information (as of 2026-04-15)

- **Python 3.12.x** — `typing.Protocol` + structural subtyping + `@runtime_checkable` are all stable since 3.8 / 3.9. `collections.abc.Mapping` / `Sequence` (PEP 585) is the canonical home; do NOT import `Mapping` from `typing`. ruff `UP035` enforces this.
- **`typing.Protocol`** — structural subtyping, no nominal inheritance required for conformance. mypy 1.x strict mode fully supports Protocol checking. Adding `@runtime_checkable` enables `isinstance(obj, ShieldPort)` at runtime (uses `__instancecheck__` introspection). Without the decorator, `isinstance` raises `TypeError` — tests that need runtime checks must either use the decorator (this story: ShieldPort only) or use the `getattr(obj, "method_name", None)` probe pattern.
- **Frozen dataclass semantics** — `@dataclass(frozen=True)` prevents attribute rebinding but NOT container mutation. `list` field on a frozen dataclass is still mutable via `instance.field.append(x)`. **Use `tuple[T, ...]` for sequence fields** — matches the `ModeRestored.apps_launched` / `ModeRestored.apps_failed` precedent from [src/nova/core/events.py:245](src/nova/core/events.py#L245). Story 1.3 carry-forward.
- **`from __future__ import annotations`** — every port file opens with this (Story 1.1 convention). Enables `X | None` syntax and lazy evaluation of annotations. Required for forward references (e.g., `BriefingViewModel` referencing `ModeInfo` from a different module without ordering issues).
- **PEP 695 `type` aliases** (`type ActionResult = Literal[...]`) — established in Stories 1.4 / 1.8. Not needed in this story — ports use concrete domain types, not type aliases. But if a future Story 1.9 code-review finding suggests a type alias, use the PEP 695 syntax (ruff `UP040` enforces).
- **`typing.Literal`** — stable since 3.8. Not used in this story's ports directly, but `ShutdownData.prompt_text` etc. could later be `Literal[...]` if we pin specific literals. For Story 1.9, plain `str` is sufficient.
- **mypy `--strict` with Protocols** — Protocol methods with `...` bodies are implicitly abstract. mypy does NOT complain about missing implementations unless the Protocol is instantiated directly (which adapters never do — they are structurally conformant plain classes). `reveal_type(adapter_instance)` shows the concrete class; structural conformance is checked at the call site when adapters are passed as Protocol-typed arguments.
- **No `typing_extensions` needed** — all Protocol features used here (Protocol, runtime_checkable) are in stdlib `typing` for Python 3.12.

### Project Structure Notes

**New files (15 production + 2 test = 17 total new files) + 1 modified:**

Production (new):
1. `src/nova/ports/brain.py`
2. `src/nova/ports/eyes.py`
3. `src/nova/ports/hands.py`
4. `src/nova/ports/shield.py`
5. `src/nova/ports/voice.py`
6. `src/nova/ports/ritual.py`
7. `src/nova/ports/skin.py`
8. `src/nova/ports/nerve.py`
9. `src/nova/systems/brain/models.py`
10. `src/nova/systems/eyes/models.py`
11. `src/nova/systems/hands/models.py`
12. `src/nova/systems/ritual/models.py`
13. `src/nova/systems/skin/models.py`
14. `src/nova/adapters/shield/__init__.py`
15. `src/nova/adapters/shield/noop.py`

Modified:
16. `src/nova/ports/__init__.py` — replace one-line docstring with 8 re-exports + `__all__`

Tests (new):
17. `tests/unit/ports/test_port_isolation.py`
18. `tests/unit/adapters/test_noop_shield_adapter.py`

**Alignment with unified project structure:** Fits architecture.md:1296–1398 exactly. `ports/{system}.py` pattern matches architecture.md:1316–1325 verbatim. `adapters/shield/{adapter_name}.py` pattern matches architecture.md:1362–1377 (new sub-package extends the existing `adapters/claude/`, `adapters/rich/`, `adapters/sqlite/`, `adapters/win32/` siblings). `systems/{system}/models.py` pattern matches architecture.md:1329–1360.

**Detected conflicts or variances:** None. The architecture document specifies `systems/shield/system.py` exists as a T1 stub (line 1343), but the epic AC clarifies the stub is adapter-layer, not system-layer. This story aligns with the epic AC (no `systems/shield/system.py` created) — architecture.md:1343 is an aspirational structure document, not a pinned Story 1.9 requirement.

**Note on `systems/voice/models.py` deferral:** Architecture.md:1347 lists `BriefingText`, `ResponseText`, `ProseEnrichment` as voice domain types. This story's `VoicePort` uses plain `str` returns for all three methods (AC #4 table). Rationale: wrapping `str` in a single-field dataclass provides no T1 value (no invariant, no additional context, no extension point). If Story 3.3 decides prose-enrichment needs to carry metadata (e.g., `tier_used`, `tokens_consumed`), that story introduces `systems/voice/models.py`. Deferring avoids premature type design.

**Note on `systems/nerve/models.py` deferral:** Architecture.md:1360 mentions "Command routing tables, policy rules" as nerve domain types. None of those are exposed through `NervePort` (AC #4 table). Routing tables and policy rules are internal to NerveSystem (Story 3.5). Deferred.

### Testing standards summary

- **Test framework:** pytest + pytest-asyncio (auto mode, already enabled in pyproject.toml per Story 1.3 setup). Shield adapter tests are `async def` because `ShieldPort` methods are async.
- **Port tests** live in `tests/unit/ports/test_port_isolation.py`. AST-based, no async execution — tests load port modules, parse them to AST, walk the tree.
- **Adapter tests** live in `tests/unit/adapters/test_noop_shield_adapter.py`. Behavioral tests are `async def`.
- **No integration tests in this story.** Integration coverage of port-adapter conformance happens implicitly when Stories 1.10 / 3.1 / 4.1 ship concrete adapters with their own test suites.
- **mypy strict** applies to every production file AND every test file. Port-isolation tests use `ast.Module`, `ast.ClassDef`, `ast.AsyncFunctionDef` — all fully typed in stdlib stubs.
- **No filesystem / DB / network** in any new test. Ports are pure type definitions; the Shield no-op adapter has no I/O; isolation tests only parse Python source.
- **`await` in Shield adapter tests required** — every `ShieldPort` method is async. Tests can't call `adapter.is_focus_protected()` without awaiting; mypy strict catches missing `await` as type error (`Coroutine[...]` returned instead of `bool`).
- **Parametrized tests over the 8 ports + 11 ActionType members.** Heavy parametrization is the shape of this story's test suite — don't write 8 near-identical tests by hand.
- **Coverage target:** 100% of every port file (trivial — bodies are `...`) and 100% of `NoOpShieldAdapter` (trivial — two-line methods). Coverage reports should not flag anything in this story's files.
- **Failure-path coverage** — N/A for ports (no failure paths, no exceptions raised). For the shield adapter, the no-op contract is "never raises, never returns None" — tests assert the `True`/`False` identity (not truthy/falsy), which is the strongest "no future drift" guard.

### Critical Constraints (carry-forward + story-specific)

- **Protocol method bodies are `...` only.** No `@abstractmethod` (conflicts with Protocol semantics), no `raise NotImplementedError` (adds a runtime failure mode for no gain), no default implementations. Ellipsis-only bodies are the idiomatic Protocol shape in Python 3.12 stdlib typing docs.
- **No `@runtime_checkable` on non-Shield ports.** Adding it doesn't break anything, but it opts into `__instancecheck__` overhead at every `isinstance` call site — and because only `NoOpShieldAdapter` is structurally verified at runtime in this story, only `ShieldPort` needs the decorator. Story 1.10's composition root will not use `isinstance` against any port (it relies on mypy strict for structural conformance). Later stories that introduce test-time mock adapters may opt their target port in to `@runtime_checkable` at that time — Story 1.9 does not pre-add the decorator speculatively.
- **No port method has a default parameter value** except `Command.is_contextual: bool = False` (which is on the domain dataclass in `systems/skin/models.py`, not on a port method). Default values on port methods muddle the contract; callers should always pass every argument explicitly. Locked by `test_port_method_parameters_have_no_defaults` (check `inspect.signature(method).parameters[name].default is inspect.Parameter.empty` for every parameter except `self`).
- **No `Callable` parameter in any port method.** The epics AC does not require a callback-shaped port method. If a future story needs one (e.g., a streaming event handler), it introduces the method then. Adding an empty-spec callback in Story 1.9 leaves an untestable hole.
- **No `Any` anywhere in production code.** Enforced by `disallow_any_generics` + `disallow_any_explicit` in mypy strict config.
- **Use `from __future__ import annotations` at the top of every port file.** Enables forward references (e.g., `RitualPort.build_briefing` annotating `aggregate: BriefingAggregate` even though `BriefingAggregate` lives in a cross-system model module) without an ordered import. Story 1.1 convention.
- **Every dataclass field type is explicit.** No `field(default_factory=tuple)` without a type annotation (ruff/mypy catches bare defaults, but stylistically the annotation reads the intent at the declaration site). Pattern: `apps_launched: tuple[str, ...] = field(default_factory=tuple)`.
- **Frozen-dataclass equality is structural.** Two `Session` instances with identical fields are equal (`==` returns True). This is what `@dataclass(frozen=True)` provides by default (also implies hashable). Tests that compare `Session` instances rely on this — do NOT override `__eq__` / `__hash__`.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.9: Port Interfaces & Shield Stub](../planning-artifacts/epics.md) — canonical AC, lines 817–831.
- [Source: _bmad-output/planning-artifacts/epics.md#Epic 1: Project Foundation & Core Infrastructure](../planning-artifacts/epics.md) — epic objectives.
- [Source: _bmad-output/planning-artifacts/architecture.md#Port & Adapter Convention](../planning-artifacts/architecture.md) — lines 948–986, the Protocol rules + BrainPort sketch + anti-patterns.
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision 2: Event Flow — T1 Continuity Loop](../planning-artifacts/architecture.md) — lines 343–400, the T1 loop that pins the minimum method set per port.
- [Source: _bmad-output/planning-artifacts/architecture.md#Complete Project Directory Structure](../planning-artifacts/architecture.md) — lines 1296–1398, the `ports/`, `systems/`, `adapters/` layout.
- [Source: _bmad-output/planning-artifacts/architecture.md#Architectural Boundaries](../planning-artifacts/architecture.md) — lines 1456–1465, the port boundary table pinning what crosses each boundary.
- [Source: _bmad-output/planning-artifacts/architecture.md#Composition Root Convention](../planning-artifacts/architecture.md) — lines 1059–1102, the app.py composition pattern ports will be wired into in Story 1.10.
- [Source: _bmad-output/planning-artifacts/architecture.md#Event Bus Convention](../planning-artifacts/architecture.md) — lines 988–1057, why port methods do NOT take `Event` parameters.
- [Source: _bmad-output/planning-artifacts/architecture.md#Command Routing Convention](../planning-artifacts/architecture.md) — lines 1104–1126, the `Command` dataclass shape (Story 1.9 places it in `systems/skin/models.py`; see AC #11 for why it is not split into a separate `commands.py` module in this story) + `NervePort.route_command`.
- [Source: _bmad-output/planning-artifacts/architecture.md#Systems Overview](../planning-artifacts/architecture.md) — lines 315–322, each system's T1 responsibilities (the source for port method shortlists).
- [Source: _bmad-output/project-context.md](../project-context.md) — rules 35 (type annotations), 38 (dataclasses), 42 (enums), 43 (absolute imports), 52 (no mutable defaults), 54 (ports use Protocols/ABCs), 62 (ports-and-adapters load-bearing), 63 (no cross-system adapter imports), 64 (Voice/Skin separation), 65 (Nerve is orchestrator), 66 (operational output bypasses Voice), 67 (Brain owns SQLite), 68 (Ritual owns ceremony), 69 (Config is single YAML reader), 74 (event bus), 76 (one-way dependency), 77 (adapters translate), 83 (one entrypoint per system), 84 (tier centralized), 130 (no wildcard imports).
- [Source: src/nova/core/types.py](../../src/nova/core/types.py) — `ActionType`, `BriefingState`, `CapabilityTier`, `MemoryCategory`, `SnapshotType`, `BluntnessLevel` (all Story 1.2 StrEnums, consumed by port signatures + models).
- [Source: src/nova/core/config.py](../../src/nova/core/config.py) — `ModeConfig`, `ExclusionConfig`, `UserSettings`, `NovaConfig` (Story 1.6 dataclasses, consumed by `HandsPort.restore_mode`).
- [Source: src/nova/core/events.py](../../src/nova/core/events.py) — `Event` + typed subclasses (Story 1.3). Referenced in the Command Routing context (Skin parses input → `Command` → Nerve routes), NOT directly parameters to port methods.
- [Source: `src/nova/core/__init__.py`](../../src/nova/core/__init__.py) — current `__all__` of 37 re-exports (Story 1.8). NOT modified by this story.
- [Source: `src/nova/ports/__init__.py`](../../src/nova/ports/__init__.py) — one-line docstring placeholder; replaced by AC #10.
- [Source: `src/nova/systems/shield/__init__.py`](../../src/nova/systems/shield/__init__.py) — one-line docstring; left as-is per AC #9.
- [Source: `src/nova/adapters/__init__.py`](../../src/nova/adapters/__init__.py) — one-line docstring; `adapters/shield/` sub-package added alongside `adapters/claude/`, `adapters/rich/`, `adapters/sqlite/`, `adapters/win32/`.
- [Source: tests/unit/core/test_core_isolation.py](../../tests/unit/core/test_core_isolation.py) — the AST-isolation test pattern the new `test_port_isolation.py` extends. Exports `FORBIDDEN_TOPLEVEL_MODULES`, `FORBIDDEN_NOVA_PREFIXES`, `_all_imports`, `_dynamic_import_targets`, `_dynamic_import_full_targets`, `_has_forbidden_prefix` — all candidates for cross-module reuse (AC #15).
- [Source: _bmad-output/implementation-artifacts/1-8-audit-logger.md](./1-8-audit-logger.md) — prior story. Test file layout, AST-based static-analysis precedent, commit style, "no carve-out" forbidden-imports posture.
- [Source: _bmad-output/implementation-artifacts/1-6-config-loader-and-immutable-novaconfig.md](./1-6-config-loader-and-immutable-novaconfig.md) — `ModeConfig` shape that `HandsPort.restore_mode` consumes.
- [Source: _bmad-output/implementation-artifacts/1-3-event-bus-and-typed-event-definitions.md](./1-3-event-bus-and-typed-event-definitions.md) — `Event` typed-subclasses pattern; `tuple[T, ...]` immutability rationale for frozen-dataclass fields.
- [Source: C:\Users\sayuj\.claude\projects\c--Projects-AI-Assistant\memory\feedback_ast_static_analysis_tests.md](../../../../Users/sayuj/.claude/projects/c--Projects-AI-Assistant/memory/feedback_ast_static_analysis_tests.md) — "For N.O.V.A., use ast.walk + ast.Call inspection, not text regex — avoids docstring false positives."

## Dev Agent Record

### Agent Model Used

claude-opus-4-6[1m]

### Debug Log References

- Ruff initially flagged `SIM102` (nested `if`) twice in `test_ports_only_import_from_system_models_modules` and `E501` (line too long) on a docstring — all three resolved by flattening the nested conditions with `and` and tightening the docstring line.
- mypy initially flagged `comparison-overlap` on `assert ModeInfo is not ModeConfig` — the identity check was a tautology from the type checker's perspective. Replaced with shape-based field-difference assertions that genuinely catch future aliasing drift.
- Ruff format rewrote three files to canonical width — intentional, left in place.
- `BriefingAggregate.last_snapshot` is typed as `WorkspaceSnapshot | None` (not `object | None`). Brain's models import the Eyes `WorkspaceSnapshot` type directly — permitted because `.models` is Story 1.9 AC #8's one portable cross-system suffix, and one `.models` module importing from another stays within that rule.
- Helper-sharing: chose AC #15's **verbatim duplication** path (frozensets + AST helpers copied into `test_port_isolation.py` with a "mirror of" comment). Rationale: `tests/` has no `__init__.py` files per the existing Story 1.4+ flat-test-layout precedent, so a cross-test-package import (`from tests.unit.core.test_core_isolation import ...`) would have required inventing a new package structure. Duplication is ~30 lines and trivially reviewable.
- `NoOpShieldAdapter.allow_action` uses `del action_type  # unused` to discard the parameter explicitly — keeps the parameter named identically to `ShieldPort.allow_action` (mandatory for structural conformance) without triggering any unused-parameter lint.

### Completion Notes List

- All 18 Acceptance Criteria satisfied. 8 port Protocol classes authored under `src/nova/ports/`, all async-method ellipsis bodies, alphabetized `__all__` re-exports, `ShieldPort` alone decorated `@runtime_checkable`. Domain models live under 5 `systems/*/models.py` files; all 16 models are frozen dataclasses with `tuple[T, ...]` sequence fields.
- `NoOpShieldAdapter` satisfies `ShieldPort` structurally (verified at runtime via `isinstance` because `ShieldPort` is `@runtime_checkable`). Returns `False` from `is_focus_protected`, `True` from `allow_action` for every `ActionType` member.
- `test_port_isolation.py` (92 parametrized rows) enforces: no relative imports, no forbidden adapter stdlib modules, no `nova.adapters.*` imports, only `.models` crosses system boundaries, exactly one Protocol class per port, async-only ellipsis-body methods, pinned method ordering per AC #4, no adapter-typed signatures, no default parameters, alphabetized `__all__`, frozen-dataclass + tuple-field shape, `ModeInfo` ≠ `ModeConfig`.
- `test_noop_shield_adapter.py` (15 rows) enforces: structural Protocol conformance, exact `False` / `True` identity returns, no instance state, zero-argument construction.
- Quality gate clean: `ruff check` + `ruff format --check` + `mypy src/ tests/` (strict) + `pytest` — 621 passed, 1 skipped. Delta from Story 1.8: +107 tests. No regressions. No cache dirs / DB artifacts left in the repo tree.
- Zero modifications to `core/__init__.py`, `app.py`, `cli.py`, any existing adapter, any existing system internals — scope held exactly as AC #11 pinned.

### File List

**New — production (15):**
- `src/nova/ports/brain.py`
- `src/nova/ports/eyes.py`
- `src/nova/ports/hands.py`
- `src/nova/ports/nerve.py`
- `src/nova/ports/ritual.py`
- `src/nova/ports/shield.py`
- `src/nova/ports/skin.py`
- `src/nova/ports/voice.py`
- `src/nova/systems/brain/models.py`
- `src/nova/systems/eyes/models.py`
- `src/nova/systems/hands/models.py`
- `src/nova/systems/ritual/models.py`
- `src/nova/systems/skin/models.py`
- `src/nova/adapters/shield/__init__.py`
- `src/nova/adapters/shield/noop.py`

**Modified — production (1):**
- `src/nova/ports/__init__.py` — replaced one-line docstring placeholder with 8 port re-exports + alphabetized `__all__`

**New — tests (2):**
- `tests/unit/ports/test_port_isolation.py`
- `tests/unit/adapters/test_noop_shield_adapter.py`

**Modified — sprint tracking (1):**
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — `1-9-port-interfaces-and-shield-stub: ready-for-dev` → `in-progress` → `review` (final); `last_updated` header updated

### Change Log

| Date | Change | By |
|---|---|---|
| 2026-04-15 | Story 1.9 implementation complete: 8 port Protocols + 5 `systems/*/models.py` + `NoOpShieldAdapter` + 107 new tests; quality gate clean | claude-opus-4-6[1m] |
| 2026-04-15 | Code review applied 11 patches (2 production cleanups + 9 test-robustness fixes); 13 deferred-work items recorded; 624 passed / 1 skipped; status → done | claude-opus-4-6[1m] |
