"""Story 1.10 AC #4 — structural guarantees for the composition root.

AST-walks every ``.py`` under ``src/nova/`` to enforce:

- ``app.py`` and ``cli.py`` are the ONLY non-adapter files that may import
  from ``nova.adapters.*``. Ports, systems, and core remain adapter-free.
- Within ``src/nova/adapters/**``, a sub-package may only import from its
  OWN sub-package (e.g. ``adapters/shield/__init__.py`` may import
  ``nova.adapters.shield.noop`` but not ``nova.adapters.sqlite.*``).
- ``app.py`` does not instantiate adapter classes at module scope — all
  construction lives inside :func:`nova.app.create_app`.
- AC #8: every ``logging.getLogger("...")`` literal across the codebase
  follows the ``nova.{layer}.{module}`` convention.

Helpers are verbatim-duplicated from
``tests/unit/core/test_core_isolation.py`` per the Story 1.9 precedent —
``tests/`` has no ``__init__.py``, so a cross-test-package import would
require inventing a new package structure. ~20 lines of duplication stay
trivially reviewable.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

NOVA_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "nova"

# --- Mirror of tests/unit/core/test_core_isolation.py helpers ---------------

RELATIVE_IMPORT_MARKER = "<relative>"


def _all_imports(tree: ast.AST) -> list[tuple[str, str | None, str]]:
    """Return (top_module, symbol_name, full_dotted_name) triples for every import.

    ``full_dotted_name`` is the raw module path from the import
    statement, preserved for prefix-match checks
    (e.g. ``"nova.adapters.sqlite"``).
    """
    triples: list[tuple[str, str | None, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                triples.append((alias.name.split(".")[0], None, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or node.module is None:
                triples.append((RELATIVE_IMPORT_MARKER, None, ""))
                continue
            top = node.module.split(".")[0]
            for alias in node.names:
                triples.append((top, alias.name, node.module))
    return triples


# --- Helpers specific to this test file -------------------------------------


def _iter_nova_py_files() -> list[Path]:
    """Every ``.py`` file under ``src/nova/``, excluding ``__pycache__``."""
    return sorted(path for path in NOVA_SRC_ROOT.rglob("*.py") if "__pycache__" not in path.parts)


def _relative_to_nova_src(path: Path) -> str:
    """POSIX-style path relative to ``src/nova/`` for stable test IDs."""
    return path.relative_to(NOVA_SRC_ROOT).as_posix()


def _is_under_adapters(path: Path) -> bool:
    parts = path.relative_to(NOVA_SRC_ROOT).parts
    return len(parts) > 0 and parts[0] == "adapters"


def _is_app_or_cli(path: Path) -> bool:
    relative = _relative_to_nova_src(path)
    return relative in {"app.py", "cli.py"}


def _adapter_subpackage(path: Path) -> str | None:
    """Return the adapter sub-package name (e.g. ``"shield"``) or ``None``."""
    parts = path.relative_to(NOVA_SRC_ROOT).parts
    if len(parts) >= 2 and parts[0] == "adapters":
        return parts[1]
    return None


def _parse_file(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _dynamic_import_full_targets(tree: ast.AST) -> list[str]:
    """String literals passed as first arg to ``__import__`` / ``importlib.import_module``.

    Full dotted path preserved so prefix-match checks against
    ``"nova.adapters"`` catch circumvention attempts like
    ``importlib.import_module("nova.adapters.sqlite.brain")``.
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


# --- AC #4 tests ------------------------------------------------------------


def test_only_app_and_cli_import_adapters() -> None:
    """AC #4 — non-adapter modules outside app.py / cli.py must not import ``nova.adapters.*``."""
    violators: list[tuple[str, str]] = []
    for py_file in _iter_nova_py_files():
        if _is_app_or_cli(py_file) or _is_under_adapters(py_file):
            continue
        tree = _parse_file(py_file)
        for _, _, full_name in _all_imports(tree):
            if full_name.startswith("nova.adapters"):
                violators.append((_relative_to_nova_src(py_file), full_name))
    assert not violators, (
        f"Non-adapter modules must not import from nova.adapters.*; violations: {violators}"
    )


def test_ports_never_import_adapters() -> None:
    """Cross-story regression guard — already locked by Story 1.9's test_port_isolation."""
    violators: list[tuple[str, str]] = []
    for py_file in _iter_nova_py_files():
        parts = py_file.relative_to(NOVA_SRC_ROOT).parts
        if not parts or parts[0] != "ports":
            continue
        tree = _parse_file(py_file)
        for _, _, full_name in _all_imports(tree):
            if full_name.startswith("nova.adapters"):
                violators.append((_relative_to_nova_src(py_file), full_name))
    assert not violators, f"Ports must never import adapters; violations: {violators}"


