"""HandsPort — system-level desktop-action surface owned by Hands.

Story 1.9 (AC #4) pinned a single T1 method: :meth:`HandsPort.restore_mode`.
Story 3.6 ships the first concrete implementation
(:class:`nova.systems.hands.system.HandsSystem`) and **reshapes the
signature** to take both ``mode_stem`` and ``mode_config`` — see
"Stem vs display name" below.

Per-app :meth:`launch_app` / :meth:`focus_window` / :meth:`arrange_windows`
specializations are NOT on this port. The per-app launch primitive
lives on a separate adapter-facing port,
:class:`~nova.ports.app_launcher.AppLauncherPort` (Story 3.6); window
focus + arrange land in Story 6.1 either as new ``HandsPort`` methods
(if Nerve consumes them directly) or as additions to ``AppLauncherPort``.

T1 safe-only action set (project-context.md:193): launch, focus, arrange
— nothing destructive.

Stem vs display name (Story 3.6 reshape rationale)
--------------------------------------------------
:class:`~nova.core.config.ModeConfig` carries two pieces of identity:

* ``ModeConfig.name`` — the user-facing **display label** from the
  YAML ``name:`` field. May contain spaces or mixed case
  (e.g. ``"Study Group"``).
* The **stem** — the YAML file basename, validated kebab-case (e.g.
  ``"study-group"``), AND the dict key in ``NovaConfig.modes``. The
  stem is the canonical mode identity: it's what the user types
  (``mode study-group``), what cross-table joins key on, and what
  ``mode edit <X>`` resolves against.

``HandsPort.restore_mode`` accepts both because Hands needs both:
``mode_stem`` for the canonical identity (``ModeRestored.mode_name``,
audit ``MODE_RESTORE`` target, the ``mode edit <stem>`` total-failure
hint), and ``mode_config`` for the apps + URLs the launcher iterates.

Port rules (architecture.md:948-986, 1462)
------------------------------------------
* :class:`HandsPort` is a :class:`typing.Protocol` (structural subtyping).
* Every method is ``async def`` with an ellipsis body.
* Adapter types (``subprocess.Popen``, ``ShellExecute`` handles) stay
  trapped in :mod:`nova.adapters.win32.actions` — only domain types
  (:class:`nova.core.config.ModeConfig`,
  :class:`nova.systems.hands.models.ActionResult`) cross this boundary.
"""

from __future__ import annotations

from typing import Protocol

from nova.core.config import ModeConfig
from nova.systems.hands.models import ActionResult


class HandsPort(Protocol):
    """Desktop-action surface owned by Hands."""

    async def restore_mode(self, mode_stem: str, mode_config: ModeConfig) -> list[ActionResult]: ...


__all__: list[str] = [
    "HandsPort",
]
