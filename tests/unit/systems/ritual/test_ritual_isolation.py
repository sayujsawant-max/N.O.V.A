"""AST guards for :mod:`nova.systems.ritual.system` and :mod:`...models` (Story 3.3).

The Ritual system consumes domain types and produces a render-ready
view model — it must NOT reach into sibling systems' internals, must NOT
import adapters, and must NOT touch Rich (Rich lives only inside
``adapters/rich/skin.py``). These invariants are mechanically enforced
here; the shape mirrors :mod:`tests.unit.systems.nerve.test_briefing_isolation`
(Story 3.2 precedent).

Allowed import surface (Story 3.3 AC #28):

* stdlib (``__future__``, dataclasses, typing).
* ``nova.core.types`` — ``BriefingState``, ``CapabilityTier``.
* ``nova.core.formatting`` — :func:`format_duration_seconds`.
* ``nova.ports.ritual`` — the port the system implements (only the
  models module currently uses this; system.py does not need a nominal
  Protocol import per structural subtyping).
* ``nova.systems.brain.models`` — ``BriefingAggregate``, ``ModeInfo``,
  ``SessionSummary`` (Story 1.9 AC #8 portable cross-system surface).
* ``nova.systems.eyes.models`` — ``WorkspaceSnapshot``, ``WindowContext``
  (Story 1.9 AC #8 portable cross-system surface; Ritual derives apps
  from ``WorkspaceSnapshot.windows``).
* ``nova.systems.ritual.models`` — same-package internal.

Forbidden surface:

* ``nova.adapters.*`` — systems consume ports, never adapters.
* ``nova.systems.{eyes,hands,nerve,shield,skin,voice}.<non-models>`` —
  no reaching into other systems' internals.
* ``nova.app`` / ``nova.cli`` / ``nova.setup.*`` — no upward reach to
  composition / entry-point layers.
* Third-party I/O modules: ``sqlite3``, ``anthropic``, ``pywin32``,
  ``pywintypes``, ``psutil``, ``win32*``, ``rich`` (Ritual is rendering-
  agnostic), ``yaml``.
* Dynamic imports of any forbidden prefix.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType

import pytest

import nova.systems.ritual.models as ritual_models_module
import nova.systems.ritual.system as ritual_system_module

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
    "nova.systems.brain.system",
    "nova.systems.eyes.system",
    "nova.systems.hands",
    "nova.systems.nerve",
    "nova.systems.shield",
    "nova.systems.skin",
    "nova.systems.voice",
)

ALLOWED_SYSTEMS_MODELS: frozenset[str] = frozenset(
    {
        "nova.systems.brain.models",
        "nova.systems.eyes.models",
        "nova.systems.ritual.models",
    }
)

_RITUAL_MODULES: tuple[ModuleType, ...] = (ritual_system_module, ritual_models_module)


def _read_module_source(module: ModuleType) -> str:
    source_path_str = inspect.getsourcefile(module)
    assert source_path_str is not None
    return Path(source_path_str).read_text(encoding="utf-8")


def _has_forbidden_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


@pytest.mark.parametrize("module", _RITUAL_MODULES)
def test_ritual_module_does_not_import_forbidden_modules(module: ModuleType) -> None:
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


@pytest.mark.parametrize("module", _RITUAL_MODULES)
def test_ritual_module_does_not_import_sqlite3_at_any_scope(module: ModuleType) -> None:
    """``sqlite3`` must not appear anywhere in Ritual.

    Ritual sits upstream of any adapter; it speaks to Brain through
    domain models only. Importing ``sqlite3`` would mean reaching past
    the storage layer.
    """
    tree = ast.parse(_read_module_source(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "sqlite3" and not alias.name.startswith("sqlite3."), (
                    f"{module.__name__} must not import sqlite3 (Ritual is system-layer)"
                )
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "sqlite3", (
                f"{module.__name__} must not import from sqlite3 (Ritual is system-layer)"
            )


@pytest.mark.parametrize("module", _RITUAL_MODULES)
def test_ritual_module_no_dynamic_forbidden_imports(module: ModuleType) -> None:
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
        or (t.startswith("nova.systems") and t not in ALLOWED_SYSTEMS_MODELS)
    ]
    assert not leaked, f"Dynamic forbidden imports in {module.__name__}: {sorted(set(leaked))}"


def test_ritual_system_imports_are_within_expected_shape() -> None:
    """Positive-list assertion: every ``nova.*`` import resolves to an expected module.

    Targets ``ritual.system`` specifically; ``ritual.models`` has its
    own minimal allowlist (only ``nova.core.types`` and
    ``nova.systems.brain.models`` are expected).
    """
    tree = ast.parse(_read_module_source(ritual_system_module))
    nova_modules: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("nova.")
        ):
            nova_modules.add(node.module)

    expected: frozenset[str] = frozenset(
        {
            "nova.core.formatting",
            "nova.core.types",
            "nova.systems.brain.models",
            "nova.systems.eyes.models",
            "nova.systems.ritual.models",
        }
    )

    def _is_expected(mod: str) -> bool:
        return any(mod == e or mod.startswith(e + ".") for e in expected)

    unexpected = [m for m in nova_modules if not _is_expected(m)]
    assert not unexpected, f"Unexpected nova imports in ritual.system: {sorted(unexpected)}"


def test_ritual_models_imports_are_within_expected_shape() -> None:
    """Positive-list — ``ritual.models`` uses only ``core.types`` + ``brain.models``."""
    tree = ast.parse(_read_module_source(ritual_models_module))
    nova_modules: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("nova.")
        ):
            nova_modules.add(node.module)

    expected: frozenset[str] = frozenset(
        {
            "nova.core.types",
            "nova.systems.brain.models",
        }
    )
    unexpected = [m for m in nova_modules if m not in expected]
    assert not unexpected, f"Unexpected nova imports in ritual.models: {sorted(unexpected)}"
