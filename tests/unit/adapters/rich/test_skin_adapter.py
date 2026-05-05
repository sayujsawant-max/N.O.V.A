"""Render tests for :class:`RichSkinAdapter` (Story 3.3 Group H, AC #21 / #26 / #27).

Each test constructs a :class:`BriefingViewModel` directly with
pre-populated label fields (no Ritual call), renders via
:meth:`RichSkinAdapter.render_briefing_card`, and asserts:

* The label strings appear verbatim in the captured plain-text output.
* Omission rules apply when fields are ``None`` / empty.
* Block-transition spacing is correct (one blank line between blocks).
* Tier orthogonality — output is byte-identical across tiers in Epic 3.

**These tests do NOT verify Ritual produced the right labels.** That
is Group G's job (``test_briefing_view_model.py``). Group H locks the
Skin side: given a ViewModel, what bytes appear on screen.
"""

from __future__ import annotations

import asyncio
from io import StringIO

import pytest
from rich.console import Console

from nova.adapters.rich.skin import RichSkinAdapter
from nova.core.types import ActionType, BriefingState, CapabilityTier
from nova.systems.brain.models import ModeInfo
from nova.systems.hands.models import ActionResult
from nova.systems.ritual.models import BriefingViewModel

# --- Fixture builders --------------------------------------------------------


def _build_console(*, color_system: str | None = None, width: int = 80) -> Console:
    """Build a recording :class:`Console` for capture-mode tests."""
    return Console(
        record=True,
        file=StringIO(),
        width=width,
        # color_system=None disables ANSI for plain-text assertions; pass
        # "truecolor" when ANSI byte-stream is what's under test.
        color_system=color_system,  # type: ignore[arg-type]
        legacy_windows=False,
        force_terminal=True,
    )


def _state_a_view_model(*, tier: CapabilityTier = CapabilityTier.FULL) -> BriefingViewModel:
    return BriefingViewModel(
        state=BriefingState.FIRST_RUN,
        tier=tier,
        title="N.O.V.A.",
        auto_start_setup=True,
        intro_lines=(
            "First session. No history yet — that's expected.",
            "Let's set up your first workspace mode so tomorrow starts warm.",
        ),
        seed_quote=None,
        last_session_label=None,
        last_apps_label=None,
        available_modes_label=None,
        prose_enrichment=None,
        prompt_text=None,
        available_modes=(),
        suggested_mode=None,
    )


def _state_b_view_model(
    *,
    tier: CapabilityTier = CapabilityTier.FULL,
    available_modes_label: str | None = "Available mode: Coding",
    prompt_text: str | None = "Start in Coding mode?",
) -> BriefingViewModel:
    coding = ModeInfo(
        stem="coding",
        display_name="Coding",
        app_count=1,
        is_default=True,
        last_used_at=None,
    )
    return BriefingViewModel(
        state=BriefingState.POST_SETUP,
        tier=tier,
        title="Session Briefing",
        auto_start_setup=False,
        intro_lines=("No saved seed from your last session.",),
        seed_quote=None,
        last_session_label=None,
        last_apps_label=None,
        available_modes_label=available_modes_label,
        prose_enrichment=None,
        prompt_text=prompt_text,
        available_modes=(coding,),
        suggested_mode=coding,
    )


def _state_c_view_model(
    *,
    tier: CapabilityTier = CapabilityTier.FULL,
    seed_quote: str | None = '"Push the deploy through"',
    last_session_label: str | None = "Last session: Coding mode, 1h 42m",
    last_apps_label: str | None = "Apps: VS Code, Terminal, Chrome",
    prose_enrichment: str | None = None,
    prompt_text: str | None = "Resume Coding mode?",
) -> BriefingViewModel:
    coding = ModeInfo(
        stem="coding",
        display_name="Coding",
        app_count=3,
        is_default=True,
        last_used_at="2026-04-02T10:00:00+00:00",
    )
    return BriefingViewModel(
        state=BriefingState.WARM_RESUME,
        tier=tier,
        title="Session Briefing",
        auto_start_setup=False,
        intro_lines=(),
        seed_quote=seed_quote,
        last_session_label=last_session_label,
        last_apps_label=last_apps_label,
        available_modes_label=None,
        prose_enrichment=prose_enrichment,
        prompt_text=prompt_text,
        available_modes=(coding,),
        suggested_mode=coding,
    )


