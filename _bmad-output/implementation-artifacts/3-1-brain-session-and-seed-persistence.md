# Story 3.1: Brain Session & Seed Persistence

**Status:** backlog (prep-only — full story authoring pending SM `create-story`)

> **Scope of this file:** Per Epic 2 retro (2026-04-18) action items **A6** (interaction-boundary detection) and **A10** (prior-story state assumptions). Full ACs, tasks, dev notes, review focus, and implementation plan will be authored by the SM agent via `bmad-create-story` when this story moves to `ready-for-dev`. Sections below are inputs the SM agent should fold into the full story; **do not ship to dev without the SM authoring pass**.

**Epic:** 3 — Core Session Loop (Hero Path)
**Depends on:** Story 1.4 (SqliteStorageEngine + `transaction()` context manager), Story 1.5 (migration runner + 001 schema), Story 1.6 (config loader), Story 1.9 (BrainPort protocol + stub), Story 2.4 (setup-time persistence seam — reconcile, see A10 below)
**Downstream stories:** 3.2 (BriefingAggregate), 3.5 (Nerve session lifecycle), 3.7 (shutdown + seed capture), 3.8 (warm resume)

---

## Story

As a developer building the continuity loop,
I want Brain to store and retrieve sessions, seeds, and workspace snapshots using typed domain models,
So that the shutdown→resume cycle has a persistence layer to write to and read from.