def test_systems_never_import_adapters() -> None:
    """Forward-looking — systems don't exist yet but the guard prevents future leakage."""
    violators: list[tuple[str, str]] = []
    for py_file in _iter_nova_py_files():
        parts = py_file.relative_to(NOVA_SRC_ROOT).parts
        if not parts or parts[0] != "systems":
            continue
        tree = _parse_file(py_file)
        for _, _, full_name in _all_imports(tree):
            if full_name.startswith("nova.adapters"):
                violators.append((_relative_to_nova_src(py_file), full_name))
    assert not violators, f"Systems must never import adapters; violations: {violators}"


def test_adapter_subpackages_stay_intra_package() -> None:
    """AC #4 — ``adapters/{name}/**.py`` may only import from ``nova.adapters.{name}.*``.

    Cross-adapter wiring must go through ports (owned by ``app.py``).
    """
    violators: list[tuple[str, str]] = []
    for py_file in _iter_nova_py_files():
        subpackage = _adapter_subpackage(py_file)
        if subpackage is None:
            continue
        tree = _parse_file(py_file)
        own_prefix = f"nova.adapters.{subpackage}"
        for _, _, full_name in _all_imports(tree):
            if not full_name.startswith("nova.adapters"):
                continue
            if full_name == own_prefix or full_name.startswith(own_prefix + "."):
                continue
            violators.append((_relative_to_nova_src(py_file), full_name))
    assert not violators, (
        f"Adapter sub-packages may only import from their own sub-package; violations: {violators}"
    )


def test_no_dynamic_adapter_imports_outside_app_and_cli() -> None:
    """AC #4 regression guard — ``importlib.import_module("nova.adapters...")``
    and ``__import__("nova.adapters...")`` must not appear in non-adapter
    modules outside ``{app.py, cli.py}``. Static ``from nova.adapters ...``
    is caught by the earlier test; this closes the dynamic-import bypass.
    """
    violators: list[tuple[str, str]] = []
    for py_file in _iter_nova_py_files():
        if _is_app_or_cli(py_file) or _is_under_adapters(py_file):
            continue
        tree = _parse_file(py_file)
        for target in _dynamic_import_full_targets(tree):
            if target == "nova.adapters" or target.startswith("nova.adapters."):
                violators.append((_relative_to_nova_src(py_file), target))
    assert not violators, (
        f"Dynamic nova.adapters imports forbidden outside app/cli; violations: {violators}"
    )


def test_app_module_level_has_no_adapter_instantiation() -> None:
    """AC #4 — no module-scope ``ast.Call`` in ``app.py`` that instantiates an adapter.

    All adapter construction must live inside ``create_app``. This test
    catches three forms a regression could take:

    1. ``from nova.adapters.X import Cls`` + ``Cls()`` at module scope
       (``ast.Name`` callee, symbol tracked via ``ast.ImportFrom``).
    2. ``import nova.adapters.X`` + ``nova.adapters.X.Cls()`` at module
       scope (``ast.Attribute`` callee whose dotted path resolves to
       ``nova.adapters.*``, tracked via ``ast.Import``).
    3. ``import nova.adapters.X as alias`` + ``alias.Cls()`` at module
       scope (``ast.Attribute`` callee whose ``value`` is an
       ``ast.Name`` bound to an adapter-rooted alias, tracked via
       ``ast.Import`` with an ``asname``).
    """
    app_path = NOVA_SRC_ROOT / "app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))

    # Form 1 — ``from nova.adapters.X import Cls``. ``Cls`` is callable
    # directly as an ``ast.Name``.
    adapter_symbols: set[str] = set()
    # Form 3 — ``import nova.adapters.X as alias`` / ``import
    # nova.adapters.X`` (no alias). Map local binding → full dotted
    # name so ``alias.Cls()`` is recognized as an adapter call site.
    adapter_module_bindings: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module is None or not (
                node.module == "nova.adapters" or node.module.startswith("nova.adapters.")
            ):
                continue
            for alias in node.names:
                # ``from nova.adapters.shield import NoOpShieldAdapter`` →
                # track the imported (or aliased) name.
                adapter_symbols.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                full = alias.name
                if full != "nova.adapters" and not full.startswith("nova.adapters."):
                    continue
                # ``import nova.adapters.shield.noop as shield_noop`` →
                # binds ``shield_noop`` locally. ``import
                # nova.adapters.shield.noop`` (no ``as``) binds the
                # TOP-LEVEL name ``nova``, so we also seed that binding
                # for completeness.
                if alias.asname:
                    adapter_module_bindings[alias.asname] = full
                else:
                    # Plain ``import nova.adapters.X`` binds ``nova``;
                    # later ``nova.adapters.X.Cls()`` resolves via
                    # attribute chain. Register the chain head.
                    adapter_module_bindings[full.split(".", 1)[0]] = full

    def _callee_is_adapter(callee: ast.AST) -> bool:
        # Form 1: bare name bound to an adapter class.
        if isinstance(callee, ast.Name) and callee.id in adapter_symbols:
            return True
        # Forms 2 + 3: attribute chain whose root resolves to an
        # adapter module binding.
        if isinstance(callee, ast.Attribute):
            # Walk leftward through the attribute chain to the root Name.
            cursor: ast.AST = callee
            while isinstance(cursor, ast.Attribute):
                cursor = cursor.value
            if isinstance(cursor, ast.Name) and cursor.id in adapter_module_bindings:
                return True
        return False

    offending_calls: list[str] = []
    for node in tree.body:
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            if _callee_is_adapter(inner.func) and not _is_inside_function_or_class(node):
                offending_calls.append(ast.unparse(inner))
    assert not offending_calls, (
        f"app.py must not instantiate adapters at module scope; found: {offending_calls}"
    )