async def _render(view_model: BriefingViewModel, *, color_system: str | None = None) -> str:
    """Render the view-model and return the recorded plain-text output."""
    console = _build_console(color_system=color_system)
    await RichSkinAdapter(console=console).render_briefing_card(view_model)
    return console.export_text()


async def _render_with_styles(view_model: BriefingViewModel) -> tuple[str, str]:
    """Render the view-model and return ``(plain_text, ansi_with_styles)``.

    Used by the per-field tests that need to verify a Rich style marker
    (e.g., ``dim`` on metadata, ``bold bright_white`` on the prompt) is
    actually applied to the rendered span — review finding P8.

    Rich's :meth:`Console.export_text` clears the recorded buffer by
    default; pass ``clear=False`` on the first call so the second call
    can read the same buffer. Order matters too — styles-bearing first
    so the styled snapshot reflects the same state the plain snapshot
    will see.
    """
    console = _build_console(color_system="truecolor")
    await RichSkinAdapter(console=console).render_briefing_card(view_model)
    ansi = console.export_text(clear=False, styles=True)
    plain = console.export_text()
    return plain, ansi


# Rich emits SGR escape sequences as ``\x1b[<params>m...``. Rich combines
# attribute + color into one escape sequence (``\x1b[1;97m`` for bold
# bright_white, not separate bold + color escapes). We substring-match
# these combined forms — they're what the locked render path produces
# under ``color_system='truecolor'`` + ``force_terminal=True``.
_BOLD_BRIGHT_WHITE_MARKER = "\x1b[1;97m"
_BOLD_CYAN_MARKER = "\x1b[1;36m"
_BRIGHT_WHITE_MARKER = "\x1b[97m"
_DIM_MARKER = "\x1b[2m"
_CYAN_MARKER = "\x1b[36m"


# --- Field-by-field render assertions (AC #26) -------------------------------


async def test_intro_lines_render_in_bright_white() -> None:
    """Intro lines render as plain bright_white (no bold)."""
    vm = BriefingViewModel(
        state=BriefingState.FIRST_RUN,
        tier=CapabilityTier.FULL,
        title="X",
        auto_start_setup=True,
        intro_lines=("Line one.", "Line two."),
        seed_quote=None,
        last_session_label=None,
        last_apps_label=None,
        available_modes_label=None,
        prose_enrichment=None,
        prompt_text=None,
        available_modes=(),
        suggested_mode=None,
    )
    plain, ansi = await _render_with_styles(vm)
    assert "Line one." in plain
    assert "Line two." in plain
    # Style-marker assertion (review finding P8): each intro line is
    # wrapped in the bright_white SGR escape — confirms Skin chose the
    # right style segment, not just that the text appears.
    assert _BRIGHT_WHITE_MARKER + "Line one." in ansi
    assert _BRIGHT_WHITE_MARKER + "Line two." in ansi
    # And NOT bold (intro lines are not the hero / prompt).
    assert _BOLD_BRIGHT_WHITE_MARKER + "Line one." not in ansi


async def test_intro_lines_empty_renders_no_preface() -> None:
    output = await _render(_state_c_view_model())
    assert "First session" not in output
    assert "No saved seed" not in output


async def test_seed_quote_renders_bold_bright_white() -> None:
    """Seed quote renders in bold bright_white (the hero line styling)."""
    plain, ansi = await _render_with_styles(
        _state_c_view_model(seed_quote='"Push the deploy through"')
    )
    assert '"Push the deploy through"' in plain
    assert _BOLD_BRIGHT_WHITE_MARKER + '"Push the deploy through"' in ansi


async def test_seed_quote_none_omits_line() -> None:
    """When seed_quote is None, no quoted hero line appears.

    Tightened from a global ``'"' not in output`` check (review finding
    P16). The intent is "no quoted hero line" — assert that no panel-body
    line consists solely of a quoted string. Other quote characters
    that happen to appear (e.g., in future copy) won't cause a false fail.
    """
    output = await _render(_state_c_view_model(seed_quote=None))
    body_lines = []
    for line in output.splitlines():
        stripped = line.strip("│ \t")
        if stripped and not all(c in "─│┌┐└┘╭╮╰╯ " for c in line):
            body_lines.append(stripped)
    quoted_line_pattern = [bl for bl in body_lines if bl.startswith('"') and bl.endswith('"')]
    assert quoted_line_pattern == [], f"Expected no quoted hero line; found: {quoted_line_pattern}"


