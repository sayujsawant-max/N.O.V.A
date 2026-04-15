"""Story 1.11 AC #13 — structural lock on the CI workflow YAML + coverage config.

Uses yaml.safe_load + tomllib.loads for parsing; asserts invariants on the parsed
structure rather than text regex (same principle as the AST-walk precedent from
Stories 1.2 / 1.9 / 1.10 — see memory/feedback_ast_static_analysis_tests.md).

The ``test_workflow_on_key_is_string_not_bool`` test is ordered FIRST below so it
fails first on a YAML 1.1 bool-coercion regression (unquoted ``on:`` parsing to
Python ``True``) — that prevents downstream tests from blowing up with a confusing
``KeyError: 'on'``.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable
from pathlib import Path

import pytest
import yaml

# --- Paths (repo-root-relative; pytest runs from repo root per pyproject testpaths) ---

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
GITIGNORE_PATH = REPO_ROOT / ".gitignore"
DOCS_DEVELOPMENT_PATH = REPO_ROOT / "docs" / "development.md"
PROJECT_CONTEXT_PATH = REPO_ROOT / "_bmad-output" / "project-context.md"


# --- Helpers (module-private; duplicated in-file per flat-test-layout precedent) -----


def _load_workflow() -> dict[object, object]:
    """Parse the CI workflow YAML. Returns the raw dict so bool-coerced keys
    (from an unquoted ``on:``) surface as ``True`` rather than being silently
    normalized to the string ``"on"`` — the test at the top of this file relies
    on that distinction."""
    loaded = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), "workflow must parse to a dict"
    return loaded


def _load_pyproject() -> dict[str, object]:
    return tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))


def _workflow_step_runs() -> list[str]:
    """Return every ``run:`` string from every step, preserving order. Multi-line
    ``run: |`` blocks collapse to a single joined string (newline-preserved) so a
    block with several commands still counts as one step for substring checks."""
    workflow = _load_workflow()
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    quality_gate = jobs["quality-gate"]
    assert isinstance(quality_gate, dict)
    steps = quality_gate["steps"]
    assert isinstance(steps, list)
    runs: list[str] = []
    for step in steps:
        assert isinstance(step, dict)
        run = step.get("run")
        if isinstance(run, str):
            runs.append(run.strip())
    return runs


def _workflow_steps() -> list[dict[object, object]]:
    workflow = _load_workflow()
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    quality_gate = jobs["quality-gate"]
    assert isinstance(quality_gate, dict)
    steps = quality_gate["steps"]
    assert isinstance(steps, list)
    typed: list[dict[object, object]] = []
    for step in steps:
        assert isinstance(step, dict)
        typed.append(step)
    return typed


def _iter_all_nodes(node: object) -> Iterable[object]:
    """Yield every nested node in a parsed YAML structure (for AC #13's
    ``test_workflow_has_no_continue_on_error`` deep-walk)."""
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _iter_all_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_all_nodes(item)


def _extract_canonical_commands() -> dict[str, str]:
    """Extract the 5 canonical CI commands from project-context.md's "Full verify" bullet.

    Full-verify is the authoritative CI-parity source (vs. the per-tool bullets at
    lines 151-155, which mix read-mode and write-mode invocations — e.g., the
    standalone "Format" bullet is ``ruff format`` while the full-verify chain uses
    ``ruff format --check``, which is what CI needs).

    Returns {label: command} keyed by the 5 CI-mapped labels:
    "Lint", "Format", "Type check", "Test (unit)", "Test (integration)".
    """
    text = PROJECT_CONTEXT_PATH.read_text(encoding="utf-8")
    full_verify_match = re.search(r"Full verify:\s*`([^`]+)`", text)
    assert full_verify_match, "full-verify bullet not found in project-context.md"
    chained = full_verify_match.group(1).strip()
    parts = [part.strip() for part in chained.split("&&")]
    # Map each chained command to its CI label by leading substring.
    label_mapping: list[tuple[str, str]] = [
        ("uv run ruff check ", "Lint"),
        ("uv run ruff format --check ", "Format"),
        ("uv run mypy ", "Type check"),
        ("uv run pytest", "Test (unit)"),  # The bare `uv run pytest` in full-verify covers both
    ]
    commands: dict[str, str] = {}
    for part in parts:
        for prefix, label in label_mapping:
            if part.startswith(prefix) and label not in commands:
                commands[label] = part
                break
    # The full-verify `uv run pytest` (no path) is the "run all tests" shorthand. CI splits
    # it into unit + integration per the separate bullets at lines 151-152. Pull those two
    # commands directly from their per-tool bullets since full-verify doesn't carry them.
    per_tool_pattern = re.compile(r"^\s*-\s+([^:]+):\s+`([^`]+)`\s*$", re.MULTILINE)
    for match in per_tool_pattern.finditer(text):
        label = match.group(1).strip()
        command = match.group(2).strip()
        if label == "Test (unit)":
            commands["Test (unit)"] = command
        elif label == "Test (integration)":
            commands["Test (integration)"] = command
    expected = {"Lint", "Format", "Type check", "Test (unit)", "Test (integration)"}
    missing = expected - commands.keys()
    assert not missing, f"canonical commands not resolved: {missing}"
    return commands


# --- Tests ---------------------------------------------------------------------------


def test_workflow_on_key_is_string_not_bool() -> None:
    """AC #1 quoting guard. Ordered first so it fails first on regression."""
    workflow = _load_workflow()
    assert "on" in workflow, "'on' key missing — if it parsed as True, AC #1 quoting broke"
    assert True not in workflow, (
        "'on' key parsed as Python True (YAML 1.1 bool coercion) — quote \"on\": in ci.yml"
    )


