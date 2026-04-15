"""Single audit-write boundary — ``AuditLogger`` for the ``audit_log`` table.

Every automated action in N.O.V.A. (app launches, mode switches, deletions,
tier changes, seed captures, database recoveries) is recorded by a single
shared ``AuditLogger`` instance. **No system writes to ``audit_log``
directly** (architecture.md:1187 / project-context.md:73): all auditable
work routes through ``log_action``. The read path lives in Brain
(``SqliteBrainAdapter``, Story 3.x) and is exposed to users via the
transparency command (Story 5.3); ``AuditLogger`` is **write-only** by
design.

Three load-bearing semantics
----------------------------
1. **Observational, not transactional** (project-context.md:86,
   architecture.md:262). A ``StorageError`` from the underlying engine
   write is caught, logged at WARNING with ``exc_info=True``, and
   swallowed — the caller's primary action proceeds. ``CancelledError``
   (BaseException in py3.12), ``TypeError`` from non-JSON-serializable
   ``details``, and any non-domain exception from the engine all
   propagate untouched.
2. **Append-only** (architecture.md:854). The class exposes one public
   method: ``log_action``. There is no ``update_action``, no
   ``delete_action``, no ``clear``/``purge``/``truncate``. The SQL is
   plain ``INSERT INTO audit_log`` — no ``OR REPLACE``, no
   ``ON CONFLICT``.
3. **Excluded-context opacity by typing** (architecture.md:583,
   project-context.md:72). ``target`` is ``str | None`` and ``details``
   is ``Mapping[str, object] | None`` — there is no struct-shaped
   parameter that could smuggle ``app_name`` / ``window_title`` /
   ``process_name`` from a ``WindowContext`` into a row. Upstream
   policy (Eyes capture-layer filtering, Story 4.2) is responsible for
   passing the canonical opaque sentinel ``"protected_app"`` instead of
   the actual app identity for excluded contexts.

Timestamp clock
---------------
Reuses the canonical ``_utc_now_iso`` from ``nova.core.events`` per
project-context.md:46 ("Any future timestamp-emitting module … MUST
reuse this pattern"). The events module is imported as a **module**
(``from nova.core import events``) and ``events._utc_now_iso()`` is
called at use time so the lookup happens against the events-module
attribute on every call. This preserves the Story 1.3 monkeypatch
contract: ``monkeypatch.setattr("nova.core.events._utc_now_iso", ...)``
takes effect inside this module automatically. A ``from
nova.core.events import _utc_now_iso`` would freeze the local binding at
import time and silently defeat the patch.

References
----------
- Source: _bmad-output/planning-artifacts/epics.md Story 1.8.
- Source: _bmad-output/planning-artifacts/architecture.md
  §Audit Logging Convention (1185–1202) and §Audit Trail (848–854).
- Source: _bmad-output/planning-artifacts/architecture.md §Decision 3
  (567–584) for the ``audit_log`` table schema and opaque-target rule.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Literal

from nova.core import events
from nova.core.exceptions import StorageError
from nova.core.storage.engine import SqliteStorageEngine
from nova.core.types import ActionType

__all__ = [
    "ActionResult",
    "AuditLogger",
    "RESULT_FAILED",
    "RESULT_SKIPPED",
    "RESULT_SUCCESS",
]

logger = logging.getLogger("nova.core.audit")


# ---------------------------------------------------------------------------
# Result vocabulary — canonical closed set for ``audit_log.result`` values.
# Pinned by architecture.md:574. Callers SHOULD use these constants
# rather than inlining the strings (project-context.md:131
# "no magic literals for domain concepts").
# ---------------------------------------------------------------------------

RESULT_SUCCESS = "success"
RESULT_FAILED = "failed"
RESULT_SKIPPED = "skipped"

type ActionResult = Literal["success", "failed", "skipped"]
"""Advisory typed alias — NOT enforced by ``log_action``'s signature.

``log_action`` accepts any non-empty ``str`` for ``result`` (per the
epics AC) so a future story can introduce values like ``"partial"`` for
graceful-partial restore flows without churn through every caller.
Mypy will NOT reject ``logger.log_action(..., result="succes")`` (typo)
or any other free-form string at the method boundary — the only
runtime validation is that ``result`` is a non-empty ``str``.

``ActionResult`` exists so caller-side code can annotate its OWN
variables / parameters with a literal-narrowed type and get strict
mypy checking against the canonical set:

    def restore_app_done() -> ActionResult: ...
    outcome: ActionResult = RESULT_SUCCESS

