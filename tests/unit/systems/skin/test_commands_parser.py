"""Pure-parser unit tests for :func:`nova.systems.skin.commands.parse` (Story 3.4 Group D).

Twelve parametrize blocks cover the full vocabulary surface mandated by
the T1 Command Grammar Contract (UX spec lines 847–948) and the
architecture's command-routing rules (architecture.md:1104–1133):

* Block 1  — empty / whitespace-only input
* Block 2  — Layer B canonical verbs
* Block 3  — Layer B verbs with target (casing preserved)
* Block 4  — partial commands (verb without expected target)
* Block 5  — case insensitivity on the verb token
* Block 6  — whitespace handling (collapse multi-space targets)
* Block 7  — Layer B natural-language phrase mappings
* Block 8  — Layer C contextual replies (tagged ``is_contextual=True``)
* Block 9  — Layer C single-token discipline (multi-token rejected)
* Block 10 — explicit T1 non-goals (``audit``, ``self-update``,
              ``nova <name>``) plus generic unknowns
* Block 11 — determinism property (same input → byte-identical Command)
* Block 12 — purity property (no clock / log / random / env / dynamic
              import) via AST walk + behavioral log-emptiness

The parser produces a Command for **every** input — it never raises.
Marker verbs (``UNKNOWN``, ``EMPTY``) cover the inputs that fall through
all canonical / NL / contextual matches.
"""

from __future__ import annotations

import ast
import inspect
import logging
from pathlib import Path

import pytest

from nova.systems.skin.commands import parse
from nova.systems.skin.models import CommandVerb

# --- Block 1: empty / whitespace-only --------------------------------------


@pytest.mark.parametrize(
    "raw_input",
    ["", " ", "\t", "   \t\n  ", "\n", "\r\n"],
)
def test_block_1_empty_input_maps_to_empty_marker(raw_input: str) -> None:
    """Empty / whitespace-only input → ``CommandVerb.EMPTY``.

    ``raw_input`` is preserved verbatim.
    """
    result = parse(raw_input)
    assert result.verb is CommandVerb.EMPTY
    assert result.target is None
    assert result.is_contextual is False
    assert result.raw_input == raw_input


# --- Block 2: Layer B canonical (lowercase, no target) ---------------------


@pytest.mark.parametrize(
    ("raw_input", "expected_verb"),
    [
        ("mode", CommandVerb.MODE),
        ("modes", CommandVerb.MODE),
        ("status", CommandVerb.STATUS),
        ("memory", CommandVerb.MEMORY),
        ("help", CommandVerb.HELP),
        ("?", CommandVerb.HELP),
        ("shutdown", CommandVerb.SHUTDOWN),
        ("quit", CommandVerb.SHUTDOWN),
        ("exit", CommandVerb.SHUTDOWN),
        ("forget", CommandVerb.FORGET),
    ],
)
def test_block_2_layer_b_canonical_no_target(raw_input: str, expected_verb: CommandVerb) -> None:
    result = parse(raw_input)
    assert result.verb is expected_verb
    assert result.target is None
    assert result.is_contextual is False
    assert result.raw_input == raw_input


# --- Block 3: Layer B with target, casing preserved -----------------------


@pytest.mark.parametrize(
    ("raw_input", "expected_verb", "expected_target"),
    [
        ("mode coding", CommandVerb.MODE, "coding"),
        ("mode Coding", CommandVerb.MODE, "Coding"),
        ("mode Deep Work", CommandVerb.MODE, "Deep Work"),
        ("forget Meridian", CommandVerb.FORGET, "Meridian"),
        ("forget the meridian project", CommandVerb.FORGET, "the meridian project"),
        ("mode edit coding", CommandVerb.MODE_EDIT, "coding"),
        ("mode edit Deep Work", CommandVerb.MODE_EDIT, "Deep Work"),
        ("mode create", CommandVerb.MODE_CREATE, None),
        # mode create + trailing tokens — target dropped (Epic 6 wizard captures name interactively)
        ("mode create coding", CommandVerb.MODE_CREATE, None),
        # ``modes`` is a list-modes alias only (UX spec table) — Switch mode
        # has no alias. ``modes <X>`` drops trailing tokens, same as other
        # canonical verbs that don't take a target (``status mode`` /
        # ``help foo``). Locks the spec-aligned behavior.
        ("modes coding", CommandVerb.MODE, None),
        ("modes Deep Work", CommandVerb.MODE, None),
        ("modes whatever", CommandVerb.MODE, None),
    ],
)
def test_block_3_layer_b_with_target_preserves_casing(
    raw_input: str, expected_verb: CommandVerb, expected_target: str | None
) -> None:
    result = parse(raw_input)
    assert result.verb is expected_verb
    assert result.target == expected_target
    assert result.is_contextual is False
    assert result.raw_input == raw_input


