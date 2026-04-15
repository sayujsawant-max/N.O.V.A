"""Shield no-op adapter behavior (Story 1.9 AC #14).

Verifies :class:`nova.adapters.shield.noop.NoOpShieldAdapter` satisfies the
contract pinned in Story 1.9 AC #6:

- Structurally conforms to :class:`nova.ports.shield.ShieldPort` (runtime-
  checked via :func:`isinstance` because ``ShieldPort`` is decorated with
  :func:`typing.runtime_checkable`).
- :meth:`NoOpShieldAdapter.is_focus_protected` returns exactly ``False``.
- :meth:`NoOpShieldAdapter.allow_action` returns exactly ``True`` for every
  :class:`nova.core.types.ActionType` member (parametrized over
  ``list(ActionType)`` so a future 12th member auto-extends coverage).
- No instance state (``vars(adapter) == {}``) — the composition root must
  be able to instantiate the adapter with zero arguments.
"""

from __future__ import annotations

import inspect

import pytest

from nova.adapters.shield.noop import NoOpShieldAdapter
from nova.core.types import ActionType
from nova.ports.shield import ShieldPort


def test_noop_shield_adapter_satisfies_shield_port() -> None:
    """``NoOpShieldAdapter`` structurally conforms to ``ShieldPort``.

    ``ShieldPort`` is the one port in Story 1.9 opted into
    :func:`typing.runtime_checkable`, so ``isinstance`` introspects the
    adapter's shape against the Protocol's methods at runtime.
    """
    adapter = NoOpShieldAdapter()
    assert isinstance(adapter, ShieldPort), (
        "NoOpShieldAdapter must satisfy ShieldPort structurally."
    )


@pytest.mark.parametrize("method_name", ["is_focus_protected", "allow_action"])
def test_noop_shield_adapter_methods_are_coroutines(method_name: str) -> None:
    """Adapter methods MUST be coroutines — ``runtime_checkable`` only checks names.

    ``isinstance(adapter, ShieldPort)`` succeeds for any object whose method
    NAMES match the Protocol — sync methods, sync ``def``s with the right
    name, even sync stubs. A regression that accidentally drops the
    ``async`` keyword would slip past structural conformance and only
    surface at ``await`` time as ``TypeError: object bool can't be used
    in 'await' expression``. This test catches that regression at import
    time on the adapter class itself.
    """
    method = getattr(NoOpShieldAdapter, method_name)
    assert inspect.iscoroutinefunction(method), (
        f"NoOpShieldAdapter.{method_name} must be ``async def`` (a coroutine "
        f"function) — ``isinstance`` against a runtime_checkable Protocol "
        f"only checks method names, not async-ness."
    )


async def test_noop_shield_adapter_is_focus_protected_returns_false() -> None:
    """Focus is never protected in the T1 no-op adapter."""
    adapter = NoOpShieldAdapter()
    result = await adapter.is_focus_protected()
    assert result is False, (
        f"is_focus_protected must return exactly False (identity), got {result!r}."
    )


@pytest.mark.parametrize("action_type", list(ActionType), ids=lambda m: m.name)
async def test_noop_shield_adapter_allow_action_returns_true_for_all_action_types(
    action_type: ActionType,
) -> None:
    """Every :class:`ActionType` is allowed by the T1 no-op adapter."""
    adapter = NoOpShieldAdapter()
    result = await adapter.allow_action(action_type)
    assert result is True, (
        f"allow_action({action_type.name}) must return exactly True, got {result!r}."
    )


def test_noop_shield_adapter_has_no_instance_state() -> None:
    """The adapter carries no instance attributes."""
    adapter = NoOpShieldAdapter()
    assert vars(adapter) == {}, (
        f"NoOpShieldAdapter must have no instance state; vars() = {vars(adapter)!r}."
    )
    # __slots__ — if declared, must be empty.
    slots = getattr(type(adapter), "__slots__", ())
    assert not slots, f"NoOpShieldAdapter must not declare __slots__ with entries; got {slots!r}."


def test_noop_shield_adapter_construction_takes_no_arguments() -> None:
    """``NoOpShieldAdapter()`` constructs with zero arguments (composition-root friendly)."""
    signature = inspect.signature(NoOpShieldAdapter)
    non_self_params = [name for name in signature.parameters if name != "self"]
    assert non_self_params == [], (
        f"NoOpShieldAdapter construction must take zero arguments; signature "
        f"exposes {non_self_params}."
    )
    # Instantiation succeeds with zero args.
    NoOpShieldAdapter()
