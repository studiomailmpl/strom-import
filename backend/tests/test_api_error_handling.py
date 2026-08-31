"""
Unit tests for classifying and describing Anthropic API failures.
"""

import pytest

from app.services.ai_extractor import describe_api_error, is_retryable_api_error


class _ApiError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


USAGE_LIMIT = _ApiError(
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
    "'message': 'You have reached your specified API usage limits. You will regain "
    "access on 2026-09-01 at 00:00 UTC.'}}",
    400,
)


class TestRetryClassification:
    def test_a_spend_cap_is_not_retried(self):
        """It fails identically until the cap resets; retrying only delays the report."""
        assert is_retryable_api_error(USAGE_LIMIT) is False

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 529])
    def test_rate_limits_and_server_errors_are_retried(self, status):
        assert is_retryable_api_error(_ApiError("boom", status)) is True

    @pytest.mark.parametrize("status", [401, 403, 404, 413])
    def test_client_errors_are_not_retried(self, status):
        assert is_retryable_api_error(_ApiError("nope", status)) is False

    def test_a_bad_key_is_not_retried_even_without_a_status(self):
        assert is_retryable_api_error(_ApiError("invalid_api_key")) is False

    def test_an_overload_reported_as_400_is_retried(self):
        assert is_retryable_api_error(_ApiError("Overloaded", 400)) is True

    def test_a_plain_400_is_not_retried(self):
        assert is_retryable_api_error(_ApiError("bad request", 400)) is False

    def test_an_unknown_error_is_retried(self):
        """Network blips have no status code and are worth another attempt."""
        assert is_retryable_api_error(Exception("connection reset")) is True


class TestErrorDescription:
    def test_the_spend_cap_message_names_the_cause_and_the_reset(self):
        message = describe_api_error(USAGE_LIMIT)
        assert "forbrugsgrænse" in message
        assert "2026-09-01" in message
        assert "00:00 UTC" in message
        assert "Limits" in message, "tell the user where to change it"
        assert "invalid_request_error" not in message, "no raw API JSON"

    def test_a_spend_cap_without_a_reset_time_still_explains_itself(self):
        message = describe_api_error(_ApiError("You have reached your specified API usage limits.", 400))
        assert "forbrugsgrænse" in message
        assert "vender tilbage" not in message

    def test_a_rejected_key_names_the_setting(self):
        assert "ANTHROPIC_API_KEY" in describe_api_error(_ApiError("invalid_api_key", 401))

    def test_a_rate_limit_suggests_retrying(self):
        assert "igen" in describe_api_error(_ApiError("rate_limit_error", 429))

    def test_an_unrecognised_error_is_passed_through_but_truncated(self):
        message = describe_api_error(Exception("x" * 900))
        assert message.startswith("x")
        assert len(message) <= 300
