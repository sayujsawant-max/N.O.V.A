"""Ritual system — concrete :class:`~nova.ports.ritual.RitualPort` implementation (Story 3.3).

Architecture (decision 3b — "Render Responsibility Boundary",
[architecture.md:747-755]):

* **Brain** owns persisted-fact queries and the storage projection.
* **Nerve** assembles :class:`~nova.systems.brain.models.BriefingAggregate`
  via :func:`nova.systems.nerve.briefing.load_briefing_aggregate` and
  determines :class:`~nova.core.types.BriefingState` via
  :func:`nova.systems.nerve.briefing.determine_briefing_state`.
* **Ritual** (this module) takes the aggregate + state + tier and
  produces a render-ready
  :class:`~nova.systems.ritual.models.BriefingViewModel` with **every
  visible string pre-rendered** (`intro_lines`, `seed_quote`,
  `last_session_label`, `last_apps_label`, `available_modes_label`,
  `prompt_text`).
* **Skin** maps each ViewModel field to a fixed Rich style and omits
  when the field is ``None`` / empty tuple. No string formatting, no
  singular/plural inflection, no concatenation in Skin.

Story 3.3 ships :meth:`RitualSystem.build_briefing` only.
:meth:`RitualSystem.begin_shutdown` raises ``NotImplementedError`` —
Story 3.7 fills the body when the shutdown flow lands.

Cross-system contract — interrupted-session signal
--------------------------------------------------
The interrupted-session marker is :attr:`SessionSummary.is_complete`
``False`` (the upstream Story 3.1 contract).
:func:`nova.core.formatting.format_duration_seconds` is value-based
and renders a 0-second completed session as ``"0s"`` —
:func:`_build_last_session_label` is the policy site that omits the
duration tail when ``is_complete`` is falsy *before* consulting the
formatter. The split prevents silent relabeling of short completed
sessions as interrupted.

Locked copy constants
---------------------
* :data:`_STATE_A_INTRO_LINE_1` / :data:`_STATE_A_INTRO_LINE_2` —
  Story 2.4 AC #1 locked verbatim copy. The byte-for-byte parity test
  (Story 3.3 AC #20) imports these constants.
* :data:`_STATE_B_INTRO_LINE` — UX spec State B body (lines 773-784).
"""

from __future__ import annotations

import logging

from nova.core.formatting import format_duration_seconds
from nova.core.types import BriefingState, CapabilityTier
from nova.systems.brain.models import BriefingAggregate, ModeInfo, SessionSummary
from nova.systems.eyes.models import WorkspaceSnapshot
from nova.systems.ritual.models import BriefingViewModel, ShutdownData

logger = logging.getLogger("nova.systems.ritual")

# --- Locked-copy constants ---------------------------------------------------

_STATE_A_INTRO_LINE_1: str = "First session. No history yet — that's expected."
_STATE_A_INTRO_LINE_2: str = "Let's set up your first workspace mode so tomorrow starts warm."
_STATE_B_INTRO_LINE: str = "No saved seed from your last session."

# --- Internal label-builder helpers (private; tests may import) --------------


def _escape_label_value(value: str) -> str:
    """Escape characters that would create ambiguity in a comma-separated label.

    Story 3.3 review (D3): a mode named ``"Coding, Deep"`` rendered into
    ``"Available modes: Coding, Deep, Writing"`` looks like three modes.
    Escaping ``,`` with a leading backslash preserves the user's chosen
    name visually while disambiguating the join boundary. The same
    escaping applies inside ``_build_last_session_label`` so a comma in
    ``display_name`` does not collide with the ``", {duration}"`` tail.

    Backslash itself is escaped first so a name already containing a
    literal backslash does not double-process.
    """
    return value.replace("\\", "\\\\").replace(",", "\\,")


def _build_seed_quote(last_seed: str | None) -> str | None:
    """Wrap a non-empty seed in straight quotes; return ``None`` for omission.

    Story 3.7's shutdown flow rejects empty seed input upstream, so the
    null / empty / whitespace-only cases here are data-corruption
    defense-in-depth. Multi-line seeds collapse internal newlines to
    spaces because the renderer's block-spacing model assumes each
    label is one visual block — a literal ``\\n`` mid-seed would split
    the hero line and confuse the layout.

    Embedded ``"`` characters are backslash-escaped so the rendered
    quote boundary stays unambiguous (review finding P3).
    """
    if last_seed is None:
        return None
    # Collapse any internal whitespace runs (including newlines) to single
    # spaces, then strip leading/trailing whitespace. Empty / pure-whitespace
    # seeds → None for omission.
    normalized = " ".join(last_seed.split())
    if not normalized:
        return None
    escaped = normalized.replace('"', '\\"')
    return f'"{escaped}"'


