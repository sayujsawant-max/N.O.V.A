"""Eyes-layer domain models consumed through :mod:`nova.ports.eyes`.

``WindowContext`` is the fundamental Eyes observation — a single foreground
window at a point in time. ``WorkspaceSnapshot`` is a point-in-time capture
of the current workspace: a timestamped, typed tuple of ``WindowContext``
rows with zero live handles (no HWND, no ``psutil.Process``, no
``win32gui`` types cross this boundary — those are trapped inside the Win32
adapter per architecture.md:1461).

Only ``.models`` crosses system boundaries (Story 1.9 AC #8).

Note on excluded-context opacity (project-context.md:72, architecture.md:583):
when Eyes' capture layer (Story 4.2) detects an excluded app, the emitted
:class:`WindowContext` carries ``app_name=None``, ``window_title=None``,
``process_name=None``, and ``is_opaque=True``. Story 1.9 just defines the
carrier shape; enforcement is the Story 4.2 capture-layer concern.
"""

from __future__ import annotations

from dataclasses import dataclass

from nova.core.types import SnapshotType


@dataclass(frozen=True)
class WindowContext:
    """A single foreground-window observation.

    When ``is_opaque`` is ``True`` (excluded app), the three identity
    fields are all ``None`` — no raw app name, window title, or process
    name ever leaves the capture layer for an excluded context. This is
    the same invariant :class:`nova.core.events.ContextChanged` enforces.
    """

    app_name: str | None
    window_title: str | None
    process_name: str | None
    is_opaque: bool


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Timestamped capture of the current workspace's foreground windows.

    ``windows`` is a tuple (not a list) so the container is genuinely
    immutable under ``frozen=True``. ``snapshot_type`` categorizes WHY the
    snapshot was taken (startup, shutdown, mode switch, periodic) — see
    :class:`nova.core.types.SnapshotType`.
    """

    captured_at: str
    snapshot_type: SnapshotType
    windows: tuple[WindowContext, ...]


__all__: list[str] = [
    "WindowContext",
    "WorkspaceSnapshot",
]