# --- Block 4: partial commands (verb expects a target, none supplied) ----


@pytest.mark.parametrize(
    ("raw_input", "expected_verb"),
    [
        ("mode edit", CommandVerb.MODE_EDIT),
        ("forget", CommandVerb.FORGET),
        # Review D2: ``edit mode`` (n=2) is the partial form of
        # ``edit <X> mode`` and routes to MODE_EDIT(target=None) — same
        # placeholder-guidance response Nerve produces for ``mode edit``.
        ("edit mode", CommandVerb.MODE_EDIT),
        ("Edit Mode", CommandVerb.MODE_EDIT),  # case-insensitive
    ],
)
def test_block_4_partial_commands_have_none_target(
    raw_input: str, expected_verb: CommandVerb
) -> None:
    """Partial verbs encode as ``Command(verb=X, target=None)``.

    Nerve (Story 3.5) maps ``MODE_EDIT`` with ``target=None`` to
    ``'Need one more detail. Try mode edit coding.'``; maps ``FORGET``
    with ``target=None`` to ``'Tell me what to forget. Example: forget Meridian'``.
    """
    result = parse(raw_input)
    assert result.verb is expected_verb
    assert result.target is None
    assert result.is_contextual is False


# --- Block 5: case insensitivity on the verb token -----------------------


@pytest.mark.parametrize(
    ("raw_input", "expected_verb", "expected_target"),
    [
        ("MODE", CommandVerb.MODE, None),
        ("Mode", CommandVerb.MODE, None),
        ("mOdE coding", CommandVerb.MODE, "coding"),
        ("STATUS", CommandVerb.STATUS, None),
        ("Help", CommandVerb.HELP, None),
        ("ShUtDoWn", CommandVerb.SHUTDOWN, None),
        ("Quit", CommandVerb.SHUTDOWN, None),
        ("EXIT", CommandVerb.SHUTDOWN, None),
        ("Forget Meridian", CommandVerb.FORGET, "Meridian"),
        ("Mode Edit Coding", CommandVerb.MODE_EDIT, "Coding"),
    ],
)
def test_block_5_verb_match_is_case_insensitive(
    raw_input: str, expected_verb: CommandVerb, expected_target: str | None
) -> None:
    result = parse(raw_input)
    assert result.verb is expected_verb
    assert result.target == expected_target


# --- Block 6: whitespace handling -----------------------------------------


@pytest.mark.parametrize(
    ("raw_input", "expected_verb", "expected_target"),
    [
        ("  mode  coding  ", CommandVerb.MODE, "coding"),
        ("mode\tcoding", CommandVerb.MODE, "coding"),
        ("mode    Deep    Work", CommandVerb.MODE, "Deep Work"),
        ("\tforget\tMeridian\t", CommandVerb.FORGET, "Meridian"),
    ],
)
def test_block_6_whitespace_collapses_in_target(
    raw_input: str, expected_verb: CommandVerb, expected_target: str
) -> None:
    """Multi-space / tab whitespace inside / around input collapses
    in the parsed target. ``raw_input`` is preserved with original
    whitespace intact.
    """
    result = parse(raw_input)
    assert result.verb is expected_verb
    assert result.target == expected_target
    assert result.raw_input == raw_input


# --- Block 7: Layer B natural-language mappings ---------------------------


@pytest.mark.parametrize(
    ("raw_input", "expected_verb", "expected_target"),
    [
        ("switch to coding mode", CommandVerb.MODE, "coding"),
        ("Switch to Coding Mode", CommandVerb.MODE, "Coding"),
        ("coding mode", CommandVerb.MODE, "coding"),
        ("Deep Work mode", CommandVerb.MODE, "Deep Work"),
        ("what modes do i have", CommandVerb.MODE, None),
        ("create a new mode", CommandVerb.MODE_CREATE, None),
        ("edit coding mode", CommandVerb.MODE_EDIT, "coding"),
        ("edit Deep Work mode", CommandVerb.MODE_EDIT, "Deep Work"),
        ("what's my status", CommandVerb.STATUS, None),
        ("whats my status", CommandVerb.STATUS, None),
        ("what do you know", CommandVerb.MEMORY, None),
        ("shut down", CommandVerb.SHUTDOWN, None),
        ("done for today", CommandVerb.SHUTDOWN, None),
    ],
)
def test_block_7_natural_language_phrases_map_to_canonical(
    raw_input: str, expected_verb: CommandVerb, expected_target: str | None
) -> None:
    result = parse(raw_input)
    assert result.verb is expected_verb
    assert result.target == expected_target
    assert result.is_contextual is False


