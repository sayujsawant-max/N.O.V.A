"""Unit tests for run_api_key_step — interactive prompt flow (Task 3 / AC #1-5, #17, #23-26)."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from nova.setup.api_key import ValidationOutcome, ValidationResult, run_api_key_step


@pytest.fixture(autouse=True)
def _fake_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``sys.stdin.isatty()`` to return True so run_api_key_step runs.

    The module's non-TTY guard (M2 patch) skips the step when stdin is
    not a real terminal — which pytest's captured stdin is not.
    """
    monkeypatch.setattr("nova.setup.api_key.sys.stdin.isatty", lambda: True)


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """Create a data directory with shipped-default settings.yaml."""
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        "bluntness: direct\n"
        "skip_briefing_if_recent: true\n"
        "briefing_recency_threshold_minutes: 60\n",
        encoding="utf-8",
    )
    return tmp_path


def _outcome(result: ValidationResult, status_code: int | None = None) -> ValidationOutcome:
    """Shorthand for constructing a ValidationOutcome in mock returns."""
    return ValidationOutcome(result, status_code=status_code)


def _make_console(*inputs: str) -> MagicMock:
    """Create a mock Console whose ``input()`` returns values in sequence."""
    console = MagicMock()
    console.input = MagicMock(side_effect=list(inputs))
    return console


# ---------------------------------------------------------------------------
# Success path (AC #8)
# ---------------------------------------------------------------------------


class TestSuccessPath:
    """Key validated and written to settings.yaml."""

    @patch("nova.setup.api_key.validate_api_key")
    def test_valid_key_written_and_returns_true(
        self, mock_validate: MagicMock, data_dir: Path
    ) -> None:
        mock_validate.return_value = _outcome(ValidationResult.SUCCESS)
        console = _make_console("sk-ant-valid-key")

        result = run_api_key_step(console, data_dir)

        assert result is True
        settings = yaml.safe_load((data_dir / "settings.yaml").read_text(encoding="utf-8"))
        assert settings["api_key"] == "sk-ant-valid-key"

    @patch("nova.setup.api_key.validate_api_key")
    def test_success_message_shown(self, mock_validate: MagicMock, data_dir: Path) -> None:
        mock_validate.return_value = _outcome(ValidationResult.SUCCESS)
        console = _make_console("sk-ant-key")

        run_api_key_step(console, data_dir)

        printed = _all_printed(console)
        assert any("\u2713" in s and "API key validated" in s for s in printed)


# ---------------------------------------------------------------------------
# Skip paths (AC #3, #5)
# ---------------------------------------------------------------------------


class TestSkipPaths:
    """Explicit skip and double-empty-enter produce skip notice."""

    @patch("nova.setup.api_key.validate_api_key")
    def test_explicit_skip_returns_false(self, mock_validate: MagicMock, data_dir: Path) -> None:
        console = _make_console("skip")

        result = run_api_key_step(console, data_dir)

        assert result is False
        mock_validate.assert_not_called()

    @patch("nova.setup.api_key.validate_api_key")
    def test_explicit_skip_case_insensitive(self, mock_validate: MagicMock, data_dir: Path) -> None:
        console = _make_console("SKIP")

        result = run_api_key_step(console, data_dir)

        assert result is False

    @patch("nova.setup.api_key.validate_api_key")
    def test_double_empty_enter_skips(self, mock_validate: MagicMock, data_dir: Path) -> None:
        console = _make_console("", "")

        result = run_api_key_step(console, data_dir)

        assert result is False
        mock_validate.assert_not_called()

    @patch("nova.setup.api_key.validate_api_key")
    def test_first_empty_then_key_works(self, mock_validate: MagicMock, data_dir: Path) -> None:
        mock_validate.return_value = _outcome(ValidationResult.SUCCESS)
        console = _make_console("", "sk-ant-key")

        result = run_api_key_step(console, data_dir)

        assert result is True

    @patch("nova.setup.api_key.validate_api_key")
    def test_skip_notice_content(self, mock_validate: MagicMock, data_dir: Path) -> None:
        console = _make_console("skip")

        run_api_key_step(console, data_dir)

        printed = _all_printed(console)
        assert any("offline mode" in s for s in printed)

    @patch("nova.setup.api_key.validate_api_key")
    def test_skip_does_not_write_key(self, mock_validate: MagicMock, data_dir: Path) -> None:
        console = _make_console("skip")

        run_api_key_step(console, data_dir)

        settings = yaml.safe_load((data_dir / "settings.yaml").read_text(encoding="utf-8"))
        assert "api_key" not in settings