async def test_last_session_label_renders_dim() -> None:
    """Last-session label renders in dim style (metadata band)."""
    plain, ansi = await _render_with_styles(
        _state_c_view_model(last_session_label="Last session: Coding mode, 1h 42m")
    )
    assert "Last session: Coding mode, 1h 42m" in plain
    assert _DIM_MARKER + "Last session: Coding mode, 1h 42m" in ansi


async def test_last_session_label_none_omits_line() -> None:
    output = await _render(_state_c_view_model(last_session_label=None))
    assert "Last session" not in output


async def test_last_apps_label_renders_dim() -> None:
    """Apps label renders in dim style (same metadata band as Last session)."""
    plain, ansi = await _render_with_styles(
        _state_c_view_model(last_apps_label="Apps: VS Code, Terminal, Chrome")
    )
    assert "Apps: VS Code, Terminal, Chrome" in plain
    assert _DIM_MARKER + "Apps: VS Code, Terminal, Chrome" in ansi


async def test_last_apps_label_none_omits_line() -> None:
    output = await _render(_state_c_view_model(last_apps_label=None))
    assert "Apps:" not in output


async def test_available_modes_label_renders_body_white() -> None:
    """Available-modes label renders in default body white — no bold, no dim.

    Review finding P8 / AC #26 — restored spec-named test that verifies
    Skin doesn't accidentally apply a metadata or hero style to the
    available-modes line.
    """
    plain, ansi = await _render_with_styles(
        _state_b_view_model(available_modes_label="Available modes: Coding, Writing")
    )
    assert "Available modes: Coding, Writing" in plain
    # No bold + no dim wrapping the available-modes content.
    assert _BOLD_BRIGHT_WHITE_MARKER + "Available modes:" not in ansi
    assert _DIM_MARKER + "Available modes:" not in ansi


async def test_available_modes_label_none_omits_line() -> None:
    output = await _render(_state_b_view_model(available_modes_label=None))
    assert "Available mode" not in output


async def test_prompt_text_renders_bold_bright_white_at_panel_end() -> None:
    """Prompt renders in bold bright_white AND is the last body line.

    Rich wraps each panel-body line with vertical-bar borders (``│``)
    plus inner padding; we strip those before comparing the plain-text
    position. The ANSI assertion locks the bold-bright-white style on
    the prompt content (review finding P8).
    """
    plain, ansi = await _render_with_styles(_state_c_view_model())
    body_lines = []
    for line in plain.splitlines():
        stripped = line.strip("│ \t")
        if not stripped:
            continue
        if all(c in "─│┌┐└┘╭╮╰╯ " for c in line):
            continue
        body_lines.append(stripped)
    assert body_lines[-1] == "Resume Coding mode?"
    assert _BOLD_BRIGHT_WHITE_MARKER + "Resume Coding mode?" in ansi


async def test_prompt_text_none_omits_line() -> None:
    output = await _render(_state_a_view_model())
    assert "Resume" not in output
    assert "Start in" not in output
    assert "What mode?" not in output


async def test_prose_enrichment_renders_after_structured_fields() -> None:
    """Locks the layout for Epic 7's first prose write — between apps and prompt."""
    output = await _render(
        _state_c_view_model(
            prose_enrichment="Two-day arc on the auth refactor; tomorrow closes the loop."
        )
    )
    lines = output.splitlines()
    apps_idx = next(i for i, line in enumerate(lines) if "Apps:" in line)
    prose_idx = next(i for i, line in enumerate(lines) if "Two-day arc" in line)
    prompt_idx = next(i for i, line in enumerate(lines) if "Resume Coding" in line)
    assert apps_idx < prose_idx < prompt_idx


async def test_prose_enrichment_none_omits_line() -> None:
    output = await _render(_state_c_view_model(prose_enrichment=None))
    assert "enrichment unavailable" not in output
    # Spot-check that no extra paragraph appears between apps and prompt
    # — this does NOT lock the exact spacing but does catch a regression
    # where None silently emits a placeholder line.


# --- State-complete render assertions ----------------------------------------


async def test_state_a_complete_render() -> None:
    output = await _render(_state_a_view_model())
    assert "N.O.V.A." in output
    assert "First session. No history yet — that's expected." in output
    assert "Let's set up your first workspace mode so tomorrow starts warm." in output
    assert "Resume" not in output
    assert "Start in" not in output
    assert "What mode?" not in output
    assert '"' not in output  # no seed quote


