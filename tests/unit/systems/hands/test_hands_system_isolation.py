"""AST guards for :mod:`nova.systems.hands.system` (Story 3.6 AC #27).

HandsSystem is the orchestration layer; it must NOT touch:

* Adapter modules (``nova.adapters.*``) — wiring is the composition root's job.
* Sibling-system internals (``nova.systems.{X}.system``) — only ``.models``
  cross system boundaries (Story 1.9 AC #8).
* OS-level subprocess / process iteration (``subprocess``, ``os.startfile``,
  ``psutil``) — that's the launcher adapter's job.
* Rendering libraries (``rich``) — Hands consumes :class:`SkinPort`.
* Storage (``sqlite3``, ``yaml``) — Brain / Config own those.

Allowed surface (positive locks):

* ``nova.core.{audit,config,events,types}`` — typed contracts.
* ``nova.ports.{app_launcher,skin}`` — the two ports HandsSystem consumes.
* ``nova.systems.hands.models`` — the dataclasses HandsSystem returns.

Mirrors :mod:`tests.unit.systems.nerve.test_nerve_system_isolation` shape.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType

import pytest

import nova.systems.hands.system as hands_system_module

FORBIDDEN_TOPLEVEL_MODULES: frozenset[str] = frozenset(
    {
        "sqlite3",
        "anthropic",
        "subprocess",  # launcher adapter's concern
        "os",  # launcher adapter's concern (os.startfile)
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
    "nova.systems.brain.system",
    "nova.systems.eyes",
    "nova.systems.nerve",
    "nova.systems.ritual.system",
    "nova.systems.shield",
    "nova.systems.skin.commands",
    "nova.systems.voice",
)

ALLOWED_NOVA_SYSTEMS: frozenset[str] = frozenset(
    {
        "nova.systems.hands.models",
    }
)


def _read_module_source(module: ModuleType) -> str:
    source_path_str = inspect.getsourcefile(module)
    assert source_path_str is not None
    return Path(source_path_str).read_text(encoding="utf-8")


def _has_forbidden_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


@pytest.mark.parametrize("module", [hands_system_module])
def test_hands_system_does_not_import_forbidden_modules(module: ModuleType) -> None:
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


@pytest.mark.parametrize("module", [hands_system_module])
def test_hands_system_does_not_import_subprocess_at_any_scope(module: ModuleType) -> None:
    """``subprocess`` is the launcher adapter's concern — never appears in HandsSystem."""
    tree = ast.parse(_read_module_source(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "subprocess" and not alias.name.startswith("subprocess."), (
                    f"{module.__name__} must not import subprocess (use AppLauncherPort)"
                )
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "subprocess" and not (
                node.module is not None and node.module.startswith("subprocess.")
            ), f"{module.__name__} must not import from subprocess (use AppLauncherPort)"


@pytest.mark.parametrize("module", [hands_system_module])
def test_hands_system_does_not_import_rich_at_any_scope(module: ModuleType) -> None:
    """``rich`` must not appear — HandsSystem consumes SkinPort."""
    tree = ast.parse(_read_module_source(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "rich" and not alias.name.startswith("rich."), (
                    f"{module.__name__} must not import rich (consume SkinPort)"
                )
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or not node.module.startswith("rich"), (
                f"{module.__name__} must not import from rich (consume SkinPort)"
            )


@pytest.mark.parametrize("module", [hands_system_module])
def test_hands_system_no_dynamic_forbidden_imports(module: ModuleType) -> None:
    """Reject ``__import__`` / ``importlib.import_module`` of forbidden prefixes."""
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


# Positive-presence parametrize — drops would silently break HandsSystem.
_EXPECTED_NOVA_IMPORTS: tuple[str, ...] = (
    "nova.core.audit",
    "nova.core.config",
    "nova.core.events",
    "nova.core.types",
    "nova.ports.app_launcher",
    "nova.ports.skin",
    "nova.systems.hands.models",
)


@pytest.mark.parametrize("expected_module", _EXPECTED_NOVA_IMPORTS)
def test_hands_system_imports_each_expected_module(expected_module: str) -> None:
    """Positive lock — confirm each expected nova import is present."""
    tree = ast.parse(_read_module_source(hands_system_module))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
    assert expected_module in found, (
        f"{hands_system_module.__name__} must import {expected_module!r}; "
        f"present nova imports: {sorted(m for m in found if m.startswith('nova.'))}"
    )
