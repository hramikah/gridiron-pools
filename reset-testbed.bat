@echo off
REM Wipe the test database and seed a fresh one (Windows). Live site is never
REM involved -- the only file this touches is testbed\pools.db in this folder.

setlocal enabledelayedexpansion
cd /d "%~dp0"
set "REPO=%CD%"
set "REPO_URI=%REPO:\=/%"

if not exist "%REPO%\testbed" mkdir "%REPO%\testbed"
set "GRIDIRON_DATABASE_URI=sqlite:///%REPO_URI%/testbed/pools.db"
if not defined SEASON_YEAR set "SEASON_YEAR=2026"

set "DB=%REPO%\testbed\pools.db"

echo.
echo Reset the test site
echo   This deletes: %DB%
echo   Nothing else is touched.
echo.

if not exist "%DB%" (
    echo No test database yet -- nothing to delete.
) else (
    set /p REPLY="Delete it and start over? [y/N] "
    if /i not "!REPLY:~0,1!"=="y" (
        echo Left alone.
        pause
        exit /b 0
    )
    REM Keep the last one, in case it had something worth going back to.
    if exist "%DB%.previous" del "%DB%.previous"
    move /y "%DB%" "%DB%.previous" >nul
    echo Moved the old database to testbed\pools.db.previous
)

if not exist "%REPO%\venv\Scripts\python.exe" (
    echo No Python environment yet -- run start-testbed.bat first.
    pause
    exit /b 1
)

"%REPO%\venv\Scripts\python.exe" "%REPO%\seed.py"
echo.
echo Fresh database seeded. Start the site with start-testbed.bat
echo Log in as admin / changeme123
pause
