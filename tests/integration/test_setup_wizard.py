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

        # Mode wizard is out of scope for this Story 2.2 test — replace
        # it with a stub that plants a valid mode file so Story 2.4's
        # "at least one mode by exit" guard doesn't trip.
        def _stub_wizard_with_mode(_console: object, data_dir: Path) -> None:
            modes_dir = data_dir / "modes"
            modes_dir.mkdir(exist_ok=True)
            (modes_dir / "coding.yaml").write_text(
                "name: coding\napps:\n  - name: VS Code\n    executable: code\n    args: []\n",
                encoding="utf-8",
            )

        monkeypatch.setattr(
            "nova.setup.__main__.run_mode_wizard_step",
            _stub_wizard_with_mode,
        )

        # Act
        exit_code = main([])

        # Assert: exit code, State A rendered, key persisted
        assert exit_code == EXIT_OK
        out = capsys.readouterr().out
        assert "N.O.V.A." in out
        assert "First session. No history yet — that's expected." in out

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

        # Mode wizard is out of scope for this test — stub it with a
        # minimal valid mode file so Story 2.4's zero-modes guard doesn't
        # trip (the test is about API-key skip, not mode creation).
        def _stub_wizard_with_mode(_console: object, data_dir: Path) -> None:
            modes_dir = data_dir / "modes"
            modes_dir.mkdir(exist_ok=True)
            (modes_dir / "coding.yaml").write_text(
                "name: coding\napps:\n  - name: VS Code\n    executable: code\n    args: []\n",
                encoding="utf-8",
            )

        monkeypatch.setattr(
            "nova.setup.__main__.run_mode_wizard_step",
            _stub_wizard_with_mode,
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


# ---------------------------------------------------------------------------
# Story 2.4: Initial workspace capture + first-run persistence + completion
# ---------------------------------------------------------------------------


class TestInitialCaptureAndCompletion:
    """End-to-end integration for Story 2.4 AC #34.

    Exercises the full ``main()`` flow including the new capture +
    transactional persistence + completion panel, but always with a
    mocked :func:`capture_initial_workspace` so tests never touch real
    Win32 APIs and run identically on any platform.
    """

    @staticmethod
    def _seed_data_dir(tmp_path: Path) -> Path:
        """Create an isolated nova data dir with settings.yaml + one mode."""
        nova_dir = tmp_path / "nova"
        nova_dir.mkdir()
        (nova_dir / "settings.yaml").write_text(
            "bluntness: direct\n"
            "skip_briefing_if_recent: true\n"
            "briefing_recency_threshold_minutes: 60\n",
            encoding="utf-8",
        )
        modes_dir = nova_dir / "modes"
        modes_dir.mkdir()
        (modes_dir / "coding.yaml").write_text(
            "name: coding\napps:\n  - name: VS Code\n    executable: code\n    args: []\n",
            encoding="utf-8",
        )
        return nova_dir

    @staticmethod
    def _mock_capture(
        monkeypatch: pytest.MonkeyPatch,
        *,
        status: str = "full",
        captured: int = 1,
        dropped: int = 0,
    ) -> None:
        from nova.core.types import SnapshotType
        from nova.setup.initial_capture import CaptureResult
        from nova.systems.eyes.models import WindowContext, WorkspaceSnapshot

        windows = tuple(
            WindowContext(
                app_name=f"app{i}",
                window_title=f"title{i}",
                process_name=f"app{i}",
                is_opaque=False,
            )
            for i in range(captured)
        )
        snapshot = WorkspaceSnapshot(
            captured_at="2026-04-17T12:00:00+00:00",
            snapshot_type=SnapshotType.STARTUP,
            windows=windows,
        )
        result = CaptureResult(
            snapshot=snapshot,
            status=status,  # type: ignore[arg-type]
            windows_captured=captured,
            windows_dropped=dropped,
        )
        monkeypatch.setattr(
            "nova.setup.__main__.capture_initial_workspace",
            lambda: result,
        )

    def _disable_interactive_io(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Wire a no-op Console.input + no-op api-key + wizard so
        ``main([])`` drives straight into the capture/persist pipeline.
        """
        monkeypatch.setattr("nova.setup.__main__.run_api_key_step", lambda *_a, **_k: True)
        monkeypatch.setattr("nova.setup.__main__.run_mode_wizard_step", lambda *_a, **_k: None)

    def test_full_flow_creates_session_snapshot_and_audit_rows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sqlite3

        from nova.setup.__main__ import EXIT_OK, main

        nova_dir = self._seed_data_dir(tmp_path)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        self._disable_interactive_io(monkeypatch)
        self._mock_capture(monkeypatch, status="full", captured=2)

        assert main([]) == EXIT_OK

        with sqlite3.connect(nova_dir / "nova.db") as conn:
            conn.row_factory = sqlite3.Row
            sessions = conn.execute("SELECT * FROM sessions").fetchall()
            snapshots = conn.execute("SELECT * FROM workspace_snapshots").fetchall()
            audits = conn.execute(
                "SELECT * FROM audit_log WHERE action_type = 'setup_complete'"
            ).fetchall()

        assert len(sessions) == 1
        assert len(snapshots) == 1
        assert len(audits) == 1
        assert sessions[0]["is_complete"] == 1
        assert sessions[0]["mode_name"] is None
        assert snapshots[0]["session_id"] == sessions[0]["id"]
        assert audits[0]["target"] is None

    def test_capture_empty_but_setup_still_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json
        import sqlite3

        from nova.setup.__main__ import EXIT_OK, main

        nova_dir = self._seed_data_dir(tmp_path)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        self._disable_interactive_io(monkeypatch)
        self._mock_capture(monkeypatch, status="empty", captured=0)

        assert main([]) == EXIT_OK

        with sqlite3.connect(nova_dir / "nova.db") as conn:
            conn.row_factory = sqlite3.Row
            snap_row = conn.execute("SELECT workspace_data FROM workspace_snapshots").fetchone()
            audit_row = conn.execute(
                "SELECT details FROM audit_log WHERE action_type = 'setup_complete'"
            ).fetchone()

        assert snap_row is not None
        assert json.loads(snap_row["workspace_data"]) == {
            "apps": [],
            "focused_app": None,
            "mode_name": None,
        }
        assert audit_row is not None
        assert json.loads(audit_row["details"])["capture_status"] == "empty"

    def test_capture_partial_threads_status_to_audit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json
        import sqlite3

        from nova.setup.__main__ import EXIT_OK, main

        nova_dir = self._seed_data_dir(tmp_path)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        self._disable_interactive_io(monkeypatch)
        self._mock_capture(monkeypatch, status="partial", captured=2, dropped=1)

        assert main([]) == EXIT_OK

        with sqlite3.connect(nova_dir / "nova.db") as conn:
            conn.row_factory = sqlite3.Row
            details = json.loads(
                conn.execute(
                    "SELECT details FROM audit_log WHERE action_type = 'setup_complete'"
                ).fetchone()["details"]
            )
        assert details["capture_status"] == "partial"

    def test_capture_unavailable_threads_status_to_audit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json
        import sqlite3

        from nova.setup.__main__ import EXIT_OK, main

        nova_dir = self._seed_data_dir(tmp_path)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        self._disable_interactive_io(monkeypatch)
        self._mock_capture(monkeypatch, status="unavailable", captured=0)

        assert main([]) == EXIT_OK

        with sqlite3.connect(nova_dir / "nova.db") as conn:
            conn.row_factory = sqlite3.Row
            details = json.loads(
                conn.execute(
                    "SELECT details FROM audit_log WHERE action_type = 'setup_complete'"
                ).fetchone()["details"]
            )
        assert details["capture_status"] == "unavailable"

    def test_fast_path_exits_without_rewriting_rows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A second ``setup.bat`` run must be idempotent (AC #3)."""
        import sqlite3

        from nova.setup.__main__ import EXIT_OK, main

        self._seed_data_dir(tmp_path)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        self._disable_interactive_io(monkeypatch)
        self._mock_capture(monkeypatch, status="full", captured=1)

        # First run: writes the setup_complete marker.
        assert main([]) == EXIT_OK

        with sqlite3.connect(tmp_path / "nova" / "nova.db") as conn:
            first_sessions = conn.execute("SELECT id FROM sessions").fetchall()
            first_snapshots = conn.execute("SELECT id FROM workspace_snapshots").fetchall()

        # Second run: fast path short-circuits without touching any table.
        assert main([]) == EXIT_OK

        with sqlite3.connect(tmp_path / "nova" / "nova.db") as conn:
            second_sessions = conn.execute("SELECT id FROM sessions").fetchall()
            second_snapshots = conn.execute("SELECT id FROM workspace_snapshots").fetchall()

        assert len(first_sessions) == 1
        assert len(first_snapshots) == 1
        assert len(second_sessions) == 1, "fast path must not write a second session"
        assert len(second_snapshots) == 1, "fast path must not write a second snapshot"

    def test_storage_error_during_persistence_exits_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A simulated StorageError mid-persist produces exit 1 with no traceback."""
        from nova.core.exceptions import StorageError
        from nova.setup.__main__ import EXIT_CONFIG_ERROR, main

        self._seed_data_dir(tmp_path)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        self._disable_interactive_io(monkeypatch)
        self._mock_capture(monkeypatch, status="full", captured=1)

        # Swap the persist helper for one that raises StorageError.
        async def _boom(*_a: object, **_kw: object) -> None:
            raise StorageError("simulated persistence failure")

        monkeypatch.setattr("nova.setup.__main__.persist_first_run", _boom)

        assert main([]) == EXIT_CONFIG_ERROR

    def test_completion_panel_uses_novaconfig_modes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC #18 / test_completion_panel_mode_list_sourced_from_novaconfig."""
        from nova.setup.__main__ import EXIT_OK, main

        nova_dir = self._seed_data_dir(tmp_path)
        # Add a second mode with mixed-case display name to verify sort.
        (nova_dir / "modes" / "research.yaml").write_text(
            "name: Research\napps:\n  - name: Chrome\n    executable: chrome\n    args: []\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        self._disable_interactive_io(monkeypatch)
        self._mock_capture(monkeypatch, status="full", captured=1)

        assert main([]) == EXIT_OK
        out = capsys.readouterr().out
        # Case-insensitive sort: "coding" before "Research".
        assert "coding, Research" in out
        assert "You have 2 modes ready" in out
        assert "uv run nova" in out

    def test_complete_flow_timing_under_8s(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC #23 — regression guard against future bloat."""
        import time

        from nova.setup.__main__ import EXIT_OK, main

        self._seed_data_dir(tmp_path)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        self._disable_interactive_io(monkeypatch)
        self._mock_capture(monkeypatch, status="full", captured=1)

        start = time.perf_counter()
        assert main([]) == EXIT_OK
        elapsed = time.perf_counter() - start
        assert elapsed < 8.0, f"setup took {elapsed:.2f}s — NFR2 regression"

    def test_wizard_skipped_with_zero_modes_does_not_write_setup_complete(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """High-severity guard — skipped/interrupted wizard must NOT lock the user out.

        Simulates the non-interactive or KeyboardInterrupt path where
        ``run_mode_wizard_step`` returns early without running its
        at-least-one-mode gate. The subsequent persistence must refuse
        to write the ``setup_complete`` audit row; otherwise the
        fast-path probe would fire on every future rerun and the user
        could never re-enter the wizard through the normal flow.
        """
        import sqlite3

        from nova.setup.__main__ import EXIT_CONFIG_ERROR, main

        # Isolated nova dir WITHOUT a modes directory populated — mimics
        # the wizard returning early on a non-interactive stdin.
        nova_dir = tmp_path / "nova"
        nova_dir.mkdir()
        (nova_dir / "settings.yaml").write_text("bluntness: direct\n", encoding="utf-8")
        # The modes/ directory may or may not exist; either way it has
        # zero valid mode files.
        (nova_dir / "modes").mkdir()

        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        self._disable_interactive_io(monkeypatch)
        self._mock_capture(monkeypatch, status="full", captured=1)

        assert main([]) == EXIT_CONFIG_ERROR

        # Critical: the guard fires BEFORE create_app — nova.db is
        # therefore not created, the fast-path probe on the next rerun
        # returns False (missing DB file), and the wizard runs again.
        db_path = tmp_path / "nova" / "nova.db"
        assert not db_path.exists(), "zero-modes guard must abort before create_app opens the DB"

        out = capsys.readouterr().out
        assert "no workspace modes configured" in out
        assert "setup.bat" in out
        del sqlite3  # import kept for other tests' reference

    def test_config_error_message_is_generic_not_settings_specific(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Low-severity guard — ConfigError remediation must not misdirect users.

        Previously the message hard-coded ``"Delete settings.yaml"`` for
        every ``ConfigError``. But ``load_config`` can raise for the modes
        directory being a file, an unparseable exclusions.yaml, or an I/O
        error — none of which are fixed by deleting settings.yaml. The
        new message points at the data directory generically and surfaces
        the underlying ``ConfigError`` message verbatim so the user sees
        what actually failed.
        """
        from nova.setup.__main__ import EXIT_CONFIG_ERROR, main

        nova_dir = tmp_path / "nova"
        nova_dir.mkdir()
        # Break config loading by making modes/ a file instead of a dir
        # — ``load_config`` raises ``ConfigError("modes path is not a
        # directory")``.
        (nova_dir / "modes").write_text("not a directory", encoding="utf-8")
        (nova_dir / "settings.yaml").write_text("bluntness: direct\n", encoding="utf-8")

        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        self._disable_interactive_io(monkeypatch)
        self._mock_capture(monkeypatch, status="full", captured=0)

        assert main([]) == EXIT_CONFIG_ERROR
        out = capsys.readouterr().out
        # Surfaces the underlying ConfigError text (non-prescriptive).
        assert "modes path is not a directory" in out
        # Does NOT misdirect users to delete settings.yaml when the
        # problem is unrelated to settings.yaml.
        assert "Delete" not in out or "settings.yaml" not in out
        # Directs to the data directory, not a specific file.
        assert "%LOCALAPPDATA%/nova/" in out

    def test_close_failure_does_not_mask_persist_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Medium-severity guard — close-time exception must not override persist error.

        If ``persist_first_run`` raises ``StorageError`` AND the
        subsequent ``app.close()`` ALSO raises, the user must see the
        original persist failure — not a secondary close-time traceback.
        """
        from nova.core.exceptions import StorageError
        from nova.core.storage.engine import SqliteStorageEngine
        from nova.setup.__main__ import EXIT_CONFIG_ERROR, main

        self._seed_data_dir(tmp_path)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        self._disable_interactive_io(monkeypatch)
        self._mock_capture(monkeypatch, status="full", captured=1)

        real_execute = SqliteStorageEngine.execute
        real_close = SqliteStorageEngine.close

        async def flaky_execute(self: SqliteStorageEngine, sql: str, params: object = ()) -> None:
            if "INSERT INTO audit_log" in sql:
                raise StorageError("primary persist failure")
            await real_execute(self, sql, params)  # type: ignore[arg-type]

        async def flaky_close(self: SqliteStorageEngine) -> None:
            import contextlib

            with contextlib.suppress(StorageError):
                await real_close(self)
            raise StorageError("secondary close failure")

        monkeypatch.setattr(SqliteStorageEngine, "execute", flaky_execute)
        monkeypatch.setattr(SqliteStorageEngine, "close", flaky_close)

        # Exit code must be 1 (ConfigError path); the secondary close
        # failure is logged but does not propagate as a traceback.
        assert main([]) == EXIT_CONFIG_ERROR

    def test_audit_failure_rolls_back_session_and_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Review patch #1 / #3 — audit write is atomic with the session/snapshot.

        Simulates a mid-persist failure at the audit INSERT. Because all
        three rows now live inside one ``storage.transaction()``, a
        failure during the audit insert must roll back the whole thing —
        zero rows land in sessions, workspace_snapshots, or audit_log.
        Without the fix, session+snapshot would be orphaned and the fast
        path on the next run would miss the marker, producing duplicates.
        """
        import sqlite3

        from nova.core.exceptions import StorageError
        from nova.setup.__main__ import EXIT_CONFIG_ERROR, main

        self._seed_data_dir(tmp_path)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        self._disable_interactive_io(monkeypatch)
        self._mock_capture(monkeypatch, status="full", captured=1)

        # Patch storage.execute to raise on the audit_log INSERT only,
        # leaving the session + snapshot inserts to run normally. The
        # original execute is captured before the patch so the wrapper
        # can delegate.
        from nova.core.storage.engine import SqliteStorageEngine

        real_execute = SqliteStorageEngine.execute

        async def flaky_execute(self: SqliteStorageEngine, sql: str, params: object = ()) -> None:
            if "INSERT INTO audit_log" in sql:
                raise StorageError("simulated audit write failure")
            await real_execute(self, sql, params)  # type: ignore[arg-type]

        monkeypatch.setattr(SqliteStorageEngine, "execute", flaky_execute)

        # The simulated audit failure propagates out of the transaction;
        # __main__ catches StorageError at the persist boundary and
        # returns EXIT_CONFIG_ERROR.
        assert main([]) == EXIT_CONFIG_ERROR

        # Atomicity check: NO rows landed in any of the three tables.
        with sqlite3.connect(tmp_path / "nova" / "nova.db") as conn:
            conn.row_factory = sqlite3.Row
            sessions = conn.execute("SELECT id FROM sessions").fetchall()
            snapshots = conn.execute("SELECT id FROM workspace_snapshots").fetchall()
            audits = conn.execute(
                "SELECT id FROM audit_log WHERE action_type = 'setup_complete'"
            ).fetchall()

        assert len(sessions) == 0, "session row must roll back when audit fails"
        assert len(snapshots) == 0, "snapshot row must roll back when audit fails"
        assert len(audits) == 0
