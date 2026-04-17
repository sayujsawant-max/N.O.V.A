"""Guided mode creation wizard — first-run step after API key configuration.

Two persistence paths are exposed (per story 2.3 AC #3 / #3a):

- **Path A — verbatim copy** (``copy_template_verbatim``): byte-level
  ``shutil.copyfile`` + ``os.replace`` swap. Preserves comments,
  ordering, and formatting from the shipped template. Never overwrites
  an existing target.
- **Path B — schema writer** (``write_mode_file``): atomic temp-file +
  ``os.replace`` with ``yaml.safe_dump``. Used for custom modes and the
  modify-template branch. Comments are not preserved (acknowledged
  trade-off).

The flow layer (``run_mode_wizard_step``) decides which writer to call.
The writers themselves are dumb I/O primitives.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel

logger = logging.getLogger("nova.setup.mode_wizard")


# ---------------------------------------------------------------------------
# App registry — known display-name → canonical-executable map (Task 1)
# ---------------------------------------------------------------------------

# Keys are lowercased display names; lookup is always case-insensitive.
# Values are the canonical executable string written into mode YAML; they
# match the executable-name normalization contract in docs/config-schemas.md
# (case-insensitive, ``.exe`` suffix optional, bare names resolved via PATH).
APP_REGISTRY: dict[str, str] = {
    # IDEs / editors
    "vs code": "code",
    "vscode": "code",
    "visual studio code": "code",
    "code": "code",
    "notepad++": "notepad++",
    "sublime text": "subl",
    "sublime": "subl",
    "pycharm": "pycharm64",
    "intellij": "idea64",
    "intellij idea": "idea64",
    # Browsers
    "chrome": "chrome",
    "google chrome": "chrome",
    "firefox": "firefox",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "brave": "brave",
    # Terminals
    "windows terminal": "wt",
    "terminal": "wt",
    "wt": "wt",
    "powershell": "powershell",
    "cmd": "cmd",
    # Communication
    "discord": "discord",
    "slack": "slack",
    "teams": "teams",
    "microsoft teams": "teams",
    "zoom": "zoom",
    # Notes / knowledge
    "notion": "notion",
    "obsidian": "obsidian",
    "evernote": "evernote",
    "onenote": "onenote",
    # Media / misc
    "spotify": "spotify",
    "postman": "postman",
    "figma": "figma",
}


def resolve_app(display_name: str) -> str | None:
    """Resolve a user-entered app display name to an executable string.

    Two-layer lookup:

    1. ``APP_REGISTRY`` keyed on ``display_name.strip().lower()`` —
       returns the canonical lowercase executable when hit.
    2. ``shutil.which(display_name.strip())`` fallback — returns the
       trimmed user input verbatim when PATH resolves it (the mode
       file records exactly what the user typed so they can tweak it
       later without surprise renames).

    Returns ``None`` when both layers miss, so the caller can surface
    the "couldn't find" message (AC #6).
    """
    trimmed = display_name.strip()
    if not trimmed:
        return None
    hit = APP_REGISTRY.get(trimmed.lower())
    if hit is not None:
        return hit
    # ``shutil.which`` can raise ``OSError`` (PATH entries that fail
    # ``os.stat``) or ``ValueError`` (embedded NUL in the input). Treat
    # any such failure as a resolution miss — never let a malformed
    # PATH / input crash the wizard.
    try:
        resolved = shutil.which(trimmed)
    except (OSError, ValueError):
        logger.debug("shutil.which raised during resolve_app", exc_info=True)
        resolved = None
    if resolved is not None:
        return trimmed
    return None


# ---------------------------------------------------------------------------
# Mode name slugification + stem validation (Task 2)
# ---------------------------------------------------------------------------

# Match core/config.py — the loader's kebab-case stem rule is the contract
# we must produce stems against.
_MODE_STEM_RE: re.Pattern[str] = re.compile(r"[a-z0-9][a-z0-9-]*")

# Reserved Windows filenames (case-insensitive, any extension) — the
# loader also rejects these, so we reject at write-time too.
_RESERVED_WIN_STEMS: frozenset[str] = (
    frozenset({"con", "prn", "aux", "nul"})
    | frozenset(f"com{i}" for i in range(1, 10))
    | frozenset(f"lpt{i}" for i in range(1, 10))
)


def slugify_mode_name(name: str) -> str:
    """Convert a display name to a kebab-case file stem.

    Lowercase, replace runs of whitespace/underscores with a single
    hyphen, drop characters outside ``[a-z0-9-]``, collapse consecutive
    hyphens, strip leading/trailing hyphens.

    May return an empty string for inputs that contain no valid
    characters (e.g. ``"***"``). The caller passes the result to
    :func:`validate_mode_stem` for the empty-and-reserved checks.
    """
    lowered = name.strip().lower()
    # Whitespace/underscore runs → single hyphen
    with_hyphens = re.sub(r"[\s_]+", "-", lowered)
    # Drop anything outside [a-z0-9-]
    cleaned = re.sub(r"[^a-z0-9-]+", "", with_hyphens)
    # Collapse consecutive hyphens and strip edges
    collapsed = re.sub(r"-+", "-", cleaned).strip("-")
    return collapsed


def validate_mode_stem(stem: str) -> str | None:
    """Return an error message if *stem* is invalid, ``None`` if valid.

    The rules match ``core/config.py:_is_valid_mode_stem`` exactly so
    the file the wizard writes will be accepted by the runtime loader.
    """
    if not stem:
        return "Mode name produces an empty identifier. Use letters or digits."
    if stem.lower() in _RESERVED_WIN_STEMS:
        return f"'{stem}' is a reserved Windows filename. Choose a different name."
    if "." in stem:
        return "Mode name cannot contain a period."
    if _MODE_STEM_RE.fullmatch(stem) is None:
        return (
            "Mode name must start with a letter or digit and contain only "
            "letters, digits, and hyphens."
        )
    return None


# ---------------------------------------------------------------------------
# Persistence — Path A (verbatim copy) and Path B (schema writer) (Task 3)
# ---------------------------------------------------------------------------


def copy_template_verbatim(source_yaml: Path, target_yaml: Path) -> None:
    """Copy a shipped template byte-for-byte to the user modes directory.

    Never overwrites an existing target — if ``target_yaml`` exists,
    the function is a no-op. This preserves a file pre-copied by
    ``setup.bat`` (the first-run script already copies shipped
    defaults); running the wizard again must not overwrite user edits.

    Atomicity: writes to a sibling ``.tmp`` file first, then
    :func:`os.replace` swaps it into place.
    """
    if target_yaml.exists():
        return
    tmp_path = target_yaml.with_suffix(target_yaml.suffix + ".tmp")
    try:
        shutil.copyfile(source_yaml, tmp_path)
        os.replace(tmp_path, target_yaml)
    except BaseException:
        # Best-effort cleanup of the temp file on any failure; leave
        # the target untouched because os.replace hasn't swapped (or
        # failed). ``suppress`` not used because ``OSError`` is the
        # only expected failure mode here.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            logger.debug(
                "temp file cleanup failed in copy_template_verbatim",
                exc_info=True,
            )
        raise


def write_mode_file(modes_dir: Path, stem: str, mode_data: dict[str, object]) -> None:
    """Write *mode_data* to ``{modes_dir}/{stem}.yaml`` atomically.

    Used by the custom-mode and modify-template paths. Output conforms
    to the ``ModeConfig`` schema exactly:

    - ``name`` first, then ``apps`` (list), then optional ``folders``,
      ``urls``, ``is_default``.
    - ``yaml.safe_dump(..., sort_keys=False)`` preserves insertion
      order so the generated file is stable and reviewable.

    Raises :class:`OSError` on any filesystem failure; the caller
    (``run_mode_wizard_step``) owns the UX message.
    """
    target = modes_dir / f"{stem}.yaml"
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(
                mode_data,
                fh,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        os.replace(tmp_path, target)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("temp file cleanup failed in write_mode_file", exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Interactive wizard flow (Task 4)
# ---------------------------------------------------------------------------

# Shipped templates live at the repo root under ``config/modes/``. The
# wizard resolves this path relative to the installed package when it
# needs to load templates (when the user data dir has no modes yet).
_REPO_TEMPLATE_RELATIVE: Path = Path("config") / "modes"


def _locate_shipped_templates() -> Path | None:
    """Locate the N.O.V.A. repo's shipped ``config/modes`` directory.

    Walks up from this module's on-disk location looking for a parent
    that contains BOTH ``config/modes/`` AND ``pyproject.toml`` — the
    pairing is the project-root anchor. This prevents the walk from
    picking up an unrelated ``config/modes`` directory in some
    ancestor of the user's machine (e.g., a user profile that happens
    to contain a same-named folder). Returns ``None`` when no
    pyproject-anchored ``config/modes`` is reachable (editable repo
    move, frozen binary, unusual install) — the wizard then falls
    back to "custom only" mode cleanly.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / _REPO_TEMPLATE_RELATIVE
        anchor = parent / "pyproject.toml"
        if candidate.is_dir() and anchor.is_file():
            return candidate
        # Stop climbing once we reach the drive root.
        if parent == parent.parent:
            break
    return None


