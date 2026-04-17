"""Unit tests for mode wizard persistence paths (Story 2.3 Task 3 / AC #3, #3a, #8, #9, #22).

Two distinct paths:

- **Path A — verbatim copy** (``copy_template_verbatim``): byte-level
  copy; never overwrites; atomic via temp-file + ``os.replace``.
- **Path B — schema writer** (``write_mode_file``): ``yaml.safe_dump``
  with deterministic ordering; atomic; round-trips through the runtime
  loader into the same ``ModeConfig``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from nova.core.config import AppConfig, ModeConfig, load_config
from nova.setup.mode_wizard import copy_template_verbatim, write_mode_file

# ---------------------------------------------------------------------------
# Path A — copy_template_verbatim
# ---------------------------------------------------------------------------


class TestCopyTemplateVerbatim:
    """Byte-level preservation, no overwrite, atomic swap."""

    def test_byte_identical_copy(self, tmp_path: Path) -> None:
        source = tmp_path / "src.yaml"
        source_bytes = (
            b"# leading comment preserved\n"
            b"name: coding\n"
            b"apps:\n"
            b"  - name: VS Code      # inline comment\n"
            b"    executable: code\n"
        )
        source.write_bytes(source_bytes)
        target = tmp_path / "modes" / "coding.yaml"
        target.parent.mkdir()

        copy_template_verbatim(source, target)

        assert target.read_bytes() == source_bytes

    def test_no_overwrite_when_target_exists(self, tmp_path: Path) -> None:
        source = tmp_path / "src.yaml"
        source.write_bytes(b"name: template-version\napps:\n  - name: X\n    executable: x\n")
        target = tmp_path / "coding.yaml"
        preexisting = b"name: user-edited\napps:\n  - name: Y\n    executable: y\n"
        target.write_bytes(preexisting)

        copy_template_verbatim(source, target)

        # No-op; user's file untouched
        assert target.read_bytes() == preexisting

    def test_cleans_up_tmp_on_copyfile_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "src.yaml"
        source.write_bytes(b"name: x\napps:\n  - name: x\n    executable: x\n")
        target = tmp_path / "coding.yaml"

        def _fail(*_args: object, **_kwargs: object) -> None:
            raise OSError("mock copyfile failure")

        monkeypatch.setattr("nova.setup.mode_wizard.shutil.copyfile", _fail)

        with pytest.raises(OSError, match="mock copyfile failure"):
            copy_template_verbatim(source, target)

        # No target written, no orphan .tmp
        assert not target.exists()
        assert not (tmp_path / "coding.yaml.tmp").exists()

    def test_cleans_up_tmp_on_replace_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "src.yaml"
        source.write_bytes(b"name: x\napps:\n  - name: x\n    executable: x\n")
        target = tmp_path / "coding.yaml"

        def _fail(*_args: object, **_kwargs: object) -> None:
            raise OSError("mock replace failure")

        monkeypatch.setattr("nova.setup.mode_wizard.os.replace", _fail)

        with pytest.raises(OSError, match="mock replace failure"):
            copy_template_verbatim(source, target)

        # Target never swapped in; temp cleaned up
        assert not target.exists()
        assert not (tmp_path / "coding.yaml.tmp").exists()


# ---------------------------------------------------------------------------
# Path B — write_mode_file
# ---------------------------------------------------------------------------


class TestWriteModeFile:
    """Schema writer produces loader-compatible output."""

    def test_round_trip_through_loader(self, tmp_path: Path) -> None:
        """The golden test: write → load_config → the ModeConfig we expected."""
        data_dir = tmp_path
        modes_dir = data_dir / "modes"
        modes_dir.mkdir()

        mode_data: dict[str, object] = {
            "name": "coding",
            "apps": [
                {"name": "VS Code", "executable": "code", "args": []},
                {"name": "Chrome", "executable": "chrome", "args": ["--new-window"]},
            ],
            "folders": [],
            "urls": [],
            "is_default": False,
        }
        write_mode_file(modes_dir, "coding", mode_data)

        cfg = load_config(data_dir)
        assert "coding" in cfg.modes
        mode = cfg.modes["coding"]
        assert isinstance(mode, ModeConfig)
        assert mode.name == "coding"
        assert mode.apps == (
            AppConfig(name="VS Code", executable="code", args=()),
            AppConfig(
                name="Chrome",
                executable="chrome",
                args=("--new-window",),
            ),
        )
        assert mode.folders == ()
        assert mode.urls == ()
        assert mode.is_default is False

    def test_field_order_deterministic(self, tmp_path: Path) -> None:
        """sort_keys=False preserves insertion order — important for readable diffs."""
        modes_dir = tmp_path / "modes"
        modes_dir.mkdir()
        mode_data: dict[str, object] = {
            "name": "coding",
            "apps": [{"name": "VS Code", "executable": "code", "args": []}],
            "folders": [],
            "urls": [],
            "is_default": False,
        }
        write_mode_file(modes_dir, "coding", mode_data)

        text = (modes_dir / "coding.yaml").read_text(encoding="utf-8")
        name_pos = text.index("name:")
        apps_pos = text.index("apps:")
        folders_pos = text.index("folders:")
        urls_pos = text.index("urls:")
        is_default_pos = text.index("is_default:")
        assert name_pos < apps_pos < folders_pos < urls_pos < is_default_pos

    def test_atomic_write_no_partial_on_replace_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        modes_dir = tmp_path / "modes"
        modes_dir.mkdir()
        # Pre-existing file content should survive a failed replace
        target = modes_dir / "coding.yaml"
        original = b"name: original\napps:\n  - name: x\n    executable: x\n"
        target.write_bytes(original)

        def _fail(*_args: object, **_kwargs: object) -> None:
            raise OSError("mock replace failure")

        monkeypatch.setattr("nova.setup.mode_wizard.os.replace", _fail)

        mode_data: dict[str, object] = {
            "name": "new",
            "apps": [{"name": "A", "executable": "a", "args": []}],
            "folders": [],
            "urls": [],
            "is_default": False,
        }
        with pytest.raises(OSError, match="mock replace failure"):
            write_mode_file(modes_dir, "coding", mode_data)

        # Original intact; temp cleaned up
        assert target.read_bytes() == original
        assert not (modes_dir / "coding.yaml.tmp").exists()

    def test_overwrites_existing_target(self, tmp_path: Path) -> None:
        """Path B overwrites the target on purpose (modify-template case)."""
        modes_dir = tmp_path / "modes"
        modes_dir.mkdir()
        target = modes_dir / "coding.yaml"
        target.write_text("name: old\napps: []\n", encoding="utf-8")

        mode_data: dict[str, object] = {
            "name": "new",
            "apps": [{"name": "A", "executable": "a", "args": []}],
            "folders": [],
            "urls": [],
            "is_default": False,
        }
        write_mode_file(modes_dir, "coding", mode_data)

        result = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert result["name"] == "new"

    def test_utf8_safe(self, tmp_path: Path) -> None:
        """allow_unicode=True keeps non-ASCII names readable rather than escaped."""
        modes_dir = tmp_path / "modes"
        modes_dir.mkdir()
        mode_data: dict[str, object] = {
            "name": "café study",
            "apps": [{"name": "Café app", "executable": "cafe", "args": []}],
            "folders": [],
            "urls": [],
            "is_default": False,
        }
        write_mode_file(modes_dir, "cafe-study", mode_data)

        text = (modes_dir / "cafe-study.yaml").read_text(encoding="utf-8")
        assert "café" in text  # not escaped as \u00e9
