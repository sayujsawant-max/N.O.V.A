"""AST guards for :mod:`nova.systems.nerve.system` (Story 3.5 AC #36).

The Nerve orchestrator must consume ports + core only; it must NOT
reach into sibling systems' internals (``ritual.system`` /
``skin.commands`` / ``brain.system`` / etc.), must NOT import adapters,
must NOT touch ``rich`` / ``sqlite3`` / Win32 / Anthropic at any scope,
and must NOT reach upward into ``nova.app`` / ``nova.cli`` /
``nova.setup``.

Allowed import surface (Story 3.5 AC #36):

* stdlib (``__future__``, ``asyncio``, ``contextlib``, ``logging``,
  ``signal``, ``sys``, ``collections.abc``, ``datetime``)
* ``nova.core.*`` — ``config`` (``NovaConfig`` / ``UserSettings``),
  ``events`` (``EventBus``, ``SessionStarted``, ``SessionEnded``),
  ``tiers`` (``TierManager``), ``types`` (``BriefingState``,
  ``CapabilityTier``)
* ``nova.ports.{brain,ritual,skin}`` — ports the orchestrator consumes
* ``nova.systems.brain.models`` — cross-system model surface
  (``SessionSummary``, Story 1.9 AC #8)
* ``nova.systems.nerve.briefing`` — Nerve-internal briefing assembly
* ``nova.systems.nerve.models`` — Nerve-internal ``CommandOutcome``
* ``nova.systems.skin.models`` — cross-system ``Command`` / ``CommandVerb``

Mirrors the shape of ``test_briefing_isolation.py`` so a future
maintainer recognizes the pattern instantly.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType

import pytest

import nova.systems.nerve.system as nerve_system_module

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
    # Disallow sibling-system non-``.models`` surfaces. Skin's parser
    # (``nova.systems.skin.commands``) is intentionally NOT consumed here
    # — Skin's adapter (``RichSkinAdapter.parse_command``) wraps the
    # parser; Nerve only sees the ``Command`` it produces.
    "nova.systems.brain.system",
    "nova.systems.eyes",
    "nova.systems.hands",
    "nova.systems.ritual.system",
    "nova.systems.shield",
    "nova.systems.skin.commands",
    "nova.systems.voice",
)

ALLOWED_NOVA_SYSTEMS: frozenset[str] = frozenset(
    {
        # Cross-system models (Story 1.9 AC #8).
        "nova.systems.brain.models",
        "nova.systems.skin.models",
        # Nerve-internal modules (sibling files).
        "nova.systems.nerve.briefing",
        "nova.systems.nerve.models",
    }
)


def _read_module_source(module: ModuleType) -> str:
    source_path_str = inspect.getsourcefile(module)
    assert source_path_str is not None
    return Path(source_path_str).read_text(encoding="utf-8")


def _has_forbidden_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


@pytest.mark.parametrize("module", [nerve_system_module])
def test_nerve_system_does_not_import_forbidden_modules(module: ModuleType) -> None:
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
            if node.module.startswith("nova.systems") and node.module not in ALLOWED_NOVA_SYSTEMS:
                leaked.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                is_forbidden_top = top in FORBIDDEN_TOPLEVEL_MODULES
                is_forbidden_prefix = _has_forbidden_prefix(alias.name, FORBIDDEN_NOVA_PREFIXES)
                is_forbidden_systems = (
                    alias.name.startswith("nova.systems") and alias.name not in ALLOWED_NOVA_SYSTEMS
                )
                if is_forbidden_top or is_forbidden_prefix or is_forbidden_systems:
                    leaked.append(alias.name)
    assert not leaked, f"Forbidden imports in {module.__name__}: {sorted(set(leaked))}"


@pytest.mark.parametrize("module", [nerve_system_module])
def test_nerve_system_does_not_import_rich_at_any_scope(module: ModuleType) -> None:
    """``rich`` must not appear anywhere — Nerve is rendering-agnostic.

    Rich-specific types stay inside ``RichSkinAdapter``; Nerve speaks
    to Skin through the port Protocol only. Importing ``rich`` here
    would mean Nerve was making rendering decisions, which Skin owns.
    """
    tree = ast.parse(_read_module_source(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "rich" and not alias.name.startswith("rich."), (
                    f"{module.__name__} must not import rich (Nerve consumes SkinPort)"
                )
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or not node.module.startswith("rich"), (
                f"{module.__name__} must not import from rich (Nerve consumes SkinPort)"
            )


@pytest.mark.parametrize("module", [nerve_system_module])
def test_nerve_system_does_not_import_sqlite3_at_any_scope(module: ModuleType) -> None:
    """``sqlite3`` must not appear anywhere — DB is Brain's concern."""
    tree = ast.parse(_read_module_source(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "sqlite3" and not alias.name.startswith("sqlite3."), (
                    f"{module.__name__} must not import sqlite3 (Nerve consumes BrainPort)"
                )
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "sqlite3", (
                f"{module.__name__} must not import from sqlite3 (Nerve consumes BrainPort)"
            )


@pytest.mark.parametrize("module", [nerve_system_module])
def test_nerve_system_no_dynamic_forbidden_imports(module: ModuleType) -> None:
    """Reject ``__import__`` / ``importlib.import_module`` targeting forbidden prefixes."""
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
        or (t.startswith("nova.systems") and t not in ALLOWED_NOVA_SYSTEMS)
    ]
    assert not leaked, f"Dynamic forbidden imports in {module.__name__}: {sorted(set(leaked))}"


# Positive-presence parametrize per AC #36 — drops would silently break
# Nerve so we early-warn here rather than at runtime.
_EXPECTED_NOVA_IMPORTS: tuple[str, ...] = (
    "nova.core.config",
    "nova.core.events",
    "nova.core.tiers",
    "nova.core.types",
    "nova.ports.brain",
    "nova.ports.ritual",
    "nova.ports.skin",
    "nova.systems.brain.models",
    "nova.systems.nerve.briefing",
    "nova.systems.nerve.models",
    "nova.systems.skin.models",
)


@pytest.mark.parametrize("expected_module", _EXPECTED_NOVA_IMPORTS)
def test_nerve_system_imports_each_expected_module(expected_module: str) -> None:
    """Positive lock — confirm each expected nova import is actually present.

    Catches the silent-deletion regression where a refactor removes,
    say, ``from nova.systems.nerve.briefing import …`` and Nerve no
    longer assembles the briefing aggregate. Mirrors the
    Story 3.4 ``test_rich_skin_imports_each_allowed_models_module`` pattern.
    """
    tree = ast.parse(_read_module_source(nerve_system_module))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
    assert expected_module in found, (
        f"{nerve_system_module.__name__} must import {expected_module!r}; "
        f"present nova imports: {sorted(m for m in found if m.startswith('nova.'))}"
    )
