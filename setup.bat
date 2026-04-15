@echo off
rem ===========================================================================
rem N.O.V.A. — First-run setup for Windows 11.
rem
rem  - Checks prerequisites: Windows 11, Python 3.12+, uv >= 0.5.11.
rem  - Runs ``uv sync`` to install project dependencies.
rem  - Validates the user data directory via ``python -m nova.setup
rem    --validate-only`` BEFORE any ``mkdir`` so bad paths never produce
rem    partial state.
rem  - Creates ``%LOCALAPPDATA%\nova\`` with subdirs ``modes/``, ``backups/``,
rem    ``logs/`` (atomic — rolls back partial state on failure).
rem  - Copies shipped defaults (``config\*``) only when the target file is
rem    missing (idempotent; never overwrites user customizations). Any copy
rem    failure after a successful copy in this run rolls back ONLY the files
rem    written by this run — never pre-existing user state.
rem  - Launches the first-run wizard via ``uv run python -m nova.setup``.
rem
rem Exit codes:
rem   0   success
rem   1   configuration / validation failure (clear next-action message)
rem
rem Dual-shell: works from cmd.exe and PowerShell.
rem No admin required.
rem ===========================================================================

setlocal enabledelayedexpansion

rem Capture the current console code page so it can be restored on exit.
rem ``chcp`` is a global console setting (not env-scoped) — ``endlocal``
rem does NOT undo our switch to UTF-8, so we restore explicitly.
rem ``chcp`` prints "Active code page: NNN" (or a localized equivalent);
rem token 2 with ``:`` delim isolates the number.
set "ORIG_CP="
for /f "tokens=2 delims=:" %%C in ('chcp') do set "ORIG_CP=%%C"
rem Strip leading / trailing spaces from the captured value.
if defined ORIG_CP set "ORIG_CP=!ORIG_CP: =!"

rem Force UTF-8 code page so ✓/✗/⚠ render on modern Windows terminals.
rem Errors are suppressed — this is a best-effort nicety, not load-bearing.
chcp 65001 >nul 2>&1

rem Resolve repo root as this script's directory.
set "REPO_ROOT=%~dp0"
if "%REPO_ROOT:~-1%"=="\" set "REPO_ROOT=%REPO_ROOT:~0,-1%"