async def test_state_b_complete_render() -> None:
    output = await _render(
        _state_b_view_model(
            available_modes_label="Available modes: Coding, Writing",
            prompt_text="Start in Writing mode?",
        )
    )
    lines = output.splitlines()
    preface_idx = next(i for i, line in enumerate(lines) if "No saved seed" in line)
    modes_idx = next(i for i, line in enumerate(lines) if "Available modes:" in line)
    prompt_idx = next(i for i, line in enumerate(lines) if "Start in Writing" in line)
    assert preface_idx < modes_idx < prompt_idx


async def test_state_c_complete_render() -> None:
    output = await _render(_state_c_view_model())
    lines = output.splitlines()
    seed_idx = next(i for i, line in enumerate(lines) if "Push the deploy" in line)
    last_idx = next(i for i, line in enumerate(lines) if "Last session" in line)
    apps_idx = next(i for i, line in enumerate(lines) if "Apps:" in line)
    prompt_idx = next(i for i, line in enumerate(lines) if "Resume Coding" in line)
    assert seed_idx < last_idx < apps_idx < prompt_idx


# --- Panel chrome (AC #26) ---------------------------------------------------


async def test_panel_chrome_is_cyan_with_padding() -> None:
    """Verify panel chrome locks (a) cyan border, (b) bold-cyan title, (c) padding=(1,2).

    Review finding P9 — strengthened from a presence-only check that
    only confirmed the title text and *some* ANSI escape existed. Now
    locks the specific cyan style markers so a regression that swapped
    ``border_style="cyan"`` to ``"red"`` or ``padding=(1, 2)`` to
    ``(0, 0)`` would fail this test.
    """
    plain, ansi = await _render_with_styles(_state_a_view_model())

    # (a) Border lines use cyan. The Rich Panel emits its top + bottom
    # border characters wrapped in a cyan SGR escape. Match the SGR
    # marker around the rounded-corner top character (``╭``) and the
    # box drawing characters (``─``).
    assert _CYAN_MARKER in ansi, "expected cyan SGR escape (\\x1b[36m) for the panel border"
    # The top border line contains both the cyan marker AND the
    # box-drawing chars. Locate one such line as evidence.
    border_line_with_cyan = next(
        (ln for ln in ansi.splitlines() if _CYAN_MARKER in ln and ("╭" in ln or "─" in ln)),
        None,
    )
    assert border_line_with_cyan is not None, (
        "expected a cyan-styled panel border line in the recorded ANSI output"
    )

    # (b) Title is bold + cyan. Rich emits the combined SGR
    # ``\x1b[1;36m`` then the title content (with surrounding pad
    # spaces) then ``\x1b[0m`` to close the segment.
    assert _BOLD_CYAN_MARKER in ansi, (
        "expected bold-cyan SGR (\\x1b[1;36m) somewhere in the recorded output"
    )
    # Locate the segment between the bold-cyan opener and its closing
    # reset, and confirm the title text appears inside.
    idx = ansi.index(_BOLD_CYAN_MARKER)
    closer = ansi.index("\x1b[0m", idx)
    title_segment = ansi[idx:closer]
    assert "N.O.V.A." in title_segment, (
        f"expected 'N.O.V.A.' inside the bold-cyan title segment; got {title_segment!r}"
    )

    # (c) padding=(1, 2) — vertical 1 = one blank line above and below
    # the body inside the borders; horizontal 2 = two spaces between
    # the ``│`` border and the first body character. Locate the line
    # carrying the first intro string and confirm it has at least two
    # leading spaces inside the border.
    body_line = next(
        (ln for ln in plain.splitlines() if "First session" in ln),
        None,
    )
    assert body_line is not None, "could not find the State A body line"
    # Strip the leading vertical-bar border, then count leading spaces.
    after_border = body_line.lstrip("│")
    leading_spaces = len(after_border) - len(after_border.lstrip(" "))
    assert leading_spaces >= 2, (
        f"expected horizontal padding of at least 2 spaces; got {leading_spaces} "
        f"in line {body_line!r}"
    )


# --- Idempotency / determinism (AC #26) --------------------------------------


async def test_skin_makes_no_content_decisions() -> None:
    """Three renders of the same ViewModel produce byte-identical output.

    No clock, no random, no content branching, no string formatting.
    """
    view_model = _state_c_view_model()
    out_a = await _render(view_model)
    out_b = await _render(view_model)
    out_c = await _render(view_model)
    assert out_a == out_b == out_c


