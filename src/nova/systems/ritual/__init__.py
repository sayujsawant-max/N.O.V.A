"""Ritual system — briefing assembly, shutdown ceremony, seed lifecycle.

Story 3.3 ships :meth:`RitualSystem.build_briefing` for the State A/B/C
Briefing Card pipeline; Story 3.7 will populate
:meth:`RitualSystem.begin_shutdown`.
"""

from nova.systems.ritual.system import RitualSystem

__all__: list[str] = ["RitualSystem"]
