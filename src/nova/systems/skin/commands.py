"""Pure deterministic parser for the T1 in-session command grammar (Story 3.4).

Architecture (architecture.md:1104–1133):

* **Skin** parses user input into a :class:`~nova.systems.skin.models.Command`.
  Parsing is deterministic — same input → same Command, every time.
* **Nerve** routes the parsed Command to the correct system; Nerve owns
  response prose for unknown / partial / empty inputs.
* **Voice** handles personality-bearing prose; the parser never reaches
  upward into Voice or Nerve.

Purity contract
---------------
``parse(raw_input)`` is a pure function — no I/O, no clock, no logging,
**no mutable global state**. Module-level lookup tables are immutable
read-only structures (``frozenset`` for sets, :class:`types.MappingProxyType`
for mappings); the parser reads them but nothing mutates them. Same input
→ same output, every time. Architecture.md:1123 invariant. The Story 3.4
purity test (``test_commands_parser.py`` Block 12) AST-walks this module
to lock the rule.

Closed vocabulary
-----------------
Every ``raw_input`` produces a Command; the parser never raises for
malformed input. Unrecognized inputs map to
``Command(verb=CommandVerb.UNKNOWN, target=raw_input, raw_input=raw_input)``
— the original text is preserved as ``target`` so Nerve / Voice can echo
it in the suggestion-response template ("Didn't catch that. Try one of
these…"). Empty / whitespace-only inputs map to
``Command(verb=CommandVerb.EMPTY, target=None, raw_input=raw_input)`` —
Skin's REPL drops these silently (Story 3.7 enforces); Nerve also handles
them as a no-op for defense-in-depth.

Layer scope
-----------
This parser handles **Layer B (in-session prompt)** and **Layer C
(contextual replies)** only. Layer A shell-form launch (``nova``,
``nova mode <name>``, ``nova status``, ``nova help``, ``nova memory``)
is handled by argparse in :mod:`nova.cli` — Story 3.5 wires the
bare-``nova`` session-loop and any Layer A subcommand surface. Layer C
contextual replies are tagged ``is_contextual=True``; Nerve enforces
"valid only when prompted" (architecture.md:1124).

Architecture deviation
----------------------
Architecture.md:1106 says *"Skin handles deterministic command parsing
(structured ``[verb] [target]`` commands and simple keyword matching).
Parsing is deterministic — same input always produces same Command
object."* This module is the canonical parser implementation. Natural-
language mappings are **literal-form aliases** (a closed lookup table),
not LLM-driven NLP. Anything that requires reasoning (ambiguous input,
conversational queries) maps to ``UNKNOWN``; Nerve routes to Voice if
the tier permits (Epic 7+).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from nova.systems.skin.models import Command, CommandVerb

# --- Closed lookup tables (immutable at runtime) ----------------------------

# Single-token aliases that map directly to a canonical CommandVerb without
# any target — the verb is bare and trailing tokens are dropped. Used for
# the fall-through path in ``_parse_layer_b_canonical``.
#
# ``modes`` is here (not in ``_SPECIAL_DISPATCH_FIRST_TOKENS``) because the
# UX spec lists ``modes`` as an alias for **List modes** only (canonical
# form: bare ``mode``); Switch mode (``mode <name>``) has no alias. So
# ``modes coding`` is NOT a valid mode-switch shape — trailing tokens are
# dropped, same as ``status mode`` / ``help foo`` (canonical verbs that
# don't take a target).
#
# ``mode`` and ``forget`` are NOT in this map because they have
# target-bearing special-case dispatch in ``_parse_mode_family`` and the
# ``forget`` arm respectively.
_BARE_VERB_ALIAS: Final[Mapping[str, CommandVerb]] = MappingProxyType(
    {
        "modes": CommandVerb.MODE,  # list-modes alias of bare "mode" (UX spec)
        "status": CommandVerb.STATUS,
        "memory": CommandVerb.MEMORY,
        "help": CommandVerb.HELP,
        "shutdown": CommandVerb.SHUTDOWN,
        "quit": CommandVerb.SHUTDOWN,
        "exit": CommandVerb.SHUTDOWN,
    }
)

# Verbs with target-bearing special-case dispatch (handled before the
# bare-verb-alias path in ``_parse_layer_b_canonical``).
_SPECIAL_DISPATCH_FIRST_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "mode",
        "forget",
    }
)

# Verb tokens that appear as the first token of canonical Layer B commands.
# Derived from the union of ``_BARE_VERB_ALIAS`` keys and the special-case
# tokens — single source of truth prevents drift between the gate set and
# the dispatcher (review patch).
_CANONICAL_FIRST_TOKENS: Final[frozenset[str]] = (
    frozenset(_BARE_VERB_ALIAS) | _SPECIAL_DISPATCH_FIRST_TOKENS
)

# ``?`` is a whole-input alias of ``help`` — only matches when the user
# typed exactly the single token ``?``. ``? help`` / ``??`` / ``? foo``
# fall through to UNKNOWN per spec (UX-DR § Partial Command Behavior).
_HELP_WHOLE_INPUT_ALIAS: Final[str] = "?"

# Layer C contextual replies (single-token, lowercased). is_contextual=True.
_CONTEXTUAL_REPLIES: Final[Mapping[str, CommandVerb]] = MappingProxyType(
    {
        "resume": CommandVerb.RESUME,
        "yes": CommandVerb.YES,
        "no": CommandVerb.NO,
        "skip": CommandVerb.SKIP,
        "cancel": CommandVerb.CANCEL,
        "confirm": CommandVerb.CONFIRM,
    }
)

# Whole-input natural-language phrases that map to a (verb, target=None)
# pair. Compared against ``" ".join(lower_tokens)`` (see ``parse`` Step 3)
# — so multi-space inputs collapse before lookup. Stored as a frozenset of
# lowercase phrases per verb. The empty intersection of every pair of
# frozensets is asserted at module load below to prevent silent collisions.
_NL_PHRASES_BARE: Final[Mapping[CommandVerb, frozenset[str]]] = MappingProxyType(
    {
        CommandVerb.MODE: frozenset({"what modes do i have"}),
        CommandVerb.MODE_CREATE: frozenset({"create a new mode"}),
        CommandVerb.STATUS: frozenset({"what's my status", "whats my status"}),
        CommandVerb.MEMORY: frozenset({"what do you know"}),
        CommandVerb.SHUTDOWN: frozenset({"shut down", "done for today"}),
    }
)

# Module-load assertion: NL phrase frozensets must be pairwise disjoint —
# otherwise iteration order silently determines which verb a colliding
# phrase routes to. Cheap runtime check; failure surfaces at import time
# with a specific ImportError message that names the colliding phrase.


def _check_nl_phrase_disjointness(table: Mapping[CommandVerb, frozenset[str]]) -> None:
    """Raise :class:`ImportError` if any phrase appears in two verbs'
    frozensets in ``table``. Module-load gate; testable with a fixture
    table to lock the collision-detection invariant.
    """
    seen: set[str] = set()
    for verb, phrases in table.items():
        overlap = seen & phrases
        if overlap:
            raise ImportError(
                f"NL phrase collision in _NL_PHRASES_BARE: {sorted(overlap)} (verb={verb!r})"
            )
        seen |= phrases


_check_nl_phrase_disjointness(_NL_PHRASES_BARE)

# Reserved leading tokens for natural-language patterns. These tokens only
# form a valid mode-switch via their full forms (``switch to <X> mode``,
# ``edit <X> mode``); any other shape (``switch mode``, ``switch to mode``,
# ``edit foo``, ``switch foo mode``) must NOT cascade to the generic
# ``<X> mode`` arm — that would produce nonsense ``MODE(target="switch")`` /
# ``MODE(target="switch to")`` / similar (review D3). Inputs starting with
# these tokens that don't match a full-form arm fall to UNKNOWN.
#
# ``?`` is also reserved here: it is only valid as the whole-input help
# alias (Step 2 of ``parse``); ``? <anything>`` must NOT cascade to the
# ``<X> mode`` arm and produce ``MODE(target="?")``.
_NL_RESERVED_LEADING_TOKENS: Final[frozenset[str]] = frozenset({"switch", "edit", "?"})


def parse(raw_input: str) -> Command:
    """Parse ``raw_input`` deterministically into a :class:`Command`.

    Tokenization order (first match wins):

    1. Empty / whitespace-only → ``CommandVerb.EMPTY``.
    2. Whole-input ``?`` alias of ``help`` → ``CommandVerb.HELP``.
    3. Layer B canonical (first token is in ``_CANONICAL_FIRST_TOKENS``).
    4. Layer B natural-language phrase (whole-input lowercase + collapsed
       whitespace match against ``_NL_PHRASES_BARE`` and the
       ``<X> mode`` / ``switch to <X> mode`` / ``edit <X> mode`` patterns).
    5. Layer C contextual reply (single-token lowercased input).
    6. Fallback → ``CommandVerb.UNKNOWN`` with ``target=raw_input``.

    Returns
    -------
    Command
        Always — the parser never raises for any ``str`` input.
    """
    # Step 1: empty / whitespace-only.
    stripped = raw_input.strip()
    if stripped == "":
        return Command(
            verb=CommandVerb.EMPTY,
            target=None,
            raw_input=raw_input,
            is_contextual=False,
        )

    # Step 2: ``?`` whole-input alias of ``help``. Spec UX-DR § Partial
    # Command Behavior fences ``?`` to the literal single token only —
    # ``? help`` / ``??`` / ``? foo`` must NOT route to HELP. Checked
    # before tokenization so trailing tokens cannot slip through.
    if stripped == _HELP_WHOLE_INPUT_ALIAS:
        return Command(
            verb=CommandVerb.HELP,
            target=None,
            raw_input=raw_input,
            is_contextual=False,
        )

    # Tokenize on any whitespace run. ``original_tokens`` preserves the
    # user's casing for target-extraction; ``lower_tokens`` is for
    # case-insensitive verb matching.
    original_tokens: list[str] = stripped.split()
    lower_tokens: list[str] = [t.lower() for t in original_tokens]
    first_lower = lower_tokens[0]
    rest_original = original_tokens[1:]
    rest_lower = lower_tokens[1:]

    # Step 3: Layer B canonical. First-token-driven dispatch. Every
    # branch inside ``_parse_layer_b_canonical`` returns a Command —
    # the gate above guarantees the first token is in the canonical
    # set.
    if first_lower in _CANONICAL_FIRST_TOKENS:
        return _parse_layer_b_canonical(
            first_lower=first_lower,
            rest_original=rest_original,
            rest_lower=rest_lower,
            raw_input=raw_input,
        )

    # Step 3: Layer B natural-language phrase mappings. Compared against
    # the lowercased + whitespace-collapsed whole input.
    collapsed_lower = " ".join(lower_tokens)

    # 3a: bare-verb NL phrases (no target).
    for verb, phrases in _NL_PHRASES_BARE.items():
        if collapsed_lower in phrases:
            return Command(
                verb=verb,
                target=None,
                raw_input=raw_input,
                is_contextual=False,
            )

    # 3b: ``switch to <X> mode`` — verb + target with leading "switch to" prefix.
    nl_with_target = _parse_natural_language_with_target(
        original_tokens=original_tokens,
        lower_tokens=lower_tokens,
        raw_input=raw_input,
    )
    if nl_with_target is not None:
        return nl_with_target

    # Step 4: Layer C contextual reply (single-token only).
    if len(lower_tokens) == 1 and first_lower in _CONTEXTUAL_REPLIES:
        return Command(
            verb=_CONTEXTUAL_REPLIES[first_lower],
            target=None,
            raw_input=raw_input,
            is_contextual=True,
        )

    # Step 5: fallback.
    return Command(
        verb=CommandVerb.UNKNOWN,
        target=raw_input,
        raw_input=raw_input,
        is_contextual=False,
    )


# --- Layer B canonical helpers ---------------------------------------------


def _parse_layer_b_canonical(
    *,
    first_lower: str,
    rest_original: list[str],
    rest_lower: list[str],
    raw_input: str,
) -> Command:
    """Dispatch a Layer B first-token match.

    Returns a Command unconditionally — the caller's gate
    (``first_lower in _CANONICAL_FIRST_TOKENS``) guarantees one of the
    branches matches. The gate set is derived from
    ``_BARE_VERB_ALIAS`` keys ∪ ``_SPECIAL_DISPATCH_FIRST_TOKENS`` so the
    dispatcher cannot drift from the gate.
    """
    if first_lower in _SPECIAL_DISPATCH_FIRST_TOKENS:
        if first_lower == "forget":
            if not rest_original:
                return Command(
                    verb=CommandVerb.FORGET,
                    target=None,
                    raw_input=raw_input,
                    is_contextual=False,
                )
            return Command(
                verb=CommandVerb.FORGET,
                target=" ".join(rest_original),
                raw_input=raw_input,
                is_contextual=False,
            )
        # ``mode`` / ``modes`` — same dispatch (review D1: ``modes <X>``
        # is symmetric with ``mode <X>``, treated as mode-switch).
        return _parse_mode_family(
            rest_original=rest_original,
            rest_lower=rest_lower,
            raw_input=raw_input,
        )
    # status / memory / help / shutdown / quit / exit — bare verbs;
    # any trailing tokens are dropped. The gate guarantees ``first_lower``
    # is in ``_BARE_VERB_ALIAS`` for this branch.
    return Command(
        verb=_BARE_VERB_ALIAS[first_lower],
        target=None,
        raw_input=raw_input,
        is_contextual=False,
    )


def _parse_mode_family(
    *,
    rest_original: list[str],
    rest_lower: list[str],
    raw_input: str,
) -> Command:
    """Handle the ``mode`` / ``mode create`` / ``mode edit ...`` / ``mode <name>``
    family. ``modes`` is NOT routed here — the UX spec lists ``modes``
    as an alias for **List modes** only, so it goes through the bare-
    verb-alias path with target dropped (like ``status mode`` /
    ``help foo``). Only ``mode`` enters this dispatcher.
    """
    # Bare ``mode`` or ``modes`` (no rest tokens) → list-modes.
    if not rest_original:
        return Command(
            verb=CommandVerb.MODE,
            target=None,
            raw_input=raw_input,
            is_contextual=False,
        )

    # ``mode create [...]``. Target dropped — Epic 6 wizard owns capture.
    if rest_lower[0] == "create":
        return Command(
            verb=CommandVerb.MODE_CREATE,
            target=None,
            raw_input=raw_input,
            is_contextual=False,
        )

    # ``mode edit [...]``. With name → MODE_EDIT(target). Without → partial.
    if rest_lower[0] == "edit":
        edit_target_tokens = rest_original[1:]
        if not edit_target_tokens:
            return Command(
                verb=CommandVerb.MODE_EDIT,
                target=None,
                raw_input=raw_input,
                is_contextual=False,
            )
        return Command(
            verb=CommandVerb.MODE_EDIT,
            target=" ".join(edit_target_tokens),
            raw_input=raw_input,
            is_contextual=False,
        )

    # ``mode <name>`` — generic mode-switch.
    return Command(
        verb=CommandVerb.MODE,
        target=" ".join(rest_original),
        raw_input=raw_input,
        is_contextual=False,
    )


# --- Natural-language with target ------------------------------------------


def _parse_natural_language_with_target(
    *,
    original_tokens: list[str],
    lower_tokens: list[str],
    raw_input: str,
) -> Command | None:
    """Match the four target-bearing NL phrase patterns:

    * ``switch to <X> mode``  → MODE(target=X)
    * ``edit <X> mode``       → MODE_EDIT(target=X)
    * ``edit mode`` (n==2)    → MODE_EDIT(target=None) — partial form (review D2)
    * ``<X> mode``            → MODE(target=X), but only when the first
                                token is neither a canonical verb (Layer B
                                canonical would have already won) nor an
                                NL-reserved leading token (review D3:
                                ``switch`` / ``edit`` only form valid
                                input via their full forms).

    ``<X>`` preserves original casing.
    """
    n = len(lower_tokens)

    # ``switch to <X> mode`` — at least 4 tokens, last is "mode". The
    # ``n >= 4`` guard guarantees ``original_tokens[2:-1]`` is non-empty.
    if (
        n >= 4
        and lower_tokens[0] == "switch"
        and lower_tokens[1] == "to"
        and lower_tokens[-1] == "mode"
    ):
        return Command(
            verb=CommandVerb.MODE,
            target=" ".join(original_tokens[2:-1]),
            raw_input=raw_input,
            is_contextual=False,
        )

    # ``edit <X> mode`` — at least 3 tokens, first is "edit", last is "mode".
    # The ``n >= 3`` guard guarantees ``original_tokens[1:-1]`` is non-empty.
    if n >= 3 and lower_tokens[0] == "edit" and lower_tokens[-1] == "mode":
        return Command(
            verb=CommandVerb.MODE_EDIT,
            target=" ".join(original_tokens[1:-1]),
            raw_input=raw_input,
            is_contextual=False,
        )

    # ``edit mode`` (n==2) — partial form of ``edit <X> mode`` (review D2).
    # Routes to MODE_EDIT(target=None); Nerve responds with the standard
    # ``Need one more detail. Try mode edit coding.`` guidance.
    if n == 2 and lower_tokens[0] == "edit" and lower_tokens[1] == "mode":
        return Command(
            verb=CommandVerb.MODE_EDIT,
            target=None,
            raw_input=raw_input,
            is_contextual=False,
        )

    # ``<X> mode`` — at least 2 tokens, last is "mode", first token is
    # NOT a canonical verb (Layer B canonical would have already won at
    # Step 3 for ``status mode`` / ``forget mode`` / etc.) and NOT an
    # NL-reserved leading token (review D3 — ``switch`` / ``edit`` only
    # produce valid input via their full forms; falling through to
    # ``<X> mode`` would yield nonsense ``MODE(target="switch")`` /
    # ``MODE(target="switch to")`` / ``MODE(target="edit foo")``).
    # The ``n >= 2`` guard guarantees ``original_tokens[:-1]`` is non-empty.
    if (
        n >= 2
        and lower_tokens[-1] == "mode"
        and lower_tokens[0] not in _CANONICAL_FIRST_TOKENS
        and lower_tokens[0] not in _NL_RESERVED_LEADING_TOKENS
    ):
        return Command(
            verb=CommandVerb.MODE,
            target=" ".join(original_tokens[:-1]),
            raw_input=raw_input,
            is_contextual=False,
        )

    return None


__all__: list[str] = ["parse"]
