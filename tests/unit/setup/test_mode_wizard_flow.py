"""Unit tests for the mode wizard interactive flow (Story 2.3 Task 4 / AC #1-4, #11-16, #23).

Covers the flow-level contracts:

- Template accept-as-is routes to Path A (verbatim copy).
- Template modify routes to Path B (schema writer).
- Custom mode creation collects apps, enforces the zero-app hard stop.
- At-least-one-mode exit gate respects pre-existing files.
- Cancel at any prompt aborts the current mode only.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from nova.setup.mode_wizard import run_mode_wizard_step


@pytest.fixture(autouse=True)
def _fake_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``sys.stdin.isatty()`` True so the step runs under pytest."""
    monkeypatch.setattr("nova.setup.mode_wizard.sys.stdin.isatty", lambda: True)


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """Empty data directory — no pre-existing modes."""
    return tmp_path


@pytest.fixture()
def template_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stub ``_locate_shipped_templates`` to point at an isolated templates dir."""
    templates = tmp_path / "fake_repo_templates"
    templates.mkdir()
    monkeypatch.setattr("nova.setup.mode_wizard._locate_shipped_templates", lambda: templates)
    return templates


def _make_console(*inputs: str) -> MagicMock:
    """Mock Console whose ``input()`` returns values in sequence and buffers ``print()``."""
    console = MagicMock()
    console.input = MagicMock(side_effect=list(inputs))
    return console


def _all_printed(console: MagicMock) -> list[str]:
    """Flatten every console.print call-arg into a list of strings for assertions."""
    printed: list[str] = []
    for call in console.print.call_args_list:
        for arg in call.args:
            printed.append(str(arg))
    return printed


# ---------------------------------------------------------------------------
# Template accept-as-is → Path A (verbatim copy)
# ---------------------------------------------------------------------------


class TestTemplateAcceptAsIs:
    """Accept-as-is routes through copy_template_verbatim."""

    def test_accept_calls_path_a_not_path_b(self, data_dir: Path, template_dir: Path) -> None:
        # Write a template with distinctive comments that Path B would lose
        src = template_dir / "coding.yaml"
        src.write_bytes(
            b"# COMMENT MARKER\n"
            b"name: coding\n"
            b"apps:\n"
            b"  - name: VS Code\n"
            b"    executable: code\n"
            b"    args: []\n"
        )

        console = _make_console(
            "accept",  # accept this template
            "n",  # no custom mode
        )

        with (
            patch("nova.setup.mode_wizard.copy_template_verbatim") as mock_a,
            patch("nova.setup.mode_wizard.write_mode_file") as mock_b,
        ):
            # Have the mocked Path A actually copy so the exit-gate sees a mode
            def _really_copy(s: Path, t: Path) -> None:
                if not t.exists():
                    t.parent.mkdir(parents=True, exist_ok=True)
                    t.write_bytes(s.read_bytes())

            mock_a.side_effect = _really_copy
            run_mode_wizard_step(console, data_dir)

        mock_a.assert_called_once()
        mock_b.assert_not_called()

    def test_accept_preserves_comments(self, data_dir: Path, template_dir: Path) -> None:
        src = template_dir / "coding.yaml"
        source_bytes = (
            b"# preserved comment\n"
            b"name: coding\n"
            b"apps:\n"
            b"  - name: VS Code\n"
            b"    executable: code\n"
            b"    args: []\n"
        )
        src.write_bytes(source_bytes)

        console = _make_console("accept", "n")
        run_mode_wizard_step(console, data_dir)

        target = data_dir / "modes" / "coding.yaml"
        assert target.read_bytes() == source_bytes


# ---------------------------------------------------------------------------
# Template skip path
# ---------------------------------------------------------------------------


class TestTemplateSkip:
    """Skip does not call either writer."""

    def test_skip_writes_nothing(self, data_dir: Path, template_dir: Path) -> None:
        src = template_dir / "coding.yaml"
        src.write_text("name: coding\napps:\n  - name: X\n    executable: x\n")

        console = _make_console(
            "skip",  # skip template
            "y",  # create custom so exit-gate passes
            "mymode",  # mode name
            "VS Code",  # first app
            "done",  # finish apps
            "",  # skip folders
            "",  # skip urls
            "y",  # save
            "n",  # no more custom modes
        )
        run_mode_wizard_step(console, data_dir)

        # Template not installed; custom mode written
        assert not (data_dir / "modes" / "coding.yaml").exists()
        assert (data_dir / "modes" / "mymode.yaml").exists()


# ---------------------------------------------------------------------------
# Template modify → Path B (schema writer)
# ---------------------------------------------------------------------------


class TestTemplateModify:
    """Modify routes through write_mode_file, not copy_template_verbatim."""

    def test_modify_calls_path_b_not_path_a(self, data_dir: Path, template_dir: Path) -> None:
        src = template_dir / "coding.yaml"
        src.write_text(
            "name: coding\napps:\n  - name: VS Code\n    executable: code\n",
            encoding="utf-8",
        )

        console = _make_console(
            "modify",  # modify this template
            "",  # keep default name
            "Chrome",  # first app
            "done",
            "",  # skip folders
            "",  # skip urls
            "y",  # save
            "n",  # no more custom modes
        )

        with (
            patch("nova.setup.mode_wizard.copy_template_verbatim") as mock_a,
            patch(
                "nova.setup.mode_wizard.write_mode_file",
                side_effect=lambda d, s, data: (d / f"{s}.yaml").write_text(
                    yaml.safe_dump(data), encoding="utf-8"
                ),
            ) as mock_b,
        ):
            run_mode_wizard_step(console, data_dir)

        mock_a.assert_not_called()
        mock_b.assert_called_once()

    def test_modify_seeds_with_template_apps_folders_urls(
        self, data_dir: Path, template_dir: Path
    ) -> None:
        """AC #3a — template values are presented as editable fields.

        The template ships with VS Code; the user types ``done``
        immediately on the apps prompt and skips folders and URLs.
        The resulting mode file should contain the template's VS Code
        app, not an empty list.
        """
        src = template_dir / "coding.yaml"
        src.write_text(
            "name: coding\n"
            "apps:\n"
            "  - name: VS Code\n"
            "    executable: code\n"
            "    args: []\n"
            "folders:\n"
            "  - C:\\Projects\n"
            "urls:\n"
            "  - https://example.com\n",
            encoding="utf-8",
        )

        console = _make_console(
            "modify",  # modify this template
            "",  # keep default name
            "done",  # keep the seeded apps (VS Code)
            "done",  # keep the seeded folders
            "done",  # keep the seeded urls
            "y",  # save
            "n",  # no more custom modes
        )
        run_mode_wizard_step(console, data_dir)

        target = data_dir / "modes" / "coding.yaml"
        assert target.exists()
        parsed = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert parsed["apps"] == [{"name": "VS Code", "executable": "code", "args": []}]
        assert parsed["folders"] == ["C:\\Projects"]
        assert parsed["urls"] == ["https://example.com"]

    def test_modify_edits_users_current_file_not_shipped_template(
        self, data_dir: Path, template_dir: Path
    ) -> None:
        """When the target file exists, modify edits THAT file — not the shipped template.

        Regression guard for the "modify on already-installed = edits
        shipped defaults" bug: user had customized their mode file
        (removed Chrome, added Slack); the shipped template has only
        VS Code. Picking "modify" must seed from the user's current
        file (Slack), not the shipped template (VS Code).
        """
        # Shipped template has a different app set than the user's file
        src = template_dir / "coding.yaml"
        src.write_text(
            "name: coding\napps:\n  - name: VS Code\n    executable: code\n    args: []\n",
            encoding="utf-8",
        )
        # User's current file has Slack (not in the template)
        modes_dir = data_dir / "modes"
        modes_dir.mkdir()
        (modes_dir / "coding.yaml").write_text(
            "name: coding\n"
            "apps:\n"
            "  - name: Slack\n"
            "    executable: slack\n"
            "    args: []\n"
            "folders: []\n"
            "urls: []\n"
            "is_default: false\n",
            encoding="utf-8",
        )

        console = _make_console(
            "modify",  # template shows as already installed
            "",  # keep default name
            "done",  # keep seeded apps (must be Slack, not VS Code)
            "done",  # keep seeded folders
            "done",  # keep seeded urls
            "y",  # save
            "y",  # confirm overwrite
            "n",  # no more custom modes
        )
        run_mode_wizard_step(console, data_dir)

        result = yaml.safe_load((modes_dir / "coding.yaml").read_text(encoding="utf-8"))
        # The user's Slack is preserved; VS Code from the template is NOT written
        assert result["apps"] == [{"name": "Slack", "executable": "slack", "args": []}], (
            f"Expected Slack (user's current app), got {result['apps']!r} — "
            "the modify path is seeding from shipped template instead of user file"
        )

    def test_modify_preserves_is_default_from_user_file(
        self, data_dir: Path, template_dir: Path
    ) -> None:
        """Editing a mode with is_default=true must keep that flag, not reset to false."""
        src = template_dir / "coding.yaml"
        src.write_text(
            "name: coding\napps:\n  - name: VS Code\n    executable: code\n    args: []\n"
            "is_default: false\n",
            encoding="utf-8",
        )
        # User's file is marked default
        modes_dir = data_dir / "modes"
        modes_dir.mkdir()
        (modes_dir / "coding.yaml").write_text(
            "name: coding\n"
            "apps:\n"
            "  - name: VS Code\n"
            "    executable: code\n"
            "    args: []\n"
            "folders: []\n"
            "urls: []\n"
            "is_default: true\n",
            encoding="utf-8",
        )

        console = _make_console(
            "modify",
            "",  # keep name
            "done",  # keep seeded apps
            "done",  # keep folders
            "done",  # keep urls
            "y",  # save
            "y",  # confirm overwrite
            "n",  # no more
        )
        run_mode_wizard_step(console, data_dir)

        result = yaml.safe_load((modes_dir / "coding.yaml").read_text(encoding="utf-8"))
        assert result["is_default"] is True, (
            f"Expected is_default=True preserved, got {result.get('is_default')!r}"
        )

    def test_modify_preserves_is_default_true_in_shipped_template(
        self, data_dir: Path, template_dir: Path
    ) -> None:
        """For a not-yet-installed template with is_default=true, that flag survives modify."""
        src = template_dir / "coding.yaml"
        src.write_text(
            "name: coding\napps:\n  - name: VS Code\n    executable: code\n    args: []\n"
            "is_default: true\n",
            encoding="utf-8",
        )
        # Target does NOT exist — modify seeds from shipped template

        console = _make_console(
            "modify",
            "",  # keep name
            "done",  # keep seeded apps
            "done",  # keep folders
            "done",  # keep urls
            "y",  # save
            "n",  # no more
        )
        run_mode_wizard_step(console, data_dir)

        result = yaml.safe_load((data_dir / "modes" / "coding.yaml").read_text(encoding="utf-8"))
        assert result["is_default"] is True

    def test_modify_requires_confirmation_to_overwrite(
        self, data_dir: Path, template_dir: Path
    ) -> None:
        """Data-loss guard: declining the overwrite confirmation leaves the file untouched."""
        src = template_dir / "coding.yaml"
        src.write_text(
            "name: coding\napps:\n  - name: VS Code\n    executable: code\n    args: []\n",
            encoding="utf-8",
        )
        # Simulate a prior accepted template that the user hand-edited
        modes_dir = data_dir / "modes"
        modes_dir.mkdir()
        user_edit = (
            b"# user-edited file\n"
            b"name: coding\n"
            b"apps:\n"
            b"  - name: Chrome\n"
            b"    executable: chrome\n"
            b"    args: []\n"
        )
        (modes_dir / "coding.yaml").write_bytes(user_edit)

        console = _make_console(
            "modify",  # the template shows as "already installed"
            "",  # keep default name
            "done",  # keep seeded apps
            "done",  # keep seeded folders
            "done",  # keep seeded urls
            "y",  # confirm save
            "n",  # but decline the overwrite confirmation
            "n",  # no more custom modes
        )
        run_mode_wizard_step(console, data_dir)

        # User's hand-edits preserved byte-for-byte
        assert (modes_dir / "coding.yaml").read_bytes() == user_edit
        printed = "\n".join(_all_printed(console))
        assert "already exists" in printed
        # "Overwrite ...?" is a confirm prompt passed to console.input
        input_prompts = [str(a) for c in console.input.call_args_list for a in c.args]
        assert any("Overwrite" in p for p in input_prompts), (
            f"Expected 'Overwrite' prompt, got: {input_prompts}"
        )


# ---------------------------------------------------------------------------
# Custom mode happy path
# ---------------------------------------------------------------------------


class TestCustomModeHappyPath:
    """Custom mode with one app writes successfully."""

    def test_custom_mode_one_app_writes_yaml(self, data_dir: Path, template_dir: Path) -> None:
        # No templates so the flow goes straight to custom-mode offer
        console = _make_console(
            "y",  # create custom
            "study group",  # mode name (will slugify to "study-group")
            "Notion",  # first app
            "done",
            "",  # skip folders
            "",  # skip urls
            "y",  # save
            "n",  # no more
        )
        run_mode_wizard_step(console, data_dir)

        target = data_dir / "modes" / "study-group.yaml"
        assert target.exists()
        parsed = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert parsed["name"] == "study group"
        assert parsed["apps"] == [{"name": "Notion", "executable": "notion", "args": []}]
        assert parsed["folders"] == []
        assert parsed["urls"] == []
        assert parsed["is_default"] is False


# ---------------------------------------------------------------------------
# Zero-apps hard stop (critical contract)
# ---------------------------------------------------------------------------


class TestZeroAppsHardStop:
    """The custom-mode flow must refuse to write a mode with zero apps."""

    def test_done_before_any_app_reprompts(self, data_dir: Path, template_dir: Path) -> None:
        console = _make_console(
            "y",  # create custom
            "coding",  # name
            "done",  # try to finish with 0 apps — must re-prompt
            "VS Code",  # now add one
            "done",
            "",  # folders
            "",  # urls
            "y",  # save
            "n",  # no more
        )
        run_mode_wizard_step(console, data_dir)

        target = data_dir / "modes" / "coding.yaml"
        assert target.exists()
        parsed = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert len(parsed["apps"]) == 1

        # Message was shown
        printed = "\n".join(_all_printed(console))
        assert "at least one app" in printed

    def test_writer_never_called_with_zero_apps(self, data_dir: Path, template_dir: Path) -> None:
        """Confirm the schema writer is unreachable with an empty apps list.

        AC #11 keeps the gate active, so we escape via KeyboardInterrupt
        after proving the zero-apps path never reached the writer.
        """
        call_count = {"n": 0}

        def _side_effect(*_a: object, **_k: object) -> str:
            call_count["n"] += 1
            script = ["y", "coding", "done", "cancel", "n"]
            if call_count["n"] <= len(script):
                return script[call_count["n"] - 1]
            raise KeyboardInterrupt

        console = MagicMock()
        console.input = MagicMock(side_effect=_side_effect)

        with patch("nova.setup.mode_wizard.write_mode_file") as mock_b:
            run_mode_wizard_step(console, data_dir)

        mock_b.assert_not_called()


# ---------------------------------------------------------------------------
# Cancel within custom flow
# ---------------------------------------------------------------------------


class TestCustomModeCancel:
    """Cancel at any prompt aborts the current mode only.

    Since AC #11 forbids exiting with zero modes, these tests supply a
    follow-up valid mode so the gate is satisfied; the assertions focus
    on the cancelled mode's absence, not the gate's escape behavior
    (see ``TestExitGate`` for the gate contract).
    """

    def test_cancel_at_name_aborts_mode(self, data_dir: Path, template_dir: Path) -> None:
        console = _make_console(
            "y",  # create custom
            "cancel",  # cancel at name — this mode abandoned
            "n",  # no more custom offers
            # Gate fires; supply a valid mode so we can exit cleanly
            "later",  # name
            "Chrome",
            "done",
            "",
            "",
            "y",  # save
        )
        run_mode_wizard_step(console, data_dir)

        modes_dir = data_dir / "modes"
        # Only the "later" mode exists; the cancelled one was never named
        assert {p.name for p in modes_dir.iterdir()} == {"later.yaml"}

    def test_cancel_at_apps_aborts_mode(self, data_dir: Path, template_dir: Path) -> None:
        console = _make_console(
            "y",  # create custom
            "coding",  # name
            "cancel",  # cancel inside apps loop — coding abandoned
            "n",  # no further offers
            # Gate: supply a different valid mode
            "work",
            "Chrome",
            "done",
            "",
            "",
            "y",
        )
        run_mode_wizard_step(console, data_dir)

        modes_dir = data_dir / "modes"
        # Coding never got written; work did
        assert not (modes_dir / "coding.yaml").exists()
        assert (modes_dir / "work.yaml").exists()


# ---------------------------------------------------------------------------
# Exit-gate: at-least-one-mode by exit
# ---------------------------------------------------------------------------


class TestExitGate:
    """The gate respects pre-existing mode files and blocks empty exits."""

    def test_preexisting_mode_satisfies_gate(self, data_dir: Path, template_dir: Path) -> None:
        """If setup.bat pre-copied a mode, the user can skip everything and still exit."""
        # Pre-populate the user data modes/ directory
        modes_dir = data_dir / "modes"
        modes_dir.mkdir()
        (modes_dir / "coding.yaml").write_text(
            "name: coding\napps:\n  - name: VS Code\n    executable: code\n",
            encoding="utf-8",
        )

        # Template loader sees the file as "already installed"
        src = template_dir / "coding.yaml"
        src.write_text(
            "name: coding\napps:\n  - name: VS Code\n    executable: code\n",
            encoding="utf-8",
        )

        console = _make_console(
            "skip",  # skip the already-installed template
            "n",  # no custom modes
            # No exit-gate prompt here — pre-existing mode satisfies it
        )
        run_mode_wizard_step(console, data_dir)

        assert (modes_dir / "coding.yaml").exists()
        # No error about "at least one workspace mode"
        printed = "\n".join(_all_printed(console))
        assert "At least one workspace mode is required" not in printed

    def test_empty_modes_dir_blocks_exit_until_mode_created(
        self, data_dir: Path, template_dir: Path
    ) -> None:
        """With zero modes, the gate MUST loop until a valid mode exists.

        AC #11 contract: "at least one mode must exist ... for the wizard
        to exit cleanly". Declining is not an exit path — the only
        escape is Ctrl+C / EOF. This test proves the gate re-enters
        custom-mode creation on every iteration where no valid mode is
        present, then exits cleanly once one is written.
        """
        console = _make_console(
            "n",  # decline the initial custom-mode offer
            # First gate iteration: user tries to cancel
            "cancel",  # cancel at the name prompt → mode NOT created
            # Second gate iteration: user cancels again
            "cancel",
            # Third gate iteration: user actually creates a mode
            "finally",  # name
            "Chrome",  # first app
            "done",
            "",  # skip folders
            "",  # skip urls
            "y",  # save
        )
        run_mode_wizard_step(console, data_dir)

        # The mode was created — exit was only reachable after this
        assert (data_dir / "modes" / "finally.yaml").exists()

        # Gate warning surfaced at least once per failed iteration
        printed = "\n".join(_all_printed(console))
        assert printed.count("At least one workspace mode is required") >= 2

    def test_gate_has_no_decline_escape(self, data_dir: Path, template_dir: Path) -> None:
        """The gate never prompts ``Create a mode now? [y/n]`` — declining is not an option.

        Regression guard against the pre-fix contract mismatch: the
        gate previously allowed the user to answer ``n`` and exit with
        zero modes after a warning. That violated AC #11.
        """
        # Supply one mode so we reach the gate path exactly once,
        # then verify the gate's output never offered a y/n escape.
        console = _make_console(
            "n",  # decline initial custom offer (no templates to accept)
            # Single gate iteration: create a valid mode immediately
            "solo",  # name
            "Chrome",
            "done",
            "",
            "",
            "y",  # save
        )
        run_mode_wizard_step(console, data_dir)

        printed = "\n".join(_all_printed(console))
        # The banned escape prompt must never appear
        assert "Create a mode now? [y/n]" not in printed
        # But the mode was created and the gate cleared
        assert (data_dir / "modes" / "solo.yaml").exists()

    def test_gate_exits_cleanly_on_keyboard_interrupt(
        self, data_dir: Path, template_dir: Path
    ) -> None:
        """Ctrl+C during the gate loop is the only clean escape path."""
        call_count = {"n": 0}

        def _side_effect(*_a: object, **_k: object) -> str:
            call_count["n"] += 1
            # Decline initial offer, then raise inside the gate loop
            if call_count["n"] == 1:
                return "n"
            raise KeyboardInterrupt

        console = MagicMock()
        console.input = MagicMock(side_effect=_side_effect)

        # Must not raise — outer handler translates KeyboardInterrupt to notice
        run_mode_wizard_step(console, data_dir)

        printed = "\n".join(str(a) for c in console.print.call_args_list for a in c.args)
        assert "interrupted" in printed.lower()


# ---------------------------------------------------------------------------
# UX compliance
# ---------------------------------------------------------------------------


class TestUxVoice:
    """Wizard output avoids emoji and sycophantic framing."""

    def test_no_sycophantic_framing(self, data_dir: Path, template_dir: Path) -> None:
        # Decline the offer, then supply a valid mode so the gate clears
        console = _make_console(
            "n",  # decline initial custom offer
            "demo",  # mode name at gate
            "Chrome",
            "done",
            "",
            "",
            "y",  # save
        )
        run_mode_wizard_step(console, data_dir)

        printed = "\n".join(_all_printed(console))
        for banned in [
            "How can I help you today",
            "I'd be happy to",
            "Great question",
            "Great!",
        ]:
            assert banned not in printed, f"Found banned phrase: {banned!r}"

    def test_uses_semantic_symbols_only(self, data_dir: Path, template_dir: Path) -> None:
        """Symbol set restricted to ✓✗⚠ — no emoji."""
        console = _make_console(
            "y",
            "test",
            "NonexistentMysteryApp",  # produces unresolvable-app warning ⚠
            "done",
            "",
            "",
            "y",
            "n",
        )
        with patch("nova.setup.mode_wizard.shutil.which", return_value=None):
            run_mode_wizard_step(console, data_dir)

        printed = "\n".join(_all_printed(console))
        # Whitelist the specific non-ASCII codepoints the wizard is
        # allowed to emit. Everything else above ASCII is treated as a
        # potential emoji / unexpected glyph and fails the test.
        # Allowed: ✓ ✗ ⚠ (AC #14 semantic symbols) and — (em-dash).
        allowed_non_ascii = {"\u2713", "\u2717", "\u26a0", "\u2014"}
        for ch in printed:
            if ord(ch) < 0x80:  # ASCII range — always fine
                continue
            if ch in allowed_non_ascii:
                continue
            raise AssertionError(
                f"Disallowed non-ASCII codepoint {hex(ord(ch))} ({ch!r}) found "
                f"in wizard output. Whitelist is {sorted(allowed_non_ascii)}."
            )


# ---------------------------------------------------------------------------
# Non-TTY skip
# ---------------------------------------------------------------------------


def test_non_tty_skips_wizard(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-TTY stdin → skip with notice, no prompts issued."""
    monkeypatch.setattr("nova.setup.mode_wizard.sys.stdin.isatty", lambda: False)
    console = MagicMock()

    run_mode_wizard_step(console, data_dir)

    console.input.assert_not_called()
    printed = "\n".join(str(a) for c in console.print.call_args_list for a in c.args)
    assert "Not running in an interactive terminal" in printed


