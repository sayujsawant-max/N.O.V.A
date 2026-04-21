"""Story 2.5 AC #14, #15 — opacity guard for the composition-root subtree.

Story 2.2 ships an equivalent guard over ``src/nova/setup/`` in
``tests/unit/setup/test_no_key_interpolation.py``. This test re-uses
the same rules against ``src/nova/app.py`` and ``src/nova/cli.py`` —
the only two modules outside ``setup/`` that ever hold a raw API key
reference (both read it as ``config.api_key`` and pass it onward).

Two access patterns are flagged as potential leaks:

- **Bare `ast.Name`** — e.g. ``logger.info(api_key)`` (caught if a future
  edit ever aliases the key to a local variable called ``api_key``).
- **`ast.Attribute` with `attr == "api_key"`** — e.g.
  ``logger.info(f"{config.api_key}")`` or ``extra={"k": config.api_key}``.
  The composition-root subtree accesses the key exclusively as
  ``config.api_key``, so this is the PRIMARY surface to guard; the
  Name-only check catches a hypothetical local-variable alias but
  misses the dominant pattern unless we also match attribute access.

Rules enforced:

1. No f-string interpolates a bare ``api_key`` name OR any
   ``<obj>.api_key`` attribute.
2. No ``print()`` / ``logger.*(...)`` / ``sys.stderr.write(...)`` call
   passes a bare key-name variable OR a ``<obj>.api_key`` attribute as
   a positional or keyword argument (including inside a ``dict`` that
   is itself the value of a keyword argument like ``extra=``).
3. No ``.format(...)`` call passes either form positionally or as a
   keyword.
4. No ``%``-formatting BinOp interpolates either form.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Narrower than the Story 2.2 setup-subtree guard (which also flags ``key``,
# ``raw``, ``secret``). In the composition-root subtree, the API key is
# always accessed as ``config.api_key`` — it is never unpacked into a local
# variable named ``key``/``raw``/``secret``. Those words have unrelated
# legitimate meanings in ``cli.py`` (``key`` is a dict-comprehension loop
# variable for log-record extras; ``raw`` holds the user-supplied log-level
# string in ``_parse_log_level``). Flagging them here would surface false
# positives rather than real leaks. If a future edit aliases the API key to
# a shorter local name, add the new identifier to this set and its setup
# counterpart in ``tests/unit/setup/test_no_key_interpolation.py``.
_KEY_VAR_NAMES: frozenset[str] = frozenset({"api_key"})

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_SCAN_TARGETS: tuple[Path, ...] = (
    _REPO_ROOT / "src" / "nova" / "app.py",
    _REPO_ROOT / "src" / "nova" / "cli.py",
)


def _match_key_node(node: ast.AST) -> str | None:
    """Return a human-readable label if ``node`` references a key-name.

    Matches:
      - ``ast.Name`` whose ``id`` is in ``_KEY_VAR_NAMES``
        (e.g. ``api_key`` as a bare local variable).
      - ``ast.Attribute`` whose ``attr`` is in ``_KEY_VAR_NAMES``
        (e.g. ``config.api_key``, ``self.api_key``, ``cfg.api_key``).

    Returns ``None`` for anything else.
    """
    if isinstance(node, ast.Name) and node.id in _KEY_VAR_NAMES:
        return node.id
    if isinstance(node, ast.Attribute) and node.attr in _KEY_VAR_NAMES:
        # Try to render the full dotted access for the finding message.
        try:
            rendered = ast.unparse(node)
        except AttributeError:
            rendered = f"<attr>.{node.attr}"
        return rendered
    return None


class _FStringInterpolationVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.findings: list[tuple[int, str]] = []

    def visit_FormattedValue(self, node: ast.FormattedValue) -> None:
        for sub in ast.walk(node.value):
            label = _match_key_node(sub)
            if label is not None:
                self.findings.append((node.lineno, f"f-string interpolates {label!r}"))
        self.generic_visit(node)


class _LeakyCallVisitor(ast.NodeVisitor):
    """Flag print/log/stderr-write calls that pass a key-named variable."""

    _LEAKY_NAMES: frozenset[str] = frozenset(
        {
            "print",
            "log",
            "debug",
            "info",
            "warning",
            "error",
            "critical",
            "exception",
            "write",  # sys.stderr.write / sys.stdout.write
        }
    )

    def __init__(self) -> None:
        self.findings: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        func_name = _callable_name(node.func)
        if func_name in self._LEAKY_NAMES:
            for arg in node.args:
                label = _match_key_node(arg)
                if label is not None:
                    self.findings.append(
                        (node.lineno, f"{func_name}(...) passes {label!r} as positional arg")
                    )
            for kw in node.keywords:
                label = _match_key_node(kw.value)
                if label is not None:
                    self.findings.append(
                        (
                            node.lineno,
                            f"{func_name}(...) passes {label!r} as keyword {kw.arg!r}",
                        )
                    )
                # ``extra={"api_key": config.api_key}`` / similar — walk dict
                # literals under keyword args for key-matching values.
                if isinstance(kw.value, ast.Dict):
                    for value_expr in kw.value.values:
                        dict_label = _match_key_node(value_expr)
                        if dict_label is not None:
                            self.findings.append(
                                (
                                    node.lineno,
                                    f"{func_name}(...) passes {dict_label!r} "
                                    f"inside {kw.arg!r} dict",
                                )
                            )
        # Also catch ``"...%s..." % <key expr>`` anywhere in the call's args.
        for arg in node.args:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Mod):
                    rhs_label = _match_key_node(sub.right)
                    if rhs_label is not None:
                        self.findings.append((node.lineno, f"%-formatting with {rhs_label!r}"))
        self.generic_visit(node)


class _FormatMethodVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.findings: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            for arg in node.args:
                label = _match_key_node(arg)
                if label is not None:
                    self.findings.append(
                        (node.lineno, f".format(...) passes {label!r} positionally")
                    )
            for kw in node.keywords:
                label = _match_key_node(kw.value)
                if label is not None:
                    self.findings.append(
                        (
                            node.lineno,
                            f".format(...) passes {label!r} as keyword {kw.arg!r}",
                        )
                    )
        self.generic_visit(node)


def _callable_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


@pytest.mark.parametrize("module_path", _SCAN_TARGETS, ids=lambda p: p.name)
def test_no_api_key_interpolation_in_composition_root_subtree(module_path: Path) -> None:
    """No source expression under ``app.py`` / ``cli.py`` interpolates an
    API-key-named variable into a log record, stderr write, or formatted
    string.
    """
    assert module_path.exists(), f"scan target missing: {module_path}"
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(module_path))

    findings: list[str] = []
    for visitor_cls in (
        _FStringInterpolationVisitor,
        _LeakyCallVisitor,
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


def test_scan_targets_exist() -> None:
    """Defensive: the two composition-root files must be present so the
    parametrize above is non-empty.
    """
    for target in _SCAN_TARGETS:
        assert target.exists(), f"scan target missing: {target}"


# --- Guard-behavior unit tests (synthetic snippets, no prod code changes) ---
#
# These tests prove the guard catches the ``config.api_key`` attribute-access
# pattern that the original ``_KEY_VAR_NAMES``-only implementation missed.
# Each snippet is an intentional leak the guard must reject; if the guard
# weakens in a future edit, these tests fail and name the regression.


def _run_all_visitors(source: str) -> list[str]:
    """Parse ``source`` and return findings from every visitor in the file."""
    tree = ast.parse(source)
    findings: list[str] = []
    for visitor_cls in (
        _FStringInterpolationVisitor,
        _LeakyCallVisitor,
        _FormatMethodVisitor,
    ):
        visitor = visitor_cls()
        visitor.visit(tree)
        for lineno, desc in visitor.findings:
            findings.append(f"L{lineno}: {desc}")
    return findings


@pytest.mark.parametrize(
    ("snippet", "expected_substring"),
    [
        # f-string with attribute access.
        (
            "logger.info(f'{config.api_key}')",
            "f-string interpolates 'config.api_key'",
        ),
        # f-string with deeper attribute chain.
        (
            "logger.info(f'key={self.config.api_key}')",
            "'self.config.api_key'",
        ),
        # Positional arg — attribute access.
        (
            "logger.info(config.api_key)",
            "info(...) passes 'config.api_key' as positional arg",
        ),
        # Keyword arg — attribute access.
        (
            "logger.info('msg', extra=config.api_key)",
            "info(...) passes 'config.api_key' as keyword 'extra'",
        ),
        # Dict value under extra= — attribute access (the user's example).
        (
            "logger.info('msg', extra={'k': config.api_key})",
            "info(...) passes 'config.api_key' inside 'extra' dict",
        ),
        # stderr.write with attribute access.
        (
            "sys.stderr.write(config.api_key)",
            "write(...) passes 'config.api_key' as positional arg",
        ),
        # .format() with attribute access.
        (
            "'{}'.format(config.api_key)",
            ".format(...) passes 'config.api_key' positionally",
        ),
        # %-formatting with attribute access.
        (
            "logger.info('msg: %s' % config.api_key)",
            "%-formatting with 'config.api_key'",
        ),
        # Bare Name — still caught (unchanged behavior).
        (
            "logger.info(api_key)",
            "info(...) passes 'api_key' as positional arg",
        ),
    ],
    ids=[
        "fstring_attribute_access",
        "fstring_deep_attribute_chain",
        "positional_attribute",
        "keyword_attribute",
        "dict_value_under_extra_attribute",
        "stderr_write_attribute",
        "format_method_attribute",
        "percent_format_attribute",
        "bare_name_still_caught",
    ],
)
def test_guard_catches_attribute_access_patterns(snippet: str, expected_substring: str) -> None:
    """Each synthetic leak must produce at least one finding containing
    the expected substring. Regression guard against weakening the
    ``_match_key_node`` matcher back to a Name-only check.
    """
    findings = _run_all_visitors(snippet)
    assert findings, f"guard missed leak in snippet: {snippet!r}"
    assert any(expected_substring in f for f in findings), (
        f"expected {expected_substring!r} among findings, got: {findings}"
    )


@pytest.mark.parametrize(
    "snippet",
    [
        # Attribute access to UNRELATED attrs must NOT fire.
        "logger.info(config.data_dir)",
        "logger.info(f'{config.modes}')",
        # Local variables with unrelated names must NOT fire.
        "logger.info(f'{level}')",
        # Accessing the attribute without passing it anywhere — declaration
        # of a variable is fine, only interpolation/passing is a leak.
        "x = config.api_key",
    ],
    ids=[
        "unrelated_attr_data_dir",
        "unrelated_attr_modes_in_fstring",
        "unrelated_local_name",
        "assignment_is_not_a_leak",
    ],
)
def test_guard_does_not_fire_on_unrelated_patterns(snippet: str) -> None:
    """Narrow false-positive check — the guard must not flag unrelated
    attribute access or plain assignment. Prevents the fix from
    over-firing on benign code.
    """
    findings = _run_all_visitors(snippet)
    assert findings == [], f"guard fired on legitimate code: {snippet!r}\nfindings: {findings}"
