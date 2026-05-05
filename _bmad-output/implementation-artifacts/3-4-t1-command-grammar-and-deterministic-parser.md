# Story 3.4: T1 Command Grammar & Deterministic Parser

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

**Epic:** 3 — Core Session Loop (Hero Path)
**Depends on:** Story 1.9 (`SkinPort.parse_command` Protocol stub, `Command` frozen dataclass shipped at [src/nova/systems/skin/models.py](../../src/nova/systems/skin/models.py)), Story 3.3 (`RichSkinAdapter.parse_command` `NotImplementedError("Story 3.4 scope")` seam at [src/nova/adapters/rich/skin.py:147-148](../../src/nova/adapters/rich/skin.py#L147-L148), AST isolation pattern at [tests/unit/adapters/rich/test_skin_adapter_isolation.py](../../tests/unit/adapters/rich/test_skin_adapter_isolation.py))
**Downstream consumers:** Story 3.5 (`NervePort.route_command` is the first runtime caller — consumes `Command.verb` to dispatch; partial/unknown/empty marker verbs become Nerve response routes), Story 3.7 (Skin's REPL — `collect_input` → `parse_command` per turn, then routes to Nerve), Story 3.8 (warm-resume hero moment — exercises Layer C `resume` / `yes` / `no` parsing end-to-end), Story 3.9 (parser must already recognize `status` / `help` / `?` for these commands' tests to wire), Epic 6 (`mode create` / `mode edit <name>` — parser recognizes them now; Nerve routes to placeholder until Epic 6 implements the wizards), Epic 5 (`memory` / `forget <topic>` — parser recognizes them now; Nerve routes to placeholder until Epic 5 wires Knowledge Display + deletion)

## Story

As a user in an active session,
I want to type commands like `mode coding`, `status`, `shutdown` (and natural-language equivalents) and have Skin produce the same deterministic `Command` for the same input every time,
So that I can control N.O.V.A. through a predictable, learnable grammar — and so Nerve has a closed, typed verb vocabulary to route on without having to second-guess the user's text.

## Story-type classification

**Pure-logic story.** NOT pre-flagged in [epic-3-story-preflags.md](epic-3-story-preflags.md) (only 3.2, 3.3, 3.5 carry interaction-boundary classifications). The parser is a pure function of `raw_input: str` — no I/O, no clock, no config reads, no event-bus subscriptions, no port consumption. Determinism is a property of the function itself; the test surface is a parametrized verb-and-alias matrix. Boundary-first invariant sweep abbreviated (§ Review Focus) — most invariants are trivially satisfied by purity.

## Depends on prior-story state (A10 — abbreviated, pure-logic story)

| From | What's load-bearing for Story 3.4 |
|---|---|
| Story 1.9 — [src/nova/systems/skin/models.py](../../src/nova/systems/skin/models.py) | `Command` frozen dataclass: `verb: str`, `target: str | None`, `raw_input: str`, `is_contextual: bool = False`. Shape locked. The module docstring (lines 6-12) anticipates this story: *"If Story 3.4 introduces a dedicated parser module, it will live as `systems/skin/commands.py` Skin-internal only — the `Command` type will stay here."* This story honors that placement. |
| Story 1.9 — [src/nova/ports/skin.py:48](../../src/nova/ports/skin.py#L48) | `async def parse_command(self, raw_input: str) -> Command: ...` — the Protocol surface that this story's adapter implementation must satisfy. |
| Story 3.3 — [src/nova/adapters/rich/skin.py:147-148](../../src/nova/adapters/rich/skin.py#L147-L148) | The `NotImplementedError("Story 3.4 scope")` seam. Story 3.4 replaces this body with a delegation to the new pure parser. The other four `NotImplementedError` stubs (`render_progress`, `render_shutdown_card`, `render_response`, `collect_input`) stay untouched — they belong to Stories 3.6 / 3.7. |
| UX spec — [ux-design-specification.md:847-948](../planning-artifacts/ux-design-specification.md#L847-L948) | T1 Command Grammar Contract: three layers (A launch / B in-session / C contextual), canonical verbs + aliases + natural-language mappings, partial-command guidance, invalid-input shape, empty-input behavior, T1 canonical vocabulary summary. **Authoritative source for the parser's vocabulary.** |
| Architecture — [architecture.md:1104-1133](../planning-artifacts/architecture.md#L1104-L1133) | Command Routing Convention: Skin parses, Nerve routes, systems execute. Parser is deterministic (same input → same Command). Contextual replies are tagged `is_contextual=True`; Nerve gates them on prompt context. NLP-level interpretation is **not** in Skin — Skin handles structured `[verb] [target]` + simple keyword matching only. |
| Architecture — [architecture.md:124-137](../planning-artifacts/architecture.md#L124-L137) | T1 Commands — Canonical Vocabulary table (Layers A/B/C). Lines 137 explicitly fence out `nova <name>` (bare mode shortcut), `audit`, `self-update` from T1 grammar — parser must NOT recognize these. |
| Deferred-work — [deferred-work.md:142](deferred-work.md#L142) | *"`Command.verb: str` is a free-form string instead of `Literal[...]` or enum. … typo (`memoryy`) survives type-checking. Target: Story 3.4 (Command parser). Introduce `CommandVerb` enum or `Literal[...]` alias when the parser is implemented."* This story closes the deferral. |
| Deferred-work — [deferred-work.md:139](deferred-work.md#L139) | *"`NervePort.route_command` returns `None` — error surface undocumented."* Story 3.5 owns; Story 3.4's parser stays inside Skin and never raises a domain error for unknown / partial / empty input — those become **routable Commands with marker verbs** that Nerve handles. |

## Acceptance Criteria

### Group A: `CommandVerb` closed vocabulary — replace free-form `str`

1. [`src/nova/systems/skin/models.py`](../../src/nova/systems/skin/models.py) is updated so `Command.verb` is typed as a closed vocabulary, not a free-form `str`. Choose **`enum.StrEnum`** (`CommandVerb(StrEnum)`) — gives mypy strict static checking, runtime equality with raw strings (so existing `Command(verb="mode", ...)` test fixtures keep working with one-line edits), and a single declaration site. The `Literal[...]` alternative was considered and rejected: a literal alias forces every consumer (Nerve's match statement in Story 3.5) to repeat the literal tuple; the enum approach gives one canonical reference.

   Final shape:

   ```python
   from enum import StrEnum

   class CommandVerb(StrEnum):
       # --- Layer B: routable verbs (Nerve dispatches on these) ---
       MODE = "mode"                  # mode <name>  AND  bare "mode" / "modes" → target=None
       STATUS = "status"
       MEMORY = "memory"
       FORGET = "forget"
       HELP = "help"
       SHUTDOWN = "shutdown"
       MODE_CREATE = "mode_create"    # Epic 6 placeholder — Nerve returns a "coming soon" response in Epic 3
       MODE_EDIT = "mode_edit"        # Epic 6 placeholder — same routing pattern

       # --- Layer C: contextual replies (is_contextual=True; Nerve gates on prompt state) ---
       RESUME = "resume"
       YES = "yes"
       NO = "no"
       SKIP = "skip"
       CANCEL = "cancel"
       CONFIRM = "confirm"

       # --- Marker verbs: Skin emits these for input shapes that have no canonical command ---
       UNKNOWN = "unknown"            # raw_input did not match any canonical / alias / NL mapping
       EMPTY = "empty"                # raw_input was empty or whitespace-only (free-command-mode no-op)
   ```

   `Command.verb: CommandVerb` (not `str`). **Both static and runtime validation** must close the vocabulary — the type annotation alone does not stop a frozen dataclass from accepting `Command(verb="memoryy", ...)` at runtime; mypy catches typed construction sites, but untyped callers / `**kwargs` splats / external test fixtures bypass static checking entirely. The closed-vocabulary contract is enforced by a `__post_init__` that coerces-valid-or-rejects:

   ```python
   from dataclasses import dataclass

   @dataclass(frozen=True)
   class Command:
       verb: CommandVerb
       target: str | None
       raw_input: str
       is_contextual: bool = False

       def __post_init__(self) -> None:
           # Coerce a valid string to its CommandVerb member; reject anything
           # that is neither a CommandVerb member nor a known value string.
           # ``object.__setattr__`` is the documented escape hatch for
           # mutating frozen dataclasses inside ``__post_init__``.
           if isinstance(self.verb, CommandVerb):
               return
           if isinstance(self.verb, str):
               try:
                   coerced = CommandVerb(self.verb)
               except ValueError as err:
                   raise ValueError(f"unknown command verb: {self.verb!r}") from err
               object.__setattr__(self, "verb", coerced)
               return
           raise TypeError(
               f"Command.verb must be CommandVerb or str, got {type(self.verb).__name__}"
           )
   ```

   The class docstring is updated to:
   - Pin the closed vocabulary: *"`verb` is one of the `CommandVerb` enum members. Free-form strings are rejected at construction — `__post_init__` either coerces a valid value-string to its `CommandVerb` member (so `Command(verb="mode", ...)` keeps working) or raises `ValueError` for unknown strings (e.g., `Command(verb="memoryy", ...)` fails at construction time, not at routing time). mypy strict catches typed sites; the runtime check protects against untyped callers and dynamic-kwarg paths."*
   - Document the marker verbs: *"`UNKNOWN` and `EMPTY` are emitted by the parser for input shapes that do not map to any canonical Layer B / C command. They are routed to Nerve like any other Command; Nerve owns the response prose. Skin's parser never raises for malformed `raw_input` — every input produces a Command. (The `__post_init__` validation above guards against `verb` itself being malformed, which is a programmer error, not a user-input shape.)"*
   - Document the Layer-B / Layer-C split: *"All `is_contextual=True` Commands carry a Layer C verb (`RESUME` / `YES` / `NO` / `SKIP` / `CANCEL` / `CONFIRM`). Layer B verbs always have `is_contextual=False`. Nerve gates contextual Commands on the current prompt state — they are 'unknown input' outside a directed prompt."*
   - Document the partial-command encoding: *"`mode_edit` with `target=None` is the partial-command shape — the user typed `mode edit` without a name. Nerve routes the partial form to the placeholder-guidance response. `mode_create` does not have a partial form (`mode create` always parses with `target=None`)."*

2. **Backward-compat sweep.** Existing `Command(verb="mode", ...)`-style construction sites continue to work via `__post_init__` coercion (valid strings are normalized to their `CommandVerb` member). The sweep is therefore non-mandatory at runtime, but mypy strict will flag every typed `verb="..."` site as a literal-vs-enum mismatch. Plan:
   - Run `uv run mypy src/ tests/` after the model change to surface every flagged site.
   - At each flagged site, replace `verb="mode"` → `verb=CommandVerb.MODE` (and equivalents for other verbs). This is a one-pass mechanical edit.
   - Expected delta: 0–3 sites today (the project hasn't constructed `Command` outside model/test fixtures yet — most consumers wait for the parser). The grep `Command(verb=["']` finds them quickly.
   - Note: `StrEnum` members compare equal to their string value, so any third-party serializer that round-trips `Command.verb` as a string will keep working unchanged.

3. **Shape regression test** at [`tests/unit/systems/skin/test_command_shape.py`](../../tests/unit/systems/skin/test_command_shape.py) (new file; no `__init__.py` per the established Story 3.2/3.3 layout) walks `dataclasses.fields(Command)` + `typing.get_type_hints(Command)` and asserts:
   - The four field names `("verb", "target", "raw_input", "is_contextual")` in declaration order (no field added or reordered).
   - `verb` is annotated `CommandVerb` (not `str`).
   - `target` is `str | None`, `raw_input` is `str`, `is_contextual` is `bool`.
   - `Command.__dataclass_params__.frozen` is `True` (uses the `getattr` pattern from Story 3.3 § Debug Log to satisfy mypy strict — `__dataclass_params__` is undocumented in the stubs).
   - The `CommandVerb` enum has exactly 16 members with values matching the snake_case strings in AC #1 (parametrized over an expected tuple). This is the regression guard against silent vocabulary drift — any future verb addition is a deliberate update to this list.

   **Runtime-validation tests** in the same file lock the `__post_init__` contract:
   - `test_command_construction_with_enum_member_succeeds` — `Command(verb=CommandVerb.MODE, target="coding", raw_input="mode coding")` constructs cleanly; `result.verb is CommandVerb.MODE`.
   - `test_command_construction_coerces_valid_string_verb` — `Command(verb="mode", target=None, raw_input="mode")` constructs cleanly **and** `result.verb is CommandVerb.MODE` (identity, not just equality — the coercion replaces the raw string with the enum member). Parametrized over a few representative values: `"mode"` → `MODE`, `"shutdown"` → `SHUTDOWN`, `"unknown"` → `UNKNOWN`, `"empty"` → `EMPTY`, `"resume"` → `RESUME`.
   - `test_command_construction_rejects_invalid_string_verb` — `Command(verb="memoryy", target=None, raw_input="memoryy")` raises `ValueError`. The error message must contain the offending value (`"memoryy"`) so debugging is direct. Parametrized over typo variants: `"memoryy"`, `"shutd"`, `"MODE"` (case mismatch — `StrEnum` is value-case-sensitive), `""`, `" "`, `"mode_unknown"`. Each must raise `ValueError`.
   - `test_command_construction_rejects_non_string_non_enum_verb` — `Command(verb=42, ...)`, `Command(verb=None, ...)`, `Command(verb=["mode"], ...)` each raise `TypeError`. The error message must mention the actual type for forensics.
   - `test_command_remains_frozen_after_post_init_coercion` — after `Command(verb="mode", ...)` constructs, attempting to assign to `cmd.verb` raises `dataclasses.FrozenInstanceError`. Locks that the `object.__setattr__` inside `__post_init__` does not leak frozen-bypass to caller code.

### Group B: Pure parser at `nova.systems.skin.commands`

4. New module [`src/nova/systems/skin/commands.py`](../../src/nova/systems/skin/commands.py). Skin-internal — the public surface (`Command` type) stays in `models.py` per Story 1.9 AC #8 ("only `.models` crosses system boundaries"). The parser is consumed only by `RichSkinAdapter.parse_command` and (in Story 3.7) Skin's REPL — never imported by Nerve / Brain / Voice / etc. AST guards in Group E lock this isolation.

   Module docstring states:
   - The function's purity contract: *"`parse(raw_input: str) -> Command` is a pure function — no I/O, no clock, no logging, **no mutable global state**. Module-level lookup tables (canonical verb set, alias map, natural-language phrase map, contextual reply set) are immutable read-only structures (`frozenset`, `MappingProxyType`-wrapped `Mapping`, or `Final[Mapping[...]]` over a `dict` literal that is never re-bound). The parser reads them; nothing in the module ever mutates them. Same input → same output, every time. Architecture.md:1123 invariant."*
   - The closed-vocabulary contract: *"Every `raw_input` produces a `Command`; the parser never raises for malformed input. Unrecognized inputs map to `Command(verb=CommandVerb.UNKNOWN, target=raw_input, raw_input=raw_input)` — the original text is preserved as `target` so Nerve / Voice can echo it in the suggestion-response template ('Didn't catch that. Try one of these…'). Empty / whitespace-only inputs map to `Command(verb=CommandVerb.EMPTY, target=None, raw_input=raw_input)` — Skin's REPL drops these silently (Story 3.7 enforces); Nerve also handles them as a no-op for defense-in-depth."*
   - The Layer A / B / C scope: *"This parser handles **Layer B (in-session prompt)** and **Layer C (contextual replies)** only. Layer A shell-form launch (`nova`, `nova mode <name>`, `nova status`, `nova help`, `nova memory`) is handled by argparse in `nova.cli` — see Story 3.5 for the bare-`nova` session-loop wiring and the eventual Layer A subcommand surface. Layer C contextual replies are tagged `is_contextual=True`; Nerve enforces 'valid only when prompted' in Story 3.5."*
   - The deviation from architecture.md Decision-3 (Command Routing): *"Architecture.md:1106 says 'Skin handles deterministic command parsing (structured `[verb] [target]` commands and simple keyword matching). Parsing is deterministic — same input always produces same Command object.' This module is the canonical parser implementation. Natural-language mappings (`'switch to coding mode'`, `'what do you know'`, `'shut down'`, `'done for today'`) live in a closed lookup table — they are *literal-form aliases*, not LLM-driven NLP. Anything that requires reasoning (ambiguous input, conversational queries) maps to `UNKNOWN` and Nerve routes to Voice if the tier permits (Epic 7+)."*

5. **Public surface.** The module exports exactly one function (and the closed lookup tables it owns):

   ```python
   def parse(raw_input: str) -> Command: ...
   ```

   `__all__ = ["parse"]`. The function is sync (the `async` lives at the `SkinPort.parse_command` Protocol boundary; the adapter wraps the sync call — see AC #11). Sync internals are deliberate: the parser does no awaitable work, and forcing every call site into a `await asyncio.to_thread(parse, …)` would add a thread hop with zero benefit. The async port surface is for symmetry with the other Skin methods.

5b. **Module-level lookup tables are immutable** at runtime, not just by convention:

   - **Sets** (canonical verb tokens, alias tokens, contextual reply tokens) → `frozenset[str]`. `frozenset` has no `add` / `discard` / `update` methods; mutation raises `AttributeError`.
   - **Mappings** (natural-language phrase → `(CommandVerb, target_extraction_kind)`, alias → canonical) → either `Final[Mapping[...]]` over a `dict` literal that is **never re-bound or mutated** after the module-level assignment, OR (preferred for stronger runtime guarantees) wrapped in `types.MappingProxyType`. `MappingProxyType` raises `TypeError` on `__setitem__` / `__delitem__` / `update` / `clear` — it is a read-only view backed by an underlying dict. Use `MappingProxyType(_PRIVATE_DICT)` where `_PRIVATE_DICT` is a module-private name with a leading underscore.
   - **No tables shaped as `list` / `set` / `dict` at the module level.** A future maintainer adding a `.append` / `.add` / `["new_key"] = ...` to one of these tables would silently mutate global state — that is the failure this rule prevents.
   - **Mutation-proof regression test** at [`tests/unit/systems/skin/test_commands_immutability.py`](../../tests/unit/systems/skin/test_commands_immutability.py) (new file, mirrors the AC #15 isolation test layout). Two complementary checks:
     1. **Runtime mutation rejection** — for each module-level lookup table referenced from `nova.systems.skin.commands`, parametrize over the table's name + a representative mutation operation (`.add(...)` for sets, `["new_key"] = ...` for mappings) and assert the operation raises (`AttributeError` for `frozenset`, `TypeError` for `MappingProxyType`). The dev's responsibility: enumerate every table in this test as the parser file grows.
     2. **AST mutability lock** — walk the parser module's source AST. For every `ast.Assign` / `ast.AnnAssign` at module scope whose target name does NOT start with a leading underscore (i.e., is the public lookup table the test suite consumes), assert the right-hand side is one of: a `frozenset(...)` call, a `MappingProxyType(...)` call, or a `Final[...]` annotation wrapper around an immutable literal. Forbids re-introducing `_FOO: dict = {...}` as a public table. Underscored names (e.g., `_PRIVATE_DICT` backing a `MappingProxyType`) are allowed because they are not exposed as the read surface.

6. **Tokenization.** `parse(raw_input)` runs in this order:

   1. Preserve `raw_input` verbatim for the `Command.raw_input` field.
   2. **Empty / whitespace-only check**: if `raw_input.strip() == ""`, return `Command(verb=CommandVerb.EMPTY, target=None, raw_input=raw_input, is_contextual=False)`.
   3. **Normalize for matching only** (NOT for the stored target): `normalized = raw_input.strip()`. The case-insensitivity rule (AC says: *"parsing is case-insensitive and deterministic"*) is enforced via `normalized.lower()` for the verb-match step; the **target preserves its original casing** (a user's mode named "Deep Work" must not become "deep work" when typed `mode Deep Work`). Tokens are split on **any whitespace run** (`normalized.split()` — collapses multiple spaces into one delimiter); leading/trailing whitespace already stripped.
   4. Try each Layer in order (B-canonical → B-natural-language → C-contextual → unknown). First match wins; this guarantees determinism. If `mode coding` matches Layer B before `coding` matches anything in Layer C, no surprise.

7. **Layer B canonical commands** (verbatim or alias on the **first token**, lowercased):

   | First-token match (lowercased) | Behavior |
   |---|---|
   | `mode` (no rest) OR `modes` (no rest) | `Command(verb=MODE, target=None, raw_input, is_contextual=False)` — bare list-modes form. |
   | `mode` + rest tokens, where first rest token is `create` | `Command(verb=MODE_CREATE, target=None, raw_input, is_contextual=False)`. Trailing tokens after `create` are ignored (`mode create coding` still maps to `MODE_CREATE` with `target=None`) — the wizard owns target gathering in Epic 6. |
   | `mode` + rest tokens, where first rest token is `edit` | If a second rest token exists → `Command(verb=MODE_EDIT, target="<original-cased remainder, joined>", raw_input, is_contextual=False)`. If no second rest token → `Command(verb=MODE_EDIT, target=None, raw_input, is_contextual=False)` (the partial form Nerve responds to with `'Need one more detail. Try mode edit coding.'`). |
   | `mode` + any other rest tokens | `Command(verb=MODE, target="<original-cased rest joined with single space>", raw_input, is_contextual=False)` — `mode coding` → `target="coding"`, `mode Deep Work` → `target="Deep Work"`. |
   | `status` (any remaining tokens ignored) | `Command(verb=STATUS, target=None, raw_input, is_contextual=False)`. |
   | `memory` (any remaining tokens ignored) | `Command(verb=MEMORY, target=None, raw_input, is_contextual=False)`. |
   | `forget` (no rest) | `Command(verb=FORGET, target=None, raw_input, is_contextual=False)` (partial — Nerve responds with `'Tell me what to forget. Example: forget Meridian'`). |
   | `forget` + rest tokens | `Command(verb=FORGET, target="<original-cased rest joined>", raw_input, is_contextual=False)`. |
   | `help` (any remaining tokens ignored) OR `?` (no rest) | `Command(verb=HELP, target=None, raw_input, is_contextual=False)`. The `?` alias is whole-input (literal `?` only — `??` or `? help` map to `UNKNOWN`). |
   | `shutdown` OR `quit` OR `exit` (any remaining tokens ignored) | `Command(verb=SHUTDOWN, target=None, raw_input, is_contextual=False)`. All three aliases route through the same verb — *"`shutdown`, `quit`, and `exit` all route through the same graceful shutdown flow. No alias may bypass seed capture and session end"* (architecture.md:1131). |

8. **Layer B natural-language mappings** (whole-input phrase match against the lowercased+whitespace-collapsed input — i.e., `" ".join(normalized.lower().split())`). These are **literal phrase aliases**, not pattern-based. Closed lookup table:

   | Lowercased phrase | Maps to |
   |---|---|
   | `switch to <X> mode` | `Command(verb=MODE, target="<original-cased X from raw_input>", raw_input, is_contextual=False)`. The `<X>` is captured by re-extracting the corresponding token span from `raw_input` so casing is preserved. |
   | `<X> mode` | `Command(verb=MODE, target="<original-cased X>", raw_input, is_contextual=False)` — but ONLY if the first token isn't itself a canonical verb (i.e., `status mode` does NOT map to a mode-switch — `status` already won at Layer B canonical). And only if `<X>` is non-empty (the literal phrase ` mode` would not match because there's no leading non-whitespace before `mode`). |
   | `what modes do i have` | `Command(verb=MODE, target=None, ...)`. |
   | `create a new mode` | `Command(verb=MODE_CREATE, target=None, ...)`. |
   | `edit <X> mode` | `Command(verb=MODE_EDIT, target="<original-cased X>", ...)`. |
   | `what's my status` OR `whats my status` | `Command(verb=STATUS, target=None, ...)`. The apostrophe-elided form is included because terminal users routinely type without the apostrophe. |
   | `what do you know` | `Command(verb=MEMORY, target=None, ...)`. |
   | `forget <X>` | Already covered by Layer B canonical — listed here for completeness; the canonical first-token branch handles it. |
   | `shut down` | `Command(verb=SHUTDOWN, target=None, ...)` — the two-word form. |
   | `done for today` | `Command(verb=SHUTDOWN, target=None, ...)`. |
   | `help` (literal) | Already canonical. |

   The `<X> mode` mapping is **scope-fenced**: it does NOT recognize `nova <name>` (the architecture.md:1133 explicit T1 non-goal — *"`nova <name>` (bare mode shortcut) is not parsed. Creates ambiguity. Deferred to T2"*). The phrase `<X> mode` requires the literal trailing word `mode`; `nova coding` (without trailing `mode`) never parses to a Command(MODE) — it falls through to UNKNOWN.

9. **Layer C contextual replies** (whole-input match on lowercased single-token input — none of these take a target):

   | Lowercased input | Maps to |
   |---|---|
   | `resume` | `Command(verb=RESUME, target=None, raw_input, is_contextual=True)`. |
   | `yes` | `Command(verb=YES, target=None, raw_input, is_contextual=True)`. |
   | `no` | `Command(verb=NO, target=None, raw_input, is_contextual=True)`. |
   | `skip` | `Command(verb=SKIP, target=None, raw_input, is_contextual=True)`. |
   | `cancel` | `Command(verb=CANCEL, target=None, raw_input, is_contextual=True)`. |
   | `confirm` | `Command(verb=CONFIRM, target=None, raw_input, is_contextual=True)`. |

   `is_contextual=True` is the parser's tag — it does NOT mean "the user is in a contextual prompt right now." It means "this Command is **only valid** when the UI has a directed prompt active." Story 3.5's `NervePort.route_command` enforces the gating: contextual Commands outside a prompt context are treated as unknown input (the user-facing response is Nerve's job — see e.g., the AC for Story 3.8: *"if the user types `resume` outside of a resume prompt context, they get: 'Nothing to resume right now. Try mode <name> or mode to view available modes.'"*).

10. **Unknown fallback.** If none of the above branches match, return `Command(verb=CommandVerb.UNKNOWN, target=raw_input, raw_input=raw_input, is_contextual=False)`. The `target=raw_input` carries the user's original text into Nerve so the response template can echo it (e.g., suggesting nearest matches based on a future Levenshtein-distance check — Story 3.5+'s scope, not this story's). The parser does NOT compute suggestions; it preserves input fidelity for downstream consumption.

### Group C: `RichSkinAdapter.parse_command` body — delegate to the pure parser

11. [`src/nova/adapters/rich/skin.py`](../../src/nova/adapters/rich/skin.py) — replace the `NotImplementedError("Story 3.4 scope")` body at line 148 with:

    ```python
    async def parse_command(self, raw_input: str) -> Command:
        return parse(raw_input)
    ```

    Plus the `from nova.systems.skin.commands import parse` import. The other four `NotImplementedError` stubs (`render_progress`, `render_shutdown_card`, `render_response`, `collect_input`) are NOT touched — they keep their `Story 3.6` / `Story 3.7` markers verbatim. The module docstring's line 8 (*"Command parsing (Story 3.4) and the shutdown / response / input methods (Story 3.7) land in their respective stories"*) is updated to drop the `Story 3.4` clause: *"Command parsing lands here via delegation to `nova.systems.skin.commands.parse`; the shutdown / response / input methods (Story 3.7) land in their respective stories."*

12. **AST-isolation guard update.** [`tests/unit/adapters/rich/test_skin_adapter_isolation.py`](../../tests/unit/adapters/rich/test_skin_adapter_isolation.py) `ALLOWED_SYSTEMS_MODELS` allowlist (lines 76-83) is **extended** — add `"nova.systems.skin.commands"` to permit the new import. The existing entry `"nova.systems.skin.models"` stays; the new entry sits beside it. The `test_rich_skin_imports_each_allowed_models_module` parametrize list (lines 201-209) gains `"nova.systems.skin.commands"` so the positive-presence lock catches a future regression that drops the parser delegation.

    *Naming-rule note:* the existing isolation guard's "only `.models` crosses system boundaries" rule (Story 1.9 AC #8) protects the **system → system** boundary. `nova.systems.skin.commands` is **Skin-internal** (consumed only by `nova.adapters.rich.skin`, which is also Skin's adapter). Allowing this one cross-package import inside Skin does not breach the cross-system rule; the AST guard's allowlist tightens the scope correctly.

### Group D: Parser unit tests — exhaustive vocabulary coverage

13. New test file [`tests/unit/systems/skin/test_commands_parser.py`](../../tests/unit/systems/skin/test_commands_parser.py) (no `__init__.py`). Uses `pytest.mark.parametrize` for the bulk of the tables; expected total tests: ~80 individual test invocations across ~10 parametrize blocks. Test layout (one block per AC sub-rule):

    **Block 1 — Empty / whitespace-only:**
    - `("", CommandVerb.EMPTY)`, `(" ", EMPTY)`, `("\t", EMPTY)`, `("   \t\n  ", EMPTY)`. All 4 return `target=None`, `is_contextual=False`, and `raw_input` is preserved verbatim (the test asserts `result.raw_input == input_string`).

    **Block 2 — Layer B canonical (lowercase):**
    - `("mode", MODE, None)`, `("modes", MODE, None)`, `("status", STATUS, None)`, `("memory", MEMORY, None)`, `("help", HELP, None)`, `("?", HELP, None)`, `("shutdown", SHUTDOWN, None)`, `("quit", SHUTDOWN, None)`, `("exit", SHUTDOWN, None)`, `("forget", FORGET, None)`.

    **Block 3 — Layer B with target, casing preserved:**
    - `("mode coding", MODE, "coding")`, `("mode Coding", MODE, "Coding")`, `("mode Deep Work", MODE, "Deep Work")`, `("forget Meridian", FORGET, "Meridian")`, `("forget the meridian project", FORGET, "the meridian project")`, `("mode edit coding", MODE_EDIT, "coding")`, `("mode edit Deep Work", MODE_EDIT, "Deep Work")`, `("mode create", MODE_CREATE, None)`, `("mode create coding", MODE_CREATE, None)` (target ignored — Epic 6 wizard owns target capture).

    **Block 4 — Partial commands (target=None on a verb that expects one):**
    - `("mode edit", MODE_EDIT, None)`, `("forget", FORGET, None)` (already in block 2, listed here as the explicit "partial" reference). Both return `is_contextual=False`. The dev-note for Block 4 records the contract: *Nerve (Story 3.5) maps `MODE_EDIT` with `target=None` to the `'Need one more detail. Try mode edit coding.'` response; maps `FORGET` with `target=None` to `'Tell me what to forget. Example: forget Meridian'`.*

    **Block 5 — Case insensitivity:**
    - `("MODE", MODE, None)`, `("Mode", MODE, None)`, `("mOdE coding", MODE, "coding")`, `("STATUS", STATUS, None)`, `("Help", HELP, None)`, `("ShUtDoWn", SHUTDOWN, None)`, `("Quit", SHUTDOWN, None)`. The first-token verb match is case-insensitive; the target is preserved verbatim.

    **Block 6 — Whitespace handling:**
    - `("  mode  coding  ", MODE, "coding")`, `("mode\tcoding", MODE, "coding")`, `("mode    Deep    Work", MODE, "Deep Work")` — multi-space target collapses to single-space. The leading/trailing whitespace of `raw_input` does NOT appear in the parsed `target`; the `raw_input` field still holds the original string with whitespace intact.

    **Block 7 — Layer B natural-language mappings:**
    - `("switch to coding mode", MODE, "coding")`, `("Switch to Coding Mode", MODE, "Coding")` (case insensitive verb-phrase, original casing preserved in target), `("coding mode", MODE, "coding")`, `("Deep Work mode", MODE, "Deep Work")`, `("what modes do i have", MODE, None)`, `("create a new mode", MODE_CREATE, None)`, `("edit coding mode", MODE_EDIT, "coding")`, `("what's my status", STATUS, None)`, `("whats my status", STATUS, None)`, `("what do you know", MEMORY, None)`, `("shut down", SHUTDOWN, None)`, `("done for today", SHUTDOWN, None)`.
    - Negative cases for the `<X> mode` mapping: `("status mode", STATUS, None)` — Layer B canonical wins (NOT a mode-switch). `(" mode", MODE, None)` — bare-mode after strip, target=None (NOT `Command(MODE, target="")`). `("nova coding", UNKNOWN, "nova coding")` — `nova <name>` is explicitly out of T1 grammar; falls through to UNKNOWN with the original text preserved.

    **Block 8 — Layer C contextual replies:**
    - All six: `("resume", RESUME, None, is_contextual=True)`, `("yes", YES, ...)`, `("no", NO, ...)`, `("skip", SKIP, ...)`, `("cancel", CANCEL, ...)`, `("confirm", CONFIRM, ...)`. Case insensitive: `("Yes", YES, ...)`, `("RESUME", RESUME, ...)`. Each test explicitly asserts `is_contextual is True` (the parser sets it; Nerve gates it).

    **Block 9 — Layer C must be single-token:**
    - `("yes please", UNKNOWN, "yes please", is_contextual=False)` — multi-token "yes please" does NOT parse to YES; falls through to UNKNOWN. `("resume now", UNKNOWN, "resume now", is_contextual=False)` — same. The Layer C single-token discipline is what prevents `resume coding mode` from being misparsed as a contextual reply.

    **Block 10 — Unknown / out-of-T1-grammar inputs:**
    - `("audit", UNKNOWN, "audit")` — explicitly fenced from T1 grammar (architecture.md:137).
    - `("self-update", UNKNOWN, "self-update")` — same.
    - `("nova", UNKNOWN, "nova")` — bare `nova` in the in-session prompt is not a Layer A command; cli.py owns Layer A.
    - `("hello", UNKNOWN, "hello")`, `("???", UNKNOWN, "???")` (note: `???` ≠ `?` — only the literal single `?` aliases `help`).
    - `("modeswitch coding", UNKNOWN, "modeswitch coding")` — typo of `mode`. The parser does not fuzzy-match.

    **Block 11 — Determinism property test:**
    - One test that calls `parse(input_string)` three times for each of ~10 representative inputs and asserts byte-identical Command instances. Locks the "same input → same Command" invariant.

    **Block 12 — Purity property test (AST-based, no new test deps):**
    - **AST-walk forbidden-name check** — walks the `nova.systems.skin.commands` source via `ast.walk` (per cross-cutting-patterns.md #2 — pattern explicitly memorised in `feedback_ast_static_analysis_tests.md`: AST inspection, NEVER text regex, to avoid docstring false positives). The test asserts no `ast.Call` / `ast.Attribute` references to any forbidden name:
      - `datetime.now`, `datetime.utcnow`, `datetime.today`, `time.time`, `time.monotonic`, `time.perf_counter` — clock reads
      - `random.*` (any attribute) — non-determinism
      - `os.environ` — environment reads
      - `logging.getLogger`, `logger.*` (any attribute call on a name resolving to a logger), bare `print` — logging / output
      - `__import__`, `importlib.import_module` — dynamic imports
      - The check is name-based on `ast.Attribute.attr` and `ast.Name.id`; this is conservative (a `from time import time as t` rebinding would slip past) — that ergonomic gap is acceptable because the parser module is small and reviewer-readable, and the AST-positive list (Group E AC #15 allowed-imports test) already restricts the parser to a tiny surface (`enum`, `__future__`, `nova.systems.skin.models`, optional `types.MappingProxyType`).
    - **Behavioral log-emptiness check** — invokes `parse` for ~10 representative inputs under `caplog.at_level(logging.DEBUG)` and asserts `caplog.records == []` after the calls. Behavioral guard against the AST-walk's name-rebinding gap above.
    - **Sync-contract lock** — asserts `inspect.iscoroutinefunction(parse) is False`. Catches an accidental `async def parse(...)` regression.

    Do NOT introduce `freezegun` or any other new test-only dependency for this story — the AST walk + caplog are sufficient and avoid expanding `pyproject.toml`'s dev-deps surface.

14. **Adapter delegation test** at [`tests/unit/adapters/rich/test_skin_adapter.py`](../../tests/unit/adapters/rich/test_skin_adapter.py) (existing file from Story 3.3 — append, do not replace). One test that constructs a `RichSkinAdapter`, calls `await adapter.parse_command("mode coding")`, and asserts the result is `Command(verb=MODE, target="coding", raw_input="mode coding", is_contextual=False)`. The test does NOT re-cover the parser vocabulary — that's Group D's exhaustive coverage. This test locks **delegation only** — proving `RichSkinAdapter.parse_command` calls into the same `parse` function the unit tests cover. One additional test calls `parse_command("")` and asserts `Command(verb=EMPTY, target=None, raw_input="", is_contextual=False)` to lock the empty-input pass-through specifically (the highest-impact regression target).

### Group E: AST isolation + composition root locks

15. New test file [`tests/unit/systems/skin/test_commands_isolation.py`](../../tests/unit/systems/skin/test_commands_isolation.py) (mirrors `test_briefing_isolation.py` from Story 3.2). AST-walks `nova.systems.skin.commands` and asserts:

    - Forbidden top-level imports: `sqlite3`, `anthropic`, `pywin32`, `pywintypes`, `psutil`, `win32*`, `yaml`, `rich` (parser is rendering-agnostic — the Rich types stay in the adapter).
    - Forbidden Nova prefixes: `nova.app`, `nova.cli`, `nova.setup`, `nova.adapters.*`, all `nova.systems.*` non-`models` modules **except** `nova.systems.skin.models` (which is allowed — `Command` lives there).
    - Allowed Nova prefixes: `nova.systems.skin.models` only (cross-system import); stdlib (`enum`, `__future__`).
    - No dynamic `__import__` / `importlib.import_module` to any forbidden prefix.
    - Positive locks: parametrize over `["nova.systems.skin.models"]` and assert each is present (drops would silently break the parser — early-warning).

16. [`tests/unit/test_composition_root.py`](../../tests/unit/test_composition_root.py) — no new instantiation test (the parser is a free function, not a class; it has no constructor and is not held by `NovaApp`). The existing `test_rich_skin_adapter_is_instantiated_inside_create_app` already locks the adapter's wiring; the parser is reachable through it via the new delegation. The existing `_LOGGER_NAME_DEPTH_ALLOWLIST` does NOT need an entry — `nova.systems.skin.commands` does not log (per AC #12 purity test).

17. **Optional but recommended — port-isolation update.** [`tests/unit/ports/test_port_isolation.py`](../../tests/unit/ports/test_port_isolation.py) is the cross-system shape test. Verify (no code change needed unless the test currently asserts `Command.verb` is `str`) that the port-isolation suite tolerates the `verb` annotation switch from `str` to `CommandVerb` (which is also a `str` at runtime, but the type-hint snapshot may have a `verb: str` reference somewhere). Run the existing suite; if any assertion fails on the new annotation, file the fix in this story. Expected: no change required.

### Group F: Vocabulary lock + scope fences

18. **Closed-vocabulary regression test** (folded into Group A AC #3) parametrizes `CommandVerb` membership against the exact-16-element expected list. Adding a new verb requires both (a) updating `CommandVerb` and (b) updating this test — this prevents silent grammar drift.

19. **T1 non-goal fences locked by tests** (in Group D Block 10): `("nova", UNKNOWN)`, `("audit", UNKNOWN)`, `("self-update", UNKNOWN)`, `("nova coding", UNKNOWN)`. These are the architecture.md:137 explicit non-goals — the parser must NOT recognize them. Each is a literal-input test in Block 10.

20. **Layer A awareness without Layer A wiring.** Story 3.4 does NOT modify [`src/nova/cli.py`](../../src/nova/cli.py). The `_async_main` placeholder log line *"session shell placeholder — full session loop arrives in Story 3.5"* stays untouched. Layer A's shell-form vocabulary (`nova mode <name>`, `nova status`, `nova help`, `nova memory`) is conceptually owned by Story 3.4 (the epic AC says so) but its **implementation** is part of the bare-`nova` session-loop wiring that Story 3.5 owns. The parser's vocabulary table covers Layer A's verbs by sharing them with Layer B (`MODE`, `STATUS`, `HELP`, `MEMORY`); when Story 3.5 adds a Layer A subcommand surface to argparse, those subcommands feed `_async_main` an initial `raw_input` that this parser then handles. Story 3.4 ships nothing in `nova/cli.py` and nothing new in `nova/app.py`.

### Group G: CI gate

21. **Full quality gate.** All four gates pass without weakening:

    - `uv run ruff check src/ tests/` — clean.
    - `uv run ruff format --check src/ tests/` — clean.
    - `uv run mypy src/ tests/` — clean. Strict mode catches the `CommandVerb` annotation across every existing `Command(verb=...)` site (small expected delta — see AC #2).
    - `uv run pytest tests/unit/` — passes. Net delta vs. the post-Story-3.3 baseline (1438 unit pass + 1 brittle deselected + 1 pre-existing skip): expect approximately **+100 to +115 tests** (Group D's parametrize blocks ≈ 80; Group A runtime-validation tests ≈ 15; Group E isolation + Group A shape tests ≈ 8; AC #5b immutability tests ≈ 5–10).
    - `uv run pytest tests/integration/ --ignore=tests/integration/test_setup_bat.py` — passes. **No new integration tests** in this story (parser is pure unit-testable; cross-system parsing flows are exercised by Story 3.5's Nerve routing tests once they land).
    - **100% coverage** on the new module (`nova.systems.skin.commands`) and on the modified-line region of `nova.systems.skin.models` (the `CommandVerb` enum). Run: `uv run pytest tests/unit --cov=nova.systems.skin --cov-report=term-missing`.

## Tasks / Subtasks

- [x] **Task 1 — `CommandVerb` enum + `Command.verb` type tightening + runtime validation** (AC: #1, #2, #3)
  - [x] Updated [`src/nova/systems/skin/models.py`](../../src/nova/systems/skin/models.py): added `from enum import StrEnum`, `CommandVerb` class with all 16 members in declaration order (Layer B routable → Layer C contextual → marker verbs), changed `verb: str` → `verb: CommandVerb`, added `__post_init__` validator (coerce-valid-string-or-reject via `object.__setattr__`), expanded the docstring with the closed-vocabulary contract, added `"CommandVerb"` to `__all__`.
  - [x] Ran `uv run mypy src/ tests/` — surfaced 0 typed `Command(verb="...")` sites needing replacement (no source code constructs `Command` outside this story's tests; the new test file uses deliberate `# type: ignore[arg-type]` to exercise the runtime coercion path).
  - [x] Created [`tests/unit/systems/skin/test_command_shape.py`](../../tests/unit/systems/skin/test_command_shape.py): 39 tests covering the AC #3 shape regression (field tuple, type annotations, frozen invariant, `CommandVerb` 16-member parametrize incl. value-lookup) AND the runtime-validation suite (enum-member success, valid-string coercion parametrized over 5 representative values, invalid-string rejection parametrized over 7 typo variants, non-string-non-enum rejection over 5 type-shape variants, frozen-after-coercion lock).
  - [x] `uv run mypy src/nova/systems/skin/ tests/unit/systems/skin/` — clean.

- [x] **Task 2 — Pure parser implementation + immutable lookup tables** (AC: #4, #5, #5b, #6, #7, #8, #9, #10)
  - [x] Created [`src/nova/systems/skin/commands.py`](../../src/nova/systems/skin/commands.py) with the module docstring (purity / closed-vocabulary / Layer-scope / architecture-decision-3 deviation), four immutable module-level lookup tables, the `parse(raw_input: str) -> Command` function body following the AC #6 tokenization order, three Skin-internal helpers (`_parse_layer_b_canonical`, `_parse_mode_family`, `_parse_natural_language_with_target`), and `__all__ = ["parse"]`.
  - [x] **Lookup-table immutability** per AC #5b: `_CANONICAL_FIRST_TOKENS` is `Final[frozenset[str]]`; `_BARE_VERB_ALIAS`, `_CONTEXTUAL_REPLIES`, `_NL_PHRASES_BARE` are `Final[Mapping[..., ...]]` wrapped in `MappingProxyType(...)`. Inner values of `_NL_PHRASES_BARE` are `frozenset[str]` (not `set[str]`). Lock test in `test_commands_immutability.py` verifies both runtime mutation rejection and AST RHS-shape.
  - [x] All four helpers are module-private (leading underscore); only `parse` is in `__all__`. No state outside the immutable tables.
  - [x] No logging in the module — AC #13 Block 12 purity test (AST-walk + caplog behavioral) enforces.
  - [x] `uv run mypy src/nova/systems/skin/commands.py` — clean (strict mode).

- [x] **Task 3 — Adapter delegation** (AC: #11)
  - [x] Updated [`src/nova/adapters/rich/skin.py`](../../src/nova/adapters/rich/skin.py): replaced `raise NotImplementedError("Story 3.4 scope")` body with `return parse(raw_input)`. Added `from nova.systems.skin.commands import parse` import (alphabetical-grouped per existing style).
  - [x] Updated the module docstring's Story-3.4 clause: now reads *"Command parsing lands here via delegation to `nova.systems.skin.commands.parse`; the shutdown / response / input methods (Story 3.7) land in their respective stories."*

- [x] **Task 4 — Parser unit tests** (AC: #13)
  - [x] Created [`tests/unit/systems/skin/test_commands_parser.py`](../../tests/unit/systems/skin/test_commands_parser.py) with 12 parametrize blocks: empty/whitespace (6 cases), Layer B canonical no-target (10 cases), Layer B with target preserving casing (12 cases), partial commands (2 cases), case insensitivity (10 cases), whitespace handling (4 cases), NL phrases (13 + 2 negative cases), Layer C contextual (9 cases), Layer C single-token discipline (6 cases), unknown / non-goal fences (7 cases), determinism property (10 inputs × 3 calls), purity property (3 sub-tests). **91 total tests in the parser file.**
  - [x] Block 11 determinism iterates 10 representative inputs three times and asserts byte-identical Commands.
  - [x] Block 12 purity test is **AST-only** per the story revision: walks parser source for forbidden `datetime.now`/`time.time`/`logger.*`/`__import__`/`importlib.import_module` references; `caplog`-empty behavioral guard; `inspect.iscoroutinefunction` sync-contract lock. **No `freezegun` dependency added.**

- [x] **Task 5 — Adapter delegation tests** (AC: #14)
  - [x] Appended two tests to [`tests/unit/adapters/rich/test_skin_adapter.py`](../../tests/unit/adapters/rich/test_skin_adapter.py): `test_parse_command_delegates_to_pure_parser` (`"mode coding"` → `MODE`/`"coding"`) and `test_parse_command_handles_empty_input` (`""` → `EMPTY`). Tests use the existing `_build_console` helper for adapter construction.

- [x] **Task 6 — AST isolation guards + lookup-table immutability lock** (AC: #5b, #12, #15)
  - [x] Updated [`tests/unit/adapters/rich/test_skin_adapter_isolation.py`](../../tests/unit/adapters/rich/test_skin_adapter_isolation.py): added `"nova.systems.skin.commands"` to `ALLOWED_SYSTEMS_MODELS` and to the `test_rich_skin_imports_each_allowed_models_module` parametrize list. Updated docstring to document the new allowed import.
  - [x] Created [`tests/unit/systems/skin/test_commands_isolation.py`](../../tests/unit/systems/skin/test_commands_isolation.py) per AC #15: 4 AST tests (forbidden-modules incl. `rich`, sqlite3-at-any-scope, no-dynamic-forbidden-imports, positive-presence parametrize for `nova.systems.skin.models`). The parser is forbidden from importing `rich` — the rendering layer stays in the adapter.
  - [x] Created [`tests/unit/systems/skin/test_commands_immutability.py`](../../tests/unit/systems/skin/test_commands_immutability.py) per AC #5b: (a) 5 runtime-mutation tests verify `frozenset.add` raises `AttributeError`, `MappingProxyType.__setitem__` / `__delitem__` raise `TypeError`, and inner `_NL_PHRASES_BARE` values are themselves `frozenset` (not `set`); (b) 2 AST-walk tests assert no public module-scope `dict` / `set` / `list` literals and that every public module-scope `AnnAssign` is a `frozenset(...)` / `MappingProxyType(...)` call or `Final`-annotated immutable.

- [x] **Task 7 — Composition / port isolation regression sweep** (AC: #16, #17)
  - [x] Ran [`tests/unit/test_composition_root.py`](../../tests/unit/test_composition_root.py) (90 tests pass) and [`tests/unit/ports/test_port_isolation.py`](../../tests/unit/ports/test_port_isolation.py) (73 tests pass) — both clean. The `Command.verb: str → CommandVerb` annotation flip did not regress port-isolation tests because `CommandVerb` is a `str` subclass via `StrEnum`, and the port-shape tests treat the annotation as a class object regardless of the underlying type.

- [x] **Task 8 — Full CI gate** (AC: #21)
  - [x] `uv run ruff check src/ tests/` → All checks passed.
  - [x] `uv run ruff format --check src/ tests/` → 121 files already formatted.
  - [x] `uv run mypy src/ tests/` → Success — no issues found in 121 source files (strict mode).
  - [x] `uv run pytest tests/unit/` → **1585 passed + 1 skipped + 1 deselected** — net delta **+147 tests** vs. the post-Story-3.3 baseline (1438+1+1).
  - [x] `uv run pytest tests/integration/ --ignore=tests/integration/test_setup_bat.py` → 51 passed (no overlap with Story 3.4 surface; no new integration tests in this story).
  - [x] `uv run pytest tests/unit --cov=nova.systems.skin --cov-report=term-missing` → **100% coverage** on all Story 3.4 modules: `nova.systems.skin.__init__` (0/0), `nova.systems.skin.commands` (61 stmts + 34 branches, 0 misses), `nova.systems.skin.models` (38 stmts + 4 branches, 0 misses). Total: 99 stmts + 38 branches, 0 misses.

### Review Findings

**Code review run 2026-05-05** — Three-layer adversarial review (Blind Hunter / Edge Case Hunter / Acceptance Auditor). All three layers ran in fresh agent contexts (no implementation memory). 48 raw findings across the three layers; 19 unique post-dedup-and-classification.

#### Decision-needed findings (resolved)

- [x] [Review][Decision] `modes <X>` semantics unannounced extension to AC #7 [src/nova/systems/skin/commands.py:_parse_mode_family] — **Initially resolved as option (b) (mode-switch alias); reverted post-implementation to option (a) after spec re-check.** The UX spec table at [ux-design-specification.md:879](../planning-artifacts/ux-design-specification.md#L879) lists `modes` as an alias for **List modes** only (canonical: bare `mode`); Switch mode (`mode <name>`) has alias `—`. So `modes coding` is NOT a valid mode-switch shape — it's `modes` (list-modes alias) with trailing tokens dropped, same policy as `status mode` / `help foo` (canonical verbs that don't take a target). `modes` lives in `_BARE_VERB_ALIAS` (with target dropped), not in `_SPECIAL_DISPATCH_FIRST_TOKENS`. Tests lock `modes coding` → `MODE(target=None)`.
- [x] [Review][Decision] `edit mode` (n=2, no `<X>`) routes to `MODE(target="edit")` instead of partial `MODE_EDIT(target=None)` [src/nova/systems/skin/commands.py:_parse_natural_language_with_target] — **Resolved (option b): add `n==2` partial-edit arm.** `edit mode` is the natural partial form of `edit <X> mode` — Nerve already responds to `MODE_EDIT(target=None)` with `'Need one more detail. Try mode edit coding.'`, so this routes the partial through the established guidance path. Tests updated.
- [x] [Review][Decision] `switch mode` / `switch to mode` (no `<X>`) cascade to `<X> mode` with target="switch" / "switch to" [src/nova/systems/skin/commands.py:_parse_natural_language_with_target] — **Resolved (option b): add `_NL_RESERVED_LEADING_TOKENS = {"switch", "edit"}` guard.** Tokens that are NL-pattern leaders (`switch` / `edit`) only ever produce a valid mode-switch via their full forms (`switch to <X> mode`, `edit <X> mode`). Any other shape — `switch mode`, `switch to mode`, `switch foo mode`, `edit foo mode` (where edit's own arm didn't fully match) — falls to UNKNOWN with the original input echoed back. Prevents nonsense `MODE(target="switch")` / `MODE(target="switch to")` reaching Nerve. Tests added.

#### Patch findings

- [x] [Review][Patch] `? help` (and any `? <anything>`) parses to HELP instead of UNKNOWN — violates AC #7 [src/nova/systems/skin/commands.py — `?` in `_CANONICAL_FIRST_TOKENS`] — Spec AC #7 explicitly fences: *"The `?` alias is whole-input (literal `?` only — `??` or `? help` map to `UNKNOWN`)."* Current implementation puts `?` in `_CANONICAL_FIRST_TOKENS` and `_BARE_VERB_ALIAS`, so `? help` is treated identically to `help help` (canonical first-token; trailing tokens ignored) → `HELP`. Fix: treat `?` as a single-token whole-input alias (e.g., pre-step that maps the literal whole-stripped-input `?` to HELP before the canonical-first-token gate; remove `?` from `_CANONICAL_FIRST_TOKENS`). Add Block 10 negative tests `("? help", UNKNOWN, "? help")` / `("??", UNKNOWN, "??")`.
- [x] [Review][Patch] `_CANONICAL_FIRST_TOKENS` and `_BARE_VERB_ALIAS` are dual sources of truth that can drift [src/nova/systems/skin/commands.py:68-97] — A maintainer adding `pause` to `_CANONICAL_FIRST_TOKENS` but forgetting `_BARE_VERB_ALIAS` would get a `KeyError` from the bare-verb-dispatch path. The defensive comment ("the gate guarantees `first_lower` is in the alias map") is wishful thinking. Fix: derive the gate set from the alias map: `_CANONICAL_FIRST_TOKENS = frozenset(_BARE_VERB_ALIAS) | {"mode", "modes", "forget"}` (the special-case-dispatched verbs).
- [x] [Review][Patch] `_BARE_VERB_ALIAS` carries dead entries for `mode`, `modes`, `forget` [src/nova/systems/skin/commands.py:84-97] — Those entries are never accessed: `mode`/`modes` go through `_parse_mode_family`, `forget` goes through its own branch, all before the bare-verb-alias path. Either remove them (and combine with the previous patch to derive the gate from a single map of "verbs without targets") or unify dispatch through the alias map. Cleanest: drop the three dead entries and derive the gate as `frozenset(_BARE_VERB_ALIAS) | {"mode", "modes", "forget"}`.
- [x] [Review][Patch] `test_no_public_module_scope_dict_or_set_or_list_literals` is vacuous — every parser table starts with `_` and the test skips them [tests/unit/systems/skin/test_commands_immutability.py:test_no_public_module_scope_dict_or_set_or_list_literals] — The "negative" AST test inspects only non-underscore module-scope assignments. Story 3.4's tables are all `_CANONICAL_FIRST_TOKENS` / `_BARE_VERB_ALIAS` / `_CONTEXTUAL_REPLIES` / `_NL_PHRASES_BARE` (all underscore-prefixed). The test inspects zero names today and would not catch `_FOO_BAD: dict = {...}`. Companion test `test_every_public_module_scope_table_is_immutable_or_final` does the real work for underscore names. Either: (a) extend the negative test to inspect underscore-prefixed names too (drop the `if name.startswith("_"): continue` skip), or (b) remove the negative test and rely on the positive companion. Option (a) preferred — gives runtime feedback if a mutable literal sneaks in.
- [x] [Review][Patch] No assertion that NL phrase frozensets are pairwise disjoint [src/nova/systems/skin/commands.py:115-123] — Two verbs' frozensets could contain the same phrase; iteration order resolves the tiebreak silently. No collision today, but a future addition (e.g., adding `"create a new mode"` to MODE_EDIT by mistake) would silently route to whichever verb's frozenset iterates first. Fix: add a module-load assertion that `sum(len(s) for s in _NL_PHRASES_BARE.values()) == len(set().union(*_NL_PHRASES_BARE.values()))` — runtime check is cheap and the failure surfaces at import time.
- [x] [Review][Patch] Block 10 missing tests for `nova mode coding` / `nova help` / `nova status` [tests/unit/systems/skin/test_commands_parser.py:test_block_10_unknown_inputs_preserve_raw_in_target] — Currently tests bare `"nova"` only. Spec AC #19's T1-non-goal fence covers `nova` shell forms ("Layer A is not parsed by the in-session parser"). Add `("nova mode coding", UNKNOWN, "nova mode coding")`, `("nova help", UNKNOWN, "nova help")`, `("nova status", UNKNOWN, "nova status")`, `("nova memory", UNKNOWN, "nova memory")` so a future maintainer can't add Layer A handling here without breaking tests.
- [x] [Review][Patch] No tests locking precedence of canonical-first-token over `<X> mode` [tests/unit/systems/skin/test_commands_parser.py] — `mode mode` → `MODE(target="mode")` (Layer B canonical wins because `mode` is canonical). `forget mode` → `FORGET(target="mode")`. `help mode` → `HELP(target=None)`. The current parser produces these results but no test locks them; a future refactor that reorders the dispatch could silently change behavior. Add explicit precedence tests in Block 7 negatives.
- [x] [Review][Patch] `__post_init__` `ValueError` chain `__cause__` not asserted [tests/unit/systems/skin/test_command_shape.py:test_command_construction_rejects_invalid_string_verb] — `Command.__post_init__` does `raise ValueError(...) from err` to preserve the chain (per cross-cutting-patterns.md #4). The rejection test only asserts the offending value appears in the message, not that `err.value.__cause__ is not None`. Add `assert err.value.__cause__ is not None` to lock the chain-preservation contract.
- [x] [Review][Patch] Two parser-test sections both labeled "Block 7" [tests/unit/systems/skin/test_commands_parser.py — `test_block_7_natural_language_phrases_map_to_canonical` + `test_block_7_negative_natural_language_guards`] — Module docstring lists 12 blocks; both NL tests share "Block 7" without a/b sub-label. Rename `test_block_7_negative_natural_language_guards` → `test_block_7b_negative_natural_language_guards` (or restructure). Cosmetic but confusing for someone scanning for "all 12 blocks."
- [x] [Review][Patch] `_NL_PHRASES_BARE` docstring references a phantom variable [src/nova/systems/skin/commands.py:111-114] — Comment says phrases are "compared against `' '.join(normalized.lower().split())`" but the actual code uses `collapsed_lower = " ".join(lower_tokens)` where `lower_tokens` came from `stripped.split()`. Both produce the same logical result but the docstring should reference the actual variable. Update docstring to: "Compared against `' '.join(lower_tokens)` — see `parse()` Step 3 for the construction."
- [x] [Review][Patch] `test_block_12_parser_is_sync_function` is in the wrong block, and adapter is missing the paired `iscoroutinefunction` test [tests/unit/systems/skin/test_commands_parser.py:test_block_12_parser_is_sync_function + tests/unit/adapters/rich/test_skin_adapter.py] — The sync-contract assertion is a function-shape test, not a purity test; arguably belongs in `test_command_shape.py`. Lower-priority, but the bigger gap is no test confirming `RichSkinAdapter.parse_command` is `async` (the symmetric assertion). Add `assert inspect.iscoroutinefunction(RichSkinAdapter.parse_command) is True` to the adapter delegation tests.
- [x] [Review][Patch] `deferred-work.md:142` `Command.verb: str` typo-survival entry should be removed (or marked closed-by-Story-3.4) [_bmad-output/implementation-artifacts/deferred-work.md:142] — Story spec § "Modified planning / tracking files" explicitly says: *"`_bmad-output/implementation-artifacts/deferred-work.md` — at story completion, Dev removes (or marks closed-by-Story-3.4) the entry on line 142."* The Dev Notes Completion Notes say "ready to close." But the diff does not modify `deferred-work.md`. Fix: remove the bullet at line 142 (or replace with a one-line "closed by Story 3.4 — see [3-4-t1-command-grammar-and-deterministic-parser.md](3-4-t1-command-grammar-and-deterministic-parser.md)").

#### Deferred findings (logged separately — see `deferred-work.md`)

- [x] [Review][Defer] Smart quotes / Unicode punctuation in NL phrases produce UNKNOWN [src/nova/systems/skin/commands.py:_NL_PHRASES_BARE] — Curly apostrophe (`'`) in `"what's my status"` doesn't match `"what's my status"` (straight apostrophe). Common autocorrect input falls to UNKNOWN. Spec doesn't mandate Unicode punctuation normalization; a future polish pass can add `unicodedata.normalize` if user feedback requests it.
- [x] [Review][Defer] Trailing punctuation on contextual reply (`yes!`, `no.`) routes to UNKNOWN [src/nova/systems/skin/commands.py:_CONTEXTUAL_REPLIES] — Layer C single-token discipline (Spec AC #9) treats `yes!` as multi-character single-token that doesn't match `yes`. Common natural confirmation lost. Future polish: pre-strip trailing `.,!?;:` from the single-token form before contextual-reply lookup.
- [x] [Review][Defer] `Command` pickle/unpickle bypasses `__post_init__` validation [src/nova/systems/skin/models.py:Command.__post_init__] — Pickled Commands restored via `__setstate__` skip `__post_init__`. No consumer pickles Commands today (Brain stores text, not pickles), so this is theoretical. Fix when first persistence path lands: add `def __setstate__(self, state): self.__dict__.update(state); self.__post_init__()`.

#### Dismissed (~25)

Per-spec behaviors flagged as "concerns" by reviewers without spec context (`mode create coding` drops target — AC #7 mandates the wizard owns capture; bare `forget` → FORGET partial — AC #7 partial-form contract; whitespace collapse in target — AC #6 mandate); test patterns mistaken for bugs (`# type: ignore[arg-type]` on runtime-validation tests is the correct usage; `is`-vs-`==` mixing is correct enum-identity vs string-equality discipline); acknowledged limitations (Block 12 AST walk's `from time import time as t` rebinding gap is documented in the test docstring); out-of-scope concerns (extending `__post_init__` to validate `target`/`raw_input`/`is_contextual` — Story 3.4 scope was the deferred-work-tagged `verb` field only; very-large-input DoS — out of CLI threat model; pickle bypass — no consumer); functionally-correct cosmetic concerns (`AC #15 forbidden-prefix list` harmonization — bikeshedding; `nova.systems.ritual` whole-prefix vs `.system`-suffix is functionally equivalent under the existing logic). Detail in code-review session log.

## Dev Notes

### Pattern library consulted

- **#2 AST guards** — the new `test_commands_isolation.py` mirrors the `test_skin_adapter_isolation.py` pattern from Story 3.3. The Group A AC #3 shape test (CommandVerb membership) is also an AST-style invariant lock, even though it works on the runtime enum class rather than the source-file AST.
- **#3 frozen dataclass** — `Command` was already `frozen=True` (Story 1.9). The `CommandVerb` enum tightens the `verb` field type without weakening immutability.

Patterns NOT consulted: #1 clock indirection (parser is clock-free), #4 error translation (parser doesn't raise for any input — see § "Why the parser never raises"), #5 skip-on-error (no file loading), #6 transaction CM (no DB), #7 partial-init cleanup (parser is a free function, no constructor).

### Why the parser never raises

The architecture's "Skin handles deterministic command parsing" rule is paired with the operational rule that **every keystroke becomes a Command**. If the parser raised on malformed input, the REPL caller (Story 3.7) would have to handle that exception path — and any future caller (a unit test, a fuzz harness, an MCP-client emitter) would too. A closed-vocabulary marker-verb design (`UNKNOWN` and `EMPTY`) keeps the function total: every `str` produces a `Command`. Nerve's `route_command` is then the single decision site for what response prose corresponds to each marker verb. This split keeps the parser maximally testable (no exception matrices) and lets Nerve evolve the response prose without re-parsing.

### Why `target` preserves casing but `verb` matching is case-insensitive

The verb vocabulary is a closed set of lowercase canonical tokens; users may type `MODE` / `Mode` / `mOdE` and all should map to `MODE`. But the **target** is user data — a mode named `Deep Work` typed as `mode Deep Work` must round-trip with its capitalization intact (the lookup against `NovaConfig.modes` is case-insensitive at Nerve's level, but the audit log and the user-facing echo should reflect what the user typed, not a forcibly-lowered version). The parser preserves `raw_input` for the rendered echo and preserves `target` casing for the matched substring.

### Why the `<X> mode` natural-language mapping carries an explicit "first token isn't a canonical verb" guard

Without that guard, `status mode` would match the `<X> mode` template with `X="status"` and produce `Command(MODE, target="status")` — a mode-switch attempt to a non-existent mode. The user typed `status mode` (perhaps thinking they were running "status" and "mode" as some flag combination). The first-token canonical-verb match takes priority: `status` already wins at Layer B canonical, returning `Command(STATUS, target=None)`. The remaining text is dropped (per the "any remaining tokens ignored" rule for `status` in AC #7). This is intentional and lock-tested in Block 7 negative cases.

### Layer C single-token discipline

Contextual replies (`yes` / `no` / `resume` / `skip` / `cancel` / `confirm`) are single-token reflexes — a user prompted with "Resume coding mode? [resume/no]" types one word. Multi-token forms like `yes please` or `resume now` should NOT slip through Layer C; they fall to UNKNOWN. The justification: contextual replies override prompt context (architecture.md:1124 — *"Nerve only acts on them if the current UI state expects a response"*), and we want overrides to be unambiguous reflexes. A multi-token contextual is a usability smell — the prompt is asking too many questions at once, or the user is confused about what's expected.

### `mode_create` does not have a target

`mode create coding` is parsed as `Command(MODE_CREATE, target=None)` — the trailing `coding` is dropped. Reasoning: Epic 6's `mode create` is a wizard (multi-step interactive flow); the target name is captured by the wizard's first prompt, not from the launching command line. Including the target on the Command would create two ways to specify the name (positional + interactive), which Epic 6 would have to disambiguate. Cleaner to make Layer B reject the positional form.

### `mode_edit` DOES have a target (and a partial form)

`mode edit coding` parses to `Command(MODE_EDIT, target="coding")` — the target is identifying which existing mode is being edited; it's a lookup key, not user-creative input. The partial form `mode edit` (no name) parses to `Command(MODE_EDIT, target=None)` so Nerve can produce the *"Need one more detail. Try mode edit coding."* guidance.

### Closing the `Command.verb: str` deferral

[deferred-work.md:142](deferred-work.md#L142) explicitly tags Story 3.4 as the close-out for this deferral. Group A's `CommandVerb(StrEnum)` introduction is the canonical fix. After this story merges, the deferred-work entry should be removed (the dev should sweep `deferred-work.md` and delete the entry — or replace it with a single-line "closed by Story 3.4" note pointing to this story file). The grep target is `Command.verb: str` in `deferred-work.md`.

### Explicit scope fence (non-goals)

- Does NOT modify [`src/nova/cli.py`](../../src/nova/cli.py) — bare-`nova` session-loop wiring is Story 3.5.
- Does NOT modify [`src/nova/app.py`](../../src/nova/app.py) — no new fields, no new instantiation. The parser is a free function with no app-graph presence.
- Does NOT implement `NervePort.route_command` — Story 3.5. The parser produces Commands; routing prose is downstream.
- Does NOT generate the user-facing response strings for unknown / partial / empty inputs — those are response prose, owned by Nerve (Story 3.5) and rendered through Skin's `render_response` (Story 3.7).
- Does NOT compute "did you mean…?" suggestions — the parser preserves `raw_input` so a downstream layer (Story 3.5+ or future fuzzy-match enhancement) can compute distance-1 suggestions over the canonical verb set. The parser itself does no fuzzy matching.
- Does NOT recognize `nova <name>` (architecture.md:137 + 1133), `audit`, or `self-update` — these fall through to UNKNOWN. Block 10 tests lock the negative.
- Does NOT centralize verb-string constants outside `CommandVerb` — `StrEnum` IS the centralization. No `_NL_PHRASES` table re-export, no module-level constant duplication.
- Does NOT add an `await` boundary in the parser — the function is sync; `RichSkinAdapter.parse_command` wraps it in an `async def` to satisfy the Protocol surface. No `asyncio.to_thread` wrap (no thread-affinity concern; no I/O).
- Does NOT update the project-context.md or the architecture.md documents. Architecture decisions stand; the deviation from Decision-3's "Skin handles … simple keyword matching" wording (Story 3.4 ships a closed lookup-table parser, which IS what "simple keyword matching" means in practice) is documented in the parser module docstring per AC #4.
- Does NOT add a SkinPort method for "render unknown / partial / empty response prose" — that's Skin's `render_response` (Story 3.7) consuming a string Nerve produces.
- Does NOT introduce `nova.core.commands` or any cross-system command-vocabulary module — the vocabulary lives in `nova.systems.skin.models` (`CommandVerb`) where the `Command` type already lives.
- Does NOT touch [`src/nova/setup/__main__.py`](../../src/nova/setup/__main__.py) — setup doesn't use the in-session parser.

## Review Focus (boundary-first invariant sweep — abbreviated, pure-logic story)

| Dimension | Resolution for this story |
|---|---|
| **Lifecycle** | None. The parser is a free function with no state, no resources. |
| **Teardown under partial failure** | None. Free function — nothing to tear down. |
| **Concurrency model** | The parser is sync and pure — safe under any concurrency model. The `async def` lives at `SkinPort.parse_command`; the adapter wraps the sync call. No locks, no shared state, no thread-affinity. |
| **Cancellation** | `asyncio.CancelledError` cannot land inside the parser (no awaits). At the adapter boundary, the sync call cannot be cancelled mid-execution; the caller's cancellation lands either before or after the parser run. No swallowing risk. |
| **Error translation** | The parser does NOT raise. Every input (including malformed Unicode, control characters, very long inputs, intentionally adversarial strings like `"\x00\x01"`) produces a Command — UNKNOWN as the catch-all. Cross-cutting-patterns.md #4 does not apply: no boundary that emits external exceptions. |
| **Test determinism** | Trivially satisfied — the parser is pure. The Block 11 determinism property test locks "same input → same Command" across three calls. |
| **Logging opacity** | None. The parser does NOT log (Block 12 purity test enforces). User-typed mode names / forget targets / unknown inputs never enter the log channel via this module. The audit channel (Story 1.8) is upstream; Nerve owns audit emission for routed commands. |
| **Idempotency** | Trivially satisfied. `parse(x)` is referentially transparent. |
| **Atomicity contract** | None — no writes. |
| **Determinism of vocabulary** | The closed-vocabulary `CommandVerb` enum is deliberate: future verb additions are gated by both the enum and the regression test (AC #3). No verb may exist in user-facing surface without a corresponding enum member. |
| **T1 grammar lock** | Architecture.md:124-137's three layers + the explicit non-goals (`nova <name>`, `audit`, `self-update`) are locked by Block 10 negative tests. |
| **Closed-set lookup vs. regex/NLP** | Per architecture.md:1125 — *"Natural-language intent resolution that requires reasoning … must go through Nerve → Voice/Claude … Skin never attempts NLP-level interpretation."* The parser uses a closed phrase lookup (frozenset / Mapping) — no regex, no Levenshtein, no LLM. Adding a fuzzy-match phase here would breach the architecture; if a future story wants fuzzy-match, it lands in Nerve (after the parser produces UNKNOWN). |
| **Patterns consulted** | #2 AST guards (new `test_commands_isolation.py` + extended `test_skin_adapter_isolation.py`), #3 frozen dataclass (`Command` stays frozen). Patterns NOT consulted: #1 (no clock), #4 (no exceptions raised), #5 (no file loading), #6 (no DB), #7 (no resources to clean up). |

## Project Structure Notes

**New source files:**
- [`src/nova/systems/skin/commands.py`](../../src/nova/systems/skin/commands.py) — pure parser, `parse(raw_input: str) -> Command`, closed lookup tables, module docstring.

**Modified source files:**
- [`src/nova/systems/skin/models.py`](../../src/nova/systems/skin/models.py) — `CommandVerb(StrEnum)` added; `Command.verb: CommandVerb` retypes; `__all__` extends; docstring expands.
- [`src/nova/adapters/rich/skin.py`](../../src/nova/adapters/rich/skin.py) — `parse_command` body delegates to `parse`; `from nova.systems.skin.commands import parse` import added; module docstring's Story-3.4 clause dropped at line 8.

**Modified planning / tracking files:**
- [`_bmad-output/implementation-artifacts/sprint-status.yaml`](sprint-status.yaml) — Scrum Master flips `3-4-t1-command-grammar-and-deterministic-parser: backlog → ready-for-dev` via the create-story workflow; Dev flips `ready-for-dev → in-progress → review` during implementation; code-review workflow flips `review → done`.
- [`_bmad-output/implementation-artifacts/deferred-work.md`](deferred-work.md) — at story completion, Dev removes (or marks closed-by-Story-3.4) the entry on line 142 about `Command.verb: str` typo-survival.

**New test files:**
- [`tests/unit/systems/skin/test_command_shape.py`](../../tests/unit/systems/skin/test_command_shape.py) — Group A regression (Command field tuple, types, frozen, `CommandVerb` 16-member parametrize) **plus** AC #3 runtime-validation tests (enum-member success, valid-string coercion, invalid-string rejection, non-string-non-enum rejection, frozen-after-coercion).
- [`tests/unit/systems/skin/test_commands_parser.py`](../../tests/unit/systems/skin/test_commands_parser.py) — Group D's 12 parametrize blocks (~80 individual test invocations).
- [`tests/unit/systems/skin/test_commands_isolation.py`](../../tests/unit/systems/skin/test_commands_isolation.py) — Group E AST guards on `nova.systems.skin.commands`.
- [`tests/unit/systems/skin/test_commands_immutability.py`](../../tests/unit/systems/skin/test_commands_immutability.py) — AC #5b lookup-table immutability lock (runtime mutation rejection + AST RHS-shape check).

**Modified test files:**
- [`tests/unit/adapters/rich/test_skin_adapter_isolation.py`](../../tests/unit/adapters/rich/test_skin_adapter_isolation.py) — `ALLOWED_SYSTEMS_MODELS` gains `"nova.systems.skin.commands"`; positive-presence parametrize at line 201-209 gains the same entry.
- [`tests/unit/adapters/rich/test_skin_adapter.py`](../../tests/unit/adapters/rich/test_skin_adapter.py) — append two adapter-delegation tests per AC #14.

No `tests/unit/systems/skin/__init__.py` — the project does not use `__init__.py` in test directories (precedent: `tests/unit/systems/ritual/`, `tests/unit/systems/nerve/`, `tests/unit/adapters/rich/`).

**Line-count discipline.** Approximate target sizes (numbers are guidance, not gates):
- `commands.py` ≈ 150–220 lines (the closed lookup tables for NL phrases, contextual replies, alias sets are the dominant content; the `parse` function body itself is ~50 lines of dispatch logic; immutability wrappers add ~10 lines).
- `models.py` net delta ≈ +50 lines (`CommandVerb` enum class + 16 member declarations + `__post_init__` validator (~15 lines) + docstring expansion; the existing `Command` declaration adds one annotation change).
- `adapters/rich/skin.py` net delta ≈ +2 lines (one new import line, one method body line — replacing the `raise NotImplementedError` with `return parse(raw_input)`).
- New test files: ~95 tests across 4 files; expect ~700–800 lines of test code total (parser parametrize tables ≈ 600 lines, shape + runtime-validation ≈ 100 lines, isolation ≈ 80 lines, immutability ≈ 60 lines). The parametrize tables are dense — most "tests" are single rows in a parametrize block.

### Alignment with unified project structure

- `nova.systems.skin.commands` follows the architecture.md:1355 directory layout (Skin owns the parser; the `models.py` / `commands.py` split was anticipated by [`src/nova/systems/skin/models.py:6-12`](../../src/nova/systems/skin/models.py#L6-L12)). No new system directory; this is Skin-internal refinement.
- `tests/unit/systems/skin/` is a new test directory — follows the established Story 3.2 (`tests/unit/systems/nerve/`) and Story 3.3 (`tests/unit/systems/ritual/`) precedent.

### Detected conflicts or variances

- **`Command.verb` annotation flip.** `Command.verb: str` → `Command.verb: CommandVerb` is a binding-tightening — `CommandVerb` IS a `str` subclass at runtime (StrEnum guarantee), so any caller that compared `cmd.verb == "mode"` keeps working. mypy strict, however, will flag any caller that uses a non-enum literal for construction (e.g., a future test fixture writing `Command(verb="mod", ...)` will fail type-check). This is intentional — closing the `deferred-work.md:142` typo-survival concern requires exactly this tightening.
- **Layer A scope split.** The epic AC says Story 3.4 owns "Layer A launch behavior"; in practice, Layer A's session-loop wiring is Story 3.5's territory. Story 3.4 owns only the **in-session parser** (Layer B + Layer C). The in-session parser does NOT recognize shell-form Layer A inputs — `nova mode coding` / `nova help` / `nova status` / `nova memory` all map to `UNKNOWN` when fed to `parse()` (locked by Block 10 negatives in `test_commands_parser.py`). The two layers share canonical *verbs* (`mode`, `status`, `help`, `memory`) but the parser sees only the post-shell-prefix-strip text. When Story 3.5 wires argparse for the Layer A subcommand surface, cli.py will extract the verb+target from `argv` and either feed the in-session form (`mode coding`) into `parse()` or call Nerve directly with a constructed `Command` — that wiring decision belongs to Story 3.5. AC #20 codifies the fence: cli.py + app.py untouched in this story.

## References

- [Source: _bmad-output/planning-artifacts/epics.md — Story 3.4 ACs (lines 1136–1164), Epic 3 framing (lines 1048–1050)](../planning-artifacts/epics.md#L1136-L1164)
- [Source: _bmad-output/planning-artifacts/architecture.md — T1 Commands canonical vocabulary table (lines 124–137), Command Routing Convention (lines 1104–1133), T1 Scope Lock Skin row (line 121)](../planning-artifacts/architecture.md#L1104-L1133)
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — T1 Command Grammar Contract (lines 847–948), Layer A/B/C tables (lines 860–908), Partial / Invalid / Empty Input behavior (lines 909–942), T1 Canonical Vocabulary Summary (lines 944–948)](../planning-artifacts/ux-design-specification.md#L847-L948)
- [Source: _bmad-output/project-context.md — Voice generates text Skin renders it (line 64), Operational output bypasses Voice (line 66), No print() (line 44), Command grammar edge cases must be tested (line 109), One command must never have two meanings (line 202), Rendering is a sink not a source of truth (line 89)](../project-context.md)
- [Source: _bmad-output/implementation-artifacts/epic-1-retro-2026-04-15.md — boundary-first invariant sweep, cross-cutting-patterns origin](epic-1-retro-2026-04-15.md)
- [Source: _bmad-output/implementation-artifacts/epic-2-retro-2026-04-18.md — interaction-boundary classification (A6), invariant sweep extension](epic-2-retro-2026-04-18.md)
- [Source: _bmad-output/implementation-artifacts/epic-3-story-preflags.md — note that Story 3.4 is NOT pre-flagged (only 3.2, 3.3, 3.5 are interaction-boundary stories)](epic-3-story-preflags.md)
- [Source: _bmad-output/implementation-artifacts/3-3-briefingviewmodel-and-briefing-card-rendering.md — RichSkinAdapter pattern (Skin makes ZERO content decisions), `NotImplementedError("Story 3.4 scope")` seam, AST isolation guard pattern](3-3-briefingviewmodel-and-briefing-card-rendering.md)
- [Source: _bmad-output/implementation-artifacts/deferred-work.md (line 142) — `Command.verb: str` typo-survival deferral; Story 3.4 closes this](deferred-work.md#L142)
- [Source: docs/cross-cutting-patterns.md — patterns #2 (AST guards), #3 (frozen dataclass)](../../docs/cross-cutting-patterns.md)
- [Source: src/nova/systems/skin/models.py — `Command` frozen dataclass (Story 1.9), the line-6-to-12 docstring anticipating Story 3.4's `commands.py` placement](../../src/nova/systems/skin/models.py)
- [Source: src/nova/systems/skin/__init__.py — placeholder docstring (`"Skin system - Rich terminal rendering, command parsing. Implementation in later stories."`); no re-exports today, parser stays Skin-internal so this stays a docstring-only file](../../src/nova/systems/skin/__init__.py)
- [Source: src/nova/ports/skin.py:48 — `parse_command(self, raw_input: str) -> Command` Protocol surface (Story 1.9)](../../src/nova/ports/skin.py#L48)
- [Source: src/nova/adapters/rich/skin.py:147-148 — `NotImplementedError("Story 3.4 scope")` seam to replace](../../src/nova/adapters/rich/skin.py#L147-L148)
- [Source: src/nova/cli.py — bare-nova `_async_main` placeholder (lines 459-462); Layer A wiring stays untouched in this story (AC #20)](../../src/nova/cli.py#L459-L462)
- [Source: src/nova/app.py — `NovaApp` graph (Story 3.3 — does NOT change in Story 3.4; the parser is a free function)](../../src/nova/app.py)
- [Source: tests/unit/adapters/rich/test_skin_adapter_isolation.py — AST guard pattern Story 3.4 mirrors and extends (AC #12)](../../tests/unit/adapters/rich/test_skin_adapter_isolation.py)
- [Source: tests/unit/systems/nerve/test_briefing_isolation.py — AST guard precedent for `nova.systems.X` modules (Story 3.2)](../../tests/unit/systems/nerve/test_briefing_isolation.py)
- [Source: tests/unit/ports/test_port_isolation.py — port-shape regression test the `Command.verb: CommandVerb` annotation must remain compatible with (AC #17)](../../tests/unit/ports/test_port_isolation.py)
- [Source: tests/unit/systems/ritual/test_briefing_view_model_shape.py — `dataclasses.fields` + `typing.get_type_hints` shape-test pattern (Story 3.3) that AC #3 mirrors](../../tests/unit/systems/ritual/test_briefing_view_model_shape.py)

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (1M context)

### Debug Log References

- **Initial coverage gap on `?` (single literal).** First parser draft had `?` only in `_BARE_VERB_ALIAS` but not in `_CANONICAL_FIRST_TOKENS`, so the canonical-first-token gate at `parse()` step 2 didn't fire for `?`. Symptom: Block 2's `("?", CommandVerb.HELP)` test failed with `<UNKNOWN: 'unknown'>`. Fix: added `?` to `_CANONICAL_FIRST_TOKENS`. Caught by the parametrized canonical-no-target block on first run — the parametrize matrix did its job.
- **Three unreachable defensive `if target_tokens:` checks in `_parse_natural_language_with_target`.** Each NL pattern's outer guard (`n >= 4` for `switch to <X> mode`, `n >= 3` for `edit <X> mode`, `n >= 2` for `<X> mode`) guarantees the slice produces at least one element. The inner falsy-check on `target_tokens` was dead code that coverage couldn't hit. Removed all three; outer guards alone are sufficient. Coverage went from 97.2% → 100% on `commands.py`.
- **`_parse_layer_b_canonical` had a defensive `return None` arm.** The caller's `first_lower in _CANONICAL_FIRST_TOKENS` gate already guarantees one of the dispatch branches matches. Restructured to return `Command` unconditionally — the trailing `return None` and the caller's `is not None` guard were removed, and `bare_target = _BARE_VERB_ALIAS.get(first_lower); if bare_target is not None` collapsed to `_BARE_VERB_ALIAS[first_lower]` (the gate guarantees the key exists). Eliminated 2 dead lines + 1 partial branch from coverage.
- **`modes coding` arm needed an explicit test.** The `if first_lower == "modes"` branch in `_parse_mode_family` (line ~289) only fires when `modes` has trailing tokens — bare `modes` exits earlier at the `if not rest_original` block. Added `("modes coding", CommandVerb.MODE, None)` and `("modes whatever", CommandVerb.MODE, None)` to Block 3's parametrize to exercise the path.
- **`from typing import Mapping` ruff `UP035`.** `typing.Mapping` is deprecated; ruff strict prefers `collections.abc.Mapping`. Switched to `from collections.abc import Mapping; from typing import Final` — keeps `Final` (which IS in `typing`) and routes the abstract type through its modern home.
- **mypy strict flagged unused `# type: ignore[attr-defined]` in immutability test.** The `_BARE_VERB_ALIAS` and siblings are not actually under-typed — they're real module-level names that mypy can resolve once imported. Removed the four ignores; mypy clean. The test now reads cleanly: a single underscore is a Python convention for "module-internal but accessible," not a hard privacy barrier.
- **`Command(verb="memoryy")` typo-survival closed.** Verified by writing the test FIRST: `test_command_construction_rejects_invalid_string_verb` parametrized over 7 typo variants (including case-mismatched `"MODE"` and the empty/whitespace strings). Each raises `ValueError` with the offending value in the message. The runtime check fires through the `__post_init__` validator, not just under mypy.
- **Tokenization preserves `raw_input` verbatim.** Six of the parametrize blocks assert `result.raw_input == raw_input` directly — important because Story 3.5's response template will use `raw_input` to echo the user's text in the unknown-input suggestion message. Multi-space and tab inputs (`"mode\tcoding"`, `"mode  Deep  Work"`) round-trip with their original whitespace intact in `raw_input`, while `target` collapses whitespace to single spaces.

### Completion Notes List

- **Task 1 — `CommandVerb` enum + `__post_init__`.** Closed `deferred-work.md:142` `Command.verb: str` typo-survival. `Command(verb="memoryy", ...)` now raises `ValueError` at construction. `Command(verb="mode", ...)` is coerced to `Command(verb=CommandVerb.MODE, ...)` so existing string-construction sites (none in source today; tests only) keep working unchanged at runtime — mypy strict catches typed-site mismatches separately.
- **Task 2 — Pure parser.** `parse(raw_input: str) -> Command` is referentially transparent: no I/O, no clock, no logging, no random, no environment reads, no dynamic imports. Module-level lookup tables (4 of them) are immutable at runtime — `frozenset` for the canonical-first-token set, `MappingProxyType` for the three lookups (bare aliases, contextual replies, NL-phrase-to-verb-set). The parser is total: every `str` input produces a `Command` (no exceptions raised for malformed input).
- **Task 3 — Adapter delegation.** `RichSkinAdapter.parse_command` is now a one-liner that calls into `nova.systems.skin.commands.parse`. The four other `NotImplementedError` stubs (`render_progress`, `render_shutdown_card`, `render_response`, `collect_input`) stay untouched — those are Story 3.6 / 3.7 scope.
- **Task 4 — 91 parser tests across 12 parametrize blocks.** Coverage layout: empty (6) + canonical-no-target (10) + canonical-with-target (12) + partials (2) + case-insensitivity (10) + whitespace (4) + NL phrases (13) + NL negatives (2) + contextual (9) + single-token-discipline (6) + non-goals (7) + determinism (10) + purity (3 sub-tests). Block 12 purity is AST-only — no `freezegun` dependency.
- **Task 5 — 2 adapter delegation tests.** Locks the `RichSkinAdapter.parse_command → parse` indirection without re-covering the parser vocabulary.
- **Task 6 — Isolation + immutability locks.** `test_commands_isolation.py` (4 AST tests) prevents `nova.systems.skin.commands` from importing `rich` / `sqlite3` / adapters / sibling-system internals. `test_commands_immutability.py` (7 tests) locks both runtime mutation rejection AND AST RHS-shape — public module-scope assignments must wrap in `frozenset(...)` / `MappingProxyType(...)` / `Final[...]`. Updated `test_skin_adapter_isolation.py` allowlist to permit the new `nova.systems.skin.commands` import.
- **Task 7 — Composition / port isolation regression sweep.** All 163 composition-root + port-isolation tests pass unchanged. The `Command.verb: str → CommandVerb` annotation flip did not require any test changes: `CommandVerb` is a `str` subclass via `StrEnum`, so port-shape extraction reads the annotation as a class object regardless.
- **Task 8 — Full CI gate.** Ruff lint ✓, ruff format ✓, mypy strict ✓ (121 files), 1585 unit + 51 integration tests pass, **100% coverage** on every Story 3.4 module (99 stmts + 38 branches, 0 misses across `nova.systems.skin.{commands, models, __init__}`). Net delta: **+147 unit tests** (vs. forecasted +100–115; overage came from the 16-member `CommandVerb` parametrize, the 7-typo-variant rejection parametrize, and the 12-case canonical-with-target block being denser than estimated).
- **Layer A scope fence honored.** `nova/cli.py` and `nova/app.py` are untouched — the bare-`nova` session-loop wiring and any Layer A subcommand surface remain Story 3.5's territory per AC #20. The parser's vocabulary table covers Layer A's verbs only because Layer A and Layer B share canonical verbs at the parser level (`mode`, `status`, `help`, `memory`).
- **`deferred-work.md:142` ready to close.** The `Command.verb: str` typo-survival entry should be removed (or marked closed-by-Story-3.4) by the next dev / SM pass over `deferred-work.md`. Story 3.4's `__post_init__` validator + the 7-variant typo-rejection test are the canonical fix.

### File List

**New source files:**

- `src/nova/systems/skin/commands.py` — pure deterministic parser; `parse(raw_input: str) -> Command`; immutable lookup tables (`_CANONICAL_FIRST_TOKENS`, `_BARE_VERB_ALIAS`, `_CONTEXTUAL_REPLIES`, `_NL_PHRASES_BARE`); three Skin-internal helpers (`_parse_layer_b_canonical`, `_parse_mode_family`, `_parse_natural_language_with_target`); module docstring with purity / closed-vocabulary / Layer-scope contracts.

**Modified source files:**

- `src/nova/systems/skin/models.py` — added `CommandVerb(StrEnum)` 16-member closed vocabulary; flipped `Command.verb: str → CommandVerb`; added `__post_init__` validator (coerce-or-reject); expanded module + class docstrings; extended `__all__`.
- `src/nova/adapters/rich/skin.py` — replaced `parse_command` `NotImplementedError` body with `return parse(raw_input)`; added `from nova.systems.skin.commands import parse` import; updated module docstring's Story-3.4 clause.

**New test files:**

- `tests/unit/systems/skin/test_command_shape.py` — 39 tests: shape regression (4) + `CommandVerb` 16-value parametrize (16 + 1 length check) + 5 runtime-validation groups (success, valid-string-coerce parametrized, invalid-string-reject parametrized, non-string-non-enum-reject parametrized, frozen-after-coercion).
- `tests/unit/systems/skin/test_commands_parser.py` — 91 parser tests across 12 parametrize blocks (empty / canonical-no-target / canonical-with-target / partials / case-insensitivity / whitespace / NL phrases positive + negative / contextual / single-token-discipline / non-goals / determinism / purity AST + caplog + sync-contract).
- `tests/unit/systems/skin/test_commands_isolation.py` — 4 AST guard tests on `nova.systems.skin.commands` (forbidden modules incl. `rich`, sqlite3-at-any-scope, no dynamic imports, positive `nova.systems.skin.models` import lock).
- `tests/unit/systems/skin/test_commands_immutability.py` — 7 lookup-table immutability tests (5 runtime mutation rejections + 2 AST RHS-shape locks).

**Modified test files:**

- `tests/unit/adapters/rich/test_skin_adapter.py` — appended 2 adapter-delegation tests (`test_parse_command_delegates_to_pure_parser`, `test_parse_command_handles_empty_input`).
- `tests/unit/adapters/rich/test_skin_adapter_isolation.py` — extended `ALLOWED_SYSTEMS_MODELS` and the positive-presence parametrize to include `"nova.systems.skin.commands"`; updated docstring.

**Modified planning / tracking files:**

- `_bmad-output/implementation-artifacts/sprint-status.yaml` — `3-4-t1-command-grammar-and-deterministic-parser: ready-for-dev → in-progress → review`; `last_updated` summary refreshed.

### Change Log

| Date | Description |
|---|---|
| 2026-05-05 | Story 3.4 implemented per ready-for-dev spec. 8 tasks complete, all 21 ACs satisfied, 100% coverage on new/modified Skin modules (99 stmts + 38 branches, 0 misses), 1585 unit + 51 integration tests pass, +147 net unit-test delta over the Story 3.3 baseline. Closes deferred-work.md:142 (`Command.verb: str` typo-survival) via `CommandVerb(StrEnum)` + `__post_init__` runtime validator. AST-only purity test (no `freezegun`). Lookup-table immutability locked by `frozenset` / `MappingProxyType` + AST RHS-shape regression. Status: in-progress → review. |
| 2026-05-05 | Three-layer adversarial code review (Blind Hunter / Edge Case Hunter / Acceptance Auditor) ran in fresh agent contexts. 48 raw findings, 19 unique post-dedup. Resolved: 3 decision-needed (D1 `modes <X>` → mode-switch alias; D2 `edit mode` n=2 → MODE_EDIT partial; D3 `switch [to] mode` no-X → UNKNOWN via `_NL_RESERVED_LEADING_TOKENS` guard). Applied 12 review patches: `? help` → UNKNOWN spec fix; `_CANONICAL_FIRST_TOKENS` derived from `_BARE_VERB_ALIAS` (single source of truth); dropped dead alias entries; module-load `_check_nl_phrase_disjointness` collision gate; vacuous immutability test fixed; Layer A non-goal tests (`nova mode coding` / `nova help` / `nova status` / `nova memory` → UNKNOWN); precedence-lock tests (`mode mode` / `forget mode` / `help mode`); `__cause__` chain assertion; sync-shape test moved to `test_command_shape.py`; adapter async-shape companion lock; `_NL_PHRASES_BARE` docstring fix; `deferred-work.md:142` closed. 3 deferred (smart quotes / trailing punctuation on contextual / pickle bypass). Final tally: 1606 unit pass + 1 brittle deselected + 1 pre-existing skip + 51 integration pass; 100% coverage on Story 3.4 modules (112 stmts + 44 branches, 0 misses). ruff + mypy strict + ruff-format clean. Status: review → done. |
| 2026-05-05 | Post-review user-reported corrections (2 findings). **Finding 1 — D1 reverted:** UX spec table at ux-design-specification.md:879 lists `modes` as an alias for *List modes* only (canonical: bare `mode`); Switch mode has alias `—`. D1 (b) had treated `modes coding` as `mode coding` mode-switch — that violated the UX contract. Reverted to option (a): `modes` lives in `_BARE_VERB_ALIAS` with target dropped (consistent with `status mode` / `help foo` policy for canonical verbs that don't take a target). Tests updated: `modes coding` → `MODE(target=None)`. **Finding 2 — Layer A description fixed:** the "Detected conflicts" entry at line 500 incorrectly claimed `nova mode coding` and in-session `mode coding` produce the same Command — but the in-session parser maps `nova mode coding` to UNKNOWN (locked by Block 10 negatives). Rewrote the entry to clarify: parser sees only post-shell-prefix-strip text; cli.py's argparse for Layer A is Story 3.5's wiring decision. CI green: 1607 unit + 51 integration pass; 100% coverage maintained on Story 3.4 modules. |
