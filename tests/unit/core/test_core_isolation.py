"""Adapter-isolation guardrail (Story 1.2 AC #4).

AST-level import inspection: `core/exceptions.py` and `core/types.py` may
import only from a small stdlib allowlist. Comments and docstrings that
mention adapter names (e.g. "sqlite3.OperationalError -> StorageError")
are allowed; only actual imports and dynamic-import call sites are
inspected.

Closes review findings P1, P2, P3, P9, P14:
- Relative imports (``from .. import X``) are forbidden in core (P1).
- Dynamic imports via ``__import__()`` and ``importlib.import_module()``
  are scanned by AST and forbidden against the same denylist (P2).
- ``from enum import <symbol>`` is restricted to a public-symbol whitelist;
  leading-underscore symbols are rejected (P3).
- Helper renamed ``_toplevel_imports`` -> ``_all_imports`` to reflect that
  ``ast.walk`` descends into functions, classes, and ``if``-blocks (P9).
- Top-level allowlist shrunk to ``{"enum", "__future__"}`` to match actual
  usage (P14). Re-add ``typing`` only when a concrete need surfaces AND
  the forbidden-pattern guard is widened to descend ``if TYPE_CHECKING:``
  blocks.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType

import pytest

import nova.core.exceptions as exceptions_module
import nova.core.types as types_module

ALLOWED_TOPLEVEL_MODULES: frozenset[str] = frozenset({"enum", "__future__"})

ALLOWED_ENUM_SYMBOLS: frozenset[str] = frozenset({"StrEnum", "Enum", "IntEnum", "auto", "unique"})

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

# Sentinel used in `_all_imports` to flag relative imports — they have no
# legitimate place in `core/exceptions.py` or `core/types.py`.
RELATIVE_IMPORT_MARKER = "<relative>"


def _read_module_source(module: ModuleType) -> str:
    source_path_str = inspect.getsourcefile(module)
    assert source_path_str is not None
    return Path(source_path_str).read_text(encoding="utf-8")


def _all_imports(tree: ast.AST) -> list[tuple[str, str | None]]:
    """Return (top_module, symbol_name) pairs for every import in the tree.

    `symbol_name` is the imported name for `from X import Y` style; `None`
    for plain `import X` style. Relative imports surface with `top_module`
    set to ``RELATIVE_IMPORT_MARKER`` so the caller can fail them.

    Walks the entire AST (not just module-level), so imports nested inside
    functions, classes, or `if`-blocks are also caught.
    """
    pairs: list[tuple[str, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                pairs.append((alias.name.split(".")[0], None))
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or node.module is None:
                pairs.append((RELATIVE_IMPORT_MARKER, None))
                continue
            top = node.module.split(".")[0]
            for alias in node.names:
                pairs.append((top, alias.name))
    return pairs


def _dynamic_import_targets(tree: ast.AST) -> list[str]:
    """Return string-literal targets of `__import__()` and `importlib.import_module()`."""
    targets: list[str] = []
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
            targets.append(node.args[0].value.split(".")[0])
    return targets


@pytest.mark.parametrize("module", [exceptions_module, types_module])
def test_no_relative_imports(module: ModuleType) -> None:
    """Relative imports are forbidden in core (P1)."""
    tree = ast.parse(_read_module_source(module))
    relatives = [m for m, _ in _all_imports(tree) if m == RELATIVE_IMPORT_MARKER]
    assert not relatives, (
        f"Relative imports are forbidden in {module.__name__}; found {len(relatives)}."
    )


@pytest.mark.parametrize("module", [exceptions_module, types_module])
def test_no_forbidden_imports(module: ModuleType) -> None:
    tree = ast.parse(_read_module_source(module))
    used = {m for m, _ in _all_imports(tree)}
    leaked = used & FORBIDDEN_TOPLEVEL_MODULES
    assert not leaked, f"Adapter imports leaked into core: {sorted(leaked)}"


@pytest.mark.parametrize("module", [exceptions_module, types_module])
def test_imports_within_allowlist(module: ModuleType) -> None:
    tree = ast.parse(_read_module_source(module))
    used = {m for m, _ in _all_imports(tree) if m != RELATIVE_IMPORT_MARKER}
    out_of_allowlist = used - ALLOWED_TOPLEVEL_MODULES
    assert not out_of_allowlist, (
        f"Imports outside the core allowlist: {sorted(out_of_allowlist)}. "
        f"Allowlist is {sorted(ALLOWED_TOPLEVEL_MODULES)}."
    )


@pytest.mark.parametrize("module", [exceptions_module, types_module])
def test_enum_imports_use_public_symbols_only(module: ModuleType) -> None:
    """`from enum import <symbol>` must use public, allowlisted names (P3)."""
    tree = ast.parse(_read_module_source(module))
    enum_symbols = [sym for top, sym in _all_imports(tree) if top == "enum" and sym is not None]
    private_or_unknown = [
        s for s in enum_symbols if s.startswith("_") or s not in ALLOWED_ENUM_SYMBOLS
    ]
    assert not private_or_unknown, (
        f"`from enum import` restricted to public symbols "
        f"{sorted(ALLOWED_ENUM_SYMBOLS)}; found {sorted(private_or_unknown)} "
        f"in {module.__name__}."
    )


@pytest.mark.parametrize("module", [exceptions_module, types_module])
def test_no_dynamic_imports_of_forbidden_modules(module: ModuleType) -> None:
    """`importlib.import_module(...)` and `__import__(...)` cannot reach adapters (P2)."""
    tree = ast.parse(_read_module_source(module))
    targets = _dynamic_import_targets(tree)
    leaked = set(targets) & FORBIDDEN_TOPLEVEL_MODULES
    assert not leaked, f"Dynamic adapter imports detected in {module.__name__}: {sorted(leaked)}."
