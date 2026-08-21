@echo off
REM Gridiron Pools -- local test site (Windows).
REM
REM Double-click this file in Explorer, or run start-testbed.bat in a
REM Command Prompt. It sets up a Python environment the first time, seeds a
REM throwaway database and starts the site at http://127.0.0.1:8090.
REM
REM It never touches the live site or the live database. The only database it
REM will open is testbed\pools.db inside this folder.

setlocal enabledelayedexpansion
cd /d "%~dp0"
set "REPO=%CD%"
REM SQLAlchemy wants forward slashes in the URI, even on Windows.
set "REPO_URI=%REPO:\=/%"

REM ---------------------------------------------------------------------
REM Everything the app writes goes under testbed\, never instance\. The
REM scripts that wipe and reseed data check for the word "testbed" in this
REM path and refuse to run without it -- see testbed_guard.py.
REM ---------------------------------------------------------------------
if not exist "%REPO%\testbed" mkdir "%REPO%\testbed"
set "GRIDIRON_DATABASE_URI=sqlite:///%REPO_URI%/testbed/pools.db"
if not defined SEASON_YEAR set "SEASON_YEAR=2026"

echo.
echo Gridiron Pools -- test site
echo   folder:   %REPO%
echo   database: testbed\pools.db  (live site untouched)
echo.

REM --- Python ----------------------------------------------------------
set "PY="
where py >nul 2>&1
if !errorlevel! equ 0 set "PY=py -3"
if not defined PY (
    where python >nul 2>&1
    if !errorlevel! equ 0 set "PY=python"
)
if not defined PY (
    echo No Python found.
    echo Install it from https://www.python.org/downloads/windows/
    echo During setup, tick "Add python.exe to PATH" or this script cannot find it.
    pause
    exit /b 1
)

if not exist "%REPO%\venv" (
    echo First run -- creating a Python environment ^(about a minute^)...
    %PY% -m venv "%REPO%\venv"
    if !errorlevel! neq 0 (
        echo Could not create the environment. Is Python installed correctly?
        pause
        exit /b 1
    )
    "%REPO%\venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    "%REPO%\venv\Scripts\python.exe" -m pip install --quiet -r "%REPO%\requirements.txt" pytest
    echo Environment ready.
)
set "VENV_PY=%REPO%\venv\Scripts\python.exe"

REM --- database --------------------------------------------------------
if not exist "%REPO%\testbed\pools.db" (
    echo Seeding a fresh database: 32 NFL teams, Loser Pool point values, one admin.
    "%VENV_PY%" "%REPO%\seed.py"
    echo.
)

REM --- go --------------------------------------------------------------
echo.
echo   Open:      http://127.0.0.1:8090
echo   Log in:    admin  /  changeme123
echo.
echo   The red "local test site" banner across the top is how you know you
echo   are not on gridironinvestment.com.
echo.
echo   From there:  Admin -^> Week Manager -^> create weeks and add games,
echo                then Admin -^> Pool Manager to enter scores.
echo.
echo   Stop the site with Ctrl-C in this window.
echo   Start over from nothing:  reset-testbed.bat
echo.

"%VENV_PY%" "%REPO%\app.py"