# ---------------------------------------------------------------------------
# Retry and failure paths (AC #4, #9-12)
# ---------------------------------------------------------------------------


class TestRetryPaths:
    """Validation failures allow retry, exhaust after 3 attempts."""

    @patch("nova.setup.api_key.validate_api_key")
    def test_auth_failure_then_success(self, mock_validate: MagicMock, data_dir: Path) -> None:
        mock_validate.side_effect = [
            _outcome(ValidationResult.AUTH_FAILED),
            _outcome(ValidationResult.SUCCESS),
        ]
        console = _make_console("bad-key", "sk-ant-good-key")

        result = run_api_key_step(console, data_dir)

        assert result is True

    @patch("nova.setup.api_key.validate_api_key")
    def test_three_failures_auto_skips(self, mock_validate: MagicMock, data_dir: Path) -> None:
        mock_validate.return_value = _outcome(ValidationResult.AUTH_FAILED)
        console = _make_console("bad1", "bad2", "bad3")

        result = run_api_key_step(console, data_dir)

        assert result is False
        printed = _all_printed(console)
        assert any("Validation failed 3 times" in s for s in printed)

    @patch("nova.setup.api_key.validate_api_key")
    def test_network_error_message(self, mock_validate: MagicMock, data_dir: Path) -> None:
        mock_validate.side_effect = [
            _outcome(ValidationResult.NETWORK_ERROR),
            _outcome(ValidationResult.SUCCESS),
        ]
        console = _make_console("sk-key", "sk-key")

        run_api_key_step(console, data_dir)

        printed = _all_printed(console)
        assert any("internet connection" in s for s in printed)

    @patch("nova.setup.api_key.validate_api_key")
    def test_server_error_message(self, mock_validate: MagicMock, data_dir: Path) -> None:
        mock_validate.side_effect = [
            _outcome(ValidationResult.SERVER_ERROR),
            _outcome(ValidationResult.SUCCESS),
        ]
        console = _make_console("sk-key", "sk-key")

        run_api_key_step(console, data_dir)

        printed = _all_printed(console)
        assert any("API error" in s for s in printed)


# ---------------------------------------------------------------------------
# Rate-limit soft-pass (AC #11)
# ---------------------------------------------------------------------------


class TestRateLimitSoftPass:
    """Rate-limited → key is written, no retry."""

    @patch("nova.setup.api_key.validate_api_key")
    def test_rate_limited_writes_key(self, mock_validate: MagicMock, data_dir: Path) -> None:
        mock_validate.return_value = _outcome(ValidationResult.RATE_LIMITED)
        console = _make_console("sk-ant-rate-limited")

        result = run_api_key_step(console, data_dir)

        assert result is True
        settings = yaml.safe_load((data_dir / "settings.yaml").read_text(encoding="utf-8"))
        assert settings["api_key"] == "sk-ant-rate-limited"

    @patch("nova.setup.api_key.validate_api_key")
    def test_rate_limited_message(self, mock_validate: MagicMock, data_dir: Path) -> None:
        mock_validate.return_value = _outcome(ValidationResult.RATE_LIMITED)
        console = _make_console("sk-ant-key")

        run_api_key_step(console, data_dir)

        printed = _all_printed(console)
        assert any("rate limited" in s.lower() for s in printed)


# ---------------------------------------------------------------------------
# Write-failure handling (AC #17)
# ---------------------------------------------------------------------------


