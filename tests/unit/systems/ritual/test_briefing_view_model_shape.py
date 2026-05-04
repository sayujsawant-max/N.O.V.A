"""Shape regression test for ``BriefingViewModel`` (Story 3.3 reshape).

Locks the field tuple, type annotations, and ``frozen=True`` flag against
accidental drift. Also pins the **negative regression** that the four
removed raw-component names (``seed_text``, ``last_mode``,
``last_duration_seconds``, ``last_duration_display``, ``last_apps``) are
NOT present on the dataclass — re-introducing any of them would betray
the pre-rendered-labels boundary the reshape established.
"""

from __future__ import annotations

import dataclasses
import typing

from nova.systems.ritual.models import BriefingViewModel


def test_briefing_view_model_field_set_matches_ac_3_3() -> None:
    """Story 3.3 AC #3 — exact 13-field shape in declaration order.

    Catches:
    - field added or removed
    - field reordered (positional construction in tests would silently
      bind to the wrong field)
    - field renamed
    """
    expected: tuple[str, ...] = (
        "state",
        "tier",
        "title",
        "auto_start_setup",
        "intro_lines",
        "seed_quote",
        "last_session_label",
        "last_apps_label",
        "available_modes_label",
        "prose_enrichment",
        "prompt_text",
        "available_modes",
        "suggested_mode",
    )
    actual = tuple(f.name for f in dataclasses.fields(BriefingViewModel))
    assert actual == expected, (
        f"BriefingViewModel field shape drifted from Story 3.3 AC #3.\n"
        f"  expected: {expected}\n"
        f"  actual:   {actual}"
    )


def test_briefing_view_model_label_field_types() -> None:
    """Pre-rendered label fields are typed as ``str | None`` or ``tuple[str, ...]``.

    No raw component types (``int`` for seconds, ``timedelta`` for
    duration, ``list[str]`` for apps) may sneak back onto the dataclass.
    """
    hints = typing.get_type_hints(BriefingViewModel)
    # intro_lines: locked-copy preface lines for State A / B.
    assert hints["intro_lines"] == tuple[str, ...]
    # All other rendered label fields are str | None.
    for field_name in (
        "seed_quote",
        "last_session_label",
        "last_apps_label",
        "available_modes_label",
        "prose_enrichment",
        "prompt_text",
    ):
        assert hints[field_name] == (str | None), (
            f"{field_name} must be `str | None` (pre-rendered label or omission signal); "
            f"got {hints[field_name]!r}"
        )


def test_briefing_view_model_is_still_frozen() -> None:
    """The reshape must not silently relax the immutability invariant."""
    # ``__dataclass_params__`` is an undocumented runtime attribute on
    # every ``@dataclass`` class; mypy doesn't see it on the type stub,
    # so use ``getattr`` to satisfy strict checking.
    params = getattr(BriefingViewModel, "__dataclass_params__", None)
    assert params is not None, "BriefingViewModel is no longer a dataclass"
    assert params.frozen is True, "BriefingViewModel lost frozen=True during the reshape"


def test_briefing_view_model_does_not_carry_raw_component_fields() -> None:
    """Negative regression — the four removed raw-component names must stay removed.

    Re-introducing any of these would betray the Story 3.3 boundary that
    Ritual produces every visible character. The ``last_duration_display``
    intermediate name is also forbidden — the reshape skipped that step
    in favor of the full ``last_session_label`` line.
    """
    removed = {
        "seed_text",
        "last_mode",
        "last_duration_seconds",
        "last_duration_display",
        "last_apps",
    }
    field_names = {f.name for f in dataclasses.fields(BriefingViewModel)}
    leaked = removed & field_names
    assert not leaked, (
        f"Raw-component fields re-introduced on BriefingViewModel: {sorted(leaked)}. "
        "Story 3.3 reshape requires Ritual to produce pre-rendered labels — "
        "no raw components cross the Ritual → Skin boundary."
    )