@pytest.mark.parametrize(
    ("raw_input", "expected_verb", "expected_target"),
    [
        # Layer B canonical wins over <X> mode pattern (precedence lock).
        ("status mode", CommandVerb.STATUS, None),
        ("forget mode", CommandVerb.FORGET, "mode"),
        ("help mode", CommandVerb.HELP, None),
        ("memory mode", CommandVerb.MEMORY, None),
        # mode mode — Layer B canonical wins; falls into mode <name> arm with target="mode"
        ("mode mode", CommandVerb.MODE, "mode"),
        # "nova coding" — bare-mode shortcut explicitly out of T1 grammar (architecture.md:1133)
        ("nova coding", CommandVerb.UNKNOWN, "nova coding"),
        # Review D3: switch / switch to / switch foo without proper "switch to <X> mode"
        # full form must NOT cascade to MODE(target="switch...") — falls to UNKNOWN.
        ("switch mode", CommandVerb.UNKNOWN, "switch mode"),
        ("switch to mode", CommandVerb.UNKNOWN, "switch to mode"),
        ("switch foo mode", CommandVerb.UNKNOWN, "switch foo mode"),
        # "edit foo" without trailing "mode" — UNKNOWN (edit is reserved leading
        # token; falls to UNKNOWN unless full edit <X> mode form matches).
        ("edit foo", CommandVerb.UNKNOWN, "edit foo"),
    ],
)
def test_block_7b_negative_natural_language_guards(
    raw_input: str, expected_verb: CommandVerb, expected_target: str | None
) -> None:
    """Negative guards on the ``<X> mode`` natural-language arm:

    - First token is a canonical verb → Layer B canonical takes precedence.
    - ``nova <X>`` → UNKNOWN (architecture.md:1133 explicit non-goal).
    - First token is an NL-reserved leader (``switch`` / ``edit``) without
      its full-form match → UNKNOWN (review D3).
    """
    result = parse(raw_input)
    assert result.verb is expected_verb
    assert result.target == expected_target


# --- Block 8: Layer C contextual replies ----------------------------------


@pytest.mark.parametrize(
    ("raw_input", "expected_verb"),
    [
        ("resume", CommandVerb.RESUME),
        ("yes", CommandVerb.YES),
        ("no", CommandVerb.NO),
        ("skip", CommandVerb.SKIP),
        ("cancel", CommandVerb.CANCEL),
        ("confirm", CommandVerb.CONFIRM),
        # Case insensitive
        ("Yes", CommandVerb.YES),
        ("RESUME", CommandVerb.RESUME),
        ("Cancel", CommandVerb.CANCEL),
    ],
)
def test_block_8_layer_c_contextual_replies(raw_input: str, expected_verb: CommandVerb) -> None:
    result = parse(raw_input)
    assert result.verb is expected_verb
    assert result.target is None
    assert result.is_contextual is True
    assert result.raw_input == raw_input


# --- Block 9: Layer C single-token discipline -----------------------------


@pytest.mark.parametrize(
    "raw_input",
    [
        "yes please",
        "resume now",
        "no thanks",
        "skip this",
        "cancel that",
        "confirm yes",
    ],
)
def test_block_9_layer_c_rejects_multi_token_replies(raw_input: str) -> None:
    """Multi-token forms of contextual replies fall through to UNKNOWN.

    Single-token discipline keeps contextual overrides unambiguous —
    a multi-token contextual is a usability smell.
    """
    result = parse(raw_input)
    assert result.verb is CommandVerb.UNKNOWN
    assert result.target == raw_input
    assert result.is_contextual is False


# --- Block 10: explicit T1 non-goals + generic unknowns -------------------


@pytest.mark.parametrize(
    "raw_input",
    [
        # Architecture.md:137 explicit T1 non-goals.
        "audit",
        "self-update",
        "nova",  # bare nova in in-session prompt isn't a Layer A command
        # Layer A shell forms must NOT route through the in-session parser
        # (Story 3.5 owns argparse-level Layer A wiring; the in-session
        # parser sees these as unknown user input).
        "nova mode coding",
        "nova help",
        "nova status",
        "nova memory",
        # ``?`` is a whole-input alias of ``help`` — anything other than
        # the literal single ``?`` token must NOT route to HELP (UX-DR
        # § Partial Command Behavior).
        "? help",
        "? something",
        "??",
        "? mode",
        # Generic unknowns.
        "hello",
        "???",  # not the literal single ?
        "modeswitch coding",  # typo; parser does not fuzzy-match
        "foo bar baz",
    ],
)
def test_block_10_unknown_inputs_preserve_raw_in_target(raw_input: str) -> None:
    """Unrecognized inputs map to UNKNOWN with target=raw_input.

    Preserving the original text in ``target`` lets Nerve / Voice
    echo it in the suggestion-response template (architecture.md:1129).
    """
    result = parse(raw_input)
    assert result.verb is CommandVerb.UNKNOWN
    assert result.target == raw_input
    assert result.is_contextual is False
    assert result.raw_input == raw_input