def _is_inside_function_or_class(node: ast.AST) -> bool:
    """True when ``node`` is itself a function/class (body statements handled below)."""
    return isinstance(
        node,
        ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    )


def test_sqlite_brain_adapter_is_instantiated_inside_create_app() -> None:
    """Story 3.1 AC #23 — positive-case AST assertion complementing
    ``test_app_module_level_has_no_adapter_instantiation``.

    The module-scope test above catches the negative case (no adapter
    instantiation outside a function). This test pins the positive case:
    the ``SqliteBrainAdapter(...)`` call must appear SOMEWHERE inside
    ``create_app``'s body, so a silent deletion of the wiring (adapter
    never constructed, ``NovaApp.brain`` would be left unassigned or
    wired to a stub) cannot slip past CI.
    """
    app_path = NOVA_SRC_ROOT / "app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))

    create_app_fn: ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "create_app":
            create_app_fn = node
            break
    assert create_app_fn is not None, "create_app function not found in app.py"

    instantiations: list[str] = []
    for inner in ast.walk(create_app_fn):
        if not isinstance(inner, ast.Call):
            continue
        callee = inner.func
        if isinstance(callee, ast.Name) and callee.id == "SqliteBrainAdapter":
            instantiations.append(ast.unparse(inner))
    assert instantiations, (
        "create_app must instantiate SqliteBrainAdapter; none found. "
        "Story 3.1 AC #23 requires the Brain adapter to be wired in the composition root."
    )


# --- AC #8 tests ------------------------------------------------------------

# Allowlist of logger-name-depth exceptions. The storage sublayer (Story
# 1.4 / 1.5) uses a three-dot name that predates the two-dot convention;
# any future exception requires a story-level convention amendment.
_LOGGER_NAME_DEPTH_ALLOWLIST: frozenset[str] = frozenset(
    {
        "nova.core.storage.engine",
        "nova.core.storage.migrations.runner",
        # Story 3.1 — concrete adapter under ``adapters/{driver}/{system}``
        # mirrors the storage-sublayer nesting precedent. Future
        # drivers (e.g. a Postgres Brain adapter in T2) follow the same
        # ``nova.adapters.{driver}.{system}`` logger-name shape.
        "nova.adapters.sqlite.brain",
    }
)


def _logger_name_literals(tree: ast.AST) -> list[str]:
    """Return every string literal passed as the first positional arg to ``logging.getLogger``."""
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match logging.getLogger(...) and getLogger(...).
        matches_attr = isinstance(func, ast.Attribute) and func.attr == "getLogger"
        matches_name = isinstance(func, ast.Name) and func.id == "getLogger"
        if not (matches_attr or matches_name):
            continue
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names.append(node.args[0].value)
    return names


def test_logger_names_follow_convention() -> None:
    """AC #8 — every ``logging.getLogger("...")`` literal matches the nova naming convention.

    Accepted shapes:
    - ``nova.{module}`` (1 dot) — top-level entrypoints (``nova.app``,
      ``nova.cli``).
    - ``nova.{layer}.{module}`` (2 dots) — the common case
      (``nova.core.tiers``, ``nova.systems.brain``, ...).
    - Explicit allowlist for the storage sublayer (3 dots) — Story 1.4 /
      1.5 precedent that predates the convention. Deeper nesting
      requires a story-level convention amendment.
    """
    violators: list[tuple[str, str]] = []
    for py_file in _iter_nova_py_files():
        tree = _parse_file(py_file)
        for name in _logger_name_literals(tree):
            if not name.startswith("nova."):
                violators.append((_relative_to_nova_src(py_file), name))
                continue
            if name in _LOGGER_NAME_DEPTH_ALLOWLIST:
                continue
            # Accept 1 or 2 dots (``nova.app`` OR ``nova.core.tiers``).
            if name.count(".") not in (1, 2):
                violators.append((_relative_to_nova_src(py_file), name))
    assert not violators, (
        "Logger names must follow nova.{module} or nova.{layer}.{module} convention "
        f"(allowlist: {sorted(_LOGGER_NAME_DEPTH_ALLOWLIST)}); violations: {violators}"
    )


# --- Parametrized smoke rows for discoverability ----------------------------


@pytest.mark.parametrize("py_file", _iter_nova_py_files(), ids=_relative_to_nova_src)
def test_every_nova_py_file_parses(py_file: Path) -> None:
    """Every file under ``src/nova/`` must parse cleanly."""
    _parse_file(py_file)