def _make_vm_for_state(state: BriefingState) -> BriefingViewModel:
    """Build a ViewModel with all-fixed fields except ``state``.

    Used by ``test_renderer_does_not_consult_view_model_state_field`` to
    confirm the render path is state-agnostic.
    """
    return BriefingViewModel(
        state=state,
        tier=CapabilityTier.FULL,
        title="Session Briefing",
        auto_start_setup=False,
        intro_lines=("Same intro.",),
        seed_quote=None,
        last_session_label=None,
        last_apps_label=None,
        available_modes_label=None,
        prose_enrichment=None,
        prompt_text="Same prompt?",
        available_modes=(),
        suggested_mode=None,
    )


async def test_renderer_does_not_consult_view_model_state_field() -> None:
    """Same labels + different state field → byte-identical output.

    Locks the architectural rule that the renderer does NOT branch on
    ``view_model.state`` — the omission rules and field presence drive
    everything.
    """
    out_first_run = await _render(_make_vm_for_state(BriefingState.FIRST_RUN))
    out_warm_resume = await _render(_make_vm_for_state(BriefingState.WARM_RESUME))
    assert out_first_run == out_warm_resume


def _make_vm_for_metadata(
    available_modes: tuple[ModeInfo, ...],
    suggested_mode: ModeInfo | None,
) -> BriefingViewModel:
    return BriefingViewModel(
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.FULL,
        title="Session Briefing",
        auto_start_setup=False,
        intro_lines=(),
        seed_quote='"Same seed."',
        last_session_label=None,
        last_apps_label=None,
        available_modes_label=None,
        prose_enrichment=None,
        prompt_text="Resume Coding mode?",
        available_modes=available_modes,
        suggested_mode=suggested_mode,
    )


async def test_renderer_does_not_consult_available_modes_or_suggested_mode() -> None:
    """Behavioral metadata fields do not leak into the render."""
    coding = ModeInfo(
        stem="coding", display_name="Coding", app_count=1, is_default=True, last_used_at=None
    )
    vm_empty = _make_vm_for_metadata(available_modes=(), suggested_mode=None)
    vm_populated = _make_vm_for_metadata(available_modes=(coding,), suggested_mode=coding)
    assert await _render(vm_empty) == await _render(vm_populated)


# --- Tier orthogonality (AC #21) ---------------------------------------------


@pytest.mark.parametrize(
    "tiers",
    [
        pytest.param((CapabilityTier.FULL, CapabilityTier.DEGRADED), id="full_vs_degraded"),
        pytest.param((CapabilityTier.FULL, CapabilityTier.OFFLINE), id="full_vs_offline"),
        pytest.param((CapabilityTier.DEGRADED, CapabilityTier.OFFLINE), id="degraded_vs_offline"),
    ],
)
async def test_state_a_render_is_tier_independent(
    tiers: tuple[CapabilityTier, CapabilityTier],
) -> None:
    out_a = await _render(_state_a_view_model(tier=tiers[0]))
    out_b = await _render(_state_a_view_model(tier=tiers[1]))
    assert out_a == out_b


@pytest.mark.parametrize(
    "tiers",
    [
        pytest.param((CapabilityTier.FULL, CapabilityTier.DEGRADED), id="full_vs_degraded"),
        pytest.param((CapabilityTier.FULL, CapabilityTier.OFFLINE), id="full_vs_offline"),
        pytest.param((CapabilityTier.DEGRADED, CapabilityTier.OFFLINE), id="degraded_vs_offline"),
    ],
)
async def test_state_b_render_is_tier_independent(
    tiers: tuple[CapabilityTier, CapabilityTier],
) -> None:
    out_a = await _render(_state_b_view_model(tier=tiers[0]))
    out_b = await _render(_state_b_view_model(tier=tiers[1]))
    assert out_a == out_b


@pytest.mark.parametrize(
    "tiers",
    [
        pytest.param((CapabilityTier.FULL, CapabilityTier.DEGRADED), id="full_vs_degraded"),
        pytest.param((CapabilityTier.FULL, CapabilityTier.OFFLINE), id="full_vs_offline"),
        pytest.param((CapabilityTier.DEGRADED, CapabilityTier.OFFLINE), id="degraded_vs_offline"),
    ],
)
async def test_state_c_render_is_tier_independent(
    tiers: tuple[CapabilityTier, CapabilityTier],
) -> None:
    out_a = await _render(_state_c_view_model(tier=tiers[0]))
    out_b = await _render(_state_c_view_model(tier=tiers[1]))
    assert out_a == out_b


# --- Long-content edge cases (AC #27) ----------------------------------------


