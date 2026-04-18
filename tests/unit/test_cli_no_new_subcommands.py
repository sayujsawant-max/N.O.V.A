"""Story 2.5 AC #1, #19 — no CLI subparsers / no new ArgumentParser in cli.py.

AC #1's scope fence: Story 2.5 ships **no** ``nova config`` /
``nova key rotate`` / any other subcommand surface. The documented
update path is editing ``settings.yaml`` directly. This AST guard
protects against accidental subparser-mode creep in subsequent stories
touching ``cli.py`` before Story 3.9 (which explicitly owns in-session
commands).

Rules enforced:

1. No call to ``parser.add_subparsers(...)`` anywhere in ``cli.py``.
2. Exactly one ``ArgumentParser(...)`` construction in ``cli.py`` —
   the one inside ``_build_parser``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_CLI_PATH: Path = Path(__file__).resolve().parents[2] / "src" / "nova" / "cli.py"


def _parse_cli() -> ast.Module:
    source = _CLI_PATH.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(_CLI_PATH))


def test_cli_path_exists() -> None:
    assert _CLI_PATH.exists(), f"missing scan target: {_CLI_PATH}"


def test_no_add_subparsers_calls() -> None:
    """AC #19 — no call to ``<parser>.add_subparsers(...)`` anywhere in cli.py."""
    tree = _parse_cli()
    subparser_calls: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match ``<any>.add_subparsers(...)``.
        if isinstance(node.func, ast.Attribute) and node.func.attr == "add_subparsers":
            subparser_calls.append(node.lineno)
    assert subparser_calls == [], (
        f"cli.py must not call add_subparsers(...) (Story 2.5 AC #1). "
        f"Found at lines: {subparser_calls}"
    )


def test_exactly_one_argument_parser_construction() -> None:
    """AC #19 — there is exactly one ``ArgumentParser(...)`` call site.

    Catches an accidental parallel parser (e.g. a subparser scaffold
    introduced as an inline fallback) before it has a chance to grow
    into an undocumented CLI surface.
    """
    tree = _parse_cli()
    parser_constructions: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        # Match ``argparse.ArgumentParser(...)`` or bare ``ArgumentParser(...)``.
        if (
            isinstance(callee, ast.Attribute) and callee.attr == "ArgumentParser"
        ) or (isinstance(callee, ast.Name) and callee.id == "ArgumentParser"):
            parser_constructions.append(node.lineno)
    assert len(parser_constructions) == 1, (
        f"cli.py must construct exactly one ArgumentParser. "
        f"Found {len(parser_constructions)} at lines: {parser_constructions}"
    )