class TestWriteFailure:
    """OSError from write_api_key → UX message, wizard continues."""

    @patch("nova.setup.api_key.validate_api_key")
    @patch("nova.setup.api_key.write_api_key")
    def test_write_failure_returns_false(
        self, mock_write: MagicMock, mock_validate: MagicMock, data_dir: Path
    ) -> None:
        mock_validate.return_value = _outcome(ValidationResult.SUCCESS)
        mock_write.side_effect = OSError("permission denied")
        console = _make_console("sk-ant-key")

        result = run_api_key_step(console, data_dir)

        assert result is False

    @patch("nova.setup.api_key.validate_api_key")
    @patch("nova.setup.api_key.write_api_key")
    def test_write_failure_shows_message(
        self, mock_write: MagicMock, mock_validate: MagicMock, data_dir: Path
    ) -> None:
        mock_validate.return_value = _outcome(ValidationResult.SUCCESS)
        mock_write.side_effect = OSError("permission denied")
        console = _make_console("sk-ant-key")

        run_api_key_step(console, data_dir)

        printed = _all_printed(console)
        assert any("Could not save" in s for s in printed)


# ---------------------------------------------------------------------------
# Key-never-in-output (AC #13, #18)
# ---------------------------------------------------------------------------


class TestKeyNeverInOutput:
    """The API key must never appear in any printed output."""

    @patch("nova.setup.api_key.validate_api_key")
    def test_key_not_in_success_output(self, mock_validate: MagicMock, data_dir: Path) -> None:
        test_key = "sk-ant-secret-key-abc123"
        mock_validate.return_value = _outcome(ValidationResult.SUCCESS)
        console = _make_console(test_key)

        run_api_key_step(console, data_dir)

        printed = _all_printed(console)
        for line in printed:
            assert test_key not in line

    @patch("nova.setup.api_key.validate_api_key")
    def test_key_not_in_failure_output(self, mock_validate: MagicMock, data_dir: Path) -> None:
        test_key = "sk-ant-bad-secret-key"
        mock_validate.return_value = _outcome(ValidationResult.AUTH_FAILED)
        console = _make_console(test_key, test_key, test_key)

        run_api_key_step(console, data_dir)

        printed = _all_printed(console)
        for line in printed:
            assert test_key not in line

    @patch("nova.setup.api_key.validate_api_key")
    @patch("nova.setup.api_key.write_api_key")
    def test_key_not_in_write_failure_output(
        self, mock_write: MagicMock, mock_validate: MagicMock, data_dir: Path
    ) -> None:
        test_key = "sk-ant-write-fail-key"
        mock_validate.return_value = _outcome(ValidationResult.SUCCESS)
        mock_write.side_effect = OSError("denied")
        console = _make_console(test_key)

        run_api_key_step(console, data_dir)

        printed = _all_printed(console)
        for line in printed:
            assert test_key not in line


# ---------------------------------------------------------------------------
# Key never in logs (AC #18)
# ---------------------------------------------------------------------------


