"""Render-safe formatting helpers used at any system → Skin boundary.

Stages new helpers here rather than inlining at the use site so duration
/ datetime / mode-name normalization rules are reused, not duplicated
(project-context.md §57 "Formatting/parsing must be centralized").

Story 3.3 introduces this module with one helper:
:func:`format_duration_seconds`. Future formatters (datetime, mode-name
normalization) join the same module rather than fragmenting across
``nova.core.*``.

Policy split — value-based formatting only
------------------------------------------
The formatters here are **value-based and policy-free**. Helpers like
:func:`format_duration_seconds` map a value to a render string for ANY
non-negative input — including zero. Session-state policy (e.g.,
"interrupted session ⇒ omit duration tail") lives in the caller, not in
the formatter. This split prevents a category of silent UX bugs where
a representable-but-rare value (a 0-second completed session) gets
treated as the marker for an unrelated state (interrupted).

Example: a completed session with ``duration_seconds == 0`` renders as
``"0s"``; an interrupted session (``is_complete is False``) is omitted
by Ritual's :func:`~nova.systems.ritual.system._build_last_session_label`
*before* the formatter is consulted. The two cases never collide.
"""

from __future__ import annotations


def format_duration_seconds(seconds: int) -> str:
    """Return a render-safe duration string for a non-negative integer of seconds.

    Pure value-to-string mapping. Does NOT encode any session-state
    policy (e.g., interrupted vs. completed) — callers that need to
    suppress the duration on interrupted sessions handle that decision
    upstream of this function.

    - ``seconds == 0`` → ``"0s"`` (a completed session that rounded to
      zero seconds — rare but representable).
    - ``0 < seconds < 60`` → ``"{n}s"`` (e.g., ``"45s"``).
    - ``60 <= seconds < 3600`` → ``"{m}m"`` (e.g., ``"5m"``, ``"42m"``).
      Sub-minute remainder is dropped.
    - ``seconds >= 3600`` → ``"{h}h {m}m"`` (e.g., ``"1h 42m"``,
      ``"12h 0m"``). Hours and minutes use integer division and
      remainder; sub-minute remainder is dropped.
    - ``seconds < 0`` → :class:`ValueError`; negative durations are
      not a representable session shape.
    - ``bool`` input → :class:`TypeError`; ``True``/``False`` are
      ``int`` subclass values but never a meaningful duration. Caller
      passing a bool is a contract bug; surface it loudly rather than
      formatting ``True`` as ``"1s"`` (review finding P15).
    """
    if isinstance(seconds, bool):
        raise TypeError("seconds must be int, not bool")
    if seconds < 0:
        raise ValueError("seconds must be non-negative")
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes}m"


__all__: list[str] = ["format_duration_seconds"]
