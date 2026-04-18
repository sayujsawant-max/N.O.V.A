# N.O.V.A.

Local-first Windows 11 workspace companion — preserves the continuity of your
work across sessions without sending your workflow to the cloud.

## Quick start

Clone the repository and run `setup.bat` from the repository root. The script
validates prerequisites, creates `%LOCALAPPDATA%/nova/`, and launches the
first-run wizard that configures your API key and initial workspace mode.

## API key management

N.O.V.A. uses your Anthropic API key for cloud reasoning in the FULL tier.

- First-run setup prompts for the key and writes it to
  `%LOCALAPPDATA%/nova/settings.yaml`.
- To update the key (expired, revoked, rotated):
  1. Open `%LOCALAPPDATA%/nova/settings.yaml` in any text editor.
  2. Change the `api_key:` value.
  3. Save and re-run `nova`. The next start reads the new value.
- To remove the key (operate locally only), delete or comment out the
  `api_key:` line. N.O.V.A. will start in the offline-local-only tier
  with a one-time notice. Local features (modes, memory, transparency)
  continue to work.
- A present but invalid key does NOT crash bootstrap — `nova` starts
  normally and the error surfaces only when cloud reasoning is actually
  requested. Automatic tier degradation from invalid-key signals will
  arrive in a later release.

## Further documentation

- [`docs/config-schemas.md`](docs/config-schemas.md) — pinned YAML schemas
  (settings, modes, exclusions).
- [`docs/cross-cutting-patterns.md`](docs/cross-cutting-patterns.md) —
  operational patterns every story consults.
- [`docs/development.md`](docs/development.md) — local development setup.
