"""Integration tests for the first-run setup wizard — API key step.

Story 2.2 AC #30: full configure + skip flows with mocked Anthropic
client and isolated LOCALAPPDATA.

Marked ``@pytest.mark.integration``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from nova.setup.api_key import ValidationOutcome, ValidationResult, run_api_key_step

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _fake_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``sys.stdin.isatty()`` to return True so run_api_key_step runs."""
    monkeypatch.setattr("nova.setup.api_key.sys.stdin.isatty", lambda: True)


@pytest.fixture()
def nova_data_dir(tmp_path: Path) -> Path:
    """Create an isolated nova data directory with shipped defaults."""
    data_dir = tmp_path / "nova"
    data_dir.mkdir()
    (data_dir / "modes").mkdir()
    settings = data_dir / "settings.yaml"
    settings.write_text(
        "bluntness: direct\n"
        "skip_briefing_if_recent: true\n"
        "briefing_recency_threshold_minutes: 60\n",
        encoding="utf-8",
    )
    return data_dir


def _mock_console(*inputs: str) -> MagicMock:
    """Create a mock Console whose ``input()`` returns values in order."""
    console = MagicMock()
    console.input = MagicMock(side_effect=list(inputs))
    return console


class TestConfigureFlow:
    """Full flow: State A → API key prompt → validation → settings.yaml written."""

    @patch("nova.setup.api_key.validate_api_key")
    def test_valid_key_written_to_settings(
        self,
        mock_validate: MagicMock,
        nova_data_dir: Path,
    ) -> None:
        mock_validate.return_value = ValidationOutcome(ValidationResult.SUCCESS)
        console = _mock_console("sk-ant-integration-test-key")

        result = run_api_key_step(console, nova_data_dir)

        assert result is True
        settings = yaml.safe_load((nova_data_dir / "settings.yaml").read_text(encoding="utf-8"))
        assert settings["api_key"] == "sk-ant-integration-test-key"
        assert settings["bluntness"] == "direct"
        assert settings["skip_briefing_if_recent"] is True
        assert settings["briefing_recency_threshold_minutes"] == 60

    @patch("nova.setup.api_key.validate_api_key")
    def test_rate_limited_key_still_written(
        self,
        mock_validate: MagicMock,
        nova_data_dir: Path,
    ) -> None:
        mock_validate.return_value = ValidationOutcome(ValidationResult.RATE_LIMITED)
        console = _mock_console("sk-ant-rate-limited-key")

        result = run_api_key_step(console, nova_data_dir)

        assert result is True
        settings = yaml.safe_load((nova_data_dir / "settings.yaml").read_text(encoding="utf-8"))
        assert settings["api_key"] == "sk-ant-rate-limited-key"

    @patch("nova.setup.api_key.validate_api_key")
    def test_retry_then_success(
        self,
        mock_validate: MagicMock,
        nova_data_dir: Path,
    ) -> None:
        mock_validate.side_effect = [
            ValidationOutcome(ValidationResult.AUTH_FAILED),
            ValidationOutcome(ValidationResult.NETWORK_ERROR),
            ValidationOutcome(ValidationResult.SUCCESS),
        ]
        console = _mock_console("bad1", "bad2", "sk-ant-good")

        result = run_api_key_step(console, nova_data_dir)

        assert result is True
        settings = yaml.safe_load((nova_data_dir / "settings.yaml").read_text(encoding="utf-8"))
        assert settings["api_key"] == "sk-ant-good"


class TestSkipFlow:
    """Skip flow: settings.yaml unchanged (no api_key field added)."""

    @patch("nova.setup.api_key.validate_api_key")
    def test_explicit_skip_leaves_settings_unchanged(
        self,
        mock_validate: MagicMock,
        nova_data_dir: Path,
    ) -> None:
        console = _mock_console("skip")

        result = run_api_key_step(console, nova_data_dir)

        assert result is False
        settings = yaml.safe_load((nova_data_dir / "settings.yaml").read_text(encoding="utf-8"))
        assert "api_key" not in settings
        mock_validate.assert_not_called()

    @patch("nova.setup.api_key.validate_api_key")
    def test_three_failures_leaves_settings_unchanged(
        self,
        mock_validate: MagicMock,
        nova_data_dir: Path,
    ) -> None:
        mock_validate.return_value = ValidationOutcome(ValidationResult.AUTH_FAILED)
        console = _mock_console("bad1", "bad2", "bad3")

        result = run_api_key_step(console, nova_data_dir)

        assert result is False
        settings = yaml.safe_load((nova_data_dir / "settings.yaml").read_text(encoding="utf-8"))
        assert "api_key" not in settings

    @patch("nova.setup.api_key.validate_api_key")
    def test_double_empty_enter_leaves_settings_unchanged(
        self,
        mock_validate: MagicMock,
        nova_data_dir: Path,
    ) -> None:
        console = _mock_console("", "")

        result = run_api_key_step(console, nova_data_dir)

        assert result is False
        settings = yaml.safe_load((nova_data_dir / "settings.yaml").read_text(encoding="utf-8"))
        assert "api_key" not in settings


