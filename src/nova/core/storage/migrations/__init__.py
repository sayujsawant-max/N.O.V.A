"""Numbered migration scripts and the migration runner.

Files under this package follow the ``NNN_short_name.py`` convention
and are discovered, diffed, and applied by
:class:`nova.core.storage.migrations.runner.MigrationRunner`. See
Story 1.5 for the full contract.
"""
