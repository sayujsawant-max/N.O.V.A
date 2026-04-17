"""Unit tests for mode wizard app registry resolution (Story 2.3 Task 1 / AC #5, #6, #20).

``resolve_app`` has two layers:

1. **Registry lookup** — case-insensitive display-name match against a
   hardcoded table of common Windows apps. Returns the canonical
   executable name.
2. **PATH fallback** — ``shutil.which(user_input)`` when the registry
   misses. Returns the raw user input if PATH resolves it (so the mode
   file still carries what the user typed).

Returns ``None`` when both layers miss, so the caller can show the
"couldn't find" message (AC #6).
"""

from __future__ import annotations

import pytest

from nova.setup.mode_wizard import APP_REGISTRY, resolve_app


class TestRegistryHits:
    """Known display names resolve to canonical executables."""

    @pytest.mark.parametrize(
        ("display_name", "expected_executable"),
        [
            ("VS Code", "code"),
            ("Chrome", "chrome"),
            ("Firefox", "firefox"),
            ("Notion", "notion"),
            ("Discord", "discord"),
            ("Spotify", "spotify"),
            ("Windows Terminal", "wt"),
            ("Notepad++", "notepad++"),
            ("Obsidian", "obsidian"),
            ("Slack", "slack"),
        ],
    )
    def test_known_app_resolves_to_registry_executable(
        self, display_name: str, expected_executable: str
    ) -> None:
        assert resolve_app(display_name) == expected_executable

    def test_lookup_is_case_insensitive(self) -> None:
        assert resolve_app("vs code") == "code"
        assert resolve_app("VS CODE") == "code"
        assert resolve_app("Vs Code") == "code"
        assert resolve_app("chrome") == "chrome"
        assert resolve_app("CHROME") == "chrome"

    def test_whitespace_is_trimmed(self) -> None:
        assert resolve_app("  VS Code  ") == "code"
        assert resolve_app("\tChrome\n") == "chrome"

    def test_registry_takes_precedence_over_path_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """shutil.which is not consulted when the registry has a hit."""
        called: list[str] = []

        def _spy_which(name: str) -> str | None:
            called.append(name)
            return "/some/path"

        monkeypatch.setattr("nova.setup.mode_wizard.shutil.which", _spy_which)
        assert resolve_app("VS Code") == "code"
        assert called == []  # registry hit; PATH never probed


class TestPathFallback:
    """Unknown display names fall through to shutil.which."""

    def test_path_hit_returns_user_input_verbatim(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "nova.setup.mode_wizard.shutil.which",
            lambda name: "/usr/bin/mystery",
        )
        # Not in the registry; shutil.which resolves it.
        assert resolve_app("mystery") == "mystery"

    def test_path_fallback_preserves_original_casing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The exec name in the mode file reflects what the user typed.

        The registry normalizes to canonical lowercase, but a PATH
        fallback has no canonical form — preserving user input is the
        honest choice (they typed it; if it works, we use it as-is).
        """
        monkeypatch.setattr(
            "nova.setup.mode_wizard.shutil.which",
            lambda name: f"/usr/bin/{name}",
        )
        assert resolve_app("MyCustomTool") == "MyCustomTool"


class TestResolutionMisses:
    """Unknown app + missing from PATH returns None."""

    def test_unknown_app_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("nova.setup.mode_wizard.shutil.which", lambda name: None)
        assert resolve_app("NonexistentAppDoesNotExist") is None

    def test_empty_input_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("nova.setup.mode_wizard.shutil.which", lambda name: None)
        assert resolve_app("") is None
        assert resolve_app("   ") is None


class TestShutilWhichCrashGuard:
    """A malformed PATH or input must never crash the wizard."""

    def test_oserror_treated_as_miss(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(_name: str) -> str | None:
            raise OSError("simulated PATH stat failure")

        monkeypatch.setattr("nova.setup.mode_wizard.shutil.which", _raise)
        # Registry miss + OSError from shutil.which → None, no exception.
        assert resolve_app("UnknownExoticApp") is None

    def test_valueerror_treated_as_miss(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(_name: str) -> str | None:
            raise ValueError("embedded null byte")

        monkeypatch.setattr("nova.setup.mode_wizard.shutil.which", _raise)
        assert resolve_app("chro\x00me") is None

    def test_registry_hit_bypasses_shutil_entirely(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the registry hits, shutil.which must not be called at all."""

        def _raise(_name: str) -> str | None:
            raise OSError("must not be called on registry hit")

        monkeypatch.setattr("nova.setup.mode_wizard.shutil.which", _raise)
        # Registry hit for "VS Code" → no shutil.which call → no crash
        assert resolve_app("VS Code") == "code"


class TestRegistryStructure:
    """Structural invariants on the registry itself."""

    def test_registry_keys_are_lowercase(self) -> None:
        """Keys must be lowercase so case-insensitive lookup is a simple lowercase compare."""
        for key in APP_REGISTRY:
            assert key == key.lower(), f"Registry key not lowercase: {key!r}"

    def test_registry_values_are_non_empty_strings(self) -> None:
        for key, value in APP_REGISTRY.items():
            assert isinstance(value, str), f"Registry value for {key!r} not str"
            assert value.strip(), f"Registry value for {key!r} empty/whitespace"

    def test_registry_covers_minimum_app_set(self) -> None:
        """Minimum coverage required by AC #5 — common Windows developer apps."""
        required_canonical_executables = {
            "code",
            "chrome",
            "firefox",
            "notion",
            "discord",
            "spotify",
            "wt",
            "notepad++",
            "obsidian",
            "slack",
        }
        actual_values = set(APP_REGISTRY.values())
        missing = required_canonical_executables - actual_values
        assert not missing, f"Registry missing required executables: {sorted(missing)}"
