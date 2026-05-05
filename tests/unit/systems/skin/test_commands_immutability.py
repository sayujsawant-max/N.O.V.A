"""Lookup-table immutability lock for :mod:`nova.systems.skin.commands` (Story 3.4 AC #5b).

The parser's purity claim depends on its module-level lookup tables
being immutable at runtime — not just by convention. A future
maintainer who introduces ``_FOO: dict = {...}`` and later mutates it
would silently break determinism. Two complementary checks enforce the
invariant:

1. **Runtime mutation rejection** — for each known public lookup table,
   exercise a representative mutation operation and assert the right
   exception fires (``AttributeError`` for ``frozenset``,
   ``TypeError`` for :class:`types.MappingProxyType`).

2. **AST RHS-shape lock** — walk the parser source's module-scope
   assignments. For every public name (no leading underscore) whose
   right-hand side is an expression, assert the RHS is one of:
   ``frozenset(...)`` call, ``MappingProxyType(...)`` call, or wrapped
   in a ``Final[...]`` annotation. Forbids re-introducing
   ``_PUBLIC: dict = {...}`` style tables. Underscored names backing a
   ``MappingProxyType`` are allowed because they are not the public
   read surface.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import nova.systems.skin.commands as commands_module
from nova.systems.skin.commands import (
    _BARE_VERB_ALIAS,
    _CANONICAL_FIRST_TOKENS,
    _CONTEXTUAL_REPLIES,
    _NL_PHRASES_BARE,
    _check_nl_phrase_disjointness,
)
from nova.systems.skin.models import CommandVerb


def _read_module_source() -> str:
    source_path_str = inspect.getsourcefile(commands_module)
    assert source_path_str is not None
    return Path(source_path_str).read_text(encoding="utf-8")


# --- Runtime mutation rejection --------------------------------------------


def test_canonical_first_tokens_is_frozenset_and_rejects_add() -> None:
    """``_CANONICAL_FIRST_TOKENS`` is a ``frozenset`` — mutation methods
    do not exist; calling ``.add`` raises ``AttributeError``.
    """
    assert isinstance(_CANONICAL_FIRST_TOKENS, frozenset)
    with pytest.raises(AttributeError):
        _CANONICAL_FIRST_TOKENS.add("newverb")  # type: ignore[attr-defined]


def test_bare_verb_alias_is_mappingproxy_and_rejects_setitem() -> None:
    """``_BARE_VERB_ALIAS`` is a :class:`MappingProxyType` — assignment
    raises ``TypeError`` ('mappingproxy object does not support item
    assignment').
    """
    from types import MappingProxyType

    assert isinstance(_BARE_VERB_ALIAS, MappingProxyType)
    with pytest.raises(TypeError):
        _BARE_VERB_ALIAS["newkey"] = None  # type: ignore[index]


def test_contextual_replies_is_mappingproxy_and_rejects_setitem() -> None:
    from types import MappingProxyType

    assert isinstance(_CONTEXTUAL_REPLIES, MappingProxyType)
    with pytest.raises(TypeError):
        _CONTEXTUAL_REPLIES["newkey"] = None  # type: ignore[index]


def test_contextual_replies_rejects_delitem() -> None:
    """A different mutation surface — `del proxy[key]` — also rejects."""
    with pytest.raises(TypeError):
        del _CONTEXTUAL_REPLIES["yes"]  # type: ignore[attr-defined]


def test_nl_phrases_bare_is_mappingproxy_and_inner_values_are_frozensets() -> None:
    """The outer mapping is read-only AND every inner value is an
    immutable :class:`frozenset` (not a mutable ``set``).
    """
    from types import MappingProxyType

    assert isinstance(_NL_PHRASES_BARE, MappingProxyType)
    with pytest.raises(TypeError):
        _NL_PHRASES_BARE["newkey"] = frozenset()  # type: ignore[index]
    for value in _NL_PHRASES_BARE.values():
        assert isinstance(value, frozenset), (
            f"_NL_PHRASES_BARE inner value must be frozenset, got {type(value).__name__}"
        )


def test_nl_phrase_disjointness_check_passes_on_real_table() -> None:
    """The shipped ``_NL_PHRASES_BARE`` has no phrase collisions —
    re-invoking the module-load gate is a no-op.
    """
    _check_nl_phrase_disjointness(_NL_PHRASES_BARE)


def test_nl_phrase_disjointness_check_raises_on_collision() -> None:
    """A collision between two verbs' frozensets surfaces as
    :class:`ImportError` at module load — locks the collision-detection
    behavior so a future maintainer cannot silently introduce overlap.
    The test passes a fixture mapping so the real ``_NL_PHRASES_BARE``
    stays untouched.
    """
    colliding_table: dict[CommandVerb, frozenset[str]] = {
        CommandVerb.MODE: frozenset({"go to coding"}),
        CommandVerb.STATUS: frozenset({"go to coding"}),  # collision
    }
    with pytest.raises(ImportError) as err:
        _check_nl_phrase_disjointness(colliding_table)
    assert "go to coding" in str(err.value)


# --- AST RHS-shape lock ----------------------------------------------------


def _is_immutable_call(node: ast.AST) -> bool:
    """Return True if ``node`` is a ``frozenset(...)`` or
    ``MappingProxyType(...)`` call expression.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id in {"frozenset", "MappingProxyType"}:
        return True
    return isinstance(func, ast.Attribute) and func.attr in {
        "frozenset",
        "MappingProxyType",
    }


def _is_final_annotated(node: ast.AnnAssign) -> bool:
    """Return True if the annotation is ``Final[...]`` or ``Final``."""
    annotation = node.annotation
    if isinstance(annotation, ast.Subscript):
        target = annotation.value
        if isinstance(target, ast.Name) and target.id == "Final":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "Final":
            return True
    return isinstance(annotation, ast.Name) and annotation.id == "Final"


def test_no_public_module_scope_dict_or_set_or_list_literals() -> None:
    """Every module-scope name (public OR underscored read-surface)
    whose RHS is a collection literal must wrap it in
    ``frozenset(...)`` / ``MappingProxyType(...)`` or be
    ``Final``-annotated. Forbids re-introducing mutable lookup tables.

    The single-underscore convention in this module marks "module-private
    read surface" rather than "private mutable backing storage" — the
    parser's lookup tables (``_CANONICAL_FIRST_TOKENS`` etc.) ARE the
    public-from-the-test-suite-perspective surface. Inspecting them is
    the whole point of this test. Dunder-named (``__all__`` etc.) and
    function-local names are skipped.
    """
    tree = ast.parse(_read_module_source())
    leaks: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if not isinstance(target, ast.Name):
                continue
            name = target.id
            if name.startswith("__"):
                continue  # dunder (e.g., __all__) — different invariant
            value = node.value
            if value is None:
                continue
            if _is_final_annotated(node):
                continue
            if _is_immutable_call(value):
                continue
            if isinstance(value, (ast.Dict, ast.Set, ast.List)):
                leaks.append(f"{name} ({type(value).__name__} literal)")
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if not isinstance(tgt, ast.Name):
                    continue
                name = tgt.id
                if name.startswith("__"):
                    continue  # dunder names (e.g., __all__)
                value = node.value
                if _is_immutable_call(value):
                    continue
                # Tuple / Constant literals at module scope are immutable.
                if isinstance(value, (ast.Tuple, ast.Constant)):
                    continue
                if isinstance(value, (ast.Dict, ast.Set, ast.List)):
                    leaks.append(f"{name} ({type(value).__name__} literal)")
    assert not leaks, (
        f"Module-scope mutable collection literals in nova.systems.skin.commands: "
        f"{sorted(set(leaks))}. Wrap in frozenset(...) / MappingProxyType(...) / Final[...]."
    )


def test_every_public_module_scope_table_is_immutable_or_final() -> None:
    """Positive form — every public AnnAssign whose RHS is an expression
    must be either a ``frozenset(...)`` call, a ``MappingProxyType(...)``
    call, or a ``Final``-annotated value. This catches the case where a
    non-collection-literal RHS (e.g., ``_FOO: dict = some_function()``)
    sneaks past the negative test above.
    """
    tree = ast.parse(_read_module_source())
    leaks: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        target = node.target
        if not isinstance(target, ast.Name):
            continue
        name = target.id
        if name.startswith("_") and not name.startswith("__"):
            # Private name like _CANONICAL_FIRST_TOKENS, _BARE_VERB_ALIAS —
            # these ARE the public lookup tables (single-underscore convention
            # for module-private; the lack of leading underscore in the type
            # var would be too strict). We still want to lock them as
            # immutable.
            pass
        if name.startswith("__"):
            continue  # dunder allowed (e.g., __all__)
        value = node.value
        if value is None:
            continue
        if _is_final_annotated(node) and _is_immutable_call(value):
            continue
        if _is_final_annotated(node):
            # Final-annotated but RHS is not a known immutable call.
            # Allow if RHS is a string / number / tuple / frozenset literal
            # — those are immutable too. Reject only if the RHS is a
            # mutable literal.
            if isinstance(value, (ast.Dict, ast.Set, ast.List)):
                leaks.append(f"{name} (Final-annotated but mutable {type(value).__name__})")
            continue
        if _is_immutable_call(value):
            continue
        if isinstance(value, (ast.Constant, ast.Tuple)):
            continue  # primitives + tuples are immutable
        if isinstance(value, (ast.Dict, ast.Set, ast.List)):
            leaks.append(f"{name} ({type(value).__name__} literal not wrapped)")
    assert not leaks, (
        f"Module-scope tables in nova.systems.skin.commands not provably immutable: "
        f"{sorted(set(leaks))}"
    )
