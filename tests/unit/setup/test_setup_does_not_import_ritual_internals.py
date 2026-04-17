"""Global layering guard for ``src/nova/setup/*.py`` (Story 2.4 AC #33).

Epic 2 must not pre-wire Ritual / Nerve / Skin / Voice internals — those
belong to Epic 3 (Stories 3.2, 3.3, etc.). Briefing Card State B/C
render logic lives in Ritual + Skin; setup should never reach across
that boundary, even accidentally.

Two guards walk every ``nova/setup/*.py`` module:

1. **Import guard** — no ``nova.systems.*.system``, no
   ``nova.systems.ritual``/``nova.systems.nerve`` imports, no
   ``nova.adapters.*``, no ``nova.ports.*``. The one exception is
   ``nova.systems.eyes.models`` used by ``initial_capture.py`` per Story
   1.9 AC #8 (cross-system ``.models`` contract).
2. **String-literal guard** — the Briefing State B/C enum values
   (``"post_setup"`` / ``"warm_resume"``) must not appear anywhere
   under ``src/nova/setup/``. Checking both values catches a naive
   developer who skipped the import guard by inlining the literal.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SETUP_PACKAGE_DIR = Path(__file__).resolve().parents[3] / "src" / "nova" / "setup"

ALLOWED_SYSTEMS_IMPORTS: frozenset[str] = frozenset(
    {
        "nova.systems.eyes.models",
    }
)

FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "nova.adapters",
    "nova.ports",
    "nova.systems.ritual",
    "nova.systems.nerve",
    "nova.systems.skin",
    "nova.systems.voice",
    "nova.systems.hands",
    "nova.systems.brain",
    "nova.systems.shield",
)

FORBIDDEN_STRING_LITERALS: tuple[str, ...] = (
    "post_setup",
    "warm_resume",
)


def _iter_setup_py_files() -> list[Path]:
    return sorted(p for p in SETUP_PACKAGE_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def _has_forbidden_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


@pytest.mark.parametrize("py_file", _iter_setup_py_files(), ids=lambda p: p.name)
def test_setup_module_has_no_forbidden_imports(py_file: Path) -> None:
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    leaked: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            # Review patch #7 — relative imports (level > 0) may target
            # forbidden ancestors without ever matching our absolute
            # prefix list. A ``from ...systems.ritual import X`` inside a
            # setup subpackage is a straight violation and is rejected
            # here regardless of the resolved module path.
            if node.level > 0:
                leaked.append(f"{'.' * node.level}{node.module}  (relative import forbidden)")
                continue
            if (
                _has_forbidden_prefix(node.module, FORBIDDEN_IMPORT_PREFIXES)
                and node.module not in ALLOWED_SYSTEMS_IMPORTS
            ):
                leaked.append(node.module)
            for alias in node.names:
                composed = f"{node.module}.{alias.name}"
                if (
                    _has_forbidden_prefix(composed, FORBIDDEN_IMPORT_PREFIXES)
                    and composed not in ALLOWED_SYSTEMS_IMPORTS
                ):
                    leaked.append(composed)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if (
                    _has_forbidden_prefix(alias.name, FORBIDDEN_IMPORT_PREFIXES)
                    and alias.name not in ALLOWED_SYSTEMS_IMPORTS
                ):
                    leaked.append(alias.name)
    assert not leaked, f"Forbidden imports in {py_file.name}: {sorted(set(leaked))}"


@pytest.mark.parametrize("py_file", _iter_setup_py_files(), ids=lambda p: p.name)
def test_setup_module_has_no_forbidden_string_literals(py_file: Path) -> None:
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    violations: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in FORBIDDEN_STRING_LITERALS
        ):
            violations.append((node.value, getattr(node, "lineno", -1)))
    assert not violations, f"Forbidden Briefing State literal(s) in {py_file.name}: {violations}"
