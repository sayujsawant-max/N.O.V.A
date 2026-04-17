"""AST guard for ``nova.setup.mode_wizard`` (Story 2.3 AC #18, #25).

The wizard module must not reach into ``nova.adapters.*``,
``nova.systems.*``, or ``nova.ports.*``. It runs at setup-time —
before the composition root wires any port/adapter pair — so those
imports would be both architecturally wrong and import-time broken.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType

import pytest

import nova.setup.mode_wizard as mode_wizard_module

FORBIDDEN_NOVA_PREFIXES: tuple[str, ...] = (
    "nova.adapters",
    "nova.systems",
    "nova.ports",
)


def _has_forbidden_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    """Exact or dotted-prefix match. Avoids ``nova.adapters_helpers`` false positives."""
    return any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)


def _read_module_source(module: ModuleType) -> str:
    source_path_str = inspect.getsourcefile(module)
    assert source_path_str is not None
    return Path(source_path_str).read_text(encoding="utf-8")


@pytest.mark.parametrize("module", [mode_wizard_module])
def test_mode_wizard_does_not_import_nova_adapters_systems_ports(
    module: ModuleType,
) -> None:
    """The wizard module reaches nothing in ``adapters/``, ``systems/``, or ``ports/``."""
    tree = ast.parse(_read_module_source(module))
    leaked: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            if _has_forbidden_prefix(node.module, FORBIDDEN_NOVA_PREFIXES):
                leaked.append(node.module)
                continue
            # ``from nova import adapters`` — node.module == "nova", alias == "adapters"
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


@pytest.mark.parametrize("module", [mode_wizard_module])
def test_mode_wizard_does_not_dynamically_import_forbidden(
    module: ModuleType,
) -> None:
    """``importlib.import_module("nova.adapters.*")`` is also forbidden."""
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
    leaked = [t for t in dynamic_targets if _has_forbidden_prefix(t, FORBIDDEN_NOVA_PREFIXES)]
    assert not leaked, (
        f"Dynamic nova sub-package imports in {module.__name__}: {sorted(set(leaked))}."
    )
