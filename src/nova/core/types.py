"""Shared domain enums for N.O.V.A.

Stable string-valued enums consumed across systems. Per project-context.md:
- "Enum for constrained values" — never use raw strings for these.
- "Stable serialization only" — enum string values appear in YAML config,
  SQLite columns, and event payloads; renaming a value is a schema migration.

Members of every enum below are ``StrEnum`` subclasses so ``str(member)``,
``f"{member}"``, and ``json.dumps(member)`` all yield the canonical value
without ``.value`` boilerplate at every call site.

Note: ``yaml.safe_dump(member)`` is NOT supported — PyYAML lacks a
``StrEnum`` representer and will raise ``RepresenterError``. Story 1.6's
config writer is responsible for converting members to ``str(member)`` (or
``.value``) before YAML serialization. Inbound YAML strings are converted
back to enum members via the ordinary ``EnumClass(value)`` constructor.

Sources:
- epics.md Story 1.2 (lines 657-676) pins the exact membership lists.
- architecture.md sections "State Determination", "Audit Trail", "Brain
  Memory", and "Personality" describe how each enum flows through the system.
- project-context.md line 41 enumerates the constrained values that must use
  enums.
"""

from __future__ import annotations

from enum import StrEnum


class CapabilityTier(StrEnum):
    """Operational capability tier (Story 1.7 owns the state machine).

    Drives degraded-mode and offline-mode behavior across all systems.
    Persisted into ``audit_log`` rows tagged ``ActionType.TIER_CHANGE`` and
    carried on tier-related events emitted by Nerve.
    """

    FULL = "full"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class BriefingState(StrEnum):
    """Briefing Card render state (architecture.md section "State Determination").

    Architecture.md line 653 originally defined this as a plain ``Enum``.
    Story 1.2 owns the divergence to ``StrEnum`` so the value is the
    canonical serialization for YAML / SQLite / event-bus contexts —
    consistent with the project-context.md "stable serialization" rule.
    Skin uses this to choose which Briefing Card render path to invoke.
    """

    FIRST_RUN = "first_run"
    POST_SETUP = "post_setup"
    WARM_RESUME = "warm_resume"


class SnapshotType(StrEnum):
    """Workspace snapshot kind, persisted into ``workspace_snapshots.snapshot_type``.

    Set when Eyes captures a snapshot; surfaced by Brain when reading
    snapshots back for briefing assembly or transparency views.
    """

    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    MODE_SWITCH = "mode_switch"
    PERIODIC = "periodic"


class ActionType(StrEnum):
    """Auditable action kind, persisted into ``audit_log.action_type``.

    The single source of truth for the audit log's action vocabulary.
    ``core/audit.py`` (Story 1.8) types its API with this enum and never
    accepts raw strings. Membership is pinned by epics.md line 672 — adding
    a new action kind is a deliberate schema change, not an in-line string.
    """

    APP_LAUNCH = "app_launch"
    APP_FOCUS = "app_focus"
    WINDOW_ARRANGE = "window_arrange"
    MODE_SWITCH = "mode_switch"
    MODE_RESTORE = "mode_restore"
    MODE_CREATE = "mode_create"
    MODE_EDIT = "mode_edit"
    DELETION = "deletion"
    SEED_CAPTURE = "seed_capture"
    TIER_CHANGE = "tier_change"
    DATABASE_RECOVERY = "database_recovery"


class MemoryCategory(StrEnum):
    """Memory item category, persisted into ``memory_items.category``.

    Brain owns memory items; Voice and Ritual read them via Brain's port for
    briefing context and prose enrichment.
    """

    SEED = "seed"
    SESSION_NOTE = "session_note"
    CONTEXT_SUMMARY = "context_summary"
    PATTERN = "pattern"


# Ruthless is deferred to T2 per epics.md AC (line 674). Do NOT add RUTHLESS
# here — Voice (Epic 7) and the settings schema both depend on the T1-only
# two-member set. Adding it would silently widen the bluntness contract.
class BluntnessLevel(StrEnum):
    """Voice bluntness level (Epic 7), configurable in ``settings.yaml``.

    T1 ships with ``calm`` and ``direct`` only. ``ruthless`` is deferred to
    T2 — the regression test ``test_bluntness_level_has_exactly_two_members``
    enforces the deferral.
    """

    CALM = "calm"
    DIRECT = "direct"