def _existing_valid_mode_stems(modes_dir: Path) -> list[str]:
    """Return sorted stems of ``*.yaml`` files in *modes_dir* with valid stems.

    Files whose stems would be skipped by the runtime loader
    (``core/config.py:_is_valid_mode_stem``) do not count toward the
    "at least one mode by exit" gate (AC #11).

    Filesystem failures (directory disappeared, permission denied,
    transient I/O error) are absorbed — return ``[]`` and let the
    gate loop re-probe. Raising here would escape the outer
    ``KeyboardInterrupt``/``EOFError`` handler and crash with a
    traceback.
    """
    try:
        if not modes_dir.is_dir():
            return []
        entries = sorted(modes_dir.iterdir(), key=lambda p: p.name)
    except OSError:
        logger.debug("modes_dir scan failed", exc_info=True, extra={"path": str(modes_dir)})
        return []
    stems: list[str] = []
    for entry in entries:
        try:
            if not entry.is_file() or entry.suffix != ".yaml":
                continue
        except OSError:
            continue
        if entry.name.startswith("."):
            continue
        stem = entry.stem
        if validate_mode_stem(stem) is None:
            stems.append(stem)
    return stems


def _load_template(path: Path) -> dict[str, object] | None:
    """Parse a shipped template YAML into a dict for the modify flow.

    Returns ``None`` on any parse / structural failure — the template
    is then skipped from the modify offer. Logs at WARNING; never
    surfaces a traceback to the user.
    """
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        logger.warning("template parse failed — skipped", extra={"path": str(path)})
        return None
    if not isinstance(parsed, dict):
        logger.warning("template root is not a mapping — skipped", extra={"path": str(path)})
        return None
    return parsed


