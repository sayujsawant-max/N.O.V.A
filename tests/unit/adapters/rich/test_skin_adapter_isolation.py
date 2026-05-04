"""AST guards for :mod:`nova.adapters.rich.skin` (Story 3.3).

The Rich skin adapter is the ONE place Rich-specific types live. It
must NOT reach into other adapter sub-packages, must NOT reach upward
into composition / entry-point layers, and must NOT touch DB / Win32 /
Anthropic / yaml. These invariants are mechanically enforced here.

Allowed import surface (Story 3.3 AC #29):

* stdlib (``__future__``, ``collections.abc``, typing).
* ``rich.console``, ``rich.panel``, ``rich.text`` — the Rich types this
  adapter uses, pinned to those three modules to prevent accidental
  dependency on ``rich.markdown`` / ``rich.tree`` / etc.
* ``nova.systems.brain.models`` — ``SessionSummary`` (declared in the
  ``SkinPort.render_shutdown_card`` signature).
* ``nova.systems.hands.models`` — ``ActionResult`` (declared in
  ``SkinPort.render_progress``).
* ``nova.systems.ritual.models`` — ``BriefingViewModel`` (input type
  for ``render_briefing_card``).
* ``nova.systems.skin.models`` — ``Command`` (declared in
  ``SkinPort.parse_command``).

Forbidden surface:

* ``nova.app`` / ``nova.cli`` / ``nova.setup.*`` — adapters do not
  reach upward.
* ``nova.adapters.{shield,sqlite}`` — adapter-subpackage isolation.
* ``nova.systems.<system>.<non-models>`` — adapters consume domain
  types only via ``.models``.
* Third-party I/O modules other than Rich: ``sqlite3``, ``anthropic``,
  ``pywin32``, ``pywintypes``, ``psutil``, ``win32*``, ``yaml``.
* Dynamic imports of any forbidden prefix.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import nova.adapters.rich.skin as rich_skin_module

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
    }
)

FORBIDDEN_NOVA_PREFIXES: tuple[str, ...] = (
    "nova.app",
    "nova.cli",
    "nova.setup",
    "nova.adapters.shield",
    "nova.adapters.sqlite",
    "nova.systems.brain.system",
    "nova.systems.eyes",
    "nova.systems.hands.system",
    "nova.systems.nerve",
    "nova.systems.shield",
    "nova.systems.skin.system",
    "nova.systems.ritual.system",
    "nova.systems.voice",
)

ALLOWED_SYSTEMS_MODELS: frozenset[str] = frozenset(
    {
        "nova.systems.brain.models",
        "nova.systems.hands.models",
        "nova.systems.ritual.models",
        "nova.systems.skin.models",
    }
)


def _read_module_source() -> str:
    source_path_str = inspect.getsourcefile(rich_skin_module)
    assert source_path_str is not None
    return Path(source_path_str).read_text(encoding="utf-8")


def _has_forbidden_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def test_rich_skin_does_not_import_forbidden_modules() -> None:
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
    assert not leaked, f"Forbidden imports in nova.adapters.rich.skin: {sorted(set(leaked))}"


def test_rich_skin_does_not_import_sqlite3_at_any_scope() -> None:
    """``sqlite3`` must not appear anywhere — Skin renders, never persists."""
    tree = ast.parse(_read_module_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "sqlite3" and not alias.name.startswith("sqlite3."), (
                    "nova.adapters.rich.skin must not import sqlite3"
                )
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "sqlite3", "nova.adapters.rich.skin must not import from sqlite3"


def test_rich_skin_no_dynamic_forbidden_imports() -> None:
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
        or (t.startswith("nova.systems") and t not in ALLOWED_SYSTEMS_MODELS)
    ]
    assert not leaked, (
        f"Dynamic forbidden imports in nova.adapters.rich.skin: {sorted(set(leaked))}"
    )


def test_rich_skin_only_imports_pinned_rich_submodules() -> None:
    """Rich is allowed, but only the three submodules this story actually uses.

    Forces a deliberate decision when a future story needs
    ``rich.tree`` (Epic 5 transparency) or ``rich.progress`` (Story
    3.6) — the new submodule must be added to this allowlist.
    """
    tree = ast.parse(_read_module_source())
    rich_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module is not None and node.module.startswith("rich"):
                rich_modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("rich"):
                    rich_modules.add(alias.name)

    allowed_rich: frozenset[str] = frozenset(
        {
            "rich.console",
            "rich.panel",
            "rich.text",
        }
    )
    unexpected = rich_modules - allowed_rich
    assert not unexpected, (
        f"nova.adapters.rich.skin imports unexpected Rich submodules: {sorted(unexpected)}. "
        f"If a new Rich submodule is needed, add it to the allowlist deliberately."
    )


@pytest.mark.parametrize(
    "expected_module",
    [
        "nova.systems.brain.models",
        "nova.systems.hands.models",
        "nova.systems.ritual.models",
        "nova.systems.skin.models",
    ],
)
def test_rich_skin_imports_each_allowed_models_module(expected_module: str) -> None:
    """Positive lock — confirm each cross-system .models import we documented is actually present.

    Catches the regression where a SkinPort method's import is silently
    deleted (e.g., a refactor removes the ``Command`` import); the module
    would then no longer satisfy the SkinPort Protocol structurally.
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
        f"nova.adapters.rich.skin must import {expected_module} for SkinPort conformance"
    )