rem Tracking list for copied-files rollback (AC #7). Each successful copy
rem appends to this list; on failure we replay it in reverse.
set "ROLLBACK_LIST=%TEMP%\nova-setup-rollback-%RANDOM%.txt"
type nul > "%ROLLBACK_LIST%" 2>nul

echo Checking prerequisites...

rem --- Prereq 1: Windows 11 ------------------------------------------------
rem Windows 11 reports build 22000 or higher. ``ver`` returns
rem "Microsoft Windows [Version 10.0.<build>.<revision>]"; splitting on
rem space + dot + close-bracket gives tokens:
rem   1=Microsoft  2=Windows  3=[Version  4=10  5=0  6=<build>  7=<revision>
for /f "tokens=6 delims=]. " %%B in ('ver') do set "WIN_BUILD=%%B"
if not defined WIN_BUILD goto :fail_windows_version
rem Reject a non-numeric WIN_BUILD before the LSS compare. Locale
rem variations or malformed ``ver`` output can leave the token with
rem trailing non-digit chars (e.g. ``22631]``); ``if ... LSS ...``
rem on a non-numeric value crashes cmd with "LSS was unexpected at
rem this time." This guard iterates using non-digits as delimiters:
rem if any non-digit char is present, the iteration runs and we
rem fail fast with the standard message.
for /f "delims=0123456789" %%X in ("!WIN_BUILD!") do goto :fail_windows_version
if %WIN_BUILD% LSS 22000 goto :fail_windows_version
echo [92m✓[0m Windows 11 ^(build %WIN_BUILD%^)

rem --- Prereq 2: Python 3.12+ ----------------------------------------------
rem Prefer the ``py`` launcher with the latest installed Python 3 (``py
rem -3``) — this accepts 3.12, 3.13, 3.14, etc. per the AC's "3.12+"
rem scope. Fall back to ``python`` on the PATH if the launcher is
rem unavailable or reports a version below 3.12.
set "PY_CMD="
where py >nul 2>&1
if not errorlevel 1 (
    call :check_py_version "py -3" && set "PY_CMD=py -3"
)
if not defined PY_CMD (
    where python >nul 2>&1
    if errorlevel 1 goto :fail_python_missing
    call :check_py_version "python" || goto :fail_python_version
    set "PY_CMD=python"
)
echo [92m✓[0m Python 3.12+

rem --- Prereq 3: uv >= 0.5.11 ----------------------------------------------
where uv >nul 2>&1
if errorlevel 1 (
    echo [93m⚠[0m uv not found. Installing via official installer...
    rem Route the installer's stdout/stderr to a log file so a raw
    rem PowerShell / Invoke-RestMethod exception does not surface
    rem after the friendly :fail_uv_install message (per AC #10 —
    rem no raw technical diagnostics on the user-visible surface).
    rem Operators can inspect the log manually if install fails.
    set "UV_INSTALL_LOG=%TEMP%\nova-uv-install-%RANDOM%.log"
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "irm https://astral.sh/uv/install.ps1 | iex" > "!UV_INSTALL_LOG!" 2>&1 || goto :fail_uv_install
    rem On success, drop the log to keep %TEMP% clean.
    del "!UV_INSTALL_LOG!" >nul 2>&1
    rem The installer updates the user's PATH for future sessions only;
    rem prepend the expected install location so this session sees uv.
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
    where uv >nul 2>&1
    if errorlevel 1 goto :fail_uv_install
)

rem Extract uv version and compare to 0.5.11 minimum.
for /f "tokens=2" %%V in ('uv --version 2^>^&1') do set "UV_VERSION=%%V"
if not defined UV_VERSION goto :fail_uv_install
for /f "tokens=1,2,3 delims=." %%a in ("!UV_VERSION!") do (
    set "UV_MAJOR=%%a"
    set "UV_MINOR=%%b"
    set "UV_PATCH=%%c"
)
rem Minimum is 0.5.11: reject 0.4.*, 0.5.0–0.5.10, and any parsing failure.
if !UV_MAJOR! GTR 0 goto :uv_version_ok
if !UV_MINOR! GTR 5 goto :uv_version_ok
if !UV_MINOR! LSS 5 goto :fail_uv_version
if !UV_PATCH! LSS 11 goto :fail_uv_version
:uv_version_ok
echo [92m✓[0m uv %UV_VERSION%

rem --- Dependency install: uv sync ------------------------------------------
rem ``NOVA_SETUP_SKIP_SYNC=1`` bypasses ``uv sync``. Used by integration
rem tests that already have a synced venv and would otherwise race
rem against pytest holding the .venv open. Not for end users.
if "%NOVA_SETUP_SKIP_SYNC%"=="1" (
    echo [93m⚠[0m Skipping uv sync ^(NOVA_SETUP_SKIP_SYNC=1^)
) else (
    echo Running uv sync...
    pushd "%REPO_ROOT%"
    uv sync
    set "UV_SYNC_RESULT=!errorlevel!"
    popd
    if not !UV_SYNC_RESULT!==0 goto :fail_uv_sync
    echo [92m✓[0m Dependencies installed
)

rem --- Resolve target data directory ----------------------------------------
if not defined LOCALAPPDATA goto :fail_localappdata_missing
set "DATA_DIR=%LOCALAPPDATA%\nova"

rem --- Validate data directory path BEFORE any mkdir (AC #5) ----------------
echo Validating data directory path...
pushd "%REPO_ROOT%"
uv run python -m nova.setup --validate-only "%DATA_DIR%"
set "VALIDATE_RESULT=!errorlevel!"
popd
if not !VALIDATE_RESULT!==0 goto :fail_path_validation
echo [92m✓[0m Path valid

rem --- Create data directory subtree atomically (AC #6) ---------------------
rem Strategy: track which top-level directories we created in this run.
rem On any subdir failure, remove only those we created — never touch
rem pre-existing user state.
set "CREATED_ROOT=0"
if not exist "%DATA_DIR%" (
    mkdir "%DATA_DIR%" || goto :fail_mkdir_root
    set "CREATED_ROOT=1"
)
call :create_subdir "%DATA_DIR%\modes"    || goto :rollback_dirs
call :create_subdir "%DATA_DIR%\backups"  || goto :rollback_dirs
call :create_subdir "%DATA_DIR%\logs"     || goto :rollback_dirs
echo [92m✓[0m %DATA_DIR%\ ready

rem --- Copy shipped defaults (AC #7) ---------------------------------------
echo Copying shipped defaults...
call :copy_if_missing "%REPO_ROOT%\config\exclusions.yaml" ^
                     "%DATA_DIR%\exclusions.yaml"        || goto :rollback_copies
call :copy_if_missing "%REPO_ROOT%\config\settings.defaults.yaml" ^
                     "%DATA_DIR%\settings.yaml"         || goto :rollback_copies

rem Copy every mode file under config\modes\ that isn't already present.
for %%F in ("%REPO_ROOT%\config\modes\*.yaml") do (
    call :copy_if_missing "%%F" "%DATA_DIR%\modes\%%~nxF" || goto :rollback_copies
)
echo [92m✓[0m Defaults ready

rem --- Clean up rollback list (successful run) ------------------------------
if exist "%ROLLBACK_LIST%" del "%ROLLBACK_LIST%" >nul 2>&1

rem --- Launch the first-run wizard (AC #12) ---------------------------------
echo Launching first-run setup...
pushd "%REPO_ROOT%"
uv run python -m nova.setup
set "WIZARD_RESULT=!errorlevel!"
popd
if defined ORIG_CP chcp !ORIG_CP! >nul 2>&1
endlocal & exit /b %WIZARD_RESULT%


rem ===========================================================================
rem Helpers
rem ===========================================================================

:create_subdir
rem %~1 = target directory. Creates if absent; tracks for rollback.
rem ``if exist "path\"`` (trailing backslash) matches ONLY directories
rem on Windows, so a pre-existing FILE at the same name is correctly
rem rejected instead of being silently accepted as "already exists."
if exist "%~1\" exit /b 0
rem If the path exists but is NOT a directory, fail out — calling code
rem is about to copy defaults into a subdir that isn't actually a dir,
rem which would produce downstream errors with no clear root cause.
if exist "%~1" exit /b 1
mkdir "%~1" 2>nul
if errorlevel 1 exit /b 1
rem Record for rollback (only directories we created in this run).
call :track_rollback DIR "%~1"
exit /b 0


:copy_if_missing
rem %~1 = source file, %~2 = destination file.
rem Copies only when destination is absent; tracks destination for rollback.
if exist "%~2" exit /b 0
copy /y "%~1" "%~2" >nul 2>&1
if errorlevel 1 exit /b 1
call :track_rollback FILE "%~2"
exit /b 0


:track_rollback
rem Append a rollback entry with delayed expansion DISABLED — otherwise a
rem literal ``!`` in the path (legal Windows filename char) gets treated
rem as a variable marker, silently truncating the recorded entry and
rem leaving partial state on disk if rollback later fires. Args:
rem   %~1 = tag ("DIR" or "FILE")
rem   %~2 = path
setlocal disabledelayedexpansion
echo %~1::%~2>> "%ROLLBACK_LIST%"
endlocal
exit /b 0


:check_py_version
rem %~1 = command to invoke (e.g. "py -3" or "python"). Returns 0 if
rem ``<cmd> --version`` reports Python 3.12 or newer, 1 otherwise.
rem Propagates no output — callers gate on the exit code and emit the
rem user-facing message themselves.
set "CHECK_PY_VERSION="
for /f "tokens=2" %%V in ('%~1 --version 2^>^&1') do set "CHECK_PY_VERSION=%%V"
if not defined CHECK_PY_VERSION exit /b 1
for /f "tokens=1,2 delims=." %%a in ("!CHECK_PY_VERSION!") do (
    set "CHECK_PY_MAJOR=%%a"
    set "CHECK_PY_MINOR=%%b"
)
if not "!CHECK_PY_MAJOR!"=="3" exit /b 1
rem Numeric guard before LSS compare — Windows Store shim prints a
rem redirect message rather than a version, which would leave
rem CHECK_PY_MINOR as a non-numeric fragment and crash ``if ... LSS``
rem with "LSS was unexpected at this time."
if not defined CHECK_PY_MINOR exit /b 1
for /f "delims=0123456789" %%X in ("!CHECK_PY_MINOR!") do exit /b 1
if !CHECK_PY_MINOR! LSS 12 exit /b 1
exit /b 0


rem ===========================================================================
rem Failure handlers — each prints ONE line with next action, then exits 1.
rem ===========================================================================

:fail_windows_version
echo [91m✗[0m N.O.V.A. requires Windows 11. Current Windows version is not supported.
goto :stop

:fail_python_missing
echo [91m✗[0m Python 3.12+ not found. Download it from https://python.org/downloads/
goto :stop

:fail_python_version
if defined CHECK_PY_VERSION (
    echo [91m✗[0m Python 3.12+ required ^(found !CHECK_PY_VERSION!^). Install from https://python.org/downloads/
) else (
    echo [91m✗[0m Python 3.12+ required. Install from https://python.org/downloads/
)
goto :stop

:fail_uv_install
echo [91m✗[0m Failed to install uv. Install manually: https://docs.astral.sh/uv/
if defined UV_INSTALL_LOG if exist "!UV_INSTALL_LOG!" (
    echo         ^(installer log: !UV_INSTALL_LOG!^)
)
goto :stop

:fail_uv_version
echo [91m✗[0m uv ^>= 0.5.11 required ^(found %UV_VERSION%^). Upgrade: ``uv self update``
goto :stop

:fail_uv_sync
echo [91m✗[0m Dependency installation failed. Check your internet connection and try again: setup.bat
goto :stop

:fail_localappdata_missing
echo [91m✗[0m %%LOCALAPPDATA%% environment variable not set. Check your user profile.
goto :stop

:fail_path_validation
rem The Python validator already printed the specific reason + "Setup stopped."
rem Route through :stop for cleanup (ROLLBACK_LIST delete, chcp restore),
rem but suppress the duplicate "Setup stopped." message since the validator
rem already emitted it.
set "SUPPRESS_STOP_MESSAGE=1"
goto :stop

:fail_mkdir_root
echo [91m✗[0m Couldn't create data directory. Check permissions on %%LOCALAPPDATA%% and try again.
goto :stop

:rollback_dirs
echo [91m✗[0m Couldn't create data subdirectories. Rolling back partial state.
call :do_rollback
goto :stop

:rollback_copies
echo [91m✗[0m Couldn't copy shipped defaults. Rolling back files created in this run.
call :do_rollback
goto :stop

:do_rollback
rem Replay rollback list in reverse: delete files first, then directories.
rem ``sort /r`` on a prefix-tagged list keeps FILE:: entries before DIR::
rem entries alphabetically, which suffices — directories come after files
rem in the tag order ``DIR`` < ``FILE`` normally, so reverse-sort puts
rem ``FILE::*`` first.
if not exist "%ROLLBACK_LIST%" exit /b 0
for /f "usebackq tokens=1,* delims=::" %%T in (`sort /r "%ROLLBACK_LIST%"`) do (
    if "%%T"=="FILE" (
        if exist "%%U" del "%%U" >nul 2>&1
    )
    if "%%T"=="DIR" (
        if exist "%%U" rmdir "%%U" >nul 2>&1
    )
)
if "%CREATED_ROOT%"=="1" (
    rem Root was created in this run; remove only if it's empty (rollback
    rem has already removed our files/dirs; any leftover is pre-existing
    rem user state we must not touch).
    rmdir "%DATA_DIR%" >nul 2>&1
)
del "%ROLLBACK_LIST%" >nul 2>&1
exit /b 0

:stop
if exist "%ROLLBACK_LIST%" del "%ROLLBACK_LIST%" >nul 2>&1
if not "%SUPPRESS_STOP_MESSAGE%"=="1" echo Setup stopped.
if defined ORIG_CP chcp !ORIG_CP! >nul 2>&1
endlocal & exit /b 1
