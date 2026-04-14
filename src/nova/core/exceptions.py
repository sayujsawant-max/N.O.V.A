"""Domain exception types for N.O.V.A.

Adapter-specific exceptions (sqlite3, anthropic, pywin32, psutil, ...) MUST
NOT cross a port boundary. Adapters catch the upstream exception and raise
the matching domain exception defined here. This module is the single
source of truth for exceptions that flow through Nova business logic.

Chaining contract
-----------------
Each exception accepts a required positional ``message: str`` and an optional
keyword-only ``cause: BaseException | None``. The ``cause=`` slot stores the
underlying exception on ``self.cause`` for ergonomic introspection. It does
**NOT** populate Python's ``__cause__`` slot. Callers MUST still use the
``from`` keyword to chain exceptions for traceback purposes::

    try:
        cursor.execute(sql)
    except sqlite3.Error as underlying:
        raise StorageError("session insert failed") from underlying

Writing ``raise StorageError("...", cause=underlying)`` without ``from`` will
NOT chain — Python prints the two exceptions as unrelated. Use ``from`` for
chaining; use ``cause=`` only when you need to retain the underlying object
without re-raising.

Sensitive content rule
----------------------
Per project-context.md: never embed sensitive content (excluded app names,
window titles, raw prompt fragments, API keys, DB row payloads) in exception
messages. Use opaque references (``"mode 'opaque'"``, ``"row id=42"``).
"""

# RULE: never embed sensitive content (excluded app names, window titles,
# prompt fragments, API keys, DB row payloads) in exception messages. Use
# opaque references.

from __future__ import annotations


class NovaError(Exception):
    """Base class for every domain exception raised inside Nova business logic.

    The top-level CLI / session boundary (Story 1.10) catches this class to
    log, classify, and exit gracefully. All other domain exceptions inherit
    from ``NovaError``.

    The constructor accepts a required ``message: str`` and an optional
    keyword-only ``cause: BaseException | None``. ``cause`` is stored on
    ``self.cause`` for introspection only — it does **NOT** chain the
    exception. To chain, callers must write ``raise NovaError(...) from
    underlying`` at the call site; Python populates ``__cause__`` only via
    the ``from`` keyword.
    """

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        if not isinstance(message, str):
            raise TypeError(f"message must be str, got {type(message).__name__}")
        if cause is not None:
            if cause is self:
                raise ValueError("cause cannot be self")
            if not isinstance(cause, BaseException):
                raise TypeError(f"cause must be BaseException or None, got {type(cause).__name__}")
        super().__init__(message)
        self.cause: BaseException | None = cause

    def __repr__(self) -> str:
        msg_repr = repr(self.args[0]) if self.args else ""
        if self.cause is None:
            return f"{type(self).__name__}({msg_repr})"
        return f"{type(self).__name__}({msg_repr}, cause={self.cause!r})"


class StorageError(NovaError):
    """Raised when persistence fails (sqlite, IO, lock contention, corruption).

    Adapters in ``adapters/sqlite/*`` and ``core/storage/*`` catch
    ``sqlite3.Error`` / ``OSError`` and re-raise as ``StorageError``. The
    catcher (typically Brain or Nerve) decides whether to surface a
    user-facing recovery flow, retry, or transition to a degraded tier.
    """


class ConfigError(NovaError):
    """Raised when YAML config is missing, malformed, or fails schema validation.

    Used by ``core/config.py`` (Story 1.6) and the first-run setup flow
    (Epic 2). The catcher typically presents a user-facing repair prompt or
    falls back to shipped defaults.
    """


class ApiUnavailableError(NovaError):
    """Raised when an external API (Claude) is unreachable, rate-limited, or errors out.

    The Claude adapter catches ``anthropic.APIError`` /
    ``anthropic.APIStatusError`` / network errors and re-raises as
    ``ApiUnavailableError``. The tier state machine (Story 1.7) consumes
    this signal to transition full -> degraded / offline. The user is
    informed honestly via Voice / Skin once the tier transitions.
    """


class ModeNotFoundError(NovaError):
    """Raised when the user references a workspace mode that does not exist.

    Used by Nerve / Hands when a command targets a mode key that has no
    YAML file under ``%LOCALAPPDATA%/nova/modes/``. The catcher typically
    offers ad-hoc mode creation (Epic 6) or a list of available modes.
    """


class AdapterError(NovaError):
    """Generic re-raise for adapter failures that do not fit a specific domain type.

    Adapters catch the upstream exception, translate, and raise
    ``AdapterError`` (or a more specific subclass like ``StorageError``,
    ``ApiUnavailableError``, ``ConfigError``) so the original
    adapter-specific exception type never crosses the port boundary.
    Prefer a specific subclass when one fits; reach for ``AdapterError``
    only when nothing more precise applies.
    """
