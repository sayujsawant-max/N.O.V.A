"""Rich-backed adapters — Panel, Table, Tree, Progress rendering.

Story 3.3 ships :class:`RichSkinAdapter` for the Briefing Card panel
surface. Future stories add Tree (transparency, Epic 5) and Progress
(mode restore, Story 3.6) renderings to the same adapter.
"""

from nova.adapters.rich.skin import RichSkinAdapter

__all__: list[str] = ["RichSkinAdapter"]
