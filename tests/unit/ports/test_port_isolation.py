"""Port-layer isolation + shape guardrail (Story 1.9 AC #13).

AST-level inspection of every ``src/nova/ports/*.py`` module and of the
``src/nova/systems/*/models.py`` models they reference. Closes Story 1.9
AC #1, #2, #3, #4, #5, #7, #8, #10:

- Every port file declares exactly one :class:`typing.Protocol` subclass.
- Every port method is ``async def`` with an ellipsis body.
- Method ordering inside each port matches the AC #4 contract.
- Port signatures reference only allowlisted types.
- Ports never import from ``nova.adapters.*``.
- Ports never import from ``nova.systems.{X}.<non-models-suffix>``.
- Ports never import a forbidden adapter stdlib module (``sqlite3``,
  ``yaml``, ``rich``, ``anthropic``, ``win32*``, ``pywin32``, ``psutil``).
- ``nova.ports.__all__`` is alphabetical and complete.
- Cross-system domain models are frozen dataclasses with ``tuple[...]``
  sequence fields (not ``list[...]``).
- ``ModeInfo`` is distinct from ``ModeConfig``.

Implementation rule (memory/feedback_ast_static_analysis_tests.md): use
``ast.walk`` / ``ast.ClassDef`` / ``ast.AsyncFunctionDef`` inspection.
Never text regex — regex trips on docstrings and comments that mention
forbidden names innocently.

Helper-sharing decision (Story 1.9 AC #15): forbidden-set frozensets and
small AST helpers are **duplicated verbatim** from
``tests/unit/core/test_core_isolation.py`` with a "mirror of" comment so
the two isolation test files stay independent. The duplication is
trivially reviewable and removes a cross-test-package import dance.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import typing
from pathlib import Path
from types import ModuleType

import pytest

import nova.ports.brain as brain_port_module
import nova.ports.eyes as eyes_port_module
import nova.ports.hands as hands_port_module
import nova.ports.nerve as nerve_port_module
import nova.ports.ritual as ritual_port_module
import nova.ports.shield as shield_port_module
import nova.ports.skin as skin_port_module
import nova.ports.voice as voice_port_module
import nova.systems.brain.models as brain_models_module
import nova.systems.eyes.models as eyes_models_module
import nova.systems.hands.models as hands_models_module
import nova.systems.ritual.models as ritual_models_module
import nova.systems.skin.models as skin_models_module
from nova.core.config import ModeConfig
from nova.systems.brain.models import ModeInfo

# ---------------------------------------------------------------------------
# Mirrors of tests/unit/core/test_core_isolation.py — keep in sync.
# ---------------------------------------------------------------------------

FORBIDDEN_TOPLEVEL_MODULES: frozenset[str] = frozenset(
    {
        "sqlite3",
        "anthropic",
        "pywin32",
        "pywintypes",
        "psutil",
        "win32api",
        "win32gui",
        "win32com",
        "win32con",
        "rich",
        "yaml",
    }
)

FORBIDDEN_NOVA_PREFIXES: tuple[str, ...] = ("nova.adapters",)

RELATIVE_IMPORT_MARKER = "<relative>"


# ---------------------------------------------------------------------------
# Port registry + AC #4 method contract
# ---------------------------------------------------------------------------

# Dotted module name -> (expected Protocol class name, ordered method tuple).
# The method tuples pin AC #4's method ordering contract. Adding a method
# out-of-order or a method not listed here trips the shape tests.
PORT_CONTRACT: dict[ModuleType, tuple[str, tuple[str, ...]]] = {
    brain_port_module: (
        "BrainPort",
        (
            # Story 3.1 reshape: granular session/seed/snapshot surface
            # replaces the Story 1.9 stub's aggregate methods.
            "create_session",
            "end_session",
            "get_last_session",
            "get_last_seed",
            "store_snapshot",
            "get_last_snapshot_for_session",
            # Story 3.2 addition — Nerve-side BriefingAggregate assembly
            # queries Brain by mode stem (the canonical identifier, matching
            # the sessions.mode_name write-side contract).
            "get_mode_last_used",
            # Epic 5 surface retained on the port; adapter stubs each with
            # ``NotImplementedError("Epic 5 scope")`` until that epic ships.
            "query_memory",
            "delete_matching",
            "confirm_deletion",
            "get_transparency_model",
        ),
    ),
    eyes_port_module: (
        "EyesPort",
        ("capture_current_workspace",),
    ),
    hands_port_module: (
        "HandsPort",
        ("restore_mode",),
    ),
    shield_port_module: (
        "ShieldPort",
        ("is_focus_protected", "allow_action"),
    ),
    voice_port_module: (
        "VoicePort",
        (
            "generate_prose_enrichment",
            "generate_restore_summary",
            "generate_shutdown_confirmation",
        ),
    ),
    ritual_port_module: (
        "RitualPort",
        ("build_briefing", "begin_shutdown"),
    ),
    skin_port_module: (
        "SkinPort",
        (
            "render_briefing_card",
            "render_progress",
            "render_shutdown_card",
            "render_response",
            "collect_input",
            "parse_command",
        ),
    ),
    nerve_port_module: (
        "NervePort",
        ("startup", "route_command"),
    ),
}

PORT_MODULES: list[ModuleType] = list(PORT_CONTRACT.keys())


# Forbidden name prefixes anywhere in port method signatures.
# Derived from FORBIDDEN_TOPLEVEL_MODULES so the two lists cannot drift —
# any new entry to the import denylist auto-applies to signature checks.
FORBIDDEN_SIGNATURE_PREFIXES: tuple[str, ...] = tuple(sorted(FORBIDDEN_TOPLEVEL_MODULES))


# ---------------------------------------------------------------------------
# AST helpers (mirrors of tests/unit/core/test_core_isolation.py)
# ---------------------------------------------------------------------------


def _read_module_source(module: ModuleType) -> str:
    source_path_str = inspect.getsourcefile(module)
    assert source_path_str is not None
    return Path(source_path_str).read_text(encoding="utf-8")


def _has_forbidden_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    """Exact prefix match with ``.`` boundary — see test_core_isolation.py."""
    return any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)


def _all_imports(tree: ast.AST) -> list[tuple[str, str | None]]:
    """Return ``(top_module, symbol_name)`` pairs for every import in the tree."""
    pairs: list[tuple[str, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                pairs.append((alias.name.split(".")[0], None))
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or node.module is None:
                pairs.append((RELATIVE_IMPORT_MARKER, None))
                continue
            top = node.module.split(".")[0]
            for alias in node.names:
                pairs.append((top, alias.name))
    return pairs


def _dynamic_import_targets(tree: ast.AST) -> list[str]:
    """Return first-segment string-literal targets of dynamic imports."""
    targets: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_dunder = isinstance(func, ast.Name) and func.id == "__import__"
        is_importlib = isinstance(func, ast.Attribute) and func.attr == "import_module"
        if not (is_dunder or is_importlib):
            continue
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            targets.append(node.args[0].value.split(".")[0])
    return targets


def _dynamic_import_full_targets(tree: ast.AST) -> list[str]:
    """Return FULL-path string-literal targets of dynamic imports."""
    targets: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_dunder = isinstance(func, ast.Name) and func.id == "__import__"
        is_importlib = isinstance(func, ast.Attribute) and func.attr == "import_module"
        if not (is_dunder or is_importlib):
            continue
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            targets.append(node.args[0].value)
    return targets


def _port_class_def(module: ModuleType, class_name: str) -> ast.ClassDef:
    """Locate the single ``ast.ClassDef`` matching ``class_name`` at module top level."""
    tree = ast.parse(_read_module_source(module))
    matches = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    ]
    assert len(matches) == 1, (
        f"Expected exactly one ClassDef named {class_name!r} at top level of "
        f"{module.__name__}; found {len(matches)}."
    )
    return matches[0]


def _collect_annotation_names(node: ast.AST | None) -> list[str]:
    """Recursively collect every dotted name referenced inside an annotation.

    Handles:
    - ``ast.Name`` (``int``) — added as-is.
    - ``ast.Attribute`` (``typing.Protocol``) — reconstructed into a single
      dotted string (``"typing.Protocol"``), and its constituent ``Name`` /
      ``Attribute`` nodes are SKIPPED to avoid double-counting (a previous
      version emitted both ``"typing"`` and ``"Protocol"`` AND ``"typing.Protocol"``
      for the same source).
    - ``ast.Subscript`` (``list[T]``, ``X | None``) and ``ast.BinOp`` — walked
      transparently because ``ast.walk`` visits every child.
    - ``ast.Constant(str)`` — string-form annotations like ``"sqlite3.Row"``
      are parsed as expressions and recursed into so forbidden types cannot
      hide behind quotes.
    """
    names: list[str] = []
    if node is None:
        return names

    # Pre-pass: collect every ast.Name / ast.Attribute that is a CHILD of some
    # other ast.Attribute. These are reconstructed into the parent's dotted
    # name and must be skipped during the main walk to prevent double-counting.
    skip_ids: set[int] = set()
    for subnode in ast.walk(node):
        if isinstance(subnode, ast.Attribute):
            inner = subnode.value
            while isinstance(inner, ast.Attribute):
                skip_ids.add(id(inner))
                inner = inner.value
            if isinstance(inner, ast.Name):
                skip_ids.add(id(inner))

    for subnode in ast.walk(node):
        if id(subnode) in skip_ids:
            continue
        if isinstance(subnode, ast.Name):
            names.append(subnode.id)
        elif isinstance(subnode, ast.Attribute):
            # Reconstruct the dotted path by walking up the attribute chain.
            parts: list[str] = [subnode.attr]
            parent = subnode.value
            while isinstance(parent, ast.Attribute):
                parts.append(parent.attr)
                parent = parent.value
            if isinstance(parent, ast.Name):
                parts.append(parent.id)
            names.append(".".join(reversed(parts)))
        elif isinstance(subnode, ast.Constant) and isinstance(subnode.value, str):
            # String-form annotation (PEP 563-style or explicit). Re-parse and
            # recurse so forbidden types like ``"sqlite3.Row"`` cannot hide.
            try:
                inner_tree = ast.parse(subnode.value, mode="eval")
            except SyntaxError:
                # Not a valid Python expression — ignore (could be a docstring
                # mistakenly typed as an annotation).
                continue
            names.extend(_collect_annotation_names(inner_tree.body))
    return names


# ---------------------------------------------------------------------------
# Tests — isolation (no forbidden imports)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", PORT_MODULES)
def test_no_relative_imports(module: ModuleType) -> None:
    """Port files use absolute imports only."""
    tree = ast.parse(_read_module_source(module))
    relatives = [m for m, _ in _all_imports(tree) if m == RELATIVE_IMPORT_MARKER]
    assert not relatives, (
        f"Relative imports are forbidden in {module.__name__}; found {len(relatives)}."
    )


@pytest.mark.parametrize("module", PORT_MODULES)
def test_no_forbidden_imports(module: ModuleType) -> None:
    """Port files never reach for ``sqlite3`` / ``rich`` / ``anthropic`` / Win32 / etc."""
    tree = ast.parse(_read_module_source(module))
    used = {m for m, _ in _all_imports(tree)}
    leaked = used & FORBIDDEN_TOPLEVEL_MODULES
    assert not leaked, f"Forbidden adapter imports leaked into {module.__name__}: {sorted(leaked)}."


@pytest.mark.parametrize("module", PORT_MODULES)
def test_no_import_of_nova_adapters(module: ModuleType) -> None:
    """Port files never import from ``nova.adapters.*``."""
    tree = ast.parse(_read_module_source(module))
    leaked: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            if _has_forbidden_prefix(node.module, FORBIDDEN_NOVA_PREFIXES):
                leaked.append(node.module)
                continue
            for alias in node.names:
                composed = f"{node.module}.{alias.name}"
                if _has_forbidden_prefix(composed, FORBIDDEN_NOVA_PREFIXES):
                    leaked.append(composed)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _has_forbidden_prefix(alias.name, FORBIDDEN_NOVA_PREFIXES):
                    leaked.append(alias.name)
    assert not leaked, (
        f"Forbidden nova.adapters imports in {module.__name__}: {sorted(set(leaked))}."
    )


@pytest.mark.parametrize("module", PORT_MODULES)
def test_ports_only_import_from_system_models_modules(module: ModuleType) -> None:
    """Port files may import from ``nova.systems.{X}.models`` only.

    Per Story 1.9 AC #8, ``.models`` is the ONE permitted cross-system
    suffix. Anything else (``.system``, ``.adapter``, ``.commands``, bare
    ``nova.systems.{X}``) is forbidden. Implementation: for each
    ``nova.systems.*`` import, split on ``.`` and verify segments[3] ==
    "models"; reject bare ``nova.systems.{X}`` imports (no 4th segment).
    """
    tree = ast.parse(_read_module_source(module))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            segments = node.module.split(".")
            if (
                len(segments) >= 2
                and segments[0] == "nova"
                and segments[1] == "systems"
                and (len(segments) < 4 or segments[3] != "models")
            ):
                offenders.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                segments = alias.name.split(".")
                if (
                    len(segments) >= 2
                    and segments[0] == "nova"
                    and segments[1] == "systems"
                    and (len(segments) < 4 or segments[3] != "models")
                ):
                    offenders.append(alias.name)
    assert not offenders, (
        f"Non-``.models`` cross-system imports in {module.__name__}: "
        f"{sorted(set(offenders))}. Only ``nova.systems.{{X}}.models`` crosses "
        f"system boundaries (Story 1.9 AC #8)."
    )


@pytest.mark.parametrize("module", PORT_MODULES)
def test_no_dynamic_imports_of_forbidden_modules(module: ModuleType) -> None:
    """``importlib.import_module`` / ``__import__`` cannot reach adapters."""
    tree = ast.parse(_read_module_source(module))
    targets = _dynamic_import_targets(tree)
    leaked = set(targets) & FORBIDDEN_TOPLEVEL_MODULES
    assert not leaked, f"Dynamic forbidden imports detected in {module.__name__}: {sorted(leaked)}."
    full_targets = _dynamic_import_full_targets(tree)
    nova_leaked = [t for t in full_targets if _has_forbidden_prefix(t, FORBIDDEN_NOVA_PREFIXES)]
    assert not nova_leaked, (
        f"Dynamic nova.adapters imports in {module.__name__}: {sorted(set(nova_leaked))}."
    )


# ---------------------------------------------------------------------------
# Tests — Protocol shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", PORT_MODULES)
def test_each_port_defines_exactly_one_protocol_class(module: ModuleType) -> None:
    """Each port file declares exactly one Protocol class with the expected name."""
    expected_name, _ = PORT_CONTRACT[module]
    tree = ast.parse(_read_module_source(module))
    class_defs = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    assert len(class_defs) == 1, (
        f"{module.__name__} must declare exactly one top-level class; found "
        f"{len(class_defs)}: {[c.name for c in class_defs]}."
    )
    class_def = class_defs[0]
    assert class_def.name == expected_name, (
        f"{module.__name__} should declare {expected_name}, found {class_def.name}."
    )
    base_names = _collect_annotation_names(
        ast.Module(body=[ast.Expr(value=base) for base in class_def.bases], type_ignores=[])
    )
    assert "Protocol" in base_names, (
        f"{expected_name} must subclass typing.Protocol; bases resolved to {base_names}."
    )


@pytest.mark.parametrize("module", PORT_MODULES)
def test_all_port_methods_are_async_with_ellipsis_body(module: ModuleType) -> None:
    """Every method is ``async def`` with a single ``...`` body and no decorators.

    Rejects ``@property`` / ``@staticmethod`` / ``@classmethod`` / ``@cached_property``
    on Protocol methods — those produce odd structural-typing semantics
    (e.g. an ``async @staticmethod`` doesn't bind ``self``, breaking adapter
    conformance) and have no business inside a port surface.
    """
    expected_name, _ = PORT_CONTRACT[module]
    class_def = _port_class_def(module, expected_name)
    method_nodes = [
        node for node in class_def.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert method_nodes, f"{expected_name} must declare at least one method."
    for method in method_nodes:
        assert isinstance(method, ast.AsyncFunctionDef), (
            f"{expected_name}.{method.name} must be ``async def``; is {type(method).__name__}."
        )
        assert method.decorator_list == [], (
            f"{expected_name}.{method.name} must have no decorators (Protocol methods "
            f"are vanilla async methods); found {len(method.decorator_list)}."
        )
        assert len(method.body) == 1, (
            f"{expected_name}.{method.name} body must be a single ``...`` "
            f"expression; found {len(method.body)} statements."
        )
        stmt = method.body[0]
        is_ellipsis = (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and stmt.value.value is Ellipsis
        )
        assert is_ellipsis, (
            f"{expected_name}.{method.name} body must be ``...`` exactly "
            f"(no ``raise NotImplementedError``, no default implementation)."
        )


@pytest.mark.parametrize("module", PORT_MODULES)
def test_port_method_ordering_matches_contract(module: ModuleType) -> None:
    """Method definitions appear in the order pinned by AC #4."""
    expected_name, expected_order = PORT_CONTRACT[module]
    class_def = _port_class_def(module, expected_name)
    actual_order = tuple(
        node.name
        for node in class_def.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    assert actual_order == expected_order, (
        f"{expected_name} method ordering drift: expected {expected_order}, found {actual_order}."
    )


# Methods that previous story revisions declared on ``BrainPort`` but that
# Story 3.1's port reshape explicitly removed. Re-introducing any of them is a
# contract regression that should fail in CI, not slip past the ordering test
# via a silent PORT_CONTRACT edit. Locked by Story 3.1 AC #26.
_BRAIN_PORT_REMOVED_METHODS: frozenset[str] = frozenset(
    {
        "load_last_session",
        "store_session",
        "load_briefing_aggregate",
    }
)


def test_nerve_port_route_command_returns_command_outcome() -> None:
    """Story 3.5 AC #4 — ``NervePort.route_command`` return annotation is ``CommandOutcome``.

    Closes ``deferred-work.md:139`` — the previous ``-> None`` return left
    the error / continue / exit surface implicit. Story 3.5 reshapes the
    return to the closed two-member ``CommandOutcome`` vocabulary so the
    REPL loop can drive its continue/exit decision off the return value.

    Uses :func:`typing.get_type_hints` for runtime type resolution rather
    than raw :func:`inspect.signature` text-comparison; the latter would
    return the literal string ``"CommandOutcome"`` under
    ``from __future__ import annotations``, masking a typo or refactor
    that re-introduced ``-> None``.
    """
    from nova.ports.nerve import NervePort
    from nova.systems.nerve.models import CommandOutcome

    hints = typing.get_type_hints(NervePort.route_command)
    assert "return" in hints, (
        "NervePort.route_command must declare a return annotation; "
        "found no 'return' key in get_type_hints."
    )
    assert hints["return"] is CommandOutcome, (
        f"NervePort.route_command return annotation must be CommandOutcome "
        f"(closes deferred-work.md:139); resolved to {hints['return']!r}."
    )
    # ``startup`` stays ``-> None`` per AC #2 — assert it explicitly so a
    # future refactor that flipped both methods together would fail this
    # guard rather than silently wide-en the port surface.
    startup_hints = typing.get_type_hints(NervePort.startup)
    assert startup_hints.get("return") is type(None), (
        f"NervePort.startup must remain ``-> None``; resolved to {startup_hints.get('return')!r}."
    )


def test_brain_port_does_not_redeclare_removed_methods() -> None:
    """Story 3.1 AC #26 — lock the removal of the three Story 1.9 stub methods.

    The ordering test above catches re-introduction as a side-effect of
    tuple-equality with ``PORT_CONTRACT[brain_port_module]``, but a future
    PR could silently edit the contract alongside the port definition. This
    test makes the removal an explicit, named invariant.
    """
    class_def = _port_class_def(brain_port_module, "BrainPort")
    actual_names = {
        node.name
        for node in class_def.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    rediscovered = _BRAIN_PORT_REMOVED_METHODS & actual_names
    assert not rediscovered, (
        f"BrainPort re-introduced Story 3.1-removed method(s): {sorted(rediscovered)}. "
        f"Story 3.1 reshape removes load_last_session / store_session / load_briefing_aggregate; "
        f"the granular methods (create_session / end_session / get_last_session / "
        f"get_last_seed / store_snapshot / get_last_snapshot_for_session) replace them."
    )


@pytest.mark.parametrize("module", PORT_MODULES)
def test_port_signatures_use_only_allowed_types(module: ModuleType) -> None:
    """No forbidden adapter types appear in any port method signature.

    Walks every annotation surface of every method:
    - positional-or-keyword args (``method.args.args``)
    - positional-only args (``method.args.posonlyargs``)
    - keyword-only args (``method.args.kwonlyargs``)
    - ``*args`` (``method.args.vararg``)
    - ``**kwargs`` (``method.args.kwarg``)
    - return type (``method.returns``)

    String-form annotations (``-> "sqlite3.Row"``) are unwrapped by
    ``_collect_annotation_names`` so quoting cannot hide a forbidden type.
    """
    expected_name, _ = PORT_CONTRACT[module]
    class_def = _port_class_def(module, expected_name)
    referenced: list[str] = []
    for method in class_def.body:
        if not isinstance(method, ast.AsyncFunctionDef):
            continue
        all_args: list[ast.arg] = [
            *method.args.posonlyargs,
            *method.args.args,
            *method.args.kwonlyargs,
        ]
        if method.args.vararg is not None:
            all_args.append(method.args.vararg)
        if method.args.kwarg is not None:
            all_args.append(method.args.kwarg)
        for arg in all_args:
            referenced.extend(_collect_annotation_names(arg.annotation))
        referenced.extend(_collect_annotation_names(method.returns))
    offenders = [
        name for name in referenced if _has_forbidden_prefix(name, FORBIDDEN_SIGNATURE_PREFIXES)
    ]
    assert not offenders, (
        f"{expected_name} signatures reference forbidden adapter types: {sorted(set(offenders))}."
    )


@pytest.mark.parametrize("module", PORT_MODULES)
def test_port_method_parameters_have_no_defaults(module: ModuleType) -> None:
    """Port methods carry no default parameter values — callers pass every arg.

    Pinned by the "Critical Constraints" section of the Story 1.9 spec
    ("No port method has a default parameter value..."). Defaults muddle the
    contract; callers should always pass every argument explicitly.
    """
    expected_name, _ = PORT_CONTRACT[module]
    class_def = _port_class_def(module, expected_name)
    offenders: list[str] = []
    for method in class_def.body:
        if not isinstance(method, ast.AsyncFunctionDef):
            continue
        # ``defaults`` covers positional + positional-or-keyword args (any entry
        # is a real default). ``kw_defaults`` aligns with ``kwonlyargs`` and uses
        # ``None`` placeholders for kwonly args without defaults — only non-None
        # entries are real defaults.
        has_real_kw_default = any(d is not None for d in method.args.kw_defaults)
        if method.args.defaults or has_real_kw_default:
            offenders.append(f"{expected_name}.{method.name}")
    assert not offenders, f"Default parameter values are forbidden on port methods: {offenders}."


# ---------------------------------------------------------------------------
# Tests — `nova.ports` package re-exports
# ---------------------------------------------------------------------------


def test_ports_init_exports_alphabetical_and_complete() -> None:
    """``nova.ports.__all__`` contains every port class and is alphabetically sorted."""
    import nova.ports as ports_package

    expected = {
        "BrainPort",
        "EyesPort",
        "HandsPort",
        "NervePort",
        "RitualPort",
        "ShieldPort",
        "SkinPort",
        "VoicePort",
    }
    actual = set(ports_package.__all__)
    assert actual == expected, (
        f"nova.ports.__all__ drift: expected {sorted(expected)}, got {sorted(actual)}."
    )
    assert list(ports_package.__all__) == sorted(ports_package.__all__), (
        "nova.ports.__all__ must be alphabetically sorted."
    )
    for name in expected:
        assert hasattr(ports_package, name), f"nova.ports package is missing re-export for {name}."


def test_shield_port_is_only_runtime_checkable_port() -> None:
    """``ShieldPort`` is the only port decorated with ``@runtime_checkable`` (Story 1.9 AC #11).

    Locks the design invariant: only ``ShieldPort`` opts into runtime
    ``isinstance()`` introspection (used by the no-op-adapter conformance
    test). Other ports rely on mypy strict for structural checking and
    avoid the ``__instancecheck__`` overhead of ABC registration. A future
    story silently adding ``@runtime_checkable`` to another port — or
    silently removing it from ``ShieldPort`` — would trip this test.
    """
    from nova.ports.brain import BrainPort
    from nova.ports.eyes import EyesPort
    from nova.ports.hands import HandsPort
    from nova.ports.nerve import NervePort
    from nova.ports.ritual import RitualPort
    from nova.ports.shield import ShieldPort
    from nova.ports.skin import SkinPort
    from nova.ports.voice import VoicePort

    expected_runtime_checkable: set[type] = {ShieldPort}
    all_ports: set[type] = {
        BrainPort,
        EyesPort,
        HandsPort,
        NervePort,
        RitualPort,
        ShieldPort,
        SkinPort,
        VoicePort,
    }
    actual_runtime_checkable = {
        port for port in all_ports if getattr(port, "_is_runtime_protocol", False) is True
    }
    assert actual_runtime_checkable == expected_runtime_checkable, (
        f"@runtime_checkable drift: expected only ShieldPort to be runtime-checkable, "
        f"got {sorted(p.__name__ for p in actual_runtime_checkable)}."
    )


# ---------------------------------------------------------------------------
# Tests — domain-model shape (AC #5)
# ---------------------------------------------------------------------------

DOMAIN_MODEL_MODULES: list[ModuleType] = [
    brain_models_module,
    eyes_models_module,
    hands_models_module,
    ritual_models_module,
    skin_models_module,
]


def _iter_module_dataclasses(module: ModuleType) -> list[type]:
    """Return every dataclass declared at module top level (public AND private).

    Walks ``inspect.getmembers`` rather than ``__all__`` so a maintainer who
    adds a private (leading-underscore) dataclass — or simply forgets to
    add a new public one to ``__all__`` — cannot silently bypass the
    frozen / tuple-field shape guards. Filters by ``cls.__module__`` so
    re-imported names from other modules don't double-count.
    """
    out: list[type] = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ != module.__name__:
            continue
        if not dataclasses.is_dataclass(obj):
            continue
        out.append(obj)
    return out


def _annotation_contains_list_origin(annotation: object) -> bool:
    """True if ``annotation`` references ``list`` at any depth (including inside Union/Optional)."""
    origin = typing.get_origin(annotation)
    if origin is list:
        return True
    args = typing.get_args(annotation)
    return any(_annotation_contains_list_origin(arg) for arg in args)


@pytest.mark.parametrize("module", DOMAIN_MODEL_MODULES)
def test_domain_models_are_frozen_dataclasses(module: ModuleType) -> None:
    """Every dataclass in ``systems/*/models.py`` is ``@dataclass(frozen=True)``.

    Walks all module classes (not just ``__all__``) so a private dataclass
    sneaking in unfrozen would still trip the test.
    """
    classes = _iter_module_dataclasses(module)
    assert classes, f"{module.__name__} must declare at least one dataclass."
    for cls in classes:
        params = cls.__dataclass_params__  # type: ignore[attr-defined]
        assert params.frozen is True, f"{cls.__module__}.{cls.__qualname__} must be frozen=True."


@pytest.mark.parametrize("module", DOMAIN_MODEL_MODULES)
def test_domain_models_use_tuple_not_list_for_sequence_fields(module: ModuleType) -> None:
    """Sequence-valued fields are ``tuple[T, ...]`` — never ``list[T]`` at any depth.

    ``frozen=True`` only freezes attribute rebinding; ``list`` fields
    remain mutable via ``instance.field.append(...)``. Story 1.3
    established the tuple-over-list precedent for genuine immutability.

    Walks unions and nested generics: ``list[str] | None``,
    ``tuple[list[str], ...]``, ``Mapping[str, list[int]]`` all fail this
    check. ``typing.get_origin(list[str] | None)`` returns ``UnionType``,
    not ``list`` — the recursive walk catches the inner ``list``.
    """
    classes = _iter_module_dataclasses(module)
    for cls in classes:
        hints = typing.get_type_hints(cls)
        for field_name, annotation in hints.items():
            assert not _annotation_contains_list_origin(annotation), (
                f"{cls.__qualname__}.{field_name} is typed with list[...] (possibly "
                f"nested inside Union/Optional) — use tuple[..., ...] so frozen=True "
                f"covers the container."
            )


def test_mode_info_is_distinct_from_mode_config() -> None:
    """``ModeInfo`` (Brain projection) is distinct from ``ModeConfig`` (Config schema).

    ModeInfo is a Brain-layer projection (``stem`` + ``display_name`` +
    usage metadata) assembled by Nerve's briefing layer (Story 3.2).
    ModeConfig is the full file-backed schema (name + apps + folders +
    URLs) owned by ``core/config.py``. Story 1.9 AC #5 locks the split;
    Story 3.2 extended ModeInfo with ``app_count`` + ``is_default`` and
    split ``name`` into ``stem`` + ``display_name``. The field-shape
    guards below fire if a future refactor collapses the two types.
    """
    info_fields = {f.name for f in dataclasses.fields(ModeInfo)}
    config_fields = {f.name for f in dataclasses.fields(ModeConfig)}
    assert info_fields != config_fields, (
        f"ModeInfo and ModeConfig have identical fields ({info_fields}); the "
        f"two-type split is load-bearing — see Story 1.9 AC #5."
    )
    assert "last_used_at" in info_fields, "ModeInfo must expose last_used_at."
    assert "last_used_at" not in config_fields, (
        "ModeConfig must not carry usage metadata — that is Brain's concern."
    )
    # Non-overlapping-but-adjacent invariant: ModeConfig carries structural
    # app/folder/URL fields ModeInfo does NOT project; assert at least one
    # such differentiating field exists on ModeConfig.
    config_only = config_fields - info_fields
    assert config_only, (
        "ModeConfig must carry at least one field that ModeInfo does not — the "
        "two types must genuinely differ in shape."
    )


# ===========================================================================
# Story 3.6 — SkinPort.render_progress reshape + AppLauncherPort
# ===========================================================================


def test_skin_port_render_progress_takes_single_action_result_not_sequence() -> None:
    """Story 3.6 reshape — ``render_progress`` accepts a single ``ActionResult``.

    The Story 1.9 stub typed the parameter as ``Sequence[ActionResult]``
    on the assumption that Hands would batch the per-app results.
    Story 3.6's epic AC requires per-app inline streaming, so the
    signature is now a single result per call. Use
    :func:`typing.get_type_hints` to resolve the runtime type past
    ``from __future__ import annotations``.
    """
    from nova.ports.skin import SkinPort
    from nova.systems.hands.models import ActionResult

    hints = typing.get_type_hints(SkinPort.render_progress)
    # The "result" parameter must resolve to ActionResult (NOT Sequence[ActionResult]).
    assert "result" in hints, (
        f"SkinPort.render_progress must take a single 'result' parameter; got hints={hints!r}."
    )
    assert hints["result"] is ActionResult, (
        f"SkinPort.render_progress.result must be ActionResult (the Story 3.6 "
        f"reshape from Sequence[ActionResult]); resolved to {hints['result']!r}."
    )
    # Reject the historical 'results' (plural) parameter name as well.
    assert "results" not in hints, (
        "SkinPort.render_progress must NOT carry a 'results' parameter — "
        "Story 3.6 reshape replaced the batch with single-result-per-call."
    )


def test_app_launcher_port_has_single_method_launch_app() -> None:
    """Story 3.6 — ``AppLauncherPort`` exposes exactly one method: ``launch_app``."""
    import nova.ports.app_launcher as app_launcher_module
    from nova.ports.app_launcher import AppLauncherPort

    class_def = _port_class_def(app_launcher_module, "AppLauncherPort")
    method_names = tuple(
        node.name
        for node in class_def.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    assert method_names == ("launch_app",), (
        f"AppLauncherPort method drift: expected ('launch_app',), got {method_names}."
    )

    # Runtime-resolved signature: launch_app(app: AppConfig) -> ActionResult.
    from nova.core.config import AppConfig
    from nova.systems.hands.models import ActionResult

    hints = typing.get_type_hints(AppLauncherPort.launch_app)
    assert hints.get("app") is AppConfig, (
        f"AppLauncherPort.launch_app.app must be AppConfig; resolved to {hints.get('app')!r}."
    )
    assert hints.get("return") is ActionResult, (
        f"AppLauncherPort.launch_app must return ActionResult; resolved to {hints.get('return')!r}."
    )


def test_app_launcher_port_module_exports_canonical_reason_constants() -> None:
    """Story 3.6 — the four canonical reason constants live in the port module.

    Locks the port-as-vocabulary-owner pattern (mirror of
    ``nova.core.audit`` exporting RESULT_SUCCESS / RESULT_FAILED). The
    closed four-member set excludes ``REASON_ALREADY_RUNNING`` because
    already-running maps to ``success=True`` per AC #3 step 2.
    """
    import nova.ports.app_launcher as app_launcher_module

    expected = {
        "REASON_NOT_FOUND",
        "REASON_PERMISSION_DENIED",
        "REASON_TIMED_OUT",
        "REASON_UNKNOWN_ERROR",
    }
    assert expected.issubset(set(app_launcher_module.__all__)), (
        f"nova.ports.app_launcher.__all__ must export the four canonical reason "
        f"constants; missing: {sorted(expected - set(app_launcher_module.__all__))}."
    )
    # Already-running is NOT in the public vocabulary (it returns success=True).
    assert "REASON_ALREADY_RUNNING" not in app_launcher_module.__all__, (
        "REASON_ALREADY_RUNNING must NOT be exported — already-running is a "
        "successful workspace outcome (success=True), not a failure reason."
    )