# ---------------------------------------------------------------------------
# Unresolvable app warning (AC #6)
# ---------------------------------------------------------------------------


def test_unresolvable_app_logs_warning_and_saves_anyway(
    data_dir: Path, template_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown app → warning + save user input as the executable value."""
    monkeypatch.setattr("nova.setup.mode_wizard.shutil.which", lambda _: None)

    console = _make_console(
        "y",
        "weird",
        "MysteryApp",  # not in registry, not on PATH
        "done",
        "",
        "",
        "y",
        "n",
    )
    run_mode_wizard_step(console, data_dir)

    parsed = yaml.safe_load((data_dir / "modes" / "weird.yaml").read_text(encoding="utf-8"))
    # Entry still written — user can fix later
    assert parsed["apps"] == [{"name": "MysteryApp", "executable": "MysteryApp", "args": []}]
    printed = "\n".join(_all_printed(console))
    assert "Couldn't find 'MysteryApp'" in printed


# ---------------------------------------------------------------------------
# Residual verification gaps closed post-review
# ---------------------------------------------------------------------------


class TestWriteModeFileYamlErrorHandled:
    """G1: yaml.YAMLError from write_mode_file does not crash the wizard (AC #9)."""

    def test_custom_mode_yaml_error_surfaces_message(
        self, data_dir: Path, template_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """write_mode_file raising YAMLError surfaces a message, not a traceback."""
        import yaml as _yaml

        def _raise(*_a: object, **_k: object) -> None:
            raise _yaml.YAMLError("simulated YAML dump failure")

        monkeypatch.setattr("nova.setup.mode_wizard.write_mode_file", _raise)

        console = _make_console(
            "y",  # create custom
            "broken",
            "Chrome",
            "done",
            "",  # folders
            "",  # urls
            "y",  # save
            # Writer fails — user must then still satisfy the exit gate.
            # Simulate giving up with KeyboardInterrupt on the next prompt.
        )

        def _side_effect_with_ki(*_a: object, **_k: object) -> str:
            # First N inputs from the scripted list, then KeyboardInterrupt
            raise KeyboardInterrupt

        # Swap in KeyboardInterrupt after scripted inputs exhaust
        original = console.input
        call_count = {"n": 0}

        def _wrapped(*a: object, **k: object) -> str:
            call_count["n"] += 1
            if call_count["n"] <= 7:
                return str(original(*a, **k))
            raise KeyboardInterrupt

        console.input = MagicMock(side_effect=_wrapped)

        # Must not raise yaml.YAMLError to the caller
        run_mode_wizard_step(console, data_dir)

        printed = "\n".join(_all_printed(console))
        assert "Could not write mode file" in printed

    def test_modify_yaml_error_surfaces_message(
        self, data_dir: Path, template_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same handling on the modify path."""
        import yaml as _yaml

        src = template_dir / "coding.yaml"
        src.write_text(
            "name: coding\napps:\n  - name: VS Code\n    executable: code\n    args: []\n",
            encoding="utf-8",
        )

        def _raise(*_a: object, **_k: object) -> None:
            raise _yaml.YAMLError("simulated YAML dump failure")

        monkeypatch.setattr("nova.setup.mode_wizard.write_mode_file", _raise)

        call_count = {"n": 0}
        scripted = ["modify", "", "done", "done", "done", "y"]

        def _side(*_a: object, **_k: object) -> str:
            call_count["n"] += 1
            if call_count["n"] <= len(scripted):
                return scripted[call_count["n"] - 1]
            raise KeyboardInterrupt

        console = MagicMock()
        console.input = MagicMock(side_effect=_side)

        run_mode_wizard_step(console, data_dir)

        printed = "\n".join(str(a) for c in console.print.call_args_list for a in c.args)
        assert "Could not write mode file" in printed


class TestExistingValidModeStemsOsErrorGuard:
    """G2: a transient filesystem failure in the gate probe does not crash."""

    def test_iterdir_oserror_treated_as_zero_modes(
        self, data_dir: Path, template_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If iterdir raises OSError, gate sees zero modes and re-prompts (no traceback)."""
        from nova.setup import mode_wizard

        modes_dir = data_dir / "modes"
        modes_dir.mkdir()

        call_count = {"n": 0}
        original = mode_wizard._existing_valid_mode_stems

        def _flaky(path: Path) -> list[str]:
            call_count["n"] += 1
            # First call (entry scan) works normally.
            # Subsequent calls (gate probes) raise to simulate the race.
            if call_count["n"] == 1:
                return original(path)
            # Raise via the guarded function — must be absorbed
            raise OSError("simulated disappearing modes_dir")

        # Easier: patch iterdir on Path directly
        real_iterdir = Path.iterdir

        def _selective(self: Path) -> object:
            if self == modes_dir:
                raise OSError("simulated disappearing modes_dir")
            return real_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", _selective)

        call_count_input = {"n": 0}

        def _input(*_a: object, **_k: object) -> str:
            call_count_input["n"] += 1
            if call_count_input["n"] == 1:
                return "n"  # decline initial custom-mode offer
            raise KeyboardInterrupt  # escape the gate loop cleanly

        console = MagicMock()
        console.input = MagicMock(side_effect=_input)

        # Must not propagate OSError
        run_mode_wizard_step(console, data_dir)

        printed = "\n".join(str(a) for c in console.print.call_args_list for a in c.args)
        # Gate fired at least once (message contains the requirement text)
        assert "At least one workspace mode is required" in printed


class TestOptionalListKeywords:
    """G3: cancel/skip/done keywords in _collect_optional_list (AC #13, #15)."""

    def test_cancel_at_folders_aborts_custom_mode(self, data_dir: Path, template_dir: Path) -> None:
        console = _make_console(
            "y",  # create custom
            "mymode",
            "Chrome",
            "done",  # finish apps
            "cancel",  # cancel at folders prompt → abort mode
            "n",  # no more custom modes
            # Gate fires — supply a valid mode to exit cleanly
            "exit",
            "Chrome",
            "done",
            "",
            "",
            "y",
        )
        run_mode_wizard_step(console, data_dir)

        modes_dir = data_dir / "modes"
        assert not (modes_dir / "mymode.yaml").exists()
        assert (modes_dir / "exit.yaml").exists()

    def test_cancel_at_urls_aborts_custom_mode(self, data_dir: Path, template_dir: Path) -> None:
        console = _make_console(
            "y",
            "mymode",
            "Chrome",
            "done",  # finish apps
            "",  # skip folders
            "cancel",  # cancel at urls prompt → abort mode
            "n",
            # Gate exit
            "exit",
            "Chrome",
            "done",
            "",
            "",
            "y",
        )
        run_mode_wizard_step(console, data_dir)

        modes_dir = data_dir / "modes"
        assert not (modes_dir / "mymode.yaml").exists()
        assert (modes_dir / "exit.yaml").exists()

    def test_skip_keyword_ends_optional_list(self, data_dir: Path, template_dir: Path) -> None:
        """Explicit 'skip' (not just blank line) ends an optional list."""
        console = _make_console(
            "y",  # create custom
            "mymode",
            "Chrome",
            "done",  # finish apps
            "skip",  # skip folders via keyword
            "skip",  # skip urls via keyword
            "y",
            "n",
        )
        run_mode_wizard_step(console, data_dir)

        parsed = yaml.safe_load((data_dir / "modes" / "mymode.yaml").read_text(encoding="utf-8"))
        assert parsed["folders"] == []
        assert parsed["urls"] == []

    def test_done_keyword_ends_optional_list_with_entries(
        self, data_dir: Path, template_dir: Path
    ) -> None:
        """'done' after entering some folders finalizes the list with those entries."""
        console = _make_console(
            "y",
            "mymode",
            "Chrome",
            "done",  # finish apps
            "C:\\Projects",  # first folder
            "done",  # finish folders (keyword instead of blank line)
            "",  # skip urls
            "y",
            "n",
        )
        run_mode_wizard_step(console, data_dir)

        parsed = yaml.safe_load((data_dir / "modes" / "mymode.yaml").read_text(encoding="utf-8"))
        assert parsed["folders"] == ["C:\\Projects"]


class TestLocateShippedTemplatesAnchor:
    """G4: _locate_shipped_templates requires pyproject.toml sibling (P7)."""

    def test_returns_none_when_no_pyproject_anchor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A config/modes/ directory without a pyproject.toml sibling must not be selected."""
        from nova.setup import mode_wizard

        # Fake install location: tmp_path/deep/site-packages/nova/setup/mode_wizard.py
        fake_module = tmp_path / "deep" / "site-packages" / "nova" / "setup" / "mode_wizard.py"
        fake_module.parent.mkdir(parents=True)
        fake_module.write_text("# fake", encoding="utf-8")

        # Create an adversarial config/modes in an ancestor WITHOUT a pyproject.toml
        adversarial = tmp_path / "config" / "modes"
        adversarial.mkdir(parents=True)
        (adversarial / "evil.yaml").write_text(
            "name: evil\napps:\n  - name: X\n    executable: x\n"
        )

        monkeypatch.setattr(
            "nova.setup.mode_wizard.Path",
            Path,  # ensure we haven't patched Path globally
        )
        # Fake __file__ resolution
        monkeypatch.setattr(
            mode_wizard,
            "__file__",
            str(fake_module),
        )

        # Should return None because no ancestor has pyproject.toml + config/modes
        assert mode_wizard._locate_shipped_templates() is None

    def test_returns_dir_when_pyproject_anchor_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When a pyproject.toml sits next to config/modes, the dir is selected."""
        from nova.setup import mode_wizard

        # Fake install: tmp_path/src/nova/setup/mode_wizard.py
        fake_module = tmp_path / "src" / "nova" / "setup" / "mode_wizard.py"
        fake_module.parent.mkdir(parents=True)
        fake_module.write_text("# fake", encoding="utf-8")

        # Legitimate project root: tmp_path has pyproject.toml AND config/modes/
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
        legitimate = tmp_path / "config" / "modes"
        legitimate.mkdir(parents=True)

        monkeypatch.setattr(mode_wizard, "__file__", str(fake_module))

        result = mode_wizard._locate_shipped_templates()
        assert result == legitimate


class TestOfferAllTemplatesInvalidStemSkip:
    """G5: _offer_all_templates skips templates whose stem is rejected by the loader (P8)."""

    def test_invalid_stem_template_skipped(self, data_dir: Path, template_dir: Path) -> None:
        """Reserved-Windows-stem templates are skipped silently — never offered."""
        # Ship a valid template + a reserved-stem one
        (template_dir / "coding.yaml").write_text(
            "name: coding\napps:\n  - name: VS Code\n    executable: code\n    args: []\n",
            encoding="utf-8",
        )
        # CON is a reserved Windows filename — loader would reject this
        (template_dir / "con.yaml").write_text(
            "name: con\napps:\n  - name: X\n    executable: x\n    args: []\n",
            encoding="utf-8",
        )

        console = _make_console(
            "accept",  # accept coding (the only legitimate offer)
            "n",  # no more
        )
        run_mode_wizard_step(console, data_dir)

        modes_dir = data_dir / "modes"
        assert (modes_dir / "coding.yaml").exists()
        # The reserved-stem template must never have been offered, so the target doesn't exist
        assert not (modes_dir / "con.yaml").exists()


class TestMalformedUserFileModifyFallback:
    """G6: modify on a malformed user file warns explicitly before falling back."""

    def test_malformed_user_file_shows_warning_before_seeding_from_template(
        self, data_dir: Path, template_dir: Path
    ) -> None:
        """User's broken file → clear warning → seed from shipped template (NOT silent)."""
        src = template_dir / "coding.yaml"
        src.write_text(
            "name: coding\napps:\n  - name: VS Code\n    executable: code\n    args: []\n",
            encoding="utf-8",
        )
        modes_dir = data_dir / "modes"
        modes_dir.mkdir()
        # Malformed YAML (unterminated list)
        (modes_dir / "coding.yaml").write_text(
            "name: coding\napps:\n  - [broken\n",
            encoding="utf-8",
        )

        console = _make_console(
            "modify",  # template shows as installed (target exists)
            "",  # keep default name
            "done",  # keep seeded apps (from shipped template since user file is broken)
            "done",  # folders
            "done",  # urls
            "y",  # save
            "y",  # confirm overwrite
            "n",  # no more
        )
        run_mode_wizard_step(console, data_dir)

        printed = "\n".join(_all_printed(console))
        # Warning must name the file and say "could not be parsed"
        assert "could not be parsed" in printed
        assert "shipped template" in printed
        # And the save went through (user confirmed overwrite)
        result = yaml.safe_load((modes_dir / "coding.yaml").read_text(encoding="utf-8"))
        assert result["apps"] == [{"name": "VS Code", "executable": "code", "args": []}]
