"""AST guards for ``nova.setup.initial_capture`` (Story 2.4 AC #25, Story 3.1 allowlist).

``initial_capture.py`` owns the setup-time seam that writes directly to
``audit_log`` (and, prior to Story 3.1, to ``sessions`` and
``workspace_snapshots`` as well — those now route through
:class:`nova.ports.brain.BrainPort`). To keep that seam narrow, the
module:

1. MUST NOT import from ``nova.adapters.*`` — setup-time code never
   references concrete adapter classes; composition root hides them.
2. MAY import from ``nova.ports.brain`` — Story 3.1 routes session /
   snapshot writes through :class:`BrainPort`, so the port protocol is
   a legitimate setup-time dependency. Every other ``nova.ports.*``
   stays forbidden (ports are an orchestrator-layer concern).
3. MUST NOT import from ``nova.systems.eyes.system`` (or any other
   system's ``system.py``) — only ``.models`` modules may cross system
   boundaries (Story 1.9 AC #8).
4. MAY import from ``nova.systems.brain.models`` for
   :class:`WorkspaceSnapshotInput` (Story 3.1 — typed input DTO the
   migrated ``persist_first_run`` passes to ``brain.store_snapshot``).
5. MUST NOT dynamically import any forbidden prefix via
   ``__import__`` / ``importlib.import_module``.

Mirrors the shape of ``tests/unit/setup/test_mode_wizard_isolation.py``.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType

import pytest

import nova.setup.initial_capture as initial_capture_module

ALLOWED_PORTS_IMPORTS: frozenset[str] = frozenset(
    {
        # Story 3.1 — setup routes session/snapshot writes through BrainPort.
        "nova.ports.brain",
    }
)

ALLOWED_BRAIN_MODELS_IMPORTS: frozenset[str] = frozenset(
    {
        # Story 3.1 — setup passes a typed ``WorkspaceSnapshotInput`` to Brain.
        "nova.systems.brain.models",
    }
)

FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "nova.adapters",
    "nova.ports",
    # All systems EXCEPT the .models subpackages are off-limits.
    "nova.systems.brain.system",
    "nova.systems.eyes.system",
    "nova.systems.hands.system",
    "nova.systems.nerve.system",
    "nova.systems.ritual.system",
    "nova.systems.shield.system",
    "nova.systems.skin.system",
    "nova.systems.voice.system",
)


def _has_forbidden_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)


def _read_module_source(module: ModuleType) -> str:
    source_path_str = inspect.getsourcefile(module)
    assert source_path_str is not None
    return Path(source_path_str).read_text(encoding="utf-8")


@pytest.mark.parametrize("module", [initial_capture_module])
def test_initial_capture_does_not_import_forbidden(module: ModuleType) -> None:
    tree = ast.parse(_read_module_source(module))
    allowlist: frozenset[str] = ALLOWED_PORTS_IMPORTS | ALLOWED_BRAIN_MODELS_IMPORTS
    leaked: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            # Review patch #7 — relative imports (level > 0) are also
            # forbidden. A pre-patch ``from ...adapters.win32 import X``
            # would slip past the ``level == 0`` filter entirely.
            # Record the raw module expression so the error surface
            # shows the attempted path rather than silently passing.
            if node.level > 0:
                leaked.append(f"{'.' * node.level}{node.module}  (relative import forbidden)")
                continue
            if (
                _has_forbidden_prefix(node.module, FORBIDDEN_PREFIXES)
                and node.module not in allowlist
            ):
                leaked.append(node.module)
                continue
            for alias in node.names:
                composed = f"{node.module}.{alias.name}"
                if (
                    _has_forbidden_prefix(composed, FORBIDDEN_PREFIXES)
                    and node.module not in allowlist
                ):
                    leaked.append(composed)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if (
                    _has_forbidden_prefix(alias.name, FORBIDDEN_PREFIXES)
                    and alias.name not in allowlist
                ):
                    leaked.append(alias.name)
    assert not leaked, f"Forbidden imports in {module.__name__}: {sorted(set(leaked))}."


@pytest.mark.parametrize("module", [initial_capture_module])
def test_initial_capture_no_dynamic_forbidden_imports(module: ModuleType) -> None:
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
    leaked = [t for t in dynamic_targets if _has_forbidden_prefix(t, FORBIDDEN_PREFIXES)]
    assert not leaked, f"Dynamic forbidden imports in {module.__name__}: {sorted(set(leaked))}."


def test_initial_capture_only_allowed_systems_models_imports() -> None:
    """Positive-list assertion: the ONLY ``nova.systems.*`` imports are the
    allowlisted ``.models`` modules.

    Story 2.4 permitted ``nova.systems.eyes.models`` for
    :class:`WorkspaceSnapshot` / :class:`WindowContext`. Story 3.1 adds
    ``nova.systems.brain.models`` for :class:`WorkspaceSnapshotInput`.
    Any other ``nova.systems.*`` import is scope creep and fails this
    test.
    """
    tree = ast.parse(_read_module_source(initial_capture_module))
    systems_imports: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("nova.systems")
        ):
            systems_imports.append(node.module)
    allowed_sorted = sorted({"nova.systems.eyes.models", "nova.systems.brain.models"})
    assert sorted(set(systems_imports)) == allowed_sorted, systems_imports
