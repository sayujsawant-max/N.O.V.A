"""Story 1.10 AC #5 — full port + model graph imports cleanly.

Closes a Story 1.9 deferred-work item: "No smoke / integration test
imports the full port + model graph. ~15 cross-system type dependencies
... a future back-reference could introduce a circular import without
test catching it. Target: Story 1.10 (composition root)."

Imports every port Protocol + every system model module + the one
concrete adapter class in one test body. Any circular-import regression
fails the test; every other port-graph test stays free to focus on
behavior.
"""

from __future__ import annotations

import inspect

from nova.adapters.shield import NoOpShieldAdapter
from nova.core.types import ActionType
from nova.ports import (
    BrainPort,
    EyesPort,
    HandsPort,
    NervePort,
    RitualPort,
    ShieldPort,
    SkinPort,
    VoicePort,
)
from nova.systems.brain import models as brain_models
from nova.systems.eyes import models as eyes_models
from nova.systems.hands import models as hands_models
from nova.systems.ritual import models as ritual_models
from nova.systems.skin import models as skin_models

_ALL_PORTS: tuple[type, ...] = (
    BrainPort,
    EyesPort,
    HandsPort,
    NervePort,
    RitualPort,
    ShieldPort,
    SkinPort,
    VoicePort,
)

_ALL_MODEL_MODULES = (
    brain_models,
    eyes_models,
    hands_models,
    ritual_models,
    skin_models,
)


def test_all_ports_and_models_importable() -> None:
    """Every port Protocol + model module + NoOpShieldAdapter resolves without error."""
    assert len(_ALL_PORTS) == 8
    assert len(_ALL_MODEL_MODULES) == 5
    assert NoOpShieldAdapter is not None


def test_every_port_is_a_protocol() -> None:
    """Every port is a ``typing.Protocol`` — catches an accidental ABC downgrade.

    ``typing.Protocol`` cannot be used directly as the second argument
    to ``issubclass``; CPython exposes an ``_is_protocol`` sentinel
    attribute on every Protocol class for runtime introspection.
    """
    for port in _ALL_PORTS:
        assert getattr(port, "_is_protocol", False), f"{port.__name__} is not a Protocol"


def test_every_port_declares_at_least_one_async_method() -> None:
    """Empty-Protocol regression guard — ports must surface at least one async method."""
    for port in _ALL_PORTS:
        async_methods = [
            name for name, member in inspect.getmembers(port) if inspect.iscoroutinefunction(member)
        ]
        assert async_methods, f"{port.__name__} declares no async methods"


def test_noop_shield_adapter_conforms_to_shield_port() -> None:
    """Runtime structural conformance — locked separately in Story 1.9 but re-verified here."""
    adapter = NoOpShieldAdapter()
    assert isinstance(adapter, ShieldPort)


def test_action_type_enum_reaches_hands_models() -> None:
    """Cross-system type graph reaches ``ActionType`` from ``core.types``."""
    # The import above succeeding is the actual test; this assertion
    # documents which cross-system edge is being exercised.
    assert ActionType.APP_LAUNCH is not None
    assert hands_models is not None
