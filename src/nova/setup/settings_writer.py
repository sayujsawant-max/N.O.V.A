"""Atomic settings.yaml writer for first-run setup.

Pure I/O module — no Console, no Rich, no user-facing messages.
Raises ``OSError`` on any failure; the caller (``run_api_key_step``)
owns the UX message and recovery decision.

Failure translation contract:

- Genuine filesystem errors (permissions, missing dir, disk full,
  cross-device replace) propagate as the underlying ``OSError``.
- ``yaml.YAMLError`` on the existing ``settings.yaml`` (corrupt /
  hand-edited file) is re-raised as ``OSError`` so the single caller
  ``except`` covers it — without this the caller crashed with a raw
  ``ScannerError`` / ``ParserError`` traceback.
- Non-mapping YAML roots (list, scalar, ``None`` when file is not
  empty) are rejected with ``OSError`` — ``data["api_key"] = ...``
  would otherwise raise ``TypeError`` which bypassed the caller.

The API key must never appear in any ``logging`` call or exception
message emitted by this module.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

import yaml


def write_api_key(data_dir: Path, api_key: str) -> None:
    """Write *api_key* into ``settings.yaml`` atomically.

    Reads the existing file, updates only the ``api_key`` field, and
    writes back via a temp-file + :func:`os.replace` swap.  All other
    fields are preserved (values survive; comments are best-effort —
    ``pyyaml`` ``safe_dump`` does not retain YAML comments).

    Raises :class:`OSError` on any failure, including malformed YAML
    input and non-mapping YAML roots (both translated from their
    stdlib exception types to ``OSError`` for a uniform caller
    contract).
    """
    settings_path = data_dir / "settings.yaml"

    with settings_path.open("r", encoding="utf-8") as fh:
        try:
            loaded = yaml.safe_load(fh)
        except yaml.YAMLError as err:
            # Translate to OSError so the caller's `except OSError`
            # covers corrupt/hand-edited settings.yaml.  Message
            # intentionally does not echo file content — only the
            # path, which the OS layer also exposes.
            raise OSError(f"settings.yaml is not valid YAML: {settings_path}") from err

    if loaded is None:
        data: dict[str, object] = {}
    elif isinstance(loaded, dict):
        data = loaded
    else:
        # List / scalar / other — `data["api_key"] = ...` would raise
        # TypeError.  Reject with OSError to match caller contract.
        raise OSError(f"settings.yaml root is not a mapping: {settings_path}")

    data["api_key"] = api_key

    tmp_path = settings_path.with_suffix(".yaml.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(
                data,
                fh,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        os.replace(tmp_path, settings_path)
    except BaseException:
        # Best-effort cleanup of the temp file on any failure
        # (including KeyboardInterrupt).  The original settings.yaml
        # is untouched because os.replace hasn't executed (or failed).
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise
