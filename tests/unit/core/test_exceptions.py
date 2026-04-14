"""Story 1.2 contract tests for `nova.core.exceptions`."""

from __future__ import annotations

import pytest

from nova.core.exceptions import (
    AdapterError,
    ApiUnavailableError,
    ConfigError,
    ModeNotFoundError,
    NovaError,
    StorageError,
)

ALL_EXCEPTIONS: list[type[NovaError]] = [
    NovaError,
    StorageError,
    ConfigError,
    ApiUnavailableError,
    ModeNotFoundError,
    AdapterError,
]


@pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
def test_subclass_of_nova_error_and_exception(exc_cls: type[NovaError]) -> None:
    assert issubclass(exc_cls, NovaError)
    assert issubclass(exc_cls, Exception)


@pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
def test_str_returns_message(exc_cls: type[NovaError]) -> None:
    msg = "something failed"
    assert str(exc_cls(msg)) == msg


@pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
def test_message_is_required_positional(exc_cls: type[NovaError]) -> None:
    with pytest.raises(TypeError):
        exc_cls()  # type: ignore[call-arg]


@pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
def test_from_clause_chains_via_dunder_cause(exc_cls: type[NovaError]) -> None:
    underlying = ValueError("root cause")
    try:
        raise exc_cls("wrapped") from underlying
    except exc_cls as caught:
        assert caught.__cause__ is underlying


@pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
def test_cause_kwarg_stores_but_does_not_chain(exc_cls: type[NovaError]) -> None:
    """Constructor `cause=` is introspection-only; chaining requires `from`.

    Regression gate against future contributors assuming `cause=` triggers
    Python exception chaining. It does not.
    """
    underlying = ValueError("root cause")
    exc = exc_cls("wrapped", cause=underlying)
    assert exc.cause is underlying
    assert exc.__cause__ is None


@pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
def test_nova_error_catches_every_subclass(exc_cls: type[NovaError]) -> None:
    try:
        raise exc_cls("boom")
    except NovaError as caught:
        assert isinstance(caught, exc_cls)


@pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
def test_cause_self_rejected_at_construction(exc_cls: type[NovaError]) -> None:
    """`cause is self` triggers ValueError in __init__ (P4).

    Realistic trigger: re-init an existing instance with itself as cause.
    Direct cycles via ``exc.cause = exc`` after construction are NOT
    caught — that requires a chain-walker with a visited set at the
    consumer site.
    """
    exc = exc_cls("first")
    with pytest.raises(ValueError, match="cause cannot be self"):
        exc_cls.__init__(exc, "again", cause=exc)


@pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
@pytest.mark.parametrize("bad_cause", ["string", 42, ["list"], object()])
def test_cause_must_be_exception_or_none(exc_cls: type[NovaError], bad_cause: object) -> None:
    """Non-`BaseException` values for `cause=` are rejected at construction (P5).

    The type hint promises ``BaseException | None``; the runtime check
    enforces the promise so the failure surfaces at the bug site, not at
    a downstream ``raise err.cause from None``.
    """
    with pytest.raises(TypeError, match="cause must be"):
        exc_cls("msg", cause=bad_cause)  # type: ignore[arg-type]


def test_repr_without_cause_matches_default_form() -> None:
    """`repr` shows the canonical Python form when `cause` is unset (P8)."""
    assert repr(StorageError("db down")) == "StorageError('db down')"


def test_repr_with_cause_surfaces_cause_attribute() -> None:
    """`repr` includes `cause=...` when set, so debuggers and `%r` log lines see it (P8)."""
    underlying = ValueError("y")
    rendered = repr(StorageError("x", cause=underlying))
    assert rendered.startswith("StorageError('x', cause=")
    assert "ValueError" in rendered
    assert "'y'" in rendered


@pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
def test_empty_string_message_pinned_as_wai(exc_cls: type[NovaError]) -> None:
    """D1 resolution (P13): empty `message` is accepted; pin behavior.

    `""` is still a `str`, so the runtime type guard passes. The exception
    classes do not police message *content*. If Story 1.10's CLI top-level
    handler needs non-empty messages, it validates at its own boundary.
    """
    exc = exc_cls("")
    assert str(exc) == ""
    assert exc.args == ("",)


@pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
@pytest.mark.parametrize("bad_message", [None, 42, ["x"], object()])
def test_non_str_message_rejected_at_construction(
    exc_cls: type[NovaError], bad_message: object
) -> None:
    """D1 reconciliation: type-hint promise (`message: str`) is enforced at runtime.

    Symmetric with the `cause` runtime check (P5) — if the type hint says
    `str`, the runtime rejects non-`str`. D1's "don't police message
    content" intent is preserved by `test_empty_string_message_pinned_as_wai`
    above (empty string is still a str). Only *type* is policed here, not
    content. Closes the contract drift between the static type hint and
    runtime behavior.
    """
    with pytest.raises(TypeError, match="message must be str"):
        exc_cls(bad_message)  # type: ignore[arg-type]