(Full epic-level ACs live in [epics.md](../planning-artifacts/epics.md#L1052-L1079) — SM agent folds them into this file during `create-story`.)

---

## Story-type classification (A6)

Three questions per Epic 2 retro:

1. **Does this story create a new contract between already-shipped pieces?**
   **YES.** `BrainPort` was defined in Story 1.9 as a stub protocol; this story ships the first concrete implementation (`SqliteBrainAdapter`). The adapter creates a new serialization contract at the port boundary ("no raw dict crosses the port boundary — adapter handles JSON ser/deser internally") and a new typed-input contract (`WorkspaceSnapshotInput`) distinct from the persistent shape (`WorkspaceSnapshot`).

2. **Does this story define new invariants in degraded / partial-failure paths?**
   **YES.** Interrupted-session semantics (`is_complete=False`, `ended_at=NULL`, `SessionSummary.duration=None`) are established here. Partial-write rollback (via `storage.transaction()`) must hold even when `store_snapshot` fails mid-sequence. `StorageError` translation from `sqlite3` exceptions is the adapter's responsibility, not the caller's.

3. **Does this story depend on prior-story state?**
   **YES, critically.** Story 2.4 is the first writer to the `sessions` / `workspace_snapshots` / `audit_log` tables. Any `nova.db` that reaches a Story 3.1-wired runtime already contains setup rows. See § A10 below for the full inventory.

**Classification:** ✅ **Interaction-boundary story.** Apply full invariant sweep (A1): lifecycle, teardown under partial failure, concurrency model, cancellation, error translation, test determinism, Review Focus subsection. Apply A9 degraded-path proof obligation (three test categories: happy + degraded + retry/rerun). Apply A10 prior-state reconciliation (this section).

---

## Depends on prior-story state (A10)

Story 3.1 runs against a `nova.db` whose tables may already contain the following rows, written atomically by Story 2.4's `persist_first_run` inside a single `storage.transaction()`:

### `sessions` — zero or one row

Written by [src/nova/setup/initial_capture.py:565-569](../../src/nova/setup/initial_capture.py#L565-L569), then updated at lines 584-587.

| Column | Value as written by Story 2.4 |
|---|---|
| `id` | auto-incremented (typically `1` on first-run DB) |
| `started_at` | ISO-8601 UTC from `CaptureResult.snapshot.captured_at` |
| `ended_at` | ISO-8601 UTC, stamped **after** snapshot INSERT (per AC #12 of Story 2.4) |
| `mode_name` | `NULL` |
| `seed_text` | `NULL` |
| `summary` | `NULL` |
| `is_complete` | `1` (SQLite INTEGER — coerced to Python `bool` in the adapter) |

**Reconciliation obligations:**
- `get_last_session()` returns this row when Brain is queried before Nerve has created any runtime session. `SessionSummary(mode_name=None, seed_text=None, summary=None, is_complete=True, duration=<short>)` must construct cleanly.
- `SessionSummary.duration` for the setup session will be small but non-zero (setup wall-clock). The "None if interrupted" rule only fires when `ended_at IS NULL` — which the setup row never satisfies.
- `is_complete` is SQLite INTEGER (`0` / `1`). The adapter must coerce to `bool` at the Brain boundary; callers see the frozen dataclass, not the raw SQL row.
- `get_last_seed()` returns `None` for the setup row (`seed_text IS NULL`) — this is the expected warm-resume "no seed yet" state that Briefing State B depends on (Story 3.2).

### `workspace_snapshots` — zero or one row

Written by [src/nova/setup/initial_capture.py:571-579](../../src/nova/setup/initial_capture.py#L571-L579).

| Column | Value as written by Story 2.4 |
|---|---|
| `id` | auto-incremented |
| `session_id` | FK to the setup `sessions.id` above |
| `captured_at` | ISO-8601 UTC (same as `sessions.started_at`) |
| `snapshot_type` | `"startup"` (string, per `SnapshotType.STARTUP`) |
| `workspace_data` | Compact JSON (no spaces, no `NaN`, `ensure_ascii=False`) produced by `_serialize_workspace_data` — keys include `apps`, `focused_app`, `captured_at`, `capture_status`, `windows_captured`, `windows_dropped` |

**Reconciliation obligations:**
- `get_last_snapshot_for_session(session_id)` must parse the compact JSON produced by Story 2.4 and reconstruct `WorkspaceSnapshot` cleanly. Ship a direct regression test that seeds a row using Story 2.4's exact JSON shape and asserts the adapter returns a `WorkspaceSnapshot` with matching fields.
- `snapshot_type = "startup"` is a pre-existing value in the data. The typed-input contract (`WorkspaceSnapshotInput.snapshot_type: SnapshotType`) must accept `SnapshotType.STARTUP` as a valid member, not only `SHUTDOWN`. If the planned typed enum omits `STARTUP`, the adapter cannot deserialize Story 2.4's row.
- JSON shape is locked by Story 2.4's `_serialize_workspace_data`. Any ser/deser drift in Story 3.1's adapter will silently corrupt setup-written snapshots. Test the round-trip both ways (2.4-writes → 3.1-reads, and 3.1-writes → 3.1-reads).

### `audit_log` — zero or one row (indirect dependency)

Written by [src/nova/setup/initial_capture.py:593-602](../../src/nova/setup/initial_capture.py#L593-L602).

| Column | Value as written by Story 2.4 |
|---|---|
| `action_type` | `"setup_complete"` |
| `target` | `NULL` |
| `result` | `"success"` |
| `details` | JSON with `modes_count`, `api_key_configured`, `capture_status` |

**Reconciliation obligations:**
- Story 3.1 does **not** write to `audit_log` per epic ACs. No direct collision.
- **Indirect coupling:** Story 2.4's fast-path probe uses this row to decide whether to re-enter the wizard. Story 3.1 must not modify the audit row or cause the probe to see inconsistent state. In practice this means: do not add audit writes to Brain methods in this story.

### Schema + engine assumptions

- **001 migration is the ground truth.** Story 3.1 must not alter columns on `sessions` / `workspace_snapshots` / `audit_log` — doing so breaks Story 2.4's SQL. New fields → new migration (002+), additive only.
- **`SqliteStorageEngine.execute_returning_lastrowid` already exists** (added in Story 2.4). Story 3.1's `create_session` should call the existing method — do not introduce a second mechanism for the same "INSERT and return id" need. Documented as the one-caller-today consolidation target.
- **`storage.transaction()` context manager** is the atomicity primitive. Multi-statement Brain operations use it; single-statement operations do not need it (the engine's `execute` is already transactional per-call).

### Test-harness assumptions

- **In-memory SQLite + migrations applied** is the established pattern (Story 2.4 integration tests use it; Story 3.1 unit tests should match).
- **Story 2.4's integration suite** (`tests/integration/test_setup_wizard.py::TestInitialCaptureAndCompletion`) asserts row counts after `main()` runs — exactly 1 session, 1 snapshot, 1 audit row. Story 3.1 must not break these. Run the suite locally before and after the Brain adapter lands.
- **Two-function clock indirection** (Pattern #1, [docs/cross-cutting-patterns.md](../../docs/cross-cutting-patterns.md)) is used throughout: `events._utc_now_iso` is the existing hook. Story 3.1's `create_session` / `end_session` should route timestamps through the same indirection so tests can monkeypatch deterministically.

---

## Degraded-path proof obligation (A9) — to be satisfied by the SM-authored task list

The full story must include at least one explicit test per category:

1. **Happy path** — create_session → store_snapshot → end_session(is_complete=True, seed_text="...") round-trip; get_last_seed returns "..."; get_last_session returns the expected `SessionSummary`.
2. **Degraded / partial-failure** — `store_snapshot` raises `StorageError` (simulated via `storage.transaction()` rollback); verify `sessions` row has `ended_at=NULL` and no orphan snapshot row. Also: interrupted session (is_complete=False, ended_at=NULL) has `SessionSummary.duration=None`.
3. **Retry / rerun / idempotency** — Brain adapter opens an existing `nova.db` that already contains Story 2.4's setup row; `get_last_session`, `get_last_seed`, `get_last_snapshot_for_session` all return expected values without errors. End-to-end regression: Story 2.4's `test_fast_path_exits_without_rewriting_rows` still passes after Brain is wired into the composition root.

Test shallowness rule (per A9 anti-pattern example): integration tests must exercise the real `SqliteStorageEngine` + real 001 migration — not mock the engine at the adapter boundary. Unit tests may mock.

---

## Open questions for SM authoring pass

1. Does `BrainPort` (Story 1.9 stub) already declare all six methods, or does this story extend the protocol? Check [src/nova/ports/brain.py](../../src/nova/ports/brain.py) before writing the Tasks section.
2. Does the composition root (`nova.app.create_app`) need to instantiate `SqliteBrainAdapter` in this story, or is wiring deferred to Story 3.5 (Nerve)? Epic 3.5's AC mentions "On bare nova boot: creates a session via Brain" — implies wiring is done by 3.5, but the adapter must exist by end of 3.1. Confirm during prep.
3. `WorkspaceSnapshotInput` vs `WorkspaceSnapshot` split: confirm the serialization boundary lives inside the adapter and the port only exposes typed domain models. No dict crossing.
4. `MemoryItem` is in the epic AC list but Epic 3's memory feature is minimal in T1 — confirm whether 3.1 ships a `create_memory_item` / `get_recent_memory` method or leaves those for Epic 4 / 5.

---

## References

- [epics.md: Story 3.1 ACs](../planning-artifacts/epics.md#L1052-L1079)
- [epic-2-retro-2026-04-18.md](epic-2-retro-2026-04-18.md) — action items A1 / A6 / A9 / A10 (origin of this prep file)
- [2-4-briefing-card-state-a-initial-capture-and-setup-completion.md](2-4-briefing-card-state-a-initial-capture-and-setup-completion.md) — prior-story state source
- [src/nova/setup/initial_capture.py:540-602](../../src/nova/setup/initial_capture.py#L540-L602) — `persist_first_run` (the setup-time seam to reconcile)
- [src/nova/core/storage/migrations/001_initial_schema.py:28-73](../../src/nova/core/storage/migrations/001_initial_schema.py#L28-L73) — table schemas
- [src/nova/ports/brain.py](../../src/nova/ports/brain.py) — BrainPort stub
- [docs/cross-cutting-patterns.md](../../docs/cross-cutting-patterns.md) — patterns #1, #3, #4, #6 all apply

---

*Prep file authored 2026-04-18 per Epic 2 retro action items A6 + A10. Full story authoring by SM agent when 3.1 moves to ready-for-dev.*
