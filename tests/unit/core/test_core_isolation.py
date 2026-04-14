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

import nova.core.events as events_module
import nova.core.exceptions as exceptions_module
import nova.core.storage.engine as storage_engine_module
import nova.core.types as types_module

ALLOWED_TOPLEVEL_MODULES: frozenset[str] = frozenset({"enum", "__future__"})

ALLOWED_ENUM_SYMBOLS: frozenset[str] = frozenset({"StrEnum", "Enum", "IntEnum", "auto", "unique"})

# `core/events.py` (Story 1.3) legitimately imports from the stdlib modules
# below and from the single first-party module `nova.core.types`. The
# FORBIDDEN_TOPLEVEL_MODULES check still blocks adapter modules; an additional
# sub-package check (`test_events_does_not_import_nova_adapters_or_systems`)
# closes the `nova.*` first-segment hole.
EVENTS_ALLOWED_TOPLEVEL_MODULES: frozenset[str] = frozenset(
    {
        "__future__",
        "asyncio",
        "collections",
        "dataclasses",
        "datetime",
        "enum",
        "logging",
        "nova",
        "typing",
    }
)

# Dotted prefixes forbidden inside `core/events.py`. The `nova.*` first
# segment is too coarse for the standard forbidden-set test (it would catch
# `nova.core.types`), so this narrower check fires inside its own test.
FORBIDDEN_NOVA_PREFIXES: tuple[str, ...] = (
    "nova.adapters",
    "nova.systems",
    "nova.ports",
)

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

# `core/storage/engine.py` (Story 1.4) is the sole core module that may
# import `sqlite3` — it IS the sqlite3 boundary. Every other adapter
# module remains forbidden; the narrower check closes the `nova.*`
# first-segment hole via `test_storage_engine_does_not_import_nova_adapters_or_systems`.
STORAGE_ENGINE_FORBIDDEN_TOPLEVEL_MODULES: frozenset[str] = FORBIDDEN_TOPLEVEL_MODULES - {"sqlite3"}

STORAGE_ENGINE_ALLOWED_TOPLEVEL_MODULES: frozenset[str] = frozenset(
    {
        "__future__",
        "asyncio",
        "collections",
        "concurrent",
        "contextlib",
        "logging",
        "nova",
        "pathlib",
        "sqlite3",
        "types",
        "typing",
    }
)

# Sentinel used in `_all_imports` to flag relative imports — they have no
# legitimate place in `core/exceptions.py` or `core/types.py`.
RELATIVE_IMPORT_MARKER = "<relative>"


def _read_module_source(module: ModuleType) -> str:
    source_path_str = inspect.getsourcefile(module)
    assert source_path_str is not None
    return Path(source_path_str).read_text(encoding="utf-8")


def _has_forbidden_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    """Exact prefix match with `.` boundary — avoids `nova.adapters_helpers` false positives.

    ``name.startswith("nova.adapters")`` would match ``"nova.adapters_helpers"``
    (no word boundary). Require either exact equality or the prefix followed
    by a dotted sub-module segment.
    """
    return any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)


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


def _dynamic_import_full_targets(tree: ast.AST) -> list[str]:
    """Return FULL string-literal targets of `__import__()` / `importlib.import_module()`.

    Unlike `_dynamic_import_targets` (which collapses to the first dotted
    segment), this helper preserves the entire path — necessary to catch
    `importlib.import_module("nova.adapters.sqlite")` via the
    `nova.adapters`-prefix check.
    """
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
            targets.append(node.args[0].value)
    return targets


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


@pytest.mark.parametrize(
    "module", [exceptions_module, types_module, events_module, storage_engine_module]
)
def test_no_relative_imports(module: ModuleType) -> None:
    """Relative imports are forbidden in core (P1)."""
    tree = ast.parse(_read_module_source(module))
    relatives = [m for m, _ in _all_imports(tree) if m == RELATIVE_IMPORT_MARKER]
    assert not relatives, (
        f"Relative imports are forbidden in {module.__name__}; found {len(relatives)}."
    )


