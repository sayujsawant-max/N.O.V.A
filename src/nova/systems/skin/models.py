"""Skin-layer domain models consumed through :mod:`nova.ports.skin`.

T1 ships one type here: :class:`Command`, the deterministically-parsed
user-input carrier produced by ``SkinPort.parse_command`` and routed by
``NervePort.route_command``. Architecture.md:1355 anticipates a future
split between ``systems/skin/models.py`` (data classes) and
``systems/skin/commands.py`` (parser logic); Story 1.9 folds both into
``models.py`` so the "only ``.models`` crosses system boundaries" rule
from Story 1.9 AC #8 stays a single-suffix invariant. If Story 3.4
introduces a dedicated parser module, it will live as
``systems/skin/commands.py`` *Skin-internal only* — the :class:`Command`
type will stay here.

Only ``.models`` crosses system boundaries (Story 1.9 AC #8).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    """Deterministically-parsed user command routed from Skin to Nerve.

    ``verb`` is the canonical command token (``"mode"``, ``"shutdown"``,
    ``"status"``, ``"forget"``, ``"memory"``, ``"help"``). ``target`` is
    the optional object (``"coding"`` for ``mode coding``, ``None`` for
    bare verbs). ``raw_input`` preserves the user's original text for
    NLP-fallback paths (Story 3.5+). ``is_contextual`` flags contextual
    replies (``yes``/``no``/``skip``/``cancel``/``resume``) that are only
    valid when Nerve has prompted for them — see the T1 Command Grammar
    Contract in the UX design specification.
    """

    verb: str
    target: str | None
    raw_input: str
    is_contextual: bool = False


__all__: list[str] = [
    "Command",
]