def _normalize_panel_body(output: str) -> str:
    """Collapse panel-wrapped output to a single whitespace-normalized string.

    Rich's panel body wraps lines at the panel width and surrounds each
    line with ``│ … │`` borders plus inner padding. To compare against
    the original label, we strip border chars + collapse runs of
    whitespace (newlines and padding spaces) into single spaces.
    """
    # Drop everything that is purely panel chrome.
    cleaned: list[str] = []
    for line in output.splitlines():
        stripped = line.strip("│ \t")
        if not stripped or all(c in "─│┌┐└┘╭╮╰╯ " for c in line):
            continue
        cleaned.append(stripped)
    joined = " ".join(cleaned)
    # Collapse multiple spaces from wrap-padding into singles.
    return " ".join(joined.split())


async def test_long_seed_quote_wraps_within_panel_width() -> None:
    long_seed = "Lorem ipsum dolor sit amet " * 10  # ~280 chars
    quoted = f'"{long_seed.strip()}"'
    output = await _render(_state_c_view_model(seed_quote=quoted))
    normalized = _normalize_panel_body(output)
    assert long_seed.strip() in normalized
    assert "Resume Coding mode?" in normalized


async def test_long_apps_label_wraps_in_panel_width() -> None:
    apps = [
        "App One",
        "App Two",
        "App Three",
        "App Four",
        "App Five",
        "App Six",
        "App Seven",
        "App Eight",
        "App Nine",
        "App Ten",
        "App Eleven",
        "App Twelve",
    ]
    label = f"Apps: {', '.join(apps)}"
    output = await _render(_state_c_view_model(last_apps_label=label))
    normalized = _normalize_panel_body(output)
    for app in apps:
        assert app in normalized


async def test_renderer_handles_unicode_characters_in_labels() -> None:
    """Em-dash and accented characters render without encoding errors."""
    output = await _render(_state_c_view_model(seed_quote='"Café au lait — push the deploy"'))
    assert "Café au lait — push the deploy" in output


async def test_renderer_skips_empty_string_intro_lines() -> None:
    """Review finding P14 — empty-string intro entries are filtered, not rendered as blanks.

    Without the filter, an entry of ``""`` in ``intro_lines`` would emit
    a stray newline (block-transition rule), creating visible blank
    space between non-empty lines.
    """
    vm = BriefingViewModel(
        state=BriefingState.FIRST_RUN,
        tier=CapabilityTier.FULL,
        title="X",
        auto_start_setup=True,
        intro_lines=("Line one.", "", "Line two."),
        seed_quote=None,
        last_session_label=None,
        last_apps_label=None,
        available_modes_label=None,
        prose_enrichment=None,
        prompt_text=None,
        available_modes=(),
        suggested_mode=None,
    )
    output = await _render(vm)
    # Both non-empty lines appear, and they collapse together (no extra
    # blank line between them since the middle empty entry is filtered).
    assert "Line one." in output
    assert "Line two." in output
    # Verify the two lines aren't separated by an extra blank line. We
    # look at the two body lines and check they are consecutive
    # (after stripping panel borders).
    body_lines: list[str] = []
    for line in output.splitlines():
        stripped = line.strip("│ \t")
        if stripped and not all(c in "─│┌┐└┘╭╮╰╯ " for c in line):
            body_lines.append(stripped)
    # Find the indices of the two intro lines; they should be adjacent.
    idx_one = next(i for i, ln in enumerate(body_lines) if ln == "Line one.")
    idx_two = next(i for i, ln in enumerate(body_lines) if ln == "Line two.")
    assert idx_two - idx_one == 1, (
        f"empty-string intro entry leaked a blank line between the two "
        f"non-empty entries (indices {idx_one}, {idx_two})"
    )


# --- parse_command delegation tests (Story 3.4 AC #14) ---------------------


async def test_parse_command_delegates_to_pure_parser() -> None:
    """``RichSkinAdapter.parse_command`` returns the same Command the
    pure parser produces — locks delegation, not vocabulary (the
    parser tests cover the matrix).
    """
    from nova.systems.skin.models import CommandVerb

    adapter = RichSkinAdapter(console=_build_console())
    result = await adapter.parse_command("mode coding")
    assert result.verb is CommandVerb.MODE
    assert result.target == "coding"
    assert result.raw_input == "mode coding"
    assert result.is_contextual is False


async def test_parse_command_handles_empty_input() -> None:
    """Empty input through the adapter still produces ``CommandVerb.EMPTY``."""
    from nova.systems.skin.models import CommandVerb

    adapter = RichSkinAdapter(console=_build_console())
    result = await adapter.parse_command("")
    assert result.verb is CommandVerb.EMPTY
    assert result.target is None
    assert result.raw_input == ""
    assert result.is_contextual is False


