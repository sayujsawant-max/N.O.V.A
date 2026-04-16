"""API key configuration for first-run setup.

Provides:

- :class:`ValidationResult` — enum tag for the validation outcome class.
- :class:`ValidationOutcome` — result object carrying the tag plus an
  optional HTTP status code (needed so AC #12 can interpolate the code
  into the user-facing error message).
- :func:`validate_api_key` — format pre-check + Anthropic API ping.
- :func:`run_api_key_step` — interactive prompt flow orchestrating
  validation, retry, skip, and persistence.

Story 2.2 uses the Anthropic SDK directly for the one-shot validation
ping.  The full ``ClaudeReasoningAdapter`` (satisfying ``HealthCheck``,
``VoicePort``) is a later story.
"""

from __future__ import annotations

import enum
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import anthropic
from rich.console import Console

from nova.setup.settings_writer import write_api_key

logger = logging.getLogger("nova.setup.api_key")


class ValidationResult(enum.Enum):
    """Result tag from API key validation — drives retry/soft-pass/skip UX."""

    SUCCESS = "success"
    AUTH_FAILED = "auth_failed"
    NETWORK_ERROR = "network_error"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    """Structured result from :func:`validate_api_key`.

    Carries the classification tag plus the HTTP status code when the
    SDK surfaced one.  The status code is used by AC #12 to emit
    ``"Anthropic API error ([status_code])."`` and by the 4xx-vs-5xx
    classifier (AC medium-severity patch #9).
    """

    result: ValidationResult
    status_code: int | None = None


def validate_api_key(key: str) -> ValidationOutcome:
    """Format pre-check then Anthropic API ping.

    Empty / whitespace-only keys fast-fail to ``AUTH_FAILED`` with no
    network call.  Non-empty keys are stripped and forwarded to
    :func:`_ping_anthropic`.
    """
    stripped = key.strip()
    if not stripped:
        return ValidationOutcome(ValidationResult.AUTH_FAILED)
    return _ping_anthropic(stripped)


