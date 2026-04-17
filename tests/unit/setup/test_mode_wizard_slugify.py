"""Unit tests for slugify_mode_name + validate_mode_stem (Story 2.3 Task 2 / AC #7, #10, #21).

The slugify output must be acceptable to ``core/config.py:_is_valid_mode_stem``
— that is the loader's contract and this module must produce stems the
loader will accept.
"""

from __future__ import annotations

import pytest

from nova.core.config import _is_valid_mode_stem
from nova.setup.mode_wizard import slugify_mode_name, validate_mode_stem


class TestSlugifyHappyPaths:
    """Common inputs produce the expected stems."""

    @pytest.mark.parametrize(
        ("display_name", "expected_stem"),
        [
            ("coding", "coding"),
            ("Coding", "coding"),
            ("CODING", "coding"),
            ("study group", "study-group"),
            ("Study Group", "study-group"),
            ("Study  Group", "study-group"),  # multiple spaces collapse
            ("study_group", "study-group"),  # underscores → hyphens
            ("study-group", "study-group"),  # already kebab-case
            ("   coding   ", "coding"),  # leading/trailing whitespace
            ("my mode 1", "my-mode-1"),
            ("mode-1-2-3", "mode-1-2-3"),
        ],
    )
    def test_display_name_to_stem(self, display_name: str, expected_stem: str) -> None:
        assert slugify_mode_name(display_name) == expected_stem

    def test_consecutive_hyphens_collapse(self) -> None:
        assert slugify_mode_name("study---group") == "study-group"
        assert slugify_mode_name("study-_- group") == "study-group"

    def test_leading_trailing_hyphens_stripped(self) -> None:
        assert slugify_mode_name("-coding-") == "coding"
        assert slugify_mode_name("---coding---") == "coding"
        assert slugify_mode_name("_coding_") == "coding"

    def test_non_alphanumeric_characters_dropped(self) -> None:
        # Characters outside [a-z0-9-] are dropped entirely
        assert slugify_mode_name("coding!") == "coding"
        assert slugify_mode_name("coding@home") == "codinghome"
        assert slugify_mode_name("my/mode") == "mymode"


class TestSlugifyEdgeCases:
    """Inputs that produce unusable stems still return a string; validation catches them."""

    @pytest.mark.parametrize("display_name", ["", "   ", "***", "!!!!", "---", "___"])
    def test_meaningless_inputs_produce_empty_stem(self, display_name: str) -> None:
        """These inputs slugify to '' — the caller uses validate_mode_stem to reject."""
        assert slugify_mode_name(display_name) == ""


class TestValidateModeStemValid:
    """Valid stems return None."""

    @pytest.mark.parametrize(
        "stem",
        # ``"1"`` is explicitly listed to lock the contract that a
        # single-digit stem is valid per the ``[a-z0-9][a-z0-9-]*``
        # regex — it's easy to lose in future refactors of the rule.
        ["coding", "study", "study-group", "mode1", "mode-1", "a", "a1", "a-b-c", "1"],
    )
    def test_valid_stem_returns_none(self, stem: str) -> None:
        assert validate_mode_stem(stem) is None

    @pytest.mark.parametrize(
        "stem",
        ["coding", "study-group", "mode1", "a-b-c"],
    )
    def test_validated_stems_accepted_by_loader(self, stem: str) -> None:
        """The loader's acceptance is the contract — every valid stem must pass it."""
        assert _is_valid_mode_stem(stem) is True


class TestValidateModeStemInvalid:
    """Invalid stems return a specific error message."""

    def test_empty_stem_rejected(self) -> None:
        msg = validate_mode_stem("")
        assert msg is not None and "empty" in msg.lower()

    @pytest.mark.parametrize(
        "reserved_name",
        [
            "con",
            "CON",
            "Con",
            "prn",
            "PRN",
            "aux",
            "AUX",
            "nul",
            "NUL",
            "com1",
            "COM1",
            "com9",
            "COM9",
            "lpt1",
            "LPT1",
            "lpt9",
            "LPT9",
        ],
    )
    def test_reserved_windows_names_rejected(self, reserved_name: str) -> None:
        msg = validate_mode_stem(reserved_name)
        assert msg is not None and "reserved" in msg.lower()

    def test_period_rejected(self) -> None:
        """Periods are not valid in stems — they split the filename into stem+suffix."""
        msg = validate_mode_stem("my.mode")
        assert msg is not None and "period" in msg.lower()

    @pytest.mark.parametrize(
        "invalid_stem",
        [
            "-coding",  # leading hyphen
            "Coding",  # uppercase
            "coding_mode",  # underscore
            "coding mode",  # space
            "coding/mode",  # slash
        ],
    )
    def test_non_kebab_case_rejected(self, invalid_stem: str) -> None:
        msg = validate_mode_stem(invalid_stem)
        assert msg is not None

    def test_loader_rejects_what_we_reject(self) -> None:
        """Negative contract: if we reject a stem, the loader must too (no divergence)."""
        for bad in ["", "con", "CON", "my.mode", "-coding", "Coding", "coding_mode"]:
            # Our validator returns a message (non-None) for these.
            our_reject = validate_mode_stem(bad) is not None
            # Loader also rejects.
            loader_reject = _is_valid_mode_stem(bad) is False
            assert our_reject == loader_reject, (
                f"Divergence on {bad!r}: our_reject={our_reject}, loader_reject={loader_reject}"
            )


class TestSlugifyValidateRoundTrip:
    """For valid user inputs, slugify → validate → loader-accept is the contract."""

    @pytest.mark.parametrize(
        "display_name",
        ["coding", "Study Group", "my mode 1", "Research-Papers", "gym time"],
    )
    def test_slugified_name_passes_validation(self, display_name: str) -> None:
        stem = slugify_mode_name(display_name)
        assert validate_mode_stem(stem) is None
        assert _is_valid_mode_stem(stem) is True
