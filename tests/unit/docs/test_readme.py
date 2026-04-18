"""Story 2.5 AC #12, #20 — ``README.md`` documents the key-update path.

The README is the user-facing update surface for post-setup key changes.
These tests pin the content contract so a future edit cannot:

- Remove the API key management section.
- Drift the exact path substring users are instructed to edit.
- Overpromise Story 3.5's automatic tier-degradation behavior.
- Leak a resolved Windows user path (opacity bleed from a copy-paste).
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _repo_root() -> Path:
    """Walk up from this test file until the repository-root sentinel
    (``pyproject.toml``) is found. Fails loud if the sentinel is absent.

    Review patch (Patch 6): the original implementation used
    ``Path(__file__).resolve().parents[3]`` which silently points at
    the wrong ancestor if the test ever moves one directory deeper.
    Anchoring on ``pyproject.toml`` makes the resolution layout-resilient.
    """
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError(
        f"could not locate repo-root sentinel (pyproject.toml) from {here}"
    )


def _readme_path() -> Path:
    """Resolve the repository-root ``README.md``."""
    return _repo_root() / "README.md"


def test_readme_exists_at_repo_root() -> None:
    """AC #12 / AC #20 — the file must live at the repository root."""
    path = _readme_path()
    assert path.exists(), f"README.md missing at repo root ({path})"
    assert path.is_file()


@pytest.fixture
def readme_text() -> str:
    return _readme_path().read_text(encoding="utf-8")


def test_readme_has_api_key_management_section(readme_text: str) -> None:
    """AC #20 — four required markers appear in the spec order.

    Unique markers (section heading, distinctive phrase) must appear
    exactly once. Path/tier-name markers may appear multiple times —
    the path shows up in both the "first-run writes it here" bullet
    and the "edit it here" numbered list, which is good reinforcement.
    """
    ordered_markers = (
        "## API key management",
        "%LOCALAPPDATA%/nova/settings.yaml",
        "offline-local-only tier",
        "does NOT crash bootstrap",
    )
    previous_idx = -1
    for marker in ordered_markers:
        idx = readme_text.find(marker)
        assert idx >= 0, f"missing README marker: {marker!r}"
        assert idx > previous_idx, f"README markers out of order at {marker!r}"
        previous_idx = idx

    # Unique markers — true section structure, must not be duplicated.
    for unique_marker in ("## API key management", "does NOT crash bootstrap"):
        assert readme_text.count(unique_marker) == 1, (
            f"{unique_marker!r} must appear exactly once "
            f"(found {readme_text.count(unique_marker)})"
        )


def test_readme_does_not_overpromise_story_35_behavior(readme_text: str) -> None:
    """AC #20 — guard against re-introducing the Story-3.5 overpromise.

    If a future edit wants to claim the "degrades on first cloud failure"
    behavior, Story 3.5 must ship first and this guard must be updated.
    """
    forbidden = "degrades to offline-local-only on the first cloud failure"
    assert forbidden not in readme_text, (
        f"README re-introduced the Story 3.5 overpromise: {forbidden!r}"
    )


def test_readme_does_not_leak_resolved_user_path(readme_text: str) -> None:
    """Opacity — the README must reference the ``%LOCALAPPDATA%`` env-var form,
    never a resolved home-directory path, regardless of the dev's OS.

    Review patch (Patch 8): the original check was Windows-biased
    (``C:\\Users\\`` / ``C:/Users/``). A dev on macOS (``/Users/...``) or
    Linux (``/home/...``) could paste a resolved path that bypassed the
    guard; lowercase ``c:\\users\\`` also slipped through. The check is
    now case-insensitive and covers all three OS conventions.
    """
    lowered = readme_text.lower()
    forbidden_substrings = (
        "c:\\users\\",
        "c:/users/",
        "/users/",   # macOS home-dir root
        "/home/",    # Linux home-dir root
    )
    for fragment in forbidden_substrings:
        assert fragment not in lowered, (
            f"README leaked a resolved home-directory path fragment: {fragment!r}"
        )
