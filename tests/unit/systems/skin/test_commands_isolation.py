"""AST guards for :mod:`nova.systems.skin.commands` (Story 3.4 Group E).

The pure command parser is rendering-agnostic and consumed only by the
Skin adapter. It must NOT reach into any other adapter / system / app
layer, must NOT touch ``rich`` (rendering belongs to the adapter), and
must NOT touch DB / Win32 / Anthropic / yaml / sqlite. These invariants
are mechanically enforced here.

Allowed import surface (Story 3.4 AC #15):

* stdlib: ``__future__``, ``enum``, ``types`` (for ``MappingProxyType``),
  ``typing`` (for ``Final`` / ``Mapping``).
* ``nova.systems.skin.models`` — :class:`Command` and
  :class:`CommandVerb` (the parser produces Command instances and
  references the closed-vocabulary enum).

Forbidden surface:

* ``rich`` (any submodule) — the parser is rendering-agnostic.
* ``sqlite3`` / ``anthropic`` / ``pywin32`` / ``pywintypes`` / ``psutil``
  / ``win32*`` / ``yaml`` — third-party I/O modules.
* ``nova.app`` / ``nova.cli`` / ``nova.setup.*`` — the parser does not
  reach upward into composition / entry-point layers.
* ``nova.adapters.*`` — parser consumes nothing from the adapter layer.
* ``nova.systems.<system>.<non-models>`` — parser consumes domain
  types only via ``.models``.
* Dynamic imports of any forbidden prefix.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import nova.systems.skin.commands as commands_module

FORBIDDEN_TOPLEVEL_MODULES: frozenset[str] = frozenset(
    {
        "sqlite3",
        "anthropic",
        "pywin32",
        "pywintypes",
        "psutil",
        "win32api",
        "win32gui",
        "win32com",
        "win32con",
        "yaml",
        "rich",  # parser is rendering-agnostic
    }
)

FORBIDDEN_NOVA_PREFIXES: tuple[str, ...] = (
    "nova.app",
    "nova.cli",
    "nova.setup",
    "nova.adapters",
    "nova.systems.brain.system",
    "nova.systems.eyes",
    "nova.systems.hands.system",
    "nova.systems.nerve",
    "nova.systems.shield",
    "nova.systems.ritual",  # parser does not reach into Ritual
    "nova.systems.voice",
)

ALLOWED_SYSTEMS_PREFIXES: frozenset[str] = frozenset(
    {
        "nova.systems.skin.models",
    }
)


def _read_module_source() -> str:
    source_path_str = inspect.getsourcefile(commands_module)
    assert source_path_str is not None
    return Path(source_path_str).read_text(encoding="utf-8")


def _has_forbidden_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def test_commands_does_not_import_forbidden_modules() -> None:
    tree = ast.parse(_read_module_source())
    leaked: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level > 0 or node.module is None:
                leaked.append(f"{'.' * node.level}{node.module or ''}  (relative import forbidden)")
                continue
            top = node.module.split(".")[0]
            if top in FORBIDDEN_TOPLEVEL_MODULES:
                leaked.append(node.module)
                continue
            if _has_forbidden_prefix(node.module, FORBIDDEN_NOVA_PREFIXES):
                leaked.append(node.module)
                continue
            if (
                node.module.startswith("nova.systems")
                and node.module not in ALLOWED_SYSTEMS_PREFIXES
            ):
                leaked.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in FORBIDDEN_TOPLEVEL_MODULES:
                    leaked.append(alias.name)
                    continue
                if _has_forbidden_prefix(alias.name, FORBIDDEN_NOVA_PREFIXES):
                    leaked.append(alias.name)
                    continue
                if (
                    alias.name.startswith("nova.systems")
                    and alias.name not in ALLOWED_SYSTEMS_PREFIXES
                ):
                    leaked.append(alias.name)
    assert not leaked, f"Forbidden imports in nova.systems.skin.commands: {sorted(set(leaked))}"


def test_commands_does_not_import_sqlite3_at_any_scope() -> None:
    """``sqlite3`` must not appear anywhere — the parser is purely value-to-value."""
    tree = ast.parse(_read_module_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "sqlite3" and not alias.name.startswith("sqlite3."), (
                    "nova.systems.skin.commands must not import sqlite3"
                )
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "sqlite3", (
                "nova.systems.skin.commands must not import from sqlite3"
            )


def test_commands_no_dynamic_forbidden_imports() -> None:
    tree = ast.parse(_read_module_source())
    dynamic_targets: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_dunder = isinstance(func, ast.Name) and func.id == "__import__"
        is_importlib = isinstance(func, ast.Attribute) and func.attr == "import_module"
        if not (is_dunder or is_importlib):
            continue
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            dynamic_targets.append(node.args[0].value)
    leaked = [
        t
        for t in dynamic_targets
        if t.split(".")[0] in FORBIDDEN_TOPLEVEL_MODULES
        or _has_forbidden_prefix(t, FORBIDDEN_NOVA_PREFIXES)
        or (t.startswith("nova.systems") and t not in ALLOWED_SYSTEMS_PREFIXES)
    ]
    assert not leaked, (
        f"Dynamic forbidden imports in nova.systems.skin.commands: {sorted(set(leaked))}"
    )


@pytest.mark.parametrize(
    "expected_module",
    [
        "nova.systems.skin.models",
    ],
)
def test_commands_imports_each_allowed_models_module(expected_module: str) -> None:
    """Positive lock — confirm each cross-system ``.models`` import we
    documented is actually present. Catches a silent regression where
    the parser's Command import is dropped (which would leave the
    parser's return type unsatisfied).
    """
    tree = ast.parse(_read_module_source())
    nova_modules: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("nova.")
        ):
            nova_modules.add(node.module)
    assert expected_module in nova_modules, (
        f"nova.systems.skin.commands must import {expected_module} for Command construction"
    )
