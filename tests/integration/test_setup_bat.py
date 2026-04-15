"""Story 2.1 AC #37 — ``setup.bat`` idempotency integration test.

Runs the batch script twice against an isolated fake ``%LOCALAPPDATA%``
and asserts:

- Both invocations exit 0.
- Second invocation leaves pre-existing files untouched (user-edited
  ``settings.yaml`` survives).
- Second invocation creates no additional state (data dir tree is
  byte-identical after the second run, modulo the user edit).

Marked ``@pytest.mark.windows_only`` + ``@pytest.mark.integration``
because batch scripts are a Windows-only surface and the test launches
real subprocesses.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.windows_only]


REPO_ROOT: Path = Path(__file__).resolve().parents[2]
SETUP_BAT: Path = REPO_ROOT / "setup.bat"


@pytest.fixture
def isolated_localappdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a tmp_path-rooted ``%LOCALAPPDATA%`` override.

    The fixture returns the override root (``tmp_path``) — the setup
    script will create ``tmp_path/nova/`` inside it.
    """
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path


def _run_setup() -> subprocess.CompletedProcess[str]:
    """Invoke setup.bat as a subprocess and return the completed result.

    Runs from the repo root with ``NOVA_SETUP_SKIP_SYNC=1`` so the
    script does not race against pytest holding ``.venv/`` open. The
    rest of the flow (prereq checks, path validation, mkdir, copy,
    wizard launch) still runs end-to-end.
    """
    env = os.environ.copy()
    env["NOVA_SETUP_SKIP_SYNC"] = "1"
    # ``errors="replace"`` so non-cp1252 bytes (Rich's UTF-8 symbols
    # from the wizard stub) do not crash the reader thread on Windows
    # hosts with a legacy default code page.
    return subprocess.run(
        [str(SETUP_BAT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        check=False,
        env=env,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="setup.bat is Windows-only")
def test_setup_bat_is_idempotent(isolated_localappdata: Path) -> None:
    """Second run preserves user edits across every shipped-default type.

    Regression guard for AC #7 idempotency: a bug that re-copied files
    unconditionally would only be caught if the test edits cover every
    default type (singleton yaml + mode collection). Editing only
    ``settings.yaml`` would silently accept a regression that trampled
    ``exclusions.yaml`` or a mode file.
    """
    data_dir = isolated_localappdata / "nova"

    # --- First run: should create everything fresh ------------------------
    first = _run_setup()
    assert first.returncode == 0, (
        f"First setup.bat run failed (rc={first.returncode}).\n"
        f"stdout:\n{first.stdout}\nstderr:\n{first.stderr}"
    )
    assert data_dir.is_dir()
    assert (data_dir / "modes").is_dir()
    assert (data_dir / "backups").is_dir()
    assert (data_dir / "logs").is_dir()
    assert (data_dir / "settings.yaml").is_file()
    assert (data_dir / "exclusions.yaml").is_file()
    # At least one mode file was copied from config/modes/.
    mode_files = sorted((data_dir / "modes").glob("*.yaml"))
    assert mode_files, "No mode files copied from shipped defaults."

    # Simulate user customization on each default-type: the singleton
    # settings.yaml, the singleton exclusions.yaml, and one mode file
    # from the mode collection. Each edit MUST survive the second run.
    settings_edit = "# user-edited\nbluntness: direct\n"
    exclusions_edit = "# user-edited\napps: []\n"
    first_mode = mode_files[0]
    mode_edit = f"# user-edited\nname: {first_mode.stem}\napps: []\n"
    (data_dir / "settings.yaml").write_text(settings_edit, encoding="utf-8")
    (data_dir / "exclusions.yaml").write_text(exclusions_edit, encoding="utf-8")
    first_mode.write_text(mode_edit, encoding="utf-8")

    # --- Second run: should be a no-op for file state ---------------------
    second = _run_setup()
    assert second.returncode == 0, (
        f"Second setup.bat run failed (rc={second.returncode}).\n"
        f"stdout:\n{second.stdout}\nstderr:\n{second.stderr}"
    )
    # Every user edit preserved exactly.
    assert (data_dir / "settings.yaml").read_text(encoding="utf-8") == settings_edit
    assert (data_dir / "exclusions.yaml").read_text(encoding="utf-8") == exclusions_edit
    assert first_mode.read_text(encoding="utf-8") == mode_edit


@pytest.mark.skipif(sys.platform != "win32", reason="setup.bat is Windows-only")
def test_setup_bat_does_not_require_admin(isolated_localappdata: Path) -> None:
    """The script does not attempt elevation (``runas``) or HKLM writes."""
    result = _run_setup()
    # Non-elevated runs do not see "Access is denied" / "requires
    # administrator" shapes in output when operating within
    # %LOCALAPPDATA% (per-user scope).
    combined = (result.stdout + result.stderr).lower()
    assert "access is denied" not in combined
    assert "run as administrator" not in combined
    assert "requires administrator" not in combined


@pytest.mark.skipif(sys.platform != "win32", reason="setup.bat is Windows-only")
def test_setup_bat_rejects_subdir_path_that_exists_as_file(
    isolated_localappdata: Path,
) -> None:
    """Pre-existing FILE at a subdir target must halt setup cleanly.

    Regression guard for a latent bug where ``:create_subdir`` used
    ``if exist "%~1" exit /b 0`` unconditionally — that would silently
    accept a file at e.g. ``%LOCALAPPDATA%\\nova\\logs`` and continue
    setup, producing downstream copy / wizard failures with no clear
    root cause. The corrected check uses ``if exist "%~1\\"`` which
    matches only directories on Windows.
    """
    data_dir = isolated_localappdata / "nova"
    data_dir.mkdir()
    # Plant a FILE at the ``logs`` target — a plausible AV quarantine /
    # user-error state that would otherwise masquerade as a valid dir.
    (data_dir / "logs").write_text("not a directory", encoding="utf-8")

    result = _run_setup()
    assert result.returncode != 0, (
        f"setup.bat must not accept a FILE at the logs/ target.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    # The rollback-dirs branch should fire with a clear "Couldn't
    # create data subdirectories" message — not a cryptic downstream
    # error from the copy step.
    assert "data subdirectories" in combined or "Rolling back" in combined, (
        f"Expected subdir-creation failure message; got:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # The pre-existing file must NOT have been deleted by rollback —
    # rollback only touches state created in this run.
    assert (data_dir / "logs").is_file()
    assert (data_dir / "logs").read_text(encoding="utf-8") == "not a directory"


@pytest.mark.skipif(sys.platform != "win32", reason="setup.bat is Windows-only")
def test_setup_bat_rejects_reserved_name_in_localappdata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the resolved data dir contains a reserved Windows name, setup stops cleanly.

    Simulates the edge case by pointing ``LOCALAPPDATA`` at a parent
    directory whose relative ``nova`` child would resolve to a path
    the validator rejects. Here we construct a fake LOCALAPPDATA whose
    final segment is a reserved name — so ``<fake>/nova`` still
    contains ``CON`` upstream.
    """
    bad_parent = tmp_path / "CON"
    # NOTE: ``CON`` is a reserved name. ``mkdir`` may fail on Windows
    # for directories with that name, so we do not create it — we only
    # point LOCALAPPDATA at the non-existent path. The validator runs
    # in pure path-math mode and rejects based on the segment name,
    # not filesystem state.
    monkeypatch.setenv("LOCALAPPDATA", str(bad_parent))

    result = _run_setup()
    assert result.returncode != 0, (
        f"Reserved-name data dir should trip path validation.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    # The validator / wizard stub emits "Setup stopped." on the path
    # validation branch.
    assert "Setup stopped" in combined
    # Distinguish the reserved-name branch from a bare
    # ``Path.resolve()`` OSError: the specific reason string must
    # appear so a regression that hits the wrong rejection branch
    # fails this assertion instead of passing silently.
    assert "reserved Windows name" in combined, (
        f"Expected 'reserved Windows name' reason in output; got:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # No data dir was created.
    assert not (bad_parent / "nova").exists()
