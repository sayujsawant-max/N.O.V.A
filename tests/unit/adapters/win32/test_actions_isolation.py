"""AST guards for :mod:`nova.adapters.win32.actions` (Story 3.6 AC #28).

Win32HandsAdapter is a translator at the OS boundary; it must NOT:

* Import any sibling adapter (``nova.adapters.{rich,sqlite,shield,claude}``)
  per project-context.md:62 (no cross-adapter imports).
* Reach into system internals (``nova.systems.*.system``) — only
  ``.models`` may cross the boundary into the adapter for return-type
  construction.
* Depend on ``nova.app`` / ``nova.cli`` / ``nova.setup``.

Allowed surface (positive locks):

* stdlib (``asyncio``, ``logging``, ``os``, ``subprocess``, ``time``,
  ``pathlib``).
* ``psutil`` — already-running detection.
* ``nova.core.config`` — for ``AppConfig``.
* ``nova.core.types`` — for ``ActionType``.
* ``nova.ports.app_launcher`` — port + reason constants.
* ``nova.systems.hands.models`` — ``ActionResult`` return type
  (adapter→cross-system-model is allowed per project-context.md:62 since
  models are part of the system's published cross-boundary surface).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType

import pytest

import nova.adapters.win32.actions as win32_actions_module

# Sibling adapters — explicitly forbidden by the no-cross-adapter rule.
FORBIDDEN_NOVA_PREFIXES: tuple[str, ...] = (
    "nova.adapters.rich",
    "nova.adapters.sqlite",
    "nova.adapters.shield",
    "nova.adapters.claude",
    "nova.app",
    "nova.cli",
    "nova.setup",
)

# Reaching into sibling-system internals — only .models is allowed.
FORBIDDEN_SYSTEM_INTERNAL_SUFFIXES: tuple[str, ...] = (
    ".system",
    ".commands",
    ".briefing",
)


def _read_module_source(module: ModuleType) -> str:
    source_path_str = inspect.getsourcefile(module)
    assert source_path_str is not None
    return Path(source_path_str).read_text(encoding="utf-8")


def _has_forbidden_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


@pytest.mark.parametrize("module", [win32_actions_module])
def test_actions_does_not_import_sibling_adapters(module: ModuleType) -> None:
    """No cross-adapter imports (project-context.md:62)."""
    tree = ast.parse(_read_module_source(module))
    leaked: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            if _has_forbidden_prefix(node.module, FORBIDDEN_NOVA_PREFIXES):
                leaked.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _has_forbidden_prefix(alias.name, FORBIDDEN_NOVA_PREFIXES):
                    leaked.append(alias.name)
    assert not leaked, f"Forbidden cross-adapter / app / cli imports: {sorted(set(leaked))}"


@pytest.mark.parametrize("module", [win32_actions_module])
def test_actions_does_not_reach_into_system_internals(module: ModuleType) -> None:
    """Only ``.models`` may cross the system→adapter boundary."""
    tree = ast.parse(_read_module_source(module))
    leaked: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module is None or not node.module.startswith("nova.systems."):
                continue
            for suffix in FORBIDDEN_SYSTEM_INTERNAL_SUFFIXES:
                if node.module.endswith(suffix) or suffix + "." in node.module:
                    leaked.append(node.module)
                    break
    assert not leaked, (
        f"Win32HandsAdapter must consume only .models from sibling systems; "
        f"forbidden imports: {sorted(set(leaked))}"
    )


# Positive presence — drops would silently break the adapter.
_EXPECTED_IMPORTS: tuple[str, ...] = (
    "psutil",
    "subprocess",
    "asyncio",
    "logging",
    "os",
    "time",
    "nova.core.config",
    "nova.core.types",
    "nova.ports.app_launcher",
    "nova.systems.hands.models",
)


@pytest.mark.parametrize("expected_module", _EXPECTED_IMPORTS)
def test_actions_imports_each_expected_module(expected_module: str) -> None:
    """Positive lock — confirm each expected import is present."""
    tree = ast.parse(_read_module_source(win32_actions_module))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
    assert expected_module in found, (
        f"{win32_actions_module.__name__} must import {expected_module!r}; "
        f"present imports: {sorted(found)}"
    )
