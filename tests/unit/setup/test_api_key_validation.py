"""Unit tests for nova.setup.api_key — validation logic (Task 1 / AC #6-13)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import anthropic

from nova.setup.api_key import ValidationOutcome, ValidationResult, validate_api_key

# ---------------------------------------------------------------------------
# Format pre-check (AC #6)
# ---------------------------------------------------------------------------


class TestFormatPreCheck:
    """Empty / whitespace-only keys fast-fail without a network call."""

    def test_empty_string_returns_auth_failed(self) -> None:
        result = validate_api_key("")
        assert result.result is ValidationResult.AUTH_FAILED

    def test_whitespace_only_returns_auth_failed(self) -> None:
        result = validate_api_key("   \t\n  ")
        assert result.result is ValidationResult.AUTH_FAILED

    @patch("nova.setup.api_key._ping_anthropic")
    def test_non_empty_delegates_to_ping(self, mock_ping: MagicMock) -> None:
        mock_ping.return_value = ValidationOutcome(ValidationResult.SUCCESS)
        result = validate_api_key("sk-ant-test-key")
        assert result.result is ValidationResult.SUCCESS
        mock_ping.assert_called_once_with("sk-ant-test-key")

    @patch("nova.setup.api_key._ping_anthropic")
    def test_whitespace_stripped_before_ping(self, mock_ping: MagicMock) -> None:
        mock_ping.return_value = ValidationOutcome(ValidationResult.SUCCESS)
        validate_api_key("  sk-ant-padded  ")
        mock_ping.assert_called_once_with("sk-ant-padded")


# ---------------------------------------------------------------------------
# API ping result mapping (AC #7-12)
# ---------------------------------------------------------------------------


class TestPingResultMapping:
    """_ping_anthropic maps SDK exceptions to ValidationResult members."""

    @patch("nova.setup.api_key.anthropic.Anthropic")
    def test_success_returns_success(self, mock_cls: MagicMock) -> None:
        mock_client = mock_cls.return_value
        mock_client.messages.create.return_value = MagicMock()
        result = validate_api_key("sk-ant-valid")
        assert result.result is ValidationResult.SUCCESS

    @patch("nova.setup.api_key.anthropic.Anthropic")
    def test_authentication_error_returns_auth_failed(self, mock_cls: MagicMock) -> None:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = anthropic.AuthenticationError(
            message="invalid x-api-key",
            response=MagicMock(status_code=401),
            body=None,
        )
        result = validate_api_key("sk-ant-bad")
        assert result.result is ValidationResult.AUTH_FAILED

    @patch("nova.setup.api_key.anthropic.Anthropic")
    def test_connection_error_returns_network_error(self, mock_cls: MagicMock) -> None:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = anthropic.APIConnectionError(
            request=MagicMock(),
        )
        result = validate_api_key("sk-ant-key")
        assert result.result is ValidationResult.NETWORK_ERROR

    @patch("nova.setup.api_key.anthropic.Anthropic")
    def test_timeout_error_returns_network_error(self, mock_cls: MagicMock) -> None:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = anthropic.APITimeoutError(
            request=MagicMock(),
        )
        result = validate_api_key("sk-ant-key")
        assert result.result is ValidationResult.NETWORK_ERROR

    @patch("nova.setup.api_key.anthropic.Anthropic")
    def test_rate_limit_returns_rate_limited(self, mock_cls: MagicMock) -> None:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = anthropic.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429),
            body=None,
        )
        result = validate_api_key("sk-ant-key")
        assert result.result is ValidationResult.RATE_LIMITED

    @patch("nova.setup.api_key.anthropic.Anthropic")
    def test_server_error_returns_server_error(self, mock_cls: MagicMock) -> None:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = anthropic.InternalServerError(
            message="internal server error",
            response=MagicMock(status_code=500),
            body=None,
        )
        result = validate_api_key("sk-ant-key")
        assert result.result is ValidationResult.SERVER_ERROR

    @patch("nova.setup.api_key.anthropic.Anthropic")
    def test_client_created_with_correct_timeout(self, mock_cls: MagicMock) -> None:
        mock_client = mock_cls.return_value
        mock_client.messages.create.return_value = MagicMock()
        validate_api_key("sk-ant-key")
        mock_cls.assert_called_once_with(api_key="sk-ant-key", timeout=15.0)

    @patch("nova.setup.api_key.anthropic.Anthropic")
    def test_messages_create_called_with_cheapest_model(self, mock_cls: MagicMock) -> None:
        mock_client = mock_cls.return_value
        mock_client.messages.create.return_value = MagicMock()
        validate_api_key("sk-ant-key")
        mock_client.messages.create.assert_called_once_with(
            model="claude-haiku-4-5-20251001",
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )


# ---------------------------------------------------------------------------
# Post-review patches: status code carrying, 4xx/5xx split, APIError catch-all,
# client close (Review M1, H5, H1/AC #12, M3).
# ---------------------------------------------------------------------------


class TestPostReviewPatches:
    """Regression coverage for issues surfaced in code review."""

    @patch("nova.setup.api_key.anthropic.Anthropic")
    def test_rate_limit_outcome_carries_status_code(self, mock_cls: MagicMock) -> None:
        """RATE_LIMITED outcomes preserve the 429 status code."""
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = anthropic.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429),
            body=None,
        )
        result = validate_api_key("sk-ant-key")
        assert result.status_code == 429

    @patch("nova.setup.api_key.anthropic.Anthropic")
    def test_server_error_outcome_carries_status_code(self, mock_cls: MagicMock) -> None:
        """AC #12: SERVER_ERROR outcomes preserve the 5xx status code."""
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = anthropic.InternalServerError(
            message="internal server error",
            response=MagicMock(status_code=503),
            body=None,
        )
        result = validate_api_key("sk-ant-key")
        assert result.result is ValidationResult.SERVER_ERROR
        assert result.status_code == 503

    @patch("nova.setup.api_key.anthropic.Anthropic")
    def test_4xx_non_auth_routes_to_server_error(self, mock_cls: MagicMock) -> None:
        """AC #12: BadRequestError (400) routes to SERVER_ERROR with status code.

        Second-pass review corrected this: 4xx-non-auth (bad request,
        not-found, unprocessable) is NOT a key-validity problem — it's
        a request-shape or model-name issue. Routing to AUTH_FAILED
        would mislead users with valid keys.
        """
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = anthropic.BadRequestError(
            message="bad request",
            response=MagicMock(status_code=400),
            body=None,
        )
        result = validate_api_key("sk-ant-key")
        assert result.result is ValidationResult.SERVER_ERROR
        assert result.status_code == 400

    @patch("nova.setup.api_key.anthropic.Anthropic")
    def test_not_found_routes_to_server_error(self, mock_cls: MagicMock) -> None:
        """AC #12: NotFoundError (404 — e.g. model name typo) routes to SERVER_ERROR."""
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = anthropic.NotFoundError(
            message="model not found",
            response=MagicMock(status_code=404),
            body=None,
        )
        result = validate_api_key("sk-ant-key")
        assert result.result is ValidationResult.SERVER_ERROR
        assert result.status_code == 404

    @patch("nova.setup.api_key.anthropic.Anthropic")
    def test_permission_denied_routes_to_server_error(self, mock_cls: MagicMock) -> None:
        """AC #12: PermissionDeniedError (403) routes to SERVER_ERROR — 403 is not 401."""
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = anthropic.PermissionDeniedError(
            message="forbidden",
            response=MagicMock(status_code=403),
            body=None,
        )
        result = validate_api_key("sk-ant-key")
        assert result.result is ValidationResult.SERVER_ERROR
        assert result.status_code == 403

    @patch("nova.setup.api_key.anthropic.Anthropic")
    def test_api_error_catchall_routes_to_server_error(self, mock_cls: MagicMock) -> None:
        """Review H5: APIError subclasses not extending APIStatusError don't crash."""
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = anthropic.APIError(
            message="generic api error",
            request=MagicMock(),
            body=None,
        )
        result = validate_api_key("sk-ant-key")
        assert result.result is ValidationResult.SERVER_ERROR

    @patch("nova.setup.api_key.anthropic.Anthropic")
    def test_client_closed_on_success(self, mock_cls: MagicMock) -> None:
        """Review M3: client.close() is always invoked (httpx pool leak)."""
        mock_client = mock_cls.return_value
        mock_client.messages.create.return_value = MagicMock()
        validate_api_key("sk-ant-key")
        mock_client.close.assert_called_once()

    @patch("nova.setup.api_key.anthropic.Anthropic")
    def test_client_closed_on_failure(self, mock_cls: MagicMock) -> None:
        """Review M3: client.close() runs even when the API call raises."""
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = anthropic.AuthenticationError(
            message="nope",
            response=MagicMock(status_code=401),
            body=None,
        )
        validate_api_key("sk-ant-key")
        mock_client.close.assert_called_once()
