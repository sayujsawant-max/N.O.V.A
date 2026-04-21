"""AST guards for :class:`nova.adapters.sqlite.brain.SqliteBrainAdapter` (Story 3.1).

The adapter speaks to the database through :class:`SqliteStorageEngine`
only — it never imports ``sqlite3`` directly, never reaches into other
systems' internals, and never touches ``nova.ports.*`` or
``nova.adapters.*`` (beyond its own sub-package). These invariants are
mechanically enforced by AST inspection; the shape of this test file
mirrors ``tests/unit/setup/test_initial_capture_isolation.py`` and
``tests/unit/ports/test_port_isolation.py``.

Allowed import surface (AC #29):

* stdlib (``json``, ``logging``, ``datetime``)
* ``nova.core.*`` (``events``, ``exceptions``, ``storage.engine``, ``types``)
* ``nova.systems.brain.models`` (``SessionSummary``,
  ``WorkspaceSnapshotInput``, Epic 5 models)
* ``nova.systems.eyes.models`` (``WorkspaceSnapshot``, ``WindowContext``)

Forbidden surface:

* ``sqlite3`` at module OR function scope — all DB I/O goes through the
  engine (cross-cutting-patterns.md #4 error-translation contract).
* ``nova.ports.*`` — adapters implement ports structurally, they never
  import them (Story 1.9 precedent in ``adapters/shield/noop.py``).
* ``nova.adapters.*`` (other than the adapter's own sub-package).
* ``nova.systems.*`` modules other than the two ``.models`` files above.
* ``nova.app`` / ``nova.cli`` / ``nova.setup.*`` — adapter must not
  reach into higher layers.
* Dynamic imports of any forbidden prefix via ``__import__`` /
  ``importlib.import_module``.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType

import pytest

import nova.adapters.sqlite.brain as brain_adapter_module

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
    "nova.ports",
    "nova.app",
    "nova.cli",
    "nova.setup",
    # Disallow all other systems' non-models surfaces.
    "nova.systems.brain.system",
    "nova.systems.eyes.system",
    "nova.systems.hands",
    "nova.systems.nerve",
    "nova.systems.ritual",
    "nova.systems.shield",
    "nova.systems.skin",
    "nova.systems.voice",
)

ALLOWED_SYSTEMS_MODELS: frozenset[str] = frozenset(
    {
        "nova.systems.brain.models",
        "nova.systems.eyes.models",
    }
)


def _read_module_source(module: ModuleType) -> str:
    source_path_str = inspect.getsourcefile(module)
    assert source_path_str is not None
    return Path(source_path_str).read_text(encoding="utf-8")


def _has_forbidden_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


@pytest.mark.parametrize("module", [brain_adapter_module])
def test_brain_adapter_does_not_import_forbidden_modules(module: ModuleType) -> None:
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


@pytest.mark.parametrize("module", [brain_adapter_module])
def test_brain_adapter_does_not_import_sqlite3_at_any_scope(module: ModuleType) -> None:
    """``sqlite3`` must not appear as an import anywhere in the adapter.

    The engine owns the ``sqlite3`` boundary and translates its errors
    to :class:`StorageError` before they cross back. If the adapter
    imports ``sqlite3`` — even inside a function, even for
    ``sqlite3.IntegrityError`` catching — the two-layer error
    translation contract silently weakens.

    Walks every ``ast.Import`` / ``ast.ImportFrom`` node (not just
    module-level) per cross-cutting-patterns.md #2.
    """
    tree = ast.parse(_read_module_source(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "sqlite3" and not alias.name.startswith("sqlite3."), (
                    f"{module.__name__} must not import sqlite3 (engine owns that boundary)"
                )
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "sqlite3", (
                f"{module.__name__} must not import from sqlite3 (engine owns that boundary)"
            )


@pytest.mark.parametrize("module", [brain_adapter_module])
def test_brain_adapter_no_dynamic_forbidden_imports(module: ModuleType) -> None:
    """Reject any ``__import__("...")`` / ``importlib.import_module("...")`` call
    whose target is a forbidden prefix. Prevents circumvention via
    dynamic-import stringly-typed paths.
    """
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


def test_brain_adapter_imports_are_within_expected_shape() -> None:
    """Positive-list assertion: every nova import resolves to an expected module."""
    tree = ast.parse(_read_module_source(brain_adapter_module))
    nova_modules: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("nova.")
        ):
            nova_modules.add(node.module)
    # Exact matches or dotted-prefix descendants are both accepted.
    expected_modules: frozenset[str] = frozenset(
        {
            "nova.core",
            "nova.systems.brain.models",
            "nova.systems.eyes.models",
        }
    )

    def _is_expected(mod: str) -> bool:
        return any(mod == e or mod.startswith(e + ".") for e in expected_modules)

    unexpected = [m for m in nova_modules if not _is_expected(m)]
    assert not unexpected, f"Unexpected nova imports in brain adapter: {sorted(unexpected)}"
