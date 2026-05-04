"""AST guards for :mod:`nova.systems.nerve.briefing` (Story 3.2).

The briefing-assembly module consumes :class:`~nova.ports.brain.BrainPort`
and reads :class:`~nova.core.config.NovaConfig` — nothing else. It must
NOT reach into sibling systems' internals, must NOT import adapters,
and must NOT touch ``sqlite3`` (the DB is Brain's concern). These
invariants are mechanically enforced here; the shape of this file
mirrors ``tests/unit/adapters/sqlite/test_brain_adapter_isolation.py``.

Allowed import surface (Story 3.2 AC #24):

* stdlib only for types / annotations (``__future__``)
* ``nova.core.*`` — ``types`` (``BriefingState``), ``config`` (``NovaConfig``)
* ``nova.ports.brain`` — the port the assembler consumes
* ``nova.systems.brain.models`` — cross-system model surface
  (``BriefingAggregate``, ``ModeInfo``, Story 1.9 AC #8)

Forbidden surface:

* ``sqlite3`` (and other third-party infra) at any scope — the DB is
  Brain's concern; nerve is upstream of any adapter.
* ``nova.adapters.*`` — nerve consumes ports, never concrete adapters
  (project-context.md one-way dependency direction).
* Other ``nova.systems.*`` non-``.models`` surfaces — no reaching into
  ritual/voice/skin/eyes/hands internals.
* ``nova.app`` / ``nova.cli`` / ``nova.setup.*`` — nerve must not
  reach upward into composition-root or entry-point layers.
* Dynamic imports of any forbidden prefix via ``__import__`` /
  ``importlib.import_module``.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType

import pytest

import nova.systems.nerve.briefing as nerve_briefing_module

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
        "rich",
        "yaml",
    }
)

FORBIDDEN_NOVA_PREFIXES: tuple[str, ...] = (
    "nova.adapters",
    "nova.app",
    "nova.cli",
    "nova.setup",
    # Disallow all other systems' non-``.models`` surfaces.
    "nova.systems.brain.system",
    "nova.systems.eyes",
    "nova.systems.hands",
    "nova.systems.ritual",
    "nova.systems.shield",
    "nova.systems.skin",
    "nova.systems.voice",
)

ALLOWED_SYSTEMS_MODELS: frozenset[str] = frozenset(
    {
        "nova.systems.brain.models",
    }
)


def _read_module_source(module: ModuleType) -> str:
    source_path_str = inspect.getsourcefile(module)
    assert source_path_str is not None
    return Path(source_path_str).read_text(encoding="utf-8")


def _has_forbidden_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


@pytest.mark.parametrize("module", [nerve_briefing_module])
def test_nerve_briefing_does_not_import_forbidden_modules(module: ModuleType) -> None:
    """Reject any ``import`` / ``from ... import`` of a forbidden module."""
    tree = ast.parse(_read_module_source(module))
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
            if node.module.startswith("nova.systems") and node.module not in ALLOWED_SYSTEMS_MODELS:
                leaked.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                is_forbidden_top = top in FORBIDDEN_TOPLEVEL_MODULES
                is_forbidden_prefix = _has_forbidden_prefix(alias.name, FORBIDDEN_NOVA_PREFIXES)
                is_forbidden_systems = (
                    alias.name.startswith("nova.systems")
                    and alias.name not in ALLOWED_SYSTEMS_MODELS
                )
                if is_forbidden_top or is_forbidden_prefix or is_forbidden_systems:
                    leaked.append(alias.name)
    assert not leaked, f"Forbidden imports in {module.__name__}: {sorted(set(leaked))}"


@pytest.mark.parametrize("module", [nerve_briefing_module])
def test_nerve_briefing_does_not_import_sqlite3_at_any_scope(module: ModuleType) -> None:
    """``sqlite3`` must not appear anywhere in briefing assembly.

    Nerve sits upstream of any adapter and speaks to Brain through the
    port Protocol only. Importing ``sqlite3`` would mean reaching past
    the port into the storage layer — a layering violation.
    """
    tree = ast.parse(_read_module_source(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "sqlite3" and not alias.name.startswith("sqlite3."), (
                    f"{module.__name__} must not import sqlite3 (nerve consumes BrainPort)"
                )
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "sqlite3", (
                f"{module.__name__} must not import from sqlite3 (nerve consumes BrainPort)"
            )


@pytest.mark.parametrize("module", [nerve_briefing_module])
def test_nerve_briefing_no_dynamic_forbidden_imports(module: ModuleType) -> None:
    """Reject ``__import__``/``importlib.import_module`` targeting forbidden prefixes."""
    tree = ast.parse(_read_module_source(module))
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
        or (t.startswith("nova.systems") and t not in ALLOWED_SYSTEMS_MODELS)
    ]
    assert not leaked, f"Dynamic forbidden imports in {module.__name__}: {sorted(set(leaked))}"


def test_nerve_briefing_imports_are_within_expected_shape() -> None:
    """Positive-list assertion: every nova import resolves to an expected module."""
    tree = ast.parse(_read_module_source(nerve_briefing_module))
    nova_modules: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("nova.")
        ):
            nova_modules.add(node.module)
    expected_modules: frozenset[str] = frozenset(
        {
            "nova.core.config",
            "nova.core.types",
            "nova.ports.brain",
            "nova.systems.brain.models",
        }
    )

    def _is_expected(mod: str) -> bool:
        return any(mod == e or mod.startswith(e + ".") for e in expected_modules)

    unexpected = [m for m in nova_modules if not _is_expected(m)]
    assert not unexpected, f"Unexpected nova imports in nerve briefing: {sorted(unexpected)}"
