# Development

Short reference for the developer tooling contract. For architecture and design,
see [project-context.md](../_bmad-output/project-context.md) and [architecture.md](../_bmad-output/planning-artifacts/architecture.md).

## Minimum `uv` Version

The committed [uv.lock](../uv.lock) uses `revision = 3`, which requires **`uv >= 0.5.11`**
(revision 3 was introduced in the uv 0.5.11 release — see the
[uv CHANGELOG](https://github.com/astral-sh/uv/blob/main/CHANGELOG.md) for details).
Older uv versions reject the lockfile at `uv sync`.

CI pins a specific known-good uv release via `astral-sh/setup-uv@v5` with an explicit
`version:` field. The current pin is in [.github/workflows/ci.yml](../.github/workflows/ci.yml);
bump it in lockstep with your local `uv --version` when upgrading.

## Canonical Full-Verify Command

From [project-context.md:156](../_bmad-output/project-context.md):

```
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest
```

Run this before every commit. The individual commands:

- `uv sync` — install dependencies from the lockfile
- `uv run nova` — launch the app
- `uv run pytest tests/unit/` — unit tests
- `uv run pytest tests/integration/` — integration tests
- `uv run ruff check src/ tests/` — lint
- `uv run ruff format src/ tests/` — format (write) / add `--check` for CI-style verification
- `uv run mypy src/ tests/` — type check (mypy strict on both source and test code)

## CI Parity

The CI workflow in [.github/workflows/ci.yml](../.github/workflows/ci.yml) runs the identical
commands documented above; edits to one must be mirrored in the other. The structural test
at [tests/unit/test_ci_workflow.py](../tests/unit/test_ci_workflow.py) enforces the mirror.

The one deliberate CI-only extension is `pytest tests/unit/ --cov=nova --cov-report=...` — CI
adds coverage instrumentation on the unit-test step only. The structural drift test uses
`startswith` on each canonical command so legitimate extensions like `--cov` pass while
changed or reordered canonical args still fail.