def _build_last_session_label(
    last_session: SessionSummary | None,
    suggested: ModeInfo | None,
) -> str | None:
    """Render the "Last session: …" line, applying interrupted-session policy.

    Returns ``None`` (omission) when:
    - ``last_session`` is ``None`` (no prior session).
    - ``last_session.mode_name`` is ``None`` (Story 2.4 setup-row case
      writes NULL — no display label is recoverable).
    - The mode stem in ``last_session.mode_name`` is not present in
      ``available_modes`` (the mode was deleted between sessions, no
      display_name available; omit rather than leak the raw stem).
    - The matching mode's ``display_name`` is empty / whitespace-only
      (data-corruption defense-in-depth — config validation should
      reject empty names upstream).

    For interrupted sessions (``is_complete`` falsy), renders the label
    *without* the duration tail. The interrupted-session decision is
    keyed on ``is_complete``, NOT on ``duration_seconds == 0`` — a short
    completed session can have ``duration_seconds == 0`` and must
    render as "Last session: X mode, 0s".

    Negative ``duration_seconds`` is clamped to ``0`` defensively
    (review finding P4) — the formatter rejects negatives, and a clock
    skew or corrupt row reaching this branch would otherwise crash the
    briefing render.
    """
    if last_session is None:
        return None
    if last_session.mode_name is None:
        return None
    if suggested is None or suggested.stem != last_session.mode_name:
        return None
    display_name = suggested.display_name.strip()
    if not display_name:
        return None
    mode_label = _escape_label_value(display_name)
    # Truthy check (NOT `is False`) — a future upstream regression that
    # passes int 0/1 instead of bool would silently flip an `is` identity
    # check. The truthy form is robust to either type.
    if not last_session.is_complete:
        return f"Last session: {mode_label} mode"
    safe_duration_seconds = max(0, last_session.duration_seconds)
    duration_display = format_duration_seconds(safe_duration_seconds)
    return f"Last session: {mode_label} mode, {duration_display}"


def _build_last_apps_label(last_snapshot: WorkspaceSnapshot | None) -> str | None:
    """Render the "Apps: A, B, C" line by mapping :class:`WorkspaceSnapshot.windows`.

    The Eyes-layer model has no ``.apps`` field — apps are derived from
    :attr:`WindowContext.app_name` per window, filtering falsy values
    (``None`` and empty string) to drop opaque / excluded / corrupt
    windows. Project-context.md §175 + ux-spec.md:811 — opaque windows
    have all identity fields ``None`` upstream by Eyes' contract, so
    this is defense-in-depth.

    Returns ``None`` when:
    - ``last_snapshot is None`` (no snapshot for the prior session).
    - All windows have falsy ``app_name`` (filtered tuple is empty).

    De-duplication is deferred — two Chrome windows render as
    "Apps: Chrome, Chrome" in T1 (accurate, just verbose).
    """
    if last_snapshot is None:
        return None
    # Truthy filter catches both `None` and the empty-string degenerate
    # case (review finding P13 — type signature `app_name: str | None`
    # permits "" but it's never user-meaningful).
    app_names = tuple(w.app_name for w in last_snapshot.windows if w.app_name)
    if not app_names:
        return None
    return f"Apps: {', '.join(_escape_label_value(name) for name in app_names)}"


def _build_available_modes_label(modes: tuple[ModeInfo, ...]) -> str | None:
    """Render the State B "Available mode(s):" line; ``None`` for omission.

    Singular/plural inflection lives here, never in Skin:
    - 0 modes (or all modes with empty/whitespace ``display_name``) → ``None``
    - 1 visible mode → ``"Available mode: <display_name>"``
    - 2+ visible modes → ``"Available modes: A, B, C"`` (in given
      order; Story 3.2 already guarantees stem-ascending).

    Modes whose ``display_name`` is empty / whitespace-only after
    stripping are filtered out as data-corruption defense-in-depth
    (review finding P6 — config validation should reject empty names
    upstream).
    """
    visible = tuple(
        _escape_label_value(m.display_name.strip()) for m in modes if m.display_name.strip()
    )
    if not visible:
        return None
    labels = ", ".join(visible)
    if len(visible) == 1:
        return f"Available mode: {labels}"
    return f"Available modes: {labels}"