def _ping_anthropic(api_key: str) -> ValidationOutcome:
    """One-shot validation ping using the cheapest model.

    Creates a disposable sync client with a 15-second timeout, sends a
    minimal ``messages.create`` request, and maps the outcome to a
    :class:`ValidationOutcome`.  The client is explicitly closed to
    avoid httpx connection-pool leaks across retries.  Response content
    is discarded — we only care about authentication success vs. error
    class.

    Exception handling covers:

    - ``AuthenticationError`` → AUTH_FAILED
    - ``APIConnectionError`` / ``APITimeoutError`` → NETWORK_ERROR
    - ``RateLimitError`` → RATE_LIMITED (soft-pass at caller)
    - ``APIStatusError`` — 4xx routed to AUTH_FAILED (with status code
      for messaging), 5xx routed to SERVER_ERROR (with status code)
    - ``APIError`` catch-all (e.g. ``APIResponseValidationError``) →
      SERVER_ERROR.  Without this, schema-mismatch responses would
      propagate as uncaught tracebacks.
    """
    client = anthropic.Anthropic(api_key=api_key, timeout=15.0)
    try:
        try:
            client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            return ValidationOutcome(ValidationResult.SUCCESS)
        except anthropic.AuthenticationError:
            return ValidationOutcome(ValidationResult.AUTH_FAILED)
        except (anthropic.APIConnectionError, anthropic.APITimeoutError):
            return ValidationOutcome(ValidationResult.NETWORK_ERROR)
        except anthropic.RateLimitError as err:
            return ValidationOutcome(
                ValidationResult.RATE_LIMITED, status_code=err.status_code
            )
        except anthropic.APIStatusError as err:
            logger.debug(
                "API status error during key validation",
                extra={"status_code": err.status_code},
            )
            # Per AC #12: any APIStatusError other than the specific
            # AuthenticationError (401) and RateLimitError (429) maps
            # to SERVER_ERROR with the status code interpolated into
            # the user message.  4xx-non-auth (400 bad request, 404
            # model not found, 422 unprocessable entity, etc.) are
            # NOT key-validity problems — routing them to AUTH_FAILED
            # would mislead the user into thinking their key is bad
            # when the real issue is a request-shape or model-name
            # problem on our side.  The "key may be valid — try again
            # or skip" wording plus the status code is accurate for
            # both 4xx-non-auth and 5xx.
            return ValidationOutcome(
                ValidationResult.SERVER_ERROR, status_code=err.status_code
            )
        except anthropic.APIError:
            # Catch-all for APIError / APIResponseValidationError /
            # any future subclass that doesn't extend APIStatusError.
            # Prevents schema-mismatch crashes from reaching the user.
            logger.debug("APIError during key validation", exc_info=True)
            return ValidationOutcome(ValidationResult.SERVER_ERROR)
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Failure-message mapping (AC #8-12)
# ---------------------------------------------------------------------------

_FAILURE_MESSAGES: dict[ValidationResult, str] = {
    ValidationResult.AUTH_FAILED: (
        "Invalid API key. Check that you copied the full key from console.anthropic.com."
    ),
    ValidationResult.NETWORK_ERROR: (
        "Could not reach the Anthropic API. Check your internet connection."
    ),
    ValidationResult.SERVER_ERROR: (
        "Anthropic API error. The key may be valid — try again or skip."
    ),
}

_MAX_ATTEMPTS: int = 3


def _format_failure_message(outcome: ValidationOutcome) -> str:
    """Render the user-facing failure message for a non-success outcome.

    SERVER_ERROR interpolates the HTTP status code when present
    (AC #12 requires ``"Anthropic API error ([status_code])."``).
    AUTH_FAILED ignores the status code — the uniform wording is
    clearer than leaking 400/404/etc. at the user surface.
    """
    if outcome.result is ValidationResult.SERVER_ERROR and outcome.status_code is not None:
        return (
            f"Anthropic API error ({outcome.status_code}). "
            "The key may be valid — try again or skip."
        )
    return _FAILURE_MESSAGES.get(outcome.result, "Validation failed.")


# ---------------------------------------------------------------------------
# Interactive prompt flow (AC #1-5, #17, #23-26)
# ---------------------------------------------------------------------------


def _prompt_for_key(console: Console) -> str:
    """Prompt the user for their API key with input masking.

    Returns the raw input string (may be empty or ``"skip"``).
    Uses ``Console.input`` for masked entry because Rich ``Prompt``
    does not support ``password=True`` natively in all versions.
    """
    return console.input(
        "[bold white]API Key Setup[/bold white]\n"
        "Enter your Anthropic API key (from console.anthropic.com): ",
        password=True,
    )


def run_api_key_step(console: Console, data_dir: Path) -> bool:
    """Orchestrate the API key prompt, validation, retry, and persistence.

    Returns ``True`` if a key was successfully configured, ``False`` if
    the step was skipped (explicitly, after retry exhaustion, on EOF /
    Ctrl+C, or in a non-interactive terminal).

    This function is the single UX owner: it maps each
    :class:`ValidationResult` member to the correct user message and
    retry/soft-pass/skip behavior, and catches :class:`OSError` from
    :func:`write_api_key` to surface the write-failure message before
    continuing.

    Attempt accounting: the ``_MAX_ATTEMPTS`` budget counts only real
    validation attempts, not empty-input re-prompts.  A user who hits
    enter twice exits via the double-empty-skip path without burning
    the retry budget.

    Non-TTY handling: when stdin is not a real terminal (piped, CI,
    redirected from ``/dev/null``), the step is skipped rather than
    risk the ``getpass`` fallback echoing the key to stdout.
    """
    if not sys.stdin.isatty():
        console.print(
            "[yellow]\u26a0[/yellow] Not running in an interactive terminal. "
            "Skipping API key step. Add your key to settings.yaml later."
        )
        return False

    empty_count = 0
    attempt = 0

    try:
        while attempt < _MAX_ATTEMPTS:
            raw = (
                _prompt_for_key(console)
                if attempt == 0 and empty_count == 0
                else console.input(
                    "Enter your Anthropic API key: ",
                    password=True,
                )
            )

            # --- Skip detection ---
            if raw.strip().lower() == "skip":
                _show_skip_notice(console)
                return False

            if not raw.strip():
                empty_count += 1
                if empty_count >= 2:
                    _show_skip_notice(console)
                    return False
                console.print(
                    "No key entered. Type 'skip' to continue without "
                    "cloud reasoning, or paste your key."
                )
                continue

            # Reset empty counter on non-empty input; a real attempt
            # is about to consume one of _MAX_ATTEMPTS.
            empty_count = 0
            attempt += 1

            # --- Validation ---
            outcome = validate_api_key(raw)

            if outcome.result is ValidationResult.SUCCESS:
                if _persist_key(console, data_dir, raw.strip()):
                    console.print("[green]\u2713[/green] API key validated.")
                    return True
                return False

            if outcome.result is ValidationResult.RATE_LIMITED:
                console.print(
                    "[yellow]\u26a0[/yellow] API rate limited. "
                    "Skipping verification and continuing with setup."
                )
                if _persist_key(console, data_dir, raw.strip()):
                    # No "validated." confirmation — the rate-limit
                    # notice above already explains that verification
                    # was skipped.  Printing "API key validated." here
                    # would contradict that and mislead the user.
                    console.print(
                        "[yellow]\u26a0[/yellow] API key saved (unverified)."
                    )
                    return True
                return False

            # Retriable failure
            console.print(f"[red]\u2717[/red] {_format_failure_message(outcome)}")

        # Exhausted retries — single-line skip notice (AC #5)
        console.print(
            "[yellow]\u26a0[/yellow] Validation failed 3 times. "
            "N.O.V.A. will run in offline mode. "
            "Add your key to settings.yaml later."
        )
        return False

    except (KeyboardInterrupt, EOFError):
        # Ctrl+C or closed stdin (EOF).  Newline first so the skip
        # notice renders on its own line rather than inline with the
        # abandoned prompt, then render the standard skip UX.
        console.print()
        _show_skip_notice(console)
        return False


def _persist_key(console: Console, data_dir: Path, key: str) -> bool:
    """Write the key to settings.yaml with error handling.

    Returns ``True`` on a successful write, ``False`` on filesystem
    failure.  Does NOT print any success confirmation — the caller
    owns the wording (success vs. rate-limit-soft-pass are different
    stories).  Failure messages are surfaced here because both paths
    ("data dir missing" vs "permissions denied") are identical across
    callers.

    ``FileNotFoundError`` (subclass of ``OSError``) is distinguished
    from other filesystem errors because its remedy is different —
    the data directory is missing, which means ``setup.bat`` was not
    run, not that permissions are wrong.

    ``write_api_key`` may also raise ``OSError`` translated from a
    malformed YAML file or a non-mapping root; the message steers the
    user toward inspecting the file, which covers both cases.
    """
    try:
        write_api_key(data_dir, key)
    except FileNotFoundError:
        console.print(
            f"[red]\u2717[/red] Data directory missing ({data_dir}). "
            "Re-run setup.bat from the repository root to create it."
        )
        logger.debug("settings.yaml not found", exc_info=True)
        return False
    except OSError:
        console.print(
            f"[red]\u2717[/red] Could not save API key to settings.yaml. "
            f"Check file permissions in {data_dir}."
        )
        logger.debug("Failed to write API key to settings.yaml", exc_info=True)
        return False
    return True


def _show_skip_notice(console: Console) -> None:
    """Display the single-line skip notice."""
    console.print(
        "[yellow]\u26a0[/yellow] Cloud reasoning unavailable. "
        "N.O.V.A. will run in offline mode. "
        "Add your key to settings.yaml later."
    )