class TestKeyNeverInLogs:
    """The API key must never appear in log records."""

    @patch("nova.setup.api_key.validate_api_key")
    def test_key_not_in_log_records_on_success(
        self,
        mock_validate: MagicMock,
        data_dir: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        test_key = "sk-ant-secret-log-check"
        mock_validate.return_value = _outcome(ValidationResult.SUCCESS)
        console = _make_console(test_key)

        with caplog.at_level(logging.DEBUG, logger="nova.setup"):
            run_api_key_step(console, data_dir)

        for record in caplog.records:
            assert test_key not in record.getMessage()
            for attr_val in vars(record).values():
                if isinstance(attr_val, str):
                    assert test_key not in attr_val

    @patch("nova.setup.api_key.validate_api_key")
    @patch("nova.setup.api_key.write_api_key")
    def test_key_not_in_log_records_on_write_failure(
        self,
        mock_write: MagicMock,
        mock_validate: MagicMock,
        data_dir: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        test_key = "sk-ant-write-fail-log"
        mock_validate.return_value = _outcome(ValidationResult.SUCCESS)
        mock_write.side_effect = OSError("denied")
        console = _make_console(test_key)

        with caplog.at_level(logging.DEBUG, logger="nova.setup"):
            run_api_key_step(console, data_dir)

        for record in caplog.records:
            assert test_key not in record.getMessage()
            for attr_val in vars(record).values():
                if isinstance(attr_val, str):
                    assert test_key not in attr_val


# ---------------------------------------------------------------------------
# Post-review patches (H3, H4, H8, M2, M4, M6, AC #12, L7)
# ---------------------------------------------------------------------------


class TestPostReviewPatches:
    """Regression coverage for code-review patches."""

    # --- H4 + L7: KeyboardInterrupt / EOFError → clean skip ---

    @patch("nova.setup.api_key.validate_api_key")
    def test_keyboard_interrupt_mid_prompt_skips_cleanly(
        self, mock_validate: MagicMock, data_dir: Path
    ) -> None:
        """Ctrl+C mid-prompt exits via skip path, no traceback."""
        console = _make_console()
        console.input = MagicMock(side_effect=KeyboardInterrupt())

        result = run_api_key_step(console, data_dir)

        assert result is False
        printed = _all_printed(console)
        assert any("offline mode" in s for s in printed)
        mock_validate.assert_not_called()

    @patch("nova.setup.api_key.validate_api_key")
    def test_eof_error_mid_prompt_skips_cleanly(
        self, mock_validate: MagicMock, data_dir: Path
    ) -> None:
        """Closed stdin (EOF) exits via skip path, no traceback."""
        console = _make_console()
        console.input = MagicMock(side_effect=EOFError())

        result = run_api_key_step(console, data_dir)

        assert result is False
        printed = _all_printed(console)
        assert any("offline mode" in s for s in printed)

    # --- H8: single-line skip notice on exhaustion ---

    @patch("nova.setup.api_key.validate_api_key")
    def test_retry_exhaustion_emits_single_skip_notice(
        self, mock_validate: MagicMock, data_dir: Path
    ) -> None:
        """AC #5: exhaustion prints one combined notice, not two separate lines."""
        mock_validate.return_value = _outcome(ValidationResult.AUTH_FAILED)
        console = _make_console("bad1", "bad2", "bad3")

        run_api_key_step(console, data_dir)

        printed = _all_printed(console)
        # Per-attempt ✗ lines (3) + single ⚠ exhaustion notice.
        warn_lines = [s for s in printed if "\u26a0" in s]
        assert len(warn_lines) == 1, (
            f"Expected exactly one ⚠ line on exhaustion, saw {len(warn_lines)}: {warn_lines}"
        )
        assert "Validation failed 3 times" in warn_lines[0]
        assert "offline mode" in warn_lines[0]

    # --- M2: non-TTY detection ---

    @patch("nova.setup.api_key.validate_api_key")
    def test_non_tty_skips_without_prompting(
        self, mock_validate: MagicMock, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When stdin is not a TTY, step is skipped without prompting (key never echoed)."""
        monkeypatch.setattr("nova.setup.api_key.sys.stdin.isatty", lambda: False)
        console = _make_console()

        result = run_api_key_step(console, data_dir)

        assert result is False
        console.input.assert_not_called()
        mock_validate.assert_not_called()
        printed = _all_printed(console)
        assert any("interactive terminal" in s for s in printed)

    # --- H3: retry counter only advances on real validations ---

    @patch("nova.setup.api_key.validate_api_key")
    def test_empty_inputs_do_not_consume_retry_budget(
        self, mock_validate: MagicMock, data_dir: Path
    ) -> None:
        """AC #4: empty-input re-prompts don't count toward the 3-attempt budget."""
        # Sequence: empty → re-prompt, bad → ✗, empty → re-prompt, bad → ✗,
        # good → success. That's 3 real validation attempts (we allow 3) with
        # 2 empty interludes. Exhaustion would fire at >3 validations.
        mock_validate.side_effect = [
            _outcome(ValidationResult.AUTH_FAILED),
            _outcome(ValidationResult.AUTH_FAILED),
            _outcome(ValidationResult.SUCCESS),
        ]
        console = _make_console("", "bad1", "bad2", "sk-ant-good")

        result = run_api_key_step(console, data_dir)

        assert result is True
        assert mock_validate.call_count == 3

    # --- AC #12: status_code interpolated into SERVER_ERROR message ---

    @patch("nova.setup.api_key.validate_api_key")
    def test_server_error_message_includes_status_code(
        self, mock_validate: MagicMock, data_dir: Path
    ) -> None:
        """AC #12: 'Anthropic API error (503). ...' includes the code."""
        mock_validate.side_effect = [
            _outcome(ValidationResult.SERVER_ERROR, status_code=503),
            _outcome(ValidationResult.SUCCESS),
        ]
        console = _make_console("sk-key", "sk-key")

        run_api_key_step(console, data_dir)

        printed = _all_printed(console)
        assert any("503" in s and "Anthropic API error" in s for s in printed), (
            f"Expected SERVER_ERROR line with status code 503; got: {printed}"
        )

    # --- M4: RATE_LIMITED wording no longer claims "format looks valid" ---

    @patch("nova.setup.api_key.validate_api_key")
    def test_rate_limited_message_does_not_claim_format_valid(
        self, mock_validate: MagicMock, data_dir: Path
    ) -> None:
        """M4: reworded RATE_LIMITED notice does not assert format is valid."""
        mock_validate.return_value = _outcome(ValidationResult.RATE_LIMITED, status_code=429)
        console = _make_console("sk-ant-key")

        run_api_key_step(console, data_dir)

        printed = _all_printed(console)
        rate_line = next((s for s in printed if "rate limited" in s.lower()), "")
        assert rate_line, "Expected a rate-limited notice line"
        assert "format looks valid" not in rate_line

    # --- Second-pass review: RATE_LIMITED must NOT print "API key validated." ---

    @patch("nova.setup.api_key.validate_api_key")
    def test_rate_limited_does_not_claim_validated(
        self, mock_validate: MagicMock, data_dir: Path
    ) -> None:
        """RATE_LIMITED writes the key but must not print 'API key validated.'.

        Validation was explicitly skipped due to rate limiting; showing
        the success confirmation contradicts the rate-limit notice and
        misleads the user into thinking auth succeeded.
        """
        mock_validate.return_value = _outcome(ValidationResult.RATE_LIMITED, status_code=429)
        console = _make_console("sk-ant-key")

        run_api_key_step(console, data_dir)

        printed = _all_printed(console)
        assert not any("API key validated" in s for s in printed), (
            f"RATE_LIMITED path printed 'API key validated' — contradicts the "
            f"rate-limit notice: {printed}"
        )
        # Should instead print an "unverified" confirmation
        assert any("unverified" in s for s in printed), (
            f"Expected an 'unverified' confirmation on RATE_LIMITED path: {printed}"
        )

    @patch("nova.setup.api_key.validate_api_key")
    def test_success_still_claims_validated(self, mock_validate: MagicMock, data_dir: Path) -> None:
        """Regression: SUCCESS path still prints 'API key validated.'."""
        mock_validate.return_value = _outcome(ValidationResult.SUCCESS)
        console = _make_console("sk-ant-key")

        run_api_key_step(console, data_dir)

        printed = _all_printed(console)
        assert any("API key validated" in s for s in printed)
        assert not any("unverified" in s for s in printed)

    # --- M6: FileNotFoundError differentiated from other OSError ---

    @patch("nova.setup.api_key.validate_api_key")
    @patch("nova.setup.api_key.write_api_key")
    def test_file_not_found_shows_data_dir_missing_message(
        self,
        mock_write: MagicMock,
        mock_validate: MagicMock,
        data_dir: Path,
    ) -> None:
        """M6: FileNotFoundError steers the user toward re-running setup.bat."""
        mock_validate.return_value = _outcome(ValidationResult.SUCCESS)
        mock_write.side_effect = FileNotFoundError("settings.yaml missing")
        console = _make_console("sk-ant-key")

        result = run_api_key_step(console, data_dir)

        assert result is False
        printed = _all_printed(console)
        assert any("Data directory missing" in s for s in printed)
        assert any("setup.bat" in s for s in printed)

    @patch("nova.setup.api_key.validate_api_key")
    @patch("nova.setup.api_key.write_api_key")
    def test_generic_oserror_shows_permissions_message(
        self,
        mock_write: MagicMock,
        mock_validate: MagicMock,
        data_dir: Path,
    ) -> None:
        """M6: generic OSError still shows the 'check permissions' message."""
        mock_validate.return_value = _outcome(ValidationResult.SUCCESS)
        mock_write.side_effect = PermissionError("denied")
        console = _make_console("sk-ant-key")

        result = run_api_key_step(console, data_dir)

        assert result is False
        printed = _all_printed(console)
        assert any("Could not save" in s for s in printed)
        assert any("Check file permissions" in s for s in printed)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_printed(console: MagicMock) -> list[str]:
    """Extract all strings passed to ``console.print(...)``."""
    return [str(c.args[0]) if c.args else "" for c in console.print.call_args_list]