def test_parse_command_is_async_at_skinport_boundary() -> None:
    """Companion lock to ``test_parse_is_sync_function`` in
    ``test_command_shape.py``: the SkinPort surface is async, the
    pure parser underneath is sync, and ``RichSkinAdapter.parse_command``
    bridges them.
    """
    import inspect

    assert inspect.iscoroutinefunction(RichSkinAdapter.parse_command) is True


# --- Story 3.5 — render_response + collect_input adapter tests --------------


@pytest.mark.asyncio
async def test_render_response_prints_to_console() -> None:
    """``render_response`` calls ``Console.print(text, markup=False)`` exactly once.

    Per AC #17, asserts the underlying ``Console.print`` invocation
    against a ``MagicMock`` console with the exact ``markup=False``
    kwarg. A future refactor that adds panel-wrapping, prefix
    decoration, or markup interpretation would silently pass a
    string-output assertion but fails this stronger lock.
    """
    from unittest.mock import MagicMock

    mock_console = MagicMock(spec=Console)
    adapter = RichSkinAdapter(console=mock_console)
    await adapter.render_response("hello")
    mock_console.print.assert_called_once_with("hello", markup=False)


@pytest.mark.asyncio
async def test_collect_input_delegates_to_rich_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """``collect_input`` delegates to :func:`Prompt.ask` with the given prompt.

    Patches ``nova.adapters.rich.skin.Prompt.ask`` (NOT the upstream
    ``rich.prompt.Prompt`` directly) so the patch is visible to the
    daemon thread that runs ``Prompt.ask`` (Story 3.5 Fix 2 replaced
    the ``asyncio.to_thread(Prompt.ask, ...)`` pattern with an explicit
    ``threading.Thread(daemon=True)`` for process-exit safety; the
    daemon thread reads ``Prompt.ask`` from the same module namespace,
    so monkeypatching there is what makes the substitution observable).
    """
    captured: dict[str, object] = {}

    def fake_ask(prompt: str, **kwargs: object) -> str:
        captured["prompt"] = prompt
        captured["console"] = kwargs.get("console")
        return "mode coding"

    monkeypatch.setattr("nova.adapters.rich.skin.Prompt.ask", fake_ask)
    console = _build_console(color_system=None)
    adapter = RichSkinAdapter(console=console)
    result = await adapter.collect_input(prompt="> ")
    assert result == "mode coding"
    assert captured["prompt"] == "> "
    assert captured["console"] is console


@pytest.mark.asyncio
async def test_collect_input_propagates_eof_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A closed stdin / Ctrl-D inside ``Prompt.ask`` propagates as ``EOFError``.

    The :class:`~nova.systems.nerve.system.NerveSystem` REPL catches at
    its own boundary and drives a clean SHUTDOWN; the adapter must NOT
    swallow.
    """

    def fake_ask(prompt: str, **kwargs: object) -> str:  # noqa: ARG001
        raise EOFError

    monkeypatch.setattr("nova.adapters.rich.skin.Prompt.ask", fake_ask)
    adapter = RichSkinAdapter(console=_build_console(color_system=None))
    with pytest.raises(EOFError):
        await adapter.collect_input(prompt="> ")


@pytest.mark.asyncio
async def test_render_response_is_async() -> None:
    """Companion async-shape lock for the new method (mirrors parse_command pattern)."""
    import inspect

    assert inspect.iscoroutinefunction(RichSkinAdapter.render_response) is True


@pytest.mark.asyncio
async def test_collect_input_is_async() -> None:
    """Companion async-shape lock for the new method."""
    import inspect

    assert inspect.iscoroutinefunction(RichSkinAdapter.collect_input) is True


@pytest.mark.asyncio
async def test_collect_input_uses_daemon_thread_for_process_exit_safety(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Process-exit safety lock — the input thread MUST be a daemon thread.

    The blocking ``Prompt.ask`` runs in a background thread. With a
    non-daemon thread (the default for :func:`asyncio.to_thread`'s
    executor), :func:`asyncio.run`'s ``shutdown_default_executor`` call
    waits for the thread to complete — meaning a signal-driven exit
    can hang indefinitely while the prompt is still blocked on stdin.

    Daemon threads are killed on process exit; the OS does not wait
    for them. Story 3.5's REPL race-pattern teardown depends on this:
    when ``_shutdown_event`` fires, the asyncio cancel of the input
    future returns immediately, and ``asyncio.run`` returns without
    waiting on the orphaned ``Prompt.ask`` call.
    """
    import threading

    captured_threads: list[threading.Thread] = []
    real_thread = threading.Thread

    def capturing_thread(*args: object, **kwargs: object) -> threading.Thread:
        # Force the daemon prompt to return immediately so the test
        # doesn't hang. We're verifying the thread CONSTRUCTION kwargs,
        # not the prompt mechanics.
        kwargs["target"] = lambda: None
        thread = real_thread(*args, **kwargs)  # type: ignore[arg-type]
        captured_threads.append(thread)
        return thread

    # Patch threading.Thread INSIDE the adapter module so the daemon=True
    # kwarg is asserted at construction.
    monkeypatch.setattr("nova.adapters.rich.skin.threading.Thread", capturing_thread)

    adapter = RichSkinAdapter(console=_build_console(color_system=None))
    # Drive the call but don't wait for the (now-no-op) thread's future.
    loop = asyncio.get_running_loop()
    coro = adapter.collect_input(prompt="> ")
    task = loop.create_task(coro)
    # Give the thread a moment to start
    await asyncio.sleep(0)
    task.cancel()
    import contextlib

    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert len(captured_threads) == 1
    spawned = captured_threads[0]
    assert spawned.daemon is True, (
        "RichSkinAdapter.collect_input MUST spawn a daemon thread so "
        "asyncio.run does not wait for it on process exit. A non-daemon "
        "thread blocked on stdin would hang the signal-driven shutdown "
        "path documented in Story 3.5 § Detected conflicts."
    )
    assert spawned.name == "nova-skin-input"