def _format_prompt(template: str, mode: ModeInfo | None) -> str:
    """Format the bold final-action prompt; falls back to ``"What mode?"`` on None.

    Always returns a non-empty string for State B / C. State A passes
    ``prompt_text=None`` directly without consulting this helper.

    The substitution uses :meth:`str.replace` (NOT :meth:`str.format`)
    so a ``display_name`` containing ``{`` / ``}`` / ``{0}`` does not
    interact with Python's format-spec parser (review finding P10).
    The ``mode.display_name`` value is treated as opaque text.
    """
    if mode is None:
        return "What mode?"
    return template.replace("{}", mode.display_name)


# --- Suggested-mode tie-break ladder -----------------------------------------


def _select_suggested_mode_for_state_b(aggregate: BriefingAggregate) -> ModeInfo | None:
    """Resolve the suggested mode for State B (rungs b → c → d → e).

    State B by the state-machine definition has no usable last_session
    (Nerve already excluded that path), so rung a (match
    ``last_session.mode_name``) is intentionally skipped.
    """
    return _pick_recent_or_default(aggregate.available_modes)


def _select_suggested_mode_for_state_c(aggregate: BriefingAggregate) -> ModeInfo | None:
    """Resolve the suggested mode for State C (full ladder, rungs a → b → c → d → e).

    Rung a — match ``last_session.mode_name`` against an available
    mode's ``stem``. Rungs b/c/d/e fall through to
    :func:`_pick_recent_or_default` when rung a fails (no last_session,
    null / empty mode_name, or mode was deleted).
    """
    last_session = aggregate.last_session
    # Truthy check on `last_session.mode_name` (not `is not None`) so an
    # empty-string column value from a corrupt row doesn't pass into the
    # iteration (no mode has `stem=""`, so the loop falls through anyway,
    # but the truthy check keeps intent explicit — review finding P12).
    if last_session is not None and last_session.mode_name:
        for mode_info in aggregate.available_modes:
            if mode_info.stem == last_session.mode_name:
                return mode_info
    return _pick_recent_or_default(aggregate.available_modes)


def _pick_recent_or_default(modes: tuple[ModeInfo, ...]) -> ModeInfo | None:
    """Apply rungs b/c/d/e of the tie-break ladder.

    Rung b: most recent ``last_used_at`` (lexicographic ISO-8601 sort;
        ``None`` is treated as smaller than any string). Ties broken by
        alphabetical stem.
    Rung c: ``is_default=True`` mode with the alphabetically-first
        stem.
    Rung d: alphabetically-first stem.
    Rung e: ``None`` (empty modes).
    """
    if not modes:
        return None

    # Rung b — most recent last_used_at (lexicographic ISO sort).
    used_modes = [m for m in modes if m.last_used_at is not None]
    if used_modes:
        # The inner `is not None` here is required for mypy's type
        # narrowing inside the generator expression — even though
        # `used_modes` is already None-free, mypy re-evaluates the
        # generator scope independently. Documented review-finding P7
        # outcome: keep the inner guard, drop the outer pre-filter
        # consideration.
        max_used_at = max(m.last_used_at for m in used_modes if m.last_used_at is not None)
        tied = sorted(
            (m for m in used_modes if m.last_used_at == max_used_at),
            key=lambda m: m.stem,
        )
        return tied[0]

    # Rung c — is_default=True (alphabetical stem tie-break).
    default_modes = sorted((m for m in modes if m.is_default), key=lambda m: m.stem)
    if default_modes:
        return default_modes[0]

    # Rung d — alphabetically-first stem.
    return sorted(modes, key=lambda m: m.stem)[0]


# --- Public surface ----------------------------------------------------------