# --- Block 11: determinism property --------------------------------------


@pytest.mark.parametrize(
    "raw_input",
    [
        "mode coding",
        "",
        "  mode  Deep  Work  ",
        "shutdown",
        "RESUME",
        "what's my status",
        "audit",
        "mode edit",
        "switch to coding mode",
        "memory",
    ],
)
def test_block_11_determinism_three_calls_byte_identical(raw_input: str) -> None:
    """Same input → byte-identical Command across three calls.

    Locks "same input always produces same Command" (architecture.md:1123).
    """
    a = parse(raw_input)
    b = parse(raw_input)
    c = parse(raw_input)
    assert a == b == c
    assert a.verb is b.verb is c.verb
    assert a.target == b.target == c.target
    assert a.raw_input == b.raw_input == c.raw_input
    assert a.is_contextual == b.is_contextual == c.is_contextual


# --- Block 12: purity property (AST + behavioral) ------------------------


_FORBIDDEN_ATTR_ACCESSES: frozenset[tuple[str, str]] = frozenset(
    {
        # Clock reads
        ("datetime", "now"),
        ("datetime", "utcnow"),
        ("datetime", "today"),
        ("time", "time"),
        ("time", "monotonic"),
        ("time", "perf_counter"),
        # Logging
        ("logging", "getLogger"),
        ("logger", "debug"),
        ("logger", "info"),
        ("logger", "warning"),
        ("logger", "error"),
        ("logger", "critical"),
        ("logger", "exception"),
        ("logger", "log"),
        # Dynamic imports
        ("importlib", "import_module"),
    }
)

_FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "__import__",
        "print",
    }
)

_FORBIDDEN_ATTR_PREFIXES: frozenset[str] = frozenset(
    {
        # Any attribute access on these modules signals impurity
        "random",
    }
)


def _read_parser_source() -> str:
    import nova.systems.skin.commands as commands_module

    source_path_str = inspect.getsourcefile(commands_module)
    assert source_path_str is not None
    return Path(source_path_str).read_text(encoding="utf-8")


def test_block_12_parser_source_has_no_forbidden_calls() -> None:
    """AST walk rejects clock / logging / random / dynamic-import names.

    Per memory/feedback_ast_static_analysis_tests.md: AST inspection,
    NOT text regex (regex trips on docstrings / comments that mention
    forbidden names innocently).
    """
    tree = ast.parse(_read_parser_source())
    leaks: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            owner = node.value
            if isinstance(owner, ast.Name):
                pair = (owner.id, node.attr)
                if pair in _FORBIDDEN_ATTR_ACCESSES:
                    leaks.append(f"{owner.id}.{node.attr}")
                if owner.id in _FORBIDDEN_ATTR_PREFIXES:
                    leaks.append(f"{owner.id}.{node.attr}")
        elif isinstance(node, ast.Name):
            if node.id in _FORBIDDEN_NAMES:
                leaks.append(node.id)
    assert not leaks, f"Forbidden purity-violating names in parser source: {sorted(set(leaks))}"


def test_block_12_parser_does_not_log_during_invocation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Behavioral guard against AST-walk's name-rebinding gap.

    Even if a future maintainer aliases ``logger`` via
    ``from logging import getLogger as _gl`` (slipping past the AST
    name check), ``caplog`` would catch any actual log emission.
    """
    representative_inputs = [
        "",
        "mode coding",
        "shutdown",
        "what's my status",
        "audit",
        "mode edit",
        "yes",
        "RESUME",
        "foo bar",
        "?",
    ]
    with caplog.at_level(logging.DEBUG):
        for raw in representative_inputs:
            parse(raw)
    assert caplog.records == [], (
        f"Parser logged unexpectedly: {[(r.name, r.message) for r in caplog.records]}"
    )


# Note: the sync-shape assertion for ``parse`` lives in
# ``test_command_shape.py`` (it's a function-shape test, not a purity
# test). The paired async-shape assertion for ``RichSkinAdapter.parse_command``
# lives in ``tests/unit/adapters/rich/test_skin_adapter.py``.