def _ask(console: Console, prompt: str) -> str:
    """Thin wrapper around ``Console.input`` for consistent styling."""
    return console.input(prompt).strip()


def _summary_panel(mode_data: dict[str, object]) -> Panel:
    """Build a Rich Panel summarizing a mode about to be written."""
    lines: list[str] = []
    lines.append(f"[bold]Name:[/bold] {mode_data.get('name', '')}")
    apps = mode_data.get("apps", [])
    if isinstance(apps, list) and apps:
        app_lines: list[str] = []
        for app in apps:
            if isinstance(app, dict):
                name = app.get("name", "")
                exe = app.get("executable", "")
                app_lines.append(f"  - {name} ({exe})")
        lines.append("[bold]Apps:[/bold]\n" + "\n".join(app_lines))
    folders = mode_data.get("folders", [])
    if isinstance(folders, list) and folders:
        lines.append("[bold]Folders:[/bold]\n" + "\n".join(f"  - {f}" for f in folders))
    urls = mode_data.get("urls", [])
    if isinstance(urls, list) and urls:
        lines.append("[bold]URLs:[/bold]\n" + "\n".join(f"  - {u}" for u in urls))
    return Panel(
        "\n".join(lines),
        title="[bold cyan]Mode Summary[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    )


def _collect_apps(
    console: Console,
    initial: list[dict[str, object]] | None = None,
) -> list[dict[str, object]] | None:
    """Prompt the user for apps until they type ``done``.

    *initial* seeds the list with already-known apps (used by the
    modify-template path per AC #3a: template values are presented as
    editable fields). Seeded entries render as ``✓ (from template)``
    lines before the prompt loop begins so the user can see what they
    start with.

    Zero-app hard stop: the loop re-prompts when the user tries to
    finish with no apps entered. Returns ``None`` if the user types
    ``cancel`` — the caller aborts the current mode.
    """
    apps: list[dict[str, object]] = list(initial) if initial else []
    for app in apps:
        name = app.get("name", "")
        executable = app.get("executable", "")
        console.print(f"[green]\u2713[/green] (from template) {name} ({executable})")
    console.print(
        "[bold]Apps[/bold] — enter one per prompt. "
        "Type [bold]done[/bold] when finished, or [bold]cancel[/bold] to abandon this mode."
    )
    while True:
        app_name = _ask(console, "App name: ")
        lowered = app_name.lower()
        if lowered == "cancel":
            return None
        if lowered == "done":
            if not apps:
                console.print(
                    "[red]\u2717[/red] A mode needs at least one app. "
                    "Add one, or type [bold]cancel[/bold] to abandon this mode."
                )
                continue
            return apps
        if not app_name:
            console.print(
                "Type an app name, [bold]done[/bold] to finish, or [bold]cancel[/bold] to abandon."
            )
            continue
        executable = resolve_app(app_name)
        if executable is None:
            console.print(
                f"[yellow]\u26a0[/yellow] Couldn't find '{app_name}' on PATH "
                "or in the known-app list. Saving it anyway — you can "
                "edit the mode file later to fix the executable path."
            )
            executable = app_name
        apps.append({"name": app_name, "executable": executable, "args": []})
        console.print(f"[green]\u2713[/green] Added: {app_name} ({executable})")


def _collect_optional_list(
    console: Console,
    label: str,
    initial: list[str] | None = None,
) -> list[str] | None:
    """Prompt for an optional list (folders / URLs).

    Returns ``None`` if the user types ``cancel`` (the caller aborts
    the current mode — AC #13). Returns the list on ``done``, ``skip``,
    or blank input (AC #15 — all three are equivalent end-of-list
    markers). *initial* seeds the list with existing values for the
    modify-template path.
    """
    entries: list[str] = list(initial) if initial else []
    for item in entries:
        console.print(f"[green]\u2713[/green] (from template) {item}")
    console.print(
        f"[bold]{label}[/bold] (optional) — enter one per line. "
        "Blank line, [bold]skip[/bold], or [bold]done[/bold] to finish; "
        "[bold]cancel[/bold] to abandon this mode."
    )
    while True:
        entry = _ask(console, f"{label[:-1] if label.endswith('s') else label}: ")
        lowered = entry.lower()
        if lowered == "cancel":
            return None
        if lowered in ("", "skip", "done"):
            return entries
        entries.append(entry)


def _confirm(console: Console, prompt: str) -> bool:
    """Yes/no confirmation. Empty input or ``no`` → False; anything starting with 'y' → True."""
    reply = _ask(console, prompt).lower()
    return reply.startswith("y")


def _create_custom_mode(console: Console, modes_dir: Path) -> bool:
    """Walk the user through a custom mode creation. Returns True if a mode was written."""
    console.print(
        Panel(
            "Create a new workspace mode. Answer the questions below, "
            "type [bold]cancel[/bold] at any name prompt to abandon.",
            title="[bold cyan]New Mode[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    while True:
        display_name = _ask(console, "Mode name (e.g. coding, study): ")
        if display_name.lower() == "cancel":
            console.print("Mode creation cancelled.")
            return False
        if not display_name:
            console.print("A mode needs a name. Try again, or type [bold]cancel[/bold].")
            continue
        stem = slugify_mode_name(display_name)
        err = validate_mode_stem(stem)
        if err is not None:
            console.print(f"[red]\u2717[/red] {err}")
            continue
        target = modes_dir / f"{stem}.yaml"
        if target.exists():
            console.print(
                f"[yellow]\u26a0[/yellow] A mode named '{stem}' already exists. "
                "Choose a different name, or type [bold]cancel[/bold]."
            )
            continue
        break

    apps = _collect_apps(console)
    if apps is None:
        console.print("Mode creation cancelled.")
        return False

    folders = _collect_optional_list(console, "Folders")
    if folders is None:
        console.print("Mode creation cancelled.")
        return False
    urls = _collect_optional_list(console, "URLs")
    if urls is None:
        console.print("Mode creation cancelled.")
        return False

    mode_data: dict[str, object] = {
        "name": display_name,
        "apps": apps,
        "folders": folders,
        "urls": urls,
        "is_default": False,
    }
    console.print(_summary_panel(mode_data))

    if not _confirm(console, "Save this mode? [y/n]: "):
        console.print("Mode discarded.")
        return False

    try:
        write_mode_file(modes_dir, stem, mode_data)
    except (OSError, yaml.YAMLError):
        # ``yaml.YAMLError`` is possible if a future ``mode_data`` shape
        # contains a non-dumpable value (tuple under a custom tag,
        # datetime, etc.). Catch it alongside the filesystem errors so
        # the wizard surfaces a clear message instead of a traceback.
        console.print(
            f"[red]\u2717[/red] Could not write mode file to {modes_dir}. "
            "Check file permissions and try again."
        )
        logger.debug("write_mode_file failed", exc_info=True)
        return False

    console.print(f"[green]\u2713[/green] Mode saved: {stem}")
    return True


def _offer_template(
    console: Console,
    template_path: Path,
    template_data: dict[str, object],
    modes_dir: Path,
) -> bool:
    """Present a template and route to Path A / Path B / skip. Returns True iff written."""
    stem = template_path.stem
    target = modes_dir / template_path.name

    # Header panel
    apps_list = template_data.get("apps", [])
    app_display_parts: list[str] = []
    if isinstance(apps_list, list):
        for app in apps_list:
            if isinstance(app, dict):
                name = app.get("name", "")
                if isinstance(name, str):
                    app_display_parts.append(name)
    app_display = ", ".join(app_display_parts) or "(no apps)"
    display_name = template_data.get("name", stem)

    if target.exists():
        console.print(
            f"[green]\u2713[/green] Template '{stem}' is already installed. "
            f"Choose [bold]modify[/bold] to edit, [bold]skip[/bold] to leave it."
        )
        choice_opts = "[modify/skip]: "
        allow_accept = False
    else:
        console.print(
            Panel(
                f"[bold]Name:[/bold] {display_name}\n[bold]Apps:[/bold] {app_display}",
                title=f"[bold cyan]Template: {stem}[/bold cyan]",
                border_style="cyan",
                padding=(1, 2),
            )
        )
        choice_opts = "[accept/modify/skip]: "
        allow_accept = True

    while True:
        reply = _ask(console, f"Use this template? {choice_opts}").lower()
        if reply in ("skip", ""):
            return False
        if reply == "accept" and allow_accept:
            try:
                copy_template_verbatim(template_path, target)
            except OSError:
                console.print(
                    f"[red]\u2717[/red] Could not copy template to {target}. "
                    "Check file permissions and try again."
                )
                logger.debug("copy_template_verbatim failed", exc_info=True)
                return False
            console.print(f"[green]\u2713[/green] Template installed: {stem}")
            return True
        if reply == "modify":
            return _modify_template(console, template_data, stem, modes_dir)
        console.print(f"[red]\u2717[/red] Expected {choice_opts.strip('[]:, ')}.")


def _modify_template(
    console: Console,
    template_data: dict[str, object],
    stem: str,
    modes_dir: Path,
) -> bool:
    """Edit a mode, seeding fields from the user's current file when it exists.

    The seed source matters for correctness (and was a fix in the
    post-review pass):

    - If ``modes/{stem}.yaml`` exists on disk, we load THAT as the
      seed. The user's intent when picking "modify" on an already-
      installed template is "edit my current file", not "start over
      from the shipped defaults and discard my customizations".
    - If the target does not exist (first-time modify of a shipped
      template), we seed from ``template_data``.

    Fields preserved from the source (user's file or template):

    - ``name`` (display name)
    - ``apps``, ``folders``, ``urls`` — presented as editable seed
      entries via ``_collect_apps`` / ``_collect_optional_list``.
    - ``is_default`` — preserved as-is; "modify" never silently
      clears a user's intentional default-mode setting.

    Data-loss guard: when the target already exists, an explicit
    overwrite confirmation runs before the write.

    Type ``cancel`` at any prompt to abort.
    """
    target = modes_dir / f"{stem}.yaml"

    # Decide seed source: user's current file if present, otherwise
    # the shipped template. ``_load_template`` handles read/parse
    # failures by returning None; in that case we WARN the user
    # (silent fall-back would set up a data-loss overwrite without
    # their knowledge) and fall back to the shipped template.
    seed_source: dict[str, object] = template_data
    is_editing_existing = target.exists()
    if is_editing_existing:
        loaded = _load_template(target)
        if loaded is not None:
            seed_source = loaded
        else:
            console.print(
                f"[yellow]\u26a0[/yellow] Your existing mode file at {target} "
                "could not be parsed. Starting from the shipped template values "
                "instead — your current file will be replaced on save."
            )
            logger.debug(
                "existing mode file could not be loaded — seeding modify from shipped template",
                extra={"path": str(target)},
            )

    panel_body = (
        "Modify your current mode file. Existing values are shown below; "
        "add more or type [bold]done[/bold] to keep them. "
        "Type [bold]cancel[/bold] at any prompt to abort."
        if is_editing_existing
        else "Modify the template. Existing values are shown below; "
        "add more or type [bold]done[/bold] to keep them. "
        "Type [bold]cancel[/bold] at any prompt to abort."
    )
    console.print(
        Panel(
            panel_body,
            title=f"[bold cyan]Modify: {stem}[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    default_name = seed_source.get("name", stem)
    default_name_str = default_name if isinstance(default_name, str) else stem
    raw_name = _ask(console, f"Name [{default_name_str}]: ") or default_name_str

    # Seed the editable lists from the chosen source.
    raw_apps = seed_source.get("apps")
    seed_apps: list[dict[str, object]] = []
    if isinstance(raw_apps, list):
        for entry in raw_apps:
            if isinstance(entry, dict):
                seed_apps.append(dict(entry))  # shallow copy per entry
    raw_folders = seed_source.get("folders")
    seed_folders: list[str] = (
        [f for f in raw_folders if isinstance(f, str)] if isinstance(raw_folders, list) else []
    )
    raw_urls = seed_source.get("urls")
    seed_urls: list[str] = (
        [u for u in raw_urls if isinstance(u, str)] if isinstance(raw_urls, list) else []
    )

    # Preserve is_default from the source. Non-bool (including None
    # via absent field) falls back to False, matching the loader's
    # validation behavior in core/config.py.
    raw_is_default = seed_source.get("is_default", False)
    seed_is_default: bool = raw_is_default if isinstance(raw_is_default, bool) else False

    apps = _collect_apps(console, initial=seed_apps)
    if apps is None:
        console.print("Mode modification cancelled.")
        return False

    folders = _collect_optional_list(console, "Folders", initial=seed_folders)
    if folders is None:
        console.print("Mode modification cancelled.")
        return False
    urls = _collect_optional_list(console, "URLs", initial=seed_urls)
    if urls is None:
        console.print("Mode modification cancelled.")
        return False

    mode_data: dict[str, object] = {
        "name": raw_name,
        "apps": apps,
        "folders": folders,
        "urls": urls,
        "is_default": seed_is_default,
    }
    console.print(_summary_panel(mode_data))
    if not _confirm(console, "Save this mode? [y/n]: "):
        console.print("Mode discarded.")
        return False

    # Data-loss guard: refuse to overwrite an existing target without
    # explicit confirmation. This catches the "user accepted the
    # template, hand-edited the file, ran setup.bat again, picked
    # 'modify'" sequence where the schema writer would otherwise
    # silently clobber hand-edits.
    if target.exists():
        console.print(
            f"[yellow]\u26a0[/yellow] A mode file at {target} already exists. "
            "Saving will replace it with your edits (comments will be lost)."
        )
        if not _confirm(console, "Overwrite the existing file? [y/n]: "):
            console.print("Mode modification cancelled.")
            return False

    try:
        write_mode_file(modes_dir, stem, mode_data)
    except (OSError, yaml.YAMLError):
        console.print(
            f"[red]\u2717[/red] Could not write mode file to {modes_dir}. "
            "Check file permissions and try again."
        )
        logger.debug("write_mode_file failed", exc_info=True)
        return False

    console.print(f"[green]\u2713[/green] Mode saved: {stem}")
    return True


def _render_entry_panel(console: Console, existing_stems: list[str]) -> None:
    """Render the opening panel summarizing pre-existing modes."""
    if existing_stems:
        body = (
            "Modes already ready: "
            + ", ".join(f"[bold]{s}[/bold]" for s in existing_stems)
            + "\n\nYou can skip the rest of this step, or add more modes below."
        )
    else:
        body = (
            "No workspace modes are configured yet.\nAccept a starter template, or create your own."
        )
    console.print(
        Panel(
            body,
            title="[bold cyan]Mode Setup[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )


def run_mode_wizard_step(console: Console, data_dir: Path) -> None:
    """Orchestrate the mode-creation step.

    Flow:
      1. Probe ``{data_dir}/modes/`` for pre-existing valid stems.
      2. Present each shipped template (accept / modify / skip).
      3. Offer custom mode creation in a loop.
      4. Enforce the "at least one valid mode by exit" gate.

    The function exits on:
      - non-TTY stdin (skip with notice),
      - user types ``skip`` at the create-another prompt and at least
        one valid mode exists in ``{data_dir}/modes/``,
      - Ctrl+C / EOF (treat as skip).

    Returns ``None`` unconditionally; the step never affects the main
    entrypoint's exit code.
    """
    if not sys.stdin.isatty():
        console.print(
            "[yellow]\u26a0[/yellow] Not running in an interactive terminal. "
            "Skipping mode setup. Edit modes/*.yaml in your data directory later."
        )
        return

    modes_dir = data_dir / "modes"
    try:
        modes_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        console.print(
            f"[red]\u2717[/red] Could not create modes directory at {modes_dir}. "
            "Check file permissions and re-run setup."
        )
        logger.debug("modes dir creation failed", exc_info=True)
        return

    existing_stems = _existing_valid_mode_stems(modes_dir)
    _render_entry_panel(console, existing_stems)

    try:
        _offer_all_templates(console, modes_dir)
        _offer_custom_modes_until_done(console, modes_dir)
        _enforce_minimum_mode_gate(console, modes_dir)
    except (KeyboardInterrupt, EOFError):
        console.print()
        console.print(
            "[yellow]\u26a0[/yellow] Mode setup interrupted. "
            "Edit modes/*.yaml in your data directory later if needed."
        )
        return


def _offer_all_templates(console: Console, modes_dir: Path) -> None:
    """Iterate shipped templates and offer each one.

    Templates whose stem would be rejected by ``validate_mode_stem``
    (same contract as the runtime loader's ``_is_valid_mode_stem``)
    are skipped with a debug log — offering them would produce a file
    the loader silently drops, so the user sees a "success" message
    but no mode. Symmetric with ``_existing_valid_mode_stems``.
    """
    template_root = _locate_shipped_templates()
    if template_root is None:
        logger.debug("no shipped templates directory located; skipping template offer")
        return
    for template_path in sorted(template_root.glob("*.yaml"), key=lambda p: p.name):
        stem_error = validate_mode_stem(template_path.stem)
        if stem_error is not None:
            logger.debug(
                "shipped template has invalid stem — skipped",
                extra={"stem": template_path.stem, "reason": stem_error},
            )
            continue
        template_data = _load_template(template_path)
        if template_data is None:
            continue
        _offer_template(console, template_path, template_data, modes_dir)


def _offer_custom_modes_until_done(console: Console, modes_dir: Path) -> None:
    """Ask the user whether to create custom modes, then loop."""
    while True:
        reply = _ask(console, "Create a custom mode? [y/n]: ").lower()
        if not reply.startswith("y"):
            return
        _create_custom_mode(console, modes_dir)


def _enforce_minimum_mode_gate(console: Console, modes_dir: Path) -> None:
    """Block exit until ``modes_dir`` contains at least one valid mode.

    AC #11 is explicit: the wizard must not exit cleanly with zero
    modes configured. There is no ``decline and exit with warning``
    path. Each iteration prints the requirement and re-enters custom
    mode creation; cancelling inside the create flow returns here and
    we re-probe. The only escape is :class:`KeyboardInterrupt` / EOF,
    handled by the outer try/except in :func:`run_mode_wizard_step`.
    """
    while not _existing_valid_mode_stems(modes_dir):
        console.print(
            "[red]\u2717[/red] At least one workspace mode is required before setup "
            "completes. Create one now, or press Ctrl+C to exit setup and "
            "re-run [bold]setup.bat[/bold] later."
        )
        _create_custom_mode(console, modes_dir)