def test_workflow_file_exists() -> None:
    assert WORKFLOW_PATH.is_file(), f"{WORKFLOW_PATH} must exist"
    # Round-trip parse confirms valid YAML.
    workflow = _load_workflow()
    assert isinstance(workflow, dict)


def test_workflow_has_one_job() -> None:
    workflow = _load_workflow()
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert list(jobs.keys()) == ["quality-gate"], (
        f"exactly one job named 'quality-gate' expected; got {list(jobs.keys())}"
    )


def test_workflow_runs_on_windows() -> None:
    workflow = _load_workflow()
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    quality_gate = jobs["quality-gate"]
    assert isinstance(quality_gate, dict)
    assert quality_gate["runs-on"] == "windows-latest"


def test_workflow_triggers_on_push_and_pr() -> None:
    workflow = _load_workflow()
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert "push" in triggers
    assert "pull_request" in triggers


def test_workflow_has_concurrency_group() -> None:
    workflow = _load_workflow()
    concurrency = workflow["concurrency"]
    assert isinstance(concurrency, dict)
    assert concurrency["cancel-in-progress"] is True
    group = concurrency["group"]
    assert isinstance(group, str)
    assert "${{ github.ref }}" in group


def test_workflow_permissions_are_read_only() -> None:
    workflow = _load_workflow()
    assert workflow["permissions"] == {"contents": "read"}


def test_workflow_uses_setup_uv_action() -> None:
    matches = []
    for step in _workflow_steps():
        uses = step.get("uses")
        if isinstance(uses, str) and uses.startswith("astral-sh/setup-uv@"):
            matches.append(step)
    assert len(matches) == 1, f"expected exactly one astral-sh/setup-uv step; got {len(matches)}"
    with_block = matches[0].get("with")
    assert isinstance(with_block, dict)
    version = with_block.get("version")
    assert isinstance(version, str) and version, (
        "astral-sh/setup-uv must pin a non-empty version string"
    )


def test_workflow_python_version_from_dot_python_version() -> None:
    """AC #2 — setup-python step must source its version from .python-version via
    either Approach A (python-version-file) or Approach B (steps.<id>.outputs.*)."""
    setup_python_steps = [
        s
        for s in _workflow_steps()
        if isinstance(uses := s.get("uses"), str) and uses.startswith("actions/setup-python@")
    ]
    assert len(setup_python_steps) == 1
    with_block = setup_python_steps[0].get("with")
    assert isinstance(with_block, dict)

    # Approach A: python-version-file: .python-version
    if with_block.get("python-version-file") == ".python-version":
        return

    # Approach B: python-version: ${{ steps.<id>.outputs.* }} + a prior cat step
    pv = with_block.get("python-version")
    if isinstance(pv, str) and pv.startswith("${{") and "steps." in pv and ".outputs." in pv:
        runs_joined = "\n".join(_workflow_step_runs())
        assert "cat .python-version" in runs_joined, (
            "Approach B requires a step that reads .python-version via cat"
        )
        assert "$GITHUB_OUTPUT" in runs_joined, "Approach B requires writing to $GITHUB_OUTPUT"
        return

    # Rejection cases
    assert pv is None or not isinstance(pv, str) or not re.fullmatch(r"3\.12(\.\w+)?", pv), (
        "hard-coded '3.12' / '3.12.x' rejected — read .python-version instead"
    )
    pytest.fail(
        "setup-python step must use python-version-file: .python-version OR "
        "python-version: ${{ steps.<id>.outputs.* }} expression"
    )


def test_workflow_uv_sync_uses_frozen() -> None:
    runs = _workflow_step_runs()
    assert any("uv sync --frozen --all-extras" in r for r in runs), (
        "expected a step running `uv sync --frozen --all-extras`"
    )


CANONICAL_ORDER = ["Lint", "Format", "Type check", "Test (unit)", "Test (integration)"]