@pytest.mark.parametrize("module", [exceptions_module, types_module, events_module])
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


@pytest.mark.parametrize(
    "module", [exceptions_module, types_module, events_module, storage_engine_module]
)
def test_no_dynamic_imports_of_forbidden_modules(module: ModuleType) -> None:
    """`importlib.import_module(...)` and `__import__(...)` cannot reach adapters (P2).

    For ``core/storage/engine.py`` this still blocks every adapter except
    ``sqlite3``; the dedicated
    ``test_storage_engine_forbidden_imports_minus_sqlite3`` guards the
    narrower dynamic-import-minus-sqlite3 check.
    """
    tree = ast.parse(_read_module_source(module))
    targets = _dynamic_import_targets(tree)
    forbidden = (
        STORAGE_ENGINE_FORBIDDEN_TOPLEVEL_MODULES
        if module is storage_engine_module
        else FORBIDDEN_TOPLEVEL_MODULES
    )
    leaked = set(targets) & forbidden
    assert not leaked, f"Dynamic adapter imports detected in {module.__name__}: {sorted(leaked)}."


@pytest.mark.parametrize("module", [events_module])
def test_events_imports_within_allowlist(module: ModuleType) -> None:
    """`core/events.py` (Story 1.3) has its own wider stdlib allowlist.

    `core/exceptions.py` and `core/types.py` stay on the tighter
    `{"enum", "__future__"}` allowlist. `events.py` needs `dataclasses`,
    `datetime`, `logging`, `collections`, and the first-party
    `nova.core.types` — all captured in EVENTS_ALLOWED_TOPLEVEL_MODULES.
    """
    tree = ast.parse(_read_module_source(module))
    used = {m for m, _ in _all_imports(tree) if m != RELATIVE_IMPORT_MARKER}
    out_of_allowlist = used - EVENTS_ALLOWED_TOPLEVEL_MODULES
    assert not out_of_allowlist, (
        f"Imports outside the events allowlist: {sorted(out_of_allowlist)}. "
        f"Allowlist is {sorted(EVENTS_ALLOWED_TOPLEVEL_MODULES)}."
    )


@pytest.mark.parametrize("module", [events_module])
def test_events_does_not_import_nova_adapters_or_systems(module: ModuleType) -> None:
    """`core/events.py` must not reach into `nova.adapters.*`, `nova.systems.*`, or `nova.ports.*`.

    The standard forbidden-set test splits on `.` and takes the first
    segment, so `"nova"` is a legitimate first segment and cannot express
    "forbid `nova.adapters.*`". This narrower check walks the full
    dotted path on every import form:

    - ``import nova.adapters.sqlite`` / ``import nova.adapters.sqlite as x``
      → caught via ``alias.name``.
    - ``from nova.adapters.sqlite import X`` → caught via ``node.module``.
    - ``from nova import adapters`` → caught via the ``(node.module,
      alias.name)`` combination: when ``node.module == "nova"`` and an
      imported symbol is a forbidden sub-package (``adapters`` /
      ``systems`` / ``ports``), the composed ``nova.<symbol>`` matches
      ``FORBIDDEN_NOVA_PREFIXES``.

    Prefix matching uses `_has_forbidden_prefix` (exact equality or
    prefix + ``.`` boundary), so legitimate future names like
    ``nova.adapters_helpers`` would NOT false-positive.
    """
    tree = ast.parse(_read_module_source(module))
    leaked: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            if _has_forbidden_prefix(node.module, FORBIDDEN_NOVA_PREFIXES):
                leaked.append(node.module)
                continue
            # `from nova import adapters` — node.module is "nova",
            # each alias.name is the sub-package being imported.
            for alias in node.names:
                composed = f"{node.module}.{alias.name}"
                if _has_forbidden_prefix(composed, FORBIDDEN_NOVA_PREFIXES):
                    leaked.append(composed)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _has_forbidden_prefix(alias.name, FORBIDDEN_NOVA_PREFIXES):
                    leaked.append(alias.name)
    assert not leaked, (
        f"Forbidden nova sub-package imports in {module.__name__}: {sorted(set(leaked))}."
    )