class RitualSystem:
    """Concrete :class:`~nova.ports.ritual.RitualPort` implementation.

    Stateless: :meth:`build_briefing` is a pure function of
    ``(aggregate, state, tier)``. :meth:`begin_shutdown` will become
    Story 3.7's pure-orchestration entry point.

    Structurally satisfies :class:`~nova.ports.ritual.RitualPort`
    (Protocol, no nominal inheritance) per the established convention
    (cf. :class:`~nova.adapters.sqlite.brain.SqliteBrainAdapter` ↔
    :class:`~nova.ports.brain.BrainPort`).
    """

    async def build_briefing(
        self,
        aggregate: BriefingAggregate,
        state: BriefingState,
        tier: CapabilityTier,
    ) -> BriefingViewModel:
        """Assemble a render-ready :class:`BriefingViewModel` for the given state.

        State A → locked first-run orientation; aggregate fields are
        not consulted (Nerve guarantees an empty aggregate for FIRST_RUN).
        If the aggregate ever arrives non-empty (an upstream contract
        violation), a WARNING is logged but the render still proceeds
        with the State A fields zeroed (review finding D1 — surface
        the upstream bug without crashing the briefing).

        State B → "No saved seed from your last session." preface +
        available-modes line + "Start in {mode} mode?" prompt.

        State C → seed quote (when present) + last-session line (when
        recoverable) + apps line (when present) + "Resume {mode} mode?"
        prompt. Progressive omission applies to every per-session
        field — Ritual returns ``None`` for missing / corrupt inputs;
        Skin omits the corresponding line entirely.

        ``tier`` rides through verbatim — Skin's tier-notice rendering
        (Story 5.4) and Voice's prose-enrichment-availability decision
        (Epic 7) read the field downstream. Epic 3's render path is
        tier-orthogonal.
        """
        if state is BriefingState.FIRST_RUN:
            if aggregate.available_modes or aggregate.last_session is not None:
                # Nerve guarantees an empty aggregate for FIRST_RUN; if we
                # see a non-empty one, surface the upstream contract
                # violation without crashing. ``extra`` carries closed-set
                # category labels only — no user data (project-context.md
                # opacity rule).
                logger.warning(
                    "FIRST_RUN with non-empty aggregate",
                    extra={
                        "available_modes_count": len(aggregate.available_modes),
                        "has_last_session": aggregate.last_session is not None,
                    },
                )
            return BriefingViewModel(
                state=BriefingState.FIRST_RUN,
                tier=tier,
                title="N.O.V.A.",
                auto_start_setup=True,
                intro_lines=(_STATE_A_INTRO_LINE_1, _STATE_A_INTRO_LINE_2),
                seed_quote=None,
                last_session_label=None,
                last_apps_label=None,
                available_modes_label=None,
                prose_enrichment=None,
                prompt_text=None,
                available_modes=(),
                suggested_mode=None,
            )

        if state is BriefingState.POST_SETUP:
            suggested = _select_suggested_mode_for_state_b(aggregate)
            return BriefingViewModel(
                state=BriefingState.POST_SETUP,
                tier=tier,
                title="Session Briefing",
                auto_start_setup=False,
                intro_lines=(_STATE_B_INTRO_LINE,),
                seed_quote=None,
                last_session_label=None,
                last_apps_label=None,
                available_modes_label=_build_available_modes_label(aggregate.available_modes),
                prose_enrichment=None,
                prompt_text=_format_prompt("Start in {} mode?", suggested),
                available_modes=aggregate.available_modes,
                suggested_mode=suggested,
            )

        if state is BriefingState.WARM_RESUME:
            suggested = _select_suggested_mode_for_state_c(aggregate)
            return BriefingViewModel(
                state=state,  # NOT a literal — pass through so a future
                # state addition surfaces correctly (review finding P1).
                tier=tier,
                title="Session Briefing",
                auto_start_setup=False,
                intro_lines=(),
                seed_quote=_build_seed_quote(aggregate.last_seed),
                last_session_label=_build_last_session_label(aggregate.last_session, suggested),
                last_apps_label=_build_last_apps_label(aggregate.last_snapshot),
                available_modes_label=None,
                prose_enrichment=None,  # Epic 7 owns; Epic 3 leaves None
                prompt_text=_format_prompt("Resume {} mode?", suggested),
                available_modes=aggregate.available_modes,
                suggested_mode=suggested,
            )

        # Exhaustiveness guard — if a future story adds a fourth
        # BriefingState member without updating this dispatch, fail
        # loudly instead of silently mislabeling. Locked by review
        # finding P1.
        raise ValueError(f"Unhandled BriefingState: {state!r}")

    async def begin_shutdown(self) -> ShutdownData:
        raise NotImplementedError("Story 3.7 scope")


__all__: list[str] = ["RitualSystem"]
