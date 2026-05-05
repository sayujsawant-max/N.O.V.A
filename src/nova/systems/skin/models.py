"""Skin-layer domain models consumed through :mod:`nova.ports.skin`.

T1 ships two types here: :class:`Command`, the deterministically-parsed
user-input carrier produced by ``SkinPort.parse_command`` and routed by
``NervePort.route_command``; and :class:`CommandVerb`, the closed
vocabulary of recognized verbs.

Architecture.md:1355 anticipates a future split between
``systems/skin/models.py`` (data classes) and ``systems/skin/commands.py``
(parser logic); Story 3.4 lands the parser in ``commands.py`` while the
:class:`Command` and :class:`CommandVerb` types stay here so that "only
``.models`` crosses system boundaries" (Story 1.9 AC #8) holds.

Only ``.models`` crosses system boundaries (Story 1.9 AC #8).

Closed-vocabulary contract (Story 3.4)
--------------------------------------
:attr:`Command.verb` is annotated :class:`CommandVerb` (a
:class:`enum.StrEnum`) — a closed 16-member vocabulary covering Layer B
routable verbs (``mode``, ``status``, ``memory``, ``forget``, ``help``,
``shutdown``, ``mode_create``, ``mode_edit``), Layer C contextual replies
(``resume``, ``yes``, ``no``, ``skip``, ``cancel``, ``confirm``), and
parser marker verbs (``unknown``, ``empty``).

The closed vocabulary is enforced **both statically and at runtime**.
mypy strict catches typed construction sites; :meth:`Command.__post_init__`
catches everything else (untyped callers, ``**kwargs`` splats, external
test fixtures, dynamic-dispatch paths). Construction with a valid
value-string (``Command(verb="mode", ...)``) is coerced to the matching
:class:`CommandVerb` member; construction with an unknown string raises
:class:`ValueError`; construction with a non-string-non-enum value raises
:class:`TypeError`. The runtime check closes the deferred-work item from
Story 1.9 (typo-survival of ``Command.verb: str``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CommandVerb(StrEnum):
    """Closed vocabulary of recognized command verbs.

    ``StrEnum`` (PEP 663) gives runtime equality with raw strings — so
    legacy callers that stringly-compare ``cmd.verb == "mode"`` keep
    working without modification — while mypy strict + the runtime
    :meth:`Command.__post_init__` validator together close the
    typo-survival concern that motivated Story 3.4.

    Members are grouped by role: Layer B routable verbs come first,
    then Layer C contextual replies, then the two marker verbs that the
    parser emits for non-canonical inputs.
    """

    # --- Layer B: routable verbs (Nerve dispatches on these) ---
    MODE = "mode"
    STATUS = "status"
    MEMORY = "memory"
    FORGET = "forget"
    HELP = "help"
    SHUTDOWN = "shutdown"
    MODE_CREATE = "mode_create"
    MODE_EDIT = "mode_edit"

    # --- Layer C: contextual replies (is_contextual=True; Nerve gates on prompt state) ---
    RESUME = "resume"
    YES = "yes"
    NO = "no"
    SKIP = "skip"
    CANCEL = "cancel"
    CONFIRM = "confirm"

    # --- Marker verbs: Skin emits these for input shapes that have no canonical command ---
    UNKNOWN = "unknown"
    EMPTY = "empty"


@dataclass(frozen=True)
class Command:
    """Deterministically-parsed user command routed from Skin to Nerve.

    Fields
    ------
    verb : CommandVerb
        One of the :class:`CommandVerb` enum members. Free-form strings
        are rejected at construction — :meth:`__post_init__` either
        coerces a valid value-string to its :class:`CommandVerb` member
        (so ``Command(verb="mode", ...)`` keeps working) or raises
        :class:`ValueError` for unknown strings (e.g.,
        ``Command(verb="memoryy", ...)`` fails at construction time, not
        at routing time). mypy strict catches typed sites; the runtime
        check protects against untyped callers and dynamic-kwarg paths.
    target : str | None
        Optional object: ``"coding"`` for ``mode coding``, ``None`` for
        bare verbs.
    raw_input : str
        The user's original text, preserved for downstream consumption
        (NLP fallback in Story 3.5+, audit logging, the
        ``UNKNOWN``-response template that echoes the input).
    is_contextual : bool
        ``True`` for Layer C verbs (``RESUME``, ``YES``, ``NO``,
        ``SKIP``, ``CANCEL``, ``CONFIRM``) — Nerve gates these on the
        current prompt state. ``False`` for every Layer B verb and the
        two marker verbs.

    Marker-verb semantics
    ---------------------
    :attr:`CommandVerb.UNKNOWN` and :attr:`CommandVerb.EMPTY` are emitted
    by the parser for input shapes that do not map to any canonical
    Layer B / C command. They are routed to Nerve like any other
    Command; Nerve owns the response prose. Skin's parser never raises
    for malformed ``raw_input`` — every input produces a Command.
    (The :meth:`__post_init__` validation guards against ``verb`` itself
    being malformed, which is a programmer error, not a user-input
    shape.)

    Layer B / C split
    -----------------
    All ``is_contextual=True`` Commands carry a Layer C verb
    (``RESUME`` / ``YES`` / ``NO`` / ``SKIP`` / ``CANCEL`` / ``CONFIRM``).
    Layer B verbs always have ``is_contextual=False``. Nerve gates
    contextual Commands on the current prompt state — they are
    "unknown input" outside a directed prompt.

    Partial-command encoding
    ------------------------
    ``MODE_EDIT`` with ``target=None`` is the partial-command shape —
    the user typed ``mode edit`` without a name. Nerve routes the
    partial form to the placeholder-guidance response. ``MODE_CREATE``
    does not have a partial form (``mode create`` always parses with
    ``target=None``).
    """

    verb: CommandVerb
    target: str | None
    raw_input: str
    is_contextual: bool = False

    def __post_init__(self) -> None:
        """Coerce a valid string verb to its :class:`CommandVerb` member;
        reject anything that is neither a member nor a known value string.

        ``object.__setattr__`` is the documented escape hatch for
        mutating frozen dataclasses inside ``__post_init__`` — the
        frozen invariant is preserved for caller-side mutation
        attempts.
        """
        if isinstance(self.verb, CommandVerb):
            return
        if isinstance(self.verb, str):
            try:
                coerced = CommandVerb(self.verb)
            except ValueError as err:
                raise ValueError(f"unknown command verb: {self.verb!r}") from err
            object.__setattr__(self, "verb", coerced)
            return
        raise TypeError(f"Command.verb must be CommandVerb or str, got {type(self.verb).__name__}")


__all__: list[str] = [
    "Command",
    "CommandVerb",
]