class TestKeyNeverExposed:
    """The API key must not appear in any console output."""

    @patch("nova.setup.api_key.validate_api_key")
    def test_key_not_in_any_console_output(
        self,
        mock_validate: MagicMock,
        nova_data_dir: Path,
    ) -> None:
        test_key = "sk-ant-super-secret-integration"
        mock_validate.return_value = ValidationOutcome(ValidationResult.SUCCESS)
        console = _mock_console(test_key)

        run_api_key_step(console, nova_data_dir)

        for call_obj in console.print.call_args_list:
            for arg in call_obj.args:
                assert test_key not in str(arg)


class TestFullWiringThroughMain:
    """End-to-end integration covering AC #30: main() wires State A → wizard.

    These tests go through ``nova.setup.__main__.main([])`` — the real
    entrypoint — exercising:

    - State A rendering
    - ``_resolve_data_dir()`` reading ``LOCALAPPDATA``
    - ``run_api_key_step`` invocation with the resolved path
    - Exit code = 0 on both configure and skip paths

    Only the network boundary (``validate_api_key``) and the
    interactive input (``Console.input``) are mocked — everything
    else runs for real, including the settings.yaml round-trip.
    """

    @patch("nova.setup.api_key.validate_api_key")
    def test_main_configures_key_end_to_end(
        self,
        mock_validate: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """main([]) → State A → prompt → validation → settings.yaml written → exit 0."""
        from nova.setup.__main__ import EXIT_OK, main

        # Arrange: isolated LOCALAPPDATA with real settings.yaml
        nova_dir = tmp_path / "nova"
        nova_dir.mkdir()
        (nova_dir / "settings.yaml").write_text(
            "bluntness: direct\n"
            "skip_briefing_if_recent: true\n"
            "briefing_recency_threshold_minutes: 60\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        # Network boundary: validation succeeds
        mock_validate.return_value = ValidationOutcome(ValidationResult.SUCCESS)

        # Interactive input: user provides a key on the first prompt
        monkeypatch.setattr("nova.setup.api_key.sys.stdin.isatty", lambda: True)
        monkeypatch.setattr(
            "rich.console.Console.input",
            lambda self, *_args, **_kwargs: "sk-ant-e2e-real-wiring",
        )
        # Mode wizard is out of scope for this Story 2.2 test — mock to no-op.
        monkeypatch.setattr(
            "nova.setup.__main__.run_mode_wizard_step",
            lambda *_a, **_k: None,
        )

        # Act
        exit_code = main([])

        # Assert: exit code, State A rendered, key persisted
        assert exit_code == EXIT_OK
        out = capsys.readouterr().out
        assert "N.O.V.A." in out
        assert "First session. No history yet." in out

        settings = yaml.safe_load((nova_dir / "settings.yaml").read_text(encoding="utf-8"))
        assert settings["api_key"] == "sk-ant-e2e-real-wiring"
        # Other fields preserved
        assert settings["bluntness"] == "direct"

    @patch("nova.setup.api_key.validate_api_key")
    def test_main_skips_key_end_to_end(
        self,
        mock_validate: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """main([]) with 'skip' input → State A renders → no key written → exit 0."""
        from nova.setup.__main__ import EXIT_OK, main

        nova_dir = tmp_path / "nova"
        nova_dir.mkdir()
        (nova_dir / "settings.yaml").write_text("bluntness: direct\n", encoding="utf-8")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        monkeypatch.setattr("nova.setup.api_key.sys.stdin.isatty", lambda: True)
        monkeypatch.setattr(
            "rich.console.Console.input",
            lambda self, *_args, **_kwargs: "skip",
        )
        # Mode wizard is out of scope for this test — mock it to a no-op.
        monkeypatch.setattr(
            "nova.setup.__main__.run_mode_wizard_step",
            lambda *_a, **_k: None,
        )

        exit_code = main([])

        assert exit_code == EXIT_OK
        out = capsys.readouterr().out
        assert "N.O.V.A." in out  # State A did render

        settings = yaml.safe_load((nova_dir / "settings.yaml").read_text(encoding="utf-8"))
        assert "api_key" not in settings
        mock_validate.assert_not_called()


# ---------------------------------------------------------------------------
# Story 2.3: Full wiring through main() — State A → API key → mode wizard
# ---------------------------------------------------------------------------


class TestModeWizardWiring:
    """main() reaches the mode wizard and writes a mode file end-to-end (AC #17, #24)."""

    @patch("nova.setup.api_key.validate_api_key")
    def test_main_runs_full_setup_and_writes_mode_from_template(
        self,
        mock_validate: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """User configures API key then accepts the shipped coding template."""
        from nova.setup.__main__ import EXIT_OK, main

        nova_dir = tmp_path / "nova"
        nova_dir.mkdir()
        (nova_dir / "settings.yaml").write_text("bluntness: direct\n", encoding="utf-8")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        # Point the template locator at an isolated source dir with one template
        templates = tmp_path / "fake_templates"
        templates.mkdir()
        (templates / "coding.yaml").write_text(
            "name: coding\napps:\n  - name: VS Code\n    executable: code\n    args: []\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "nova.setup.mode_wizard._locate_shipped_templates",
            lambda: templates,
        )

        mock_validate.return_value = ValidationOutcome(ValidationResult.SUCCESS)
        monkeypatch.setattr("nova.setup.api_key.sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("nova.setup.mode_wizard.sys.stdin.isatty", lambda: True)

        scripted_inputs = iter(
            [
                "sk-ant-integration",  # API key
                "accept",  # accept the coding template
                "n",  # no additional custom mode
            ]
        )
        monkeypatch.setattr(
            "rich.console.Console.input",
            lambda self, *_args, **_kwargs: next(scripted_inputs),
        )

        exit_code = main([])

        assert exit_code == EXIT_OK

        # API key persisted
        settings = yaml.safe_load((nova_dir / "settings.yaml").read_text(encoding="utf-8"))
        assert settings["api_key"] == "sk-ant-integration"

        # Mode file persisted (verbatim — Path A)
        modes_dir = nova_dir / "modes"
        assert (modes_dir / "coding.yaml").exists()
        written = (modes_dir / "coding.yaml").read_text(encoding="utf-8")
        assert "name: coding" in written
        assert "executable: code" in written

    @patch("nova.setup.api_key.validate_api_key")
    def test_main_writes_custom_mode_when_no_templates(
        self,
        mock_validate: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No templates available → custom mode path exercised end-to-end."""
        from nova.setup.__main__ import EXIT_OK, main

        nova_dir = tmp_path / "nova"
        nova_dir.mkdir()
        (nova_dir / "settings.yaml").write_text("bluntness: direct\n", encoding="utf-8")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        # No templates directory
        monkeypatch.setattr("nova.setup.mode_wizard._locate_shipped_templates", lambda: None)

        mock_validate.return_value = ValidationOutcome(ValidationResult.SUCCESS)
        monkeypatch.setattr("nova.setup.api_key.sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("nova.setup.mode_wizard.sys.stdin.isatty", lambda: True)

        scripted_inputs = iter(
            [
                "sk-ant-integration",  # API key
                "y",  # yes, create custom mode
                "study",  # mode name
                "Notion",  # first app
                "done",  # finish apps
                "",  # skip folders
                "",  # skip urls
                "y",  # save
                "n",  # no more custom modes
            ]
        )
        monkeypatch.setattr(
            "rich.console.Console.input",
            lambda self, *_args, **_kwargs: next(scripted_inputs),
        )

        exit_code = main([])

        assert exit_code == EXIT_OK
        mode_file = nova_dir / "modes" / "study.yaml"
        assert mode_file.exists()
        parsed = yaml.safe_load(mode_file.read_text(encoding="utf-8"))
        assert parsed["name"] == "study"
        assert parsed["apps"] == [{"name": "Notion", "executable": "notion", "args": []}]