@pytest.mark.parametrize("label", CANONICAL_ORDER)
def test_workflow_step_starts_with_canonical_command(label: str) -> None:
    """AC #13 drift test — each of the 5 canonical commands from project-context.md
    appears as a prefix of some workflow step's run string (workflow may extend
    with additional args, but must not substitute or reorder)."""
    canonicals = _extract_canonical_commands()
    canonical_cmd = canonicals[label]
    runs = _workflow_step_runs()
    assert any(r.startswith(canonical_cmd) for r in runs), (
        f"no workflow step starts with canonical {label!r} command: {canonical_cmd!r}"
    )


def test_workflow_runs_canonical_commands_in_order() -> None:
    """The 5 canonical commands appear in CI order: lint → format → mypy → unit → integration."""
    canonicals = _extract_canonical_commands()
    runs = _workflow_step_runs()
    positions: list[int] = []
    for label in CANONICAL_ORDER:
        canonical_cmd = canonicals[label]
        first_match = next((i for i, r in enumerate(runs) if r.startswith(canonical_cmd)), -1)
        assert first_match >= 0, f"canonical {label!r} missing from workflow"
        positions.append(first_match)
    assert positions == sorted(positions), (
        f"canonical commands out of order: {list(zip(CANONICAL_ORDER, positions, strict=True))}"
    )


def test_workflow_has_no_continue_on_error() -> None:
    for node in _iter_all_nodes(_load_workflow()):
        if isinstance(node, dict):
            assert "continue-on-error" not in node, (
                "continue-on-error breaks AC #6 fail-fast guarantee"
            )


def test_workflow_has_no_marker_based_pytest() -> None:
    """AC #8 — no step filters pytest via -m <marker>; directory-based selection only."""
    marker_pattern = re.compile(r"\bpytest\b.*\s-m\s+[a-z_]+", re.MULTILINE)
    for run in _workflow_step_runs():
        assert not marker_pattern.search(run), (
            f"marker-based pytest filter found (AC #8 forbids): {run!r}"
        )


def test_workflow_coverage_passes_cov_on_unit_step_only() -> None:
    unit_runs = [r for r in _workflow_step_runs() if r.startswith("uv run pytest tests/unit/")]
    integration_runs = [
        r for r in _workflow_step_runs() if r.startswith("uv run pytest tests/integration/")
    ]
    assert len(unit_runs) == 1
    assert len(integration_runs) == 1
    assert "--cov=nova" in unit_runs[0], "unit tests must run with --cov=nova"
    assert "--cov" not in integration_runs[0], (
        "integration tests must NOT pass --cov (AC #7 coverage is unit-tests-only)"
    )


def test_pyproject_has_coverage_run_section() -> None:
    pyproject = _load_pyproject()
    tool = pyproject["tool"]
    assert isinstance(tool, dict)
    coverage = tool["coverage"]
    assert isinstance(coverage, dict)
    run_section = coverage["run"]
    assert isinstance(run_section, dict)
    assert run_section["source"] == ["src/nova"]
    assert run_section["branch"] is True


def test_pyproject_has_coverage_report_section() -> None:
    pyproject = _load_pyproject()
    tool = pyproject["tool"]
    assert isinstance(tool, dict)
    coverage = tool["coverage"]
    assert isinstance(coverage, dict)
    report = coverage["report"]
    assert isinstance(report, dict)
    exclude_lines = report["exclude_lines"]
    assert isinstance(exclude_lines, list)
    assert len(exclude_lines) >= 4
    assert report["precision"] == 1


def test_pyproject_does_not_set_coverage_fail_under() -> None:
    """AC #7 — no threshold enforcement in T1. If a future story adds fail_under,
    it deletes this test."""
    pyproject = _load_pyproject()
    tool = pyproject["tool"]
    assert isinstance(tool, dict)
    coverage = tool["coverage"]
    assert isinstance(coverage, dict)
    report = coverage["report"]
    assert isinstance(report, dict)
    assert "fail_under" not in report


CI_ARTIFACT_PATTERNS = ["coverage.xml", "junit.xml", ".uv_cache/", ".hatch/"]


@pytest.mark.parametrize("pattern", CI_ARTIFACT_PATTERNS)
def test_gitignore_covers_ci_artifacts(pattern: str) -> None:
    lines = GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()
    assert pattern in lines, f".gitignore missing required CI-artifact pattern: {pattern!r}"


def test_docs_development_md_exists() -> None:
    assert DOCS_DEVELOPMENT_PATH.is_file()
    text = DOCS_DEVELOPMENT_PATH.read_text(encoding="utf-8").lower()
    assert "minimum `uv` version" in text or "minimum uv version" in text
    # Canonical full-verify command substring — lenient on exact punctuation but requires
    # the full chained invocation from project-context.md:156.
    assert "uv run ruff check src/ tests/" in text
    assert "uv run ruff format --check src/ tests/" in text
    assert "uv run mypy src/" in text
    assert "uv run pytest" in text