@pytest.mark.parametrize("module", [events_module])
def test_events_does_not_dynamically_import_nova_adapters_or_systems(
    module: ModuleType,
) -> None:
    """`importlib.import_module("nova.adapters.*")` must be blocked too.

    The shared `_dynamic_import_targets` helper collapses to the first
    dotted segment, which would surface `"nova.adapters.sqlite"` as just
    `"nova"` (not in the forbidden set). Use `_dynamic_import_full_targets`
    instead so the full path is available for `_has_forbidden_prefix`.
    """
    tree = ast.parse(_read_module_source(module))
    leaked = [
        t
        for t in _dynamic_import_full_targets(tree)
        if _has_forbidden_prefix(t, FORBIDDEN_NOVA_PREFIXES)
    ]
    assert not leaked, (
        f"Dynamic nova sub-package imports in {module.__name__}: {sorted(set(leaked))}."
    )


# --- Storage engine (Story 1.4) isolation guards -----------------------------


@pytest.mark.parametrize("module", [storage_engine_module])
def test_storage_engine_forbidden_imports_minus_sqlite3(module: ModuleType) -> None:
    """`core/storage/engine.py` may import `sqlite3` — every other adapter remains forbidden."""
    tree = ast.parse(_read_module_source(module))
    used = {m for m, _ in _all_imports(tree)}
    leaked = used & STORAGE_ENGINE_FORBIDDEN_TOPLEVEL_MODULES
    assert not leaked, f"Forbidden adapter imports leaked into {module.__name__}: {sorted(leaked)}."


@pytest.mark.parametrize("module", [storage_engine_module])
def test_storage_engine_imports_within_allowlist(module: ModuleType) -> None:
    """`core/storage/engine.py` has its own wider stdlib allowlist (includes `sqlite3`)."""
    tree = ast.parse(_read_module_source(module))
    used = {m for m, _ in _all_imports(tree) if m != RELATIVE_IMPORT_MARKER}
    out_of_allowlist = used - STORAGE_ENGINE_ALLOWED_TOPLEVEL_MODULES
    assert not out_of_allowlist, (
        f"Imports outside the storage-engine allowlist: {sorted(out_of_allowlist)}. "
        f"Allowlist is {sorted(STORAGE_ENGINE_ALLOWED_TOPLEVEL_MODULES)}."
    )


@pytest.mark.parametrize("module", [storage_engine_module])
def test_storage_engine_does_not_import_nova_adapters_or_systems(module: ModuleType) -> None:
    """Storage engine must not reach into nova adapters/systems/ports sub-packages.

    Same dotted-prefix check as the events-specific guard — consumes
    this module from ``nova.adapters.sqlite.brain`` (Story 3.x), never
    the other direction.
    """
    tree = ast.parse(_read_module_source(module))
    leaked: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            if _has_forbidden_prefix(node.module, FORBIDDEN_NOVA_PREFIXES):
                leaked.append(node.module)
                continue
            for alias in node.names:
                composed = f"{node.module}.{alias.name}"
                if _has_forbidden_prefix(composed, FORBIDDEN_NOVA_PREFIXES):
                    leaked.append(composed)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _has_forbidden_prefix(alias.name, FORBIDDEN_NOVA_PREFIXES):
                    leaked.append(alias.name)
    assert not leaked, (
        f"Forbidden nova sub-package imports in {module.__name__}: {sorted(set(leaked))}."
    )


@pytest.mark.parametrize("module", [storage_engine_module])
def test_storage_engine_does_not_dynamically_import_nova_adapters_or_systems(
    module: ModuleType,
) -> None:
    """`importlib.import_module("nova.adapters.*")` must also be blocked from storage engine."""
    tree = ast.parse(_read_module_source(module))
    leaked = [
        t
        for t in _dynamic_import_full_targets(tree)
        if _has_forbidden_prefix(t, FORBIDDEN_NOVA_PREFIXES)
    ]
    assert not leaked, (
        f"Dynamic nova sub-package imports in {module.__name__}: {sorted(set(leaked))}."
    )
