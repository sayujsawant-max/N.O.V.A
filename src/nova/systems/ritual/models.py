"""Ritual-layer domain models consumed through :mod:`nova.ports.ritual`.

``BriefingViewModel`` is the render-ready contract Ritual produces from a
:class:`nova.systems.brain.models.BriefingAggregate` plus briefing state and
capability tier. ``ShutdownData`` is the seed/prompt carrier for the
shutdown flow.

Only ``.models`` crosses system boundaries (Story 1.9 AC #8).

Story 3.3 reshape — pre-rendered labels at the Ritual → Skin boundary
---------------------------------------------------------------------
The placeholder shape (Story 1.9) carried raw component fields
(``seed_text``, ``last_mode``, ``last_duration_seconds``, ``last_apps``)
on the assumption Skin would compose rendered lines from them. Story 3.3
reshapes the dataclass to carry **pre-rendered, user-facing label
strings** — ``intro_lines``, ``seed_quote``, ``last_session_label``,
``last_apps_label``, ``available_modes_label``. Every visible character
originates in :class:`~nova.systems.ritual.system.RitualSystem`; Skin
maps each label to a fixed Rich style and omits when ``None`` / empty.

Three motivations drove the reshape (see story 3.3 §
"Why pre-rendered labels and not raw component fields"):

1. **"Skin makes zero content decisions" is load-bearing.** Composing
   ``f"Last session: {last_mode} mode, {duration_display}"`` in Skin
   would be content composition (chooses the prefix label, decides
   comma placement, decides the "mode" suffix). With the reshape Ritual
   produces the literal string and Skin only chooses the ``dim`` style.
2. **Pre-flag's serialization-at-boundary invariant generalizes.** No
   ``timedelta`` / ``datetime`` / raw ``int`` of seconds crosses into
   Skin where Skin would have to format. Same logic applies to every
   other component value — singular/plural mode count, opaque-window
   filtering, seed quoting.
3. **Centralized formatting (project-context.md §57).**
   :func:`nova.core.formatting.format_duration_seconds` is the single
   home for duration rendering; placing the call in Ritual (not Skin)
   means every consumer that builds a ViewModel-like structure goes
   through the same vocabulary.

Progressive omission contract — Skin omits a line entirely when its
source field is ``None`` (for ``str | None`` fields) or empty tuple
(for ``intro_lines``). No empty placeholders, no "N/A", no fake history.

Design rule — prose is additive, not structural (architecture.md:715):
``prose_enrichment`` is optional Claude-generated flavor rendered *after*
the structured fields by Skin, never *instead of* them. When cloud is
unreachable (Degraded / Offline tiers), ``prose_enrichment`` is ``None``
and the structured fields alone are always sufficient to render a complete
briefing.

Architecture deviation note — architecture.md Decision 3b lists the
original raw-component shape (``seed_text``, ``last_mode``,
``last_duration: timedelta``, ``last_apps: list[str]``). Story 3.3 ships
the reshape per the user-clarified principle that Ritual owns
user-facing copy and Skin only styles. The architecture's separation of
concerns (Brain projection → Nerve state → Ritual ViewModel → Skin
render) is preserved; only the field granularity at the Ritual → Skin
step changed. A future architecture.md revision pass (post-Epic 3
retrospective) would reflect the shipped shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from nova.core.types import BriefingState, CapabilityTier
from nova.systems.brain.models import ModeInfo


@dataclass(frozen=True)
class BriefingViewModel:
    """Render-ready briefing output consumed by ``SkinPort.render_briefing_card``.

    Each rendered field carries a complete user-facing string — including
    any prefix label (``"Last session: "``), punctuation (the seed's
    quote characters), and singular/plural inflection (``"Available
    mode:"`` vs ``"Available modes:"``). Skin's only render-time
    decision is which Rich style to apply per field; no string
    formatting, no concatenation, no inflection happens in Skin.

    Progressive omission contract: ``None`` (for ``Optional[str]``
    fields) and the empty-tuple ``intro_lines == ()`` are render-safe
    omission signals — Skin omits the corresponding line entirely
    rather than rendering an empty placeholder.

    Behavioral metadata — ``state``, ``tier``, ``title``,
    ``auto_start_setup``, ``available_modes``, ``suggested_mode`` — is
    not rendered directly by Skin's briefing-card path in Epic 3.
    Downstream consumers (Voice/Epic 7 contextual prose, Story 5.4 tier
    display, Epic 5 transparency) read these fields via the
    composition-root-wired Ritual pipeline.
    """

    # --- Render control ---
    state: BriefingState
    tier: CapabilityTier

    # --- UI chrome ---
    title: str

    # --- Behavioral signal — Skin triggers setup wizard auto-transition. Not rendered. ---
    auto_start_setup: bool

    # --- Pre-rendered body lines, in render order. Each is one line of
    # the panel body (intro_lines is a tuple of lines for multi-line
    # locked copy). Skin maps each to a fixed style and OMITS the line
    # entirely when None / empty tuple. ---
    intro_lines: tuple[str, ...]
    seed_quote: str | None
    last_session_label: str | None
    last_apps_label: str | None
    available_modes_label: str | None
    prose_enrichment: str | None
    prompt_text: str | None

    # --- Behavioral metadata for downstream consumers (Voice/Epic 7
    # contextual prose, Story 5.4 tier display, Epic 5 transparency).
    # Skin does NOT consume these for the briefing-card render. ---
    available_modes: tuple[ModeInfo, ...]
    suggested_mode: ModeInfo | None


@dataclass(frozen=True)
class ShutdownData:
    """Opening payload of the shutdown flow, returned by ``RitualPort.begin_shutdown``.

    ``prompt_text`` is the Voice-generated (or tier-degraded structured)
    seed prompt. ``last_context`` is an opaque summary of what the user
    was working on — ``None`` if the session never accumulated context
    (e.g., first-run session exiting immediately after setup).
    """

    session_id: int
    prompt_text: str
    last_context: str | None


__all__: list[str] = [
    "BriefingViewModel",
    "ShutdownData",
]