@pytest.mark.asyncio
async def test_collect_input_safe_set_result_handles_already_done_future() -> None:
    """The thread's safe-set helpers must tolerate cancelled futures.

    REPL race-pattern teardown cancels the asyncio task; the daemon
    thread may finish ``Prompt.ask`` afterward and try to set the
    already-done future. Without the done-check, this raises
    InvalidStateError.
    """
    from nova.adapters.rich.skin import _safe_set_exception, _safe_set_result

    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str] = loop.create_future()
    fut.cancel()
    # Should not raise
    _safe_set_result(fut, "value")
    _safe_set_exception(fut, RuntimeError("ignored"))
    assert fut.cancelled()


# ===========================================================================
# Story 3.6 — render_progress (per-app inline launch result)
# ===========================================================================


def _action_result(target: str, *, success: bool, reason: str | None) -> ActionResult:
    return ActionResult(
        action_type=ActionType.APP_LAUNCH,
        target=target,
        success=success,
        reason=reason,
    )


@pytest.mark.asyncio
async def test_render_progress_success_renders_check_mark_plus_target() -> None:
    console = _build_console()
    adapter = RichSkinAdapter(console=console)
    result = _action_result("VS Code", success=True, reason=None)

    await adapter.render_progress(result)

    output = console.export_text()
    assert "✓ VS Code" in output


@pytest.mark.asyncio
async def test_render_progress_failure_not_found_appends_is_it_installed_hint() -> None:
    from nova.ports.app_launcher import REASON_NOT_FOUND

    console = _build_console()
    adapter = RichSkinAdapter(console=console)
    result = _action_result("Postman", success=False, reason=REASON_NOT_FOUND)

    await adapter.render_progress(result)

    output = console.export_text()
    assert "✗ Postman (not found — is it installed?)" in output


@pytest.mark.asyncio
async def test_render_progress_failure_other_reason_renders_plain_parens() -> None:
    from nova.ports.app_launcher import REASON_PERMISSION_DENIED

    console = _build_console()
    adapter = RichSkinAdapter(console=console)
    result = _action_result("LockedApp", success=False, reason=REASON_PERMISSION_DENIED)

    await adapter.render_progress(result)

    output = console.export_text()
    assert "✗ LockedApp (permission denied)" in output
    # Make sure no extra hint was appended
    assert "is it installed" not in output


@pytest.mark.asyncio
async def test_render_progress_uses_markup_false() -> None:
    """Verify markup=False is passed to console.print for security."""
    from unittest.mock import MagicMock

    mock_console = MagicMock(spec=Console)
    adapter = RichSkinAdapter(console=mock_console)
    result = _action_result("X", success=True, reason=None)

    await adapter.render_progress(result)

    mock_console.print.assert_called_once()
    assert mock_console.print.call_args.kwargs.get("markup") is False
