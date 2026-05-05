"""Unit tests for the Story 3.6 ``__post_init__`` validators on
:class:`~nova.systems.hands.models.ActionResult` and
:class:`~nova.systems.hands.models.ActionRequest`.

Closes deferred-work.md:137 (ActionRequest.details MappingProxyType
freeze) + deferred-work.md:146 (ActionResult tri-state validator).
"""

from __future__ import annotations

from types import MappingProxyType

import pytest

from nova.core.types import ActionType
from nova.systems.hands.models import ActionRequest, ActionResult

# ---------------------------------------------------------------------------
# ActionResult tri-state validator
# ---------------------------------------------------------------------------


def test_action_result_success_true_with_reason_raises_value_error() -> None:
    with pytest.raises(ValueError, match="success=True requires reason=None"):
        ActionResult(
            action_type=ActionType.APP_LAUNCH,
            target="x",
            success=True,
            reason="failed",
        )


def test_action_result_success_false_with_none_reason_raises_value_error() -> None:
    with pytest.raises(ValueError, match="success=False requires non-empty reason"):
        ActionResult(
            action_type=ActionType.APP_LAUNCH,
            target="x",
            success=False,
            reason=None,
        )


def test_action_result_success_false_with_empty_reason_raises_value_error() -> None:
    with pytest.raises(ValueError, match="success=False requires non-empty reason"):
        ActionResult(
            action_type=ActionType.APP_LAUNCH,
            target="x",
            success=False,
            reason="",
        )


def test_action_result_success_true_with_none_reason_constructs_cleanly() -> None:
    result = ActionResult(
        action_type=ActionType.APP_LAUNCH,
        target="VS Code",
        success=True,
        reason=None,
    )
    assert result.success is True
    assert result.reason is None


def test_action_result_success_false_with_non_empty_reason_constructs_cleanly() -> None:
    result = ActionResult(
        action_type=ActionType.APP_LAUNCH,
        target="Postman",
        success=False,
        reason="not found",
    )
    assert result.success is False
    assert result.reason == "not found"


# ---------------------------------------------------------------------------
# ActionRequest details freeze
# ---------------------------------------------------------------------------


def test_action_request_wraps_dict_details_in_mapping_proxy() -> None:
    """A caller-supplied dict must be wrapped (defensively copied) in MappingProxyType."""
    original_dict: dict[str, object] = {"executable": "code", "args": ()}
    req = ActionRequest(
        action_type=ActionType.APP_LAUNCH,
        target="VS Code",
        details=original_dict,
    )
    assert isinstance(req.details, MappingProxyType)
    # Defensive copy: mutating the original must NOT reach the request.
    original_dict["smuggled"] = "leak"
    assert req.details is not None
    assert "smuggled" not in req.details


def test_action_request_none_details_stays_none() -> None:
    req = ActionRequest(
        action_type=ActionType.APP_LAUNCH,
        target=None,
        details=None,
    )
    assert req.details is None


def test_action_request_existing_mapping_proxy_is_still_re_wrapped_for_isolation() -> None:
    """Even MappingProxyType inputs are re-wrapped via dict copy for isolation.

    Closes /bmad-code-review patch #3 (BH#3). A MappingProxyType is
    only a read-only VIEW over a mutable dict; if the caller built the
    proxy and still holds the source dict, mutating it would otherwise
    leak through ``req.details``. The re-wrap (``MappingProxyType(dict
    (self.details))``) snapshots the contents into a fresh dict so the
    caller's source becomes irrelevant.
    """
    source: dict[str, object] = {"a": 1}
    proxy = MappingProxyType(source)
    req = ActionRequest(
        action_type=ActionType.APP_LAUNCH,
        target="x",
        details=proxy,
    )
    assert isinstance(req.details, MappingProxyType)
    # NOT identity — re-wrapped via dict copy.
    assert req.details is not proxy
    # Mutation of the original source does NOT leak into req.details.
    source["smuggled"] = "leak"
    assert req.details is not None
    assert "smuggled" not in req.details