Callers wanting that narrowing opt in by annotating; the audit
boundary itself stays loose by deliberate design.
"""


# ---------------------------------------------------------------------------
# SQL — exactly one ``INSERT`` form, defined once. The append-only
# guarantee is enforced at module-structure level: the AST gate in
# ``test_audit.py`` walks this module and rejects any string constant
# that contains ``UPDATE`` / ``DELETE`` / ``REPLACE`` / ``TRUNCATE`` /
# ``ALTER`` / ``DROP`` against ``audit_log``.
# ---------------------------------------------------------------------------

_INSERT_SQL = (
    "INSERT INTO audit_log (timestamp, action_type, target, result, details) VALUES (?, ?, ?, ?, ?)"
)


class AuditLogger:
    """The single writer to the ``audit_log`` table.

    Construct with ``AuditLogger(storage=engine)`` after the storage
    engine has started and migrations have run (composition root —
    Story 1.10). Inject the instance into every system that performs an
    auditable action (Hands, Brain-for-deletions, Nerve-for-tier-changes,
    Ritual-for-seed-captures).

    The class deliberately exposes a tiny surface — one ``async``
    method, no read methods, no batching primitives, no background
    tasks. The audit-trail read path goes through Brain's transparency
    model (Story 5.3); batching is a T2 concern at the earliest.
    """

    def __init__(self, *, storage: SqliteStorageEngine) -> None:
        self._storage = storage

    async def log_action(
        self,
        action_type: ActionType,
        target: str | None,
        result: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        """Append one row to the ``audit_log`` table.

        Parameters
        ----------
        action_type
            Required ``ActionType`` enum member (no raw strings — mypy
            strict rejects ``"app_launch"`` literals at the type level).
            Persists as ``str(action_type)`` (e.g. ``"app_launch"``).
        target
            Opaque reference to what was acted on. For excluded
            contexts, callers MUST pass the canonical opaque sentinel
            ``"protected_app"`` (or another opaque string), NEVER the
            actual app name, window title, or process name. ``None`` is
            allowed (e.g. ``ActionType.SEED_CAPTURE`` has no specific
            target). Persists as-is into ``audit_log.target``.
        result
            Short outcome label. Use the module-level ``RESULT_SUCCESS``
            / ``RESULT_FAILED`` / ``RESULT_SKIPPED`` constants at call
            sites. The signature accepts any non-empty ``str`` so future
            additions (e.g. ``"partial"``) need no signature change;
            ``ActionResult`` is an advisory alias for caller-side
            annotations only — see its docstring. Non-``str`` inputs
            (``None`` / ``True`` / ``int`` / ...) and empty /
            whitespace-only strings raise ``ValueError`` at the API
            boundary.
        details
            Optional JSON-serializable additional context. ``None``
            writes ``NULL`` to the ``details`` column; an empty dict
            ``{}`` writes the literal string ``"{}"``. Serialization
            uses ``json.dumps(..., separators=(",", ":"),
            ensure_ascii=False, allow_nan=False)`` — compact,
            unicode-preserving, strict-JSON. **Never contains raw
            excluded content** — that boundary is enforced by the
            caller (Eyes capture-layer filtering); this method does not
            introspect values for sensitivity. Any serialization
            failure — non-serializable values (``datetime``, ``Path``,
            ``set``, ...), circular references, or non-finite floats
            (``NaN`` / ``Infinity``) — is normalized to a single
            ``TypeError`` and propagates to the caller; the row is NOT
            inserted.

        Returns
        -------
        ``None``. Audit logging is observational; there is nothing for
        the caller to consume.

        Raises
        ------
        TypeError
            If ``action_type`` is not an ``ActionType`` member, OR if
            ``details`` cannot be serialized to strict JSON
            (non-serializable type, circular reference, or non-finite
            float). All three ``details``-serialization failure modes
            normalize to a single ``TypeError`` with the underlying
            ``json.dumps`` exception chained via ``__cause__``.
        ValueError
            If ``result`` is not a ``str`` instance, or is empty /
            whitespace-only.
        asyncio.CancelledError
            Propagates untouched (project-context.md:49 — never swallow
            cancellation).
        Exception
            Any non-``StorageError`` raised by the storage engine
            propagates as a non-domain bug (engine failed to translate
            at its boundary).

        Notes
        -----
        ``StorageError`` from the underlying engine write is **caught**,
        logged at WARNING with ``exc_info=True``, and **swallowed** —
        audit logging is observational (project-context.md:86). The
        primary action of the calling system continues regardless of
        whether the audit row landed.
        """
        # Runtime guard: ``mypy strict`` is the primary defense for the
        # ``ActionType`` enum boundary, but a ``# type: ignore`` caller
        # could pass a different ``StrEnum`` member (e.g. ``BriefingState
        # .FIRST_RUN``) and silently widen the audit-vocabulary invariant
        # — ``str(action_type)`` would write ``"first_run"`` into
        # ``audit_log.action_type`` even though it is not an
        # ``ActionType`` member. The whole module's purpose is to hold
        # this boundary; the cheap ``isinstance`` check closes the
        # ``# type: ignore`` path.
        if not isinstance(action_type, ActionType):
            raise TypeError(
                f"action_type must be an ActionType member, got {type(action_type).__name__}"
            )

        # Same boundary discipline for ``result``: ``# type: ignore``
        # callers (or adapters at the type boundary) passing ``None`` /
        # ``True`` / non-``str`` would otherwise crash with
        # ``AttributeError`` from ``.strip()`` instead of the documented
        # ``ValueError``. Check ``isinstance`` BEFORE ``.strip()``.
        if not isinstance(result, str) or not result.strip():
            raise ValueError("result must be a non-empty string")

        # Capture the timestamp BEFORE the try block so the persisted
        # value records when the action *happened* (call time), not when
        # sqlite finished writing. The call goes through the imported
        # ``events`` module reference (not a locally-bound symbol) so
        # ``monkeypatch.setattr("nova.core.events._utc_now_iso", ...)``
        # propagates here automatically.
        timestamp = events._utc_now_iso()

        # Normalize all ``details``-serialization failures to a single
        # ``TypeError`` at the audit boundary:
        #   - non-JSON-serializable values (datetime, Path, set, ...)
        #     → ``json.dumps`` raises ``TypeError`` directly.
        #   - circular references → ``json.dumps`` raises ``ValueError``.
        #   - non-finite floats (NaN / Infinity) → ``allow_nan=False``
        #     makes ``json.dumps`` raise ``ValueError``; without this
        #     flag the default would silently emit non-standard JSON
        #     tokens that any strict downstream parser (Story 5.3
        #     transparency model) would reject when reading the row
        #     back.
        # Catching both and re-raising as ``TypeError`` gives ``log_action``
        # one consistent failure-exception class for caller-supplied bad
        # payloads. The row is never inserted on this path.
        details_json: str | None
        if details is None:
            details_json = None
        else:
            try:
                details_json = json.dumps(
                    details,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
            except (TypeError, ValueError) as err:
                raise TypeError(
                    "details must be JSON-serializable with finite numeric "
                    "values and no circular references"
                ) from err

        try:
            await self._storage.execute(
                _INSERT_SQL,
                (timestamp, str(action_type), target, result, details_json),
            )
        except StorageError:
            # Observational failure: log at WARNING with full traceback,
            # do NOT propagate. The primary action continues.
            #
            # ``logger.warning(..., exc_info=True)`` — NOT
            # ``logger.exception(...)`` — because ``logger.exception`` is
            # hard-coded to ERROR level in the stdlib. WARNING is the
            # correct severity for a degraded sub-feature
            # (project-context.md:129).
            #
            # Neither ``target`` nor ``details`` is included in
            # ``extra``. ``target`` is opaque-by-caller-contract and
            # ``AuditLogger`` does not validate that contract; a buggy
            # upstream caller (Hands / Eyes) that passed a raw app name
            # instead of an opaque sentinel would otherwise leak the raw
            # identity into the log file — exactly the failure mode the
            # ``audit_log`` schema was designed to prevent. Dropping
            # ``target`` from the failure log preserves opacity by
            # construction. ``details`` is dropped for the same privacy
            # reason. The remaining ``action_type`` + ``result`` pair is
            # enough for an analyst to correlate the lost row with the
            # caller's traceback (in ``exc_info``) without leaking
            # caller-supplied content.
            logger.warning(
                "audit write failed; primary action continues",
                extra={
                    "action_type": str(action_type),
                    "result": result,
                },
                exc_info=True,
            )
            return

        # Successful-write DEBUG follows the same opacity discipline as
        # the failure WARNING — no ``target``, no ``details``. Keeps the
        # two log paths consistent so analysts cannot accidentally see
        # caller-supplied content via one path that the other path
        # screens out.
        logger.debug(
            "audit row written",
            extra={
                "action_type": str(action_type),
                "result": result,
            },
        )
