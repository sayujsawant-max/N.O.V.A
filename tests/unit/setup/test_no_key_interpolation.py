"""AST guard test for AC #31 — no API key value ever interpolated into source strings.

Walks every module under ``src/nova/setup/`` and asserts:

1. No f-string interpolates a variable named ``key``, ``api_key``, or
   ``raw`` (the three names used throughout the module to hold the
   user-supplied key value).
2. No ``.format(...)`` or ``%``-formatting expression interpolates
   such a variable.

Variable-name *references* (imports, parameter lists, attribute
accesses, assignments) are allowed — only string-interpolation of the
value is forbidden.  This is a best-effort static check as specified
in the story spec.  Dynamic paths (``console.print(raw)``,
``print(api_key)``) are also flagged since they're equivalent leaks.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Parameter names that hold the raw API key value in src/nova/setup/.
# If a new variable name is introduced for the key, add it here.
_KEY_VAR_NAMES: frozenset[str] = frozenset({"api_key", "key", "raw"})

_SETUP_DIR: Path = Path(__file__).resolve().parents[3] / "src" / "nova" / "setup"


def _setup_modules() -> list[Path]:
    """Enumerate Python modules under ``src/nova/setup/``."""
    return sorted(p for p in _SETUP_DIR.rglob("*.py") if p.is_file())


class _FStringInterpolationVisitor(ast.NodeVisitor):
    """Collect f-string `{var}` expressions whose root Name is a key variable."""

    def __init__(self) -> None:
        self.findings: list[tuple[int, str]] = []

    def visit_FormattedValue(self, node: ast.FormattedValue) -> None:
        # `f"...{expr}..."` — `expr` is `node.value`.  We flag if the
        # expression is a bare Name with id in _KEY_VAR_NAMES.  Deeper
        # expressions (e.g. `f"{len(key)}"`, `f"{key[:4]}"`) are also
        # suspect because they can leak a prefix/length side-channel,
        # so we recurse and flag any contained Name that matches.
        for sub in ast.walk(node.value):
            if isinstance(sub, ast.Name) and sub.id in _KEY_VAR_NAMES:
                self.findings.append(
                    (node.lineno, f"f-string interpolates {sub.id!r}")
                )
        self.generic_visit(node)


class _PrintCallVisitor(ast.NodeVisitor):
    """Flag print()/console.print(...)/logger.*(...) calls passing the key as an arg."""

    _LEAKY_METHODS: frozenset[str] = frozenset(
        {"print", "log", "debug", "info", "warning", "error", "critical", "exception"}
    )

    def __init__(self) -> None:
        self.findings: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        func_name = _callable_name(node.func)
        if func_name is not None and func_name in self._LEAKY_METHODS:
            for arg in node.args:
                if isinstance(arg, ast.Name) and arg.id in _KEY_VAR_NAMES:
                    self.findings.append(
                        (node.lineno, f"{func_name}(...) passes {arg.id!r} as positional arg")
                    )
        # Also check % formatting: `"..." % key` would appear as a
        # BinOp with Mod operator; scan all args recursively.
        for arg in node.args:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Mod):
                    rhs = sub.right
                    if isinstance(rhs, ast.Name) and rhs.id in _KEY_VAR_NAMES:
                        self.findings.append(
                            (node.lineno, f"%-formatting with {rhs.id!r}")
                        )
        self.generic_visit(node)


class _FormatMethodVisitor(ast.NodeVisitor):
    """Flag `"...".format(key)` / `str.format(key)` patterns."""

    def __init__(self) -> None:
        self.findings: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        # Match `<str>.format(...)` where <str> is a string literal
        # or a Name — we care only about the args passed.
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
        ):
            for arg in node.args:
                if isinstance(arg, ast.Name) and arg.id in _KEY_VAR_NAMES:
                    self.findings.append(
                        (node.lineno, f".format(...) passes {arg.id!r} positionally")
                    )
            for kw in node.keywords:
                if isinstance(kw.value, ast.Name) and kw.value.id in _KEY_VAR_NAMES:
                    self.findings.append(
                        (
                            node.lineno,
                            f".format(...) passes {kw.value.id!r} as keyword {kw.arg}",
                        )
                    )
        self.generic_visit(node)


def _callable_name(func: ast.expr) -> str | None:
    """Extract the simple name of a call target (e.g. ``print`` or ``logger.info``)."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


@pytest.mark.parametrize("module_path", _setup_modules(), ids=lambda p: p.name)
def test_no_key_interpolation_in_source(module_path: Path) -> None:
    """AC #31: no source string in nova.setup interpolates the API key value."""
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(module_path))

    findings: list[str] = []

    for visitor_cls in (
        _FStringInterpolationVisitor,
        _PrintCallVisitor,
        _FormatMethodVisitor,
    ):
        visitor = visitor_cls()
        visitor.visit(tree)
        for lineno, desc in visitor.findings:
            findings.append(f"{module_path.name}:{lineno} — {desc}")

    assert findings == [], (
        "AST guard caught potential API key leaks in source strings:\n"
        + "\n".join(f"  - {f}" for f in findings)
    )


def test_setup_modules_are_enumerated() -> None:
    """Defensive check: the glob actually finds modules so the parametrize is non-empty."""
    modules = _setup_modules()
    assert modules, f"No modules found under {_SETUP_DIR}"
    names = {m.name for m in modules}
    # These are the core Story 2.2 modules; if the glob breaks, we
    # notice here rather than silently passing with zero parametrize.
    assert "api_key.py" in names
    assert "settings_writer.py" in names
    assert "__main__.py" in names
