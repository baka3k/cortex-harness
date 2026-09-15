@echo off
REM CortexHarness Context Menu Wrapper Script
REM This script is called from Windows Explorer context menus
REM Arguments: %1 = action (e.g., "sync code"), %2 = folder path

setlocal enabledelayedexpansion

REM Parse arguments
set "ACTION=%~1"
set "FOLDER_PATH=%~2"

REM Validate arguments
if "%ACTION%"=="" (
    echo Error: No action specified
    echo Usage: wrapper.bat "action" "folder_path"
    pause
    exit /b 1
)

if "%FOLDER_PATH%"=="" (
    echo Error: No folder path specified
    echo Usage: wrapper.bat "action" "folder_path"
    pause
    exit /b 1
)

REM Check if folder exists
if not exist "%FOLDER_PATH%" (
    echo Error: Folder does not exist: "%FOLDER_PATH%"
    pause
    exit /b 1
)

REM Detect CortexHarness installation directory
set "CORTEX_DIR="
if exist "C:\Program Files\CortexHarness\scripts\wrapper.bat" (
    set "CORTEX_DIR=C:\Program Files\CortexHarness"
) else if exist "C:\ai\cortex-harness" (
    set "CORTEX_DIR=C:\ai\cortex-harness"
) else (
    REM Try to find from script location
    set "CORTEX_DIR=%~dp0.."
)

if not exist "%CORTEX_DIR%" (
    echo Error: CortexHarness installation directory not found
    echo Please reinstall CortexHarness or set CORTEX_HARNESS_DIR environment variable
    pause
    exit /b 1
)

REM Binary-only entry (phase-06): exec cortex-dev.exe per D1 resolution.
if exist "%CORTEX_DIR%\rust\target\release\cortex-dev.exe" (
    "%CORTEX_DIR%\rust\target\release\cortex-dev.exe" %*
    exit /b %ERRORLEVEL%
)
if exist "%USERPROFILE%\.local\bin\cortex-dev.exe" (
    "%USERPROFILE%\.local\bin\cortex-dev.exe" %*
    exit /b %ERRORLEVEL%
)
echo Error: cortex-dev.exe not found. Install CortexHarness or set CORTEX_DEV_BIN.
pause
exit /b 1

REM Display execution information
echo ============================================
echo  CortexHarness Context Menu Action
echo ============================================
echo Action:    %ACTION%
echo Folder:    %FOLDER_PATH%
echo Time:      %date% %time%
echo ============================================
echo.

REM Execute the CortexHarness command
cd /d "%FOLDER_PATH%"

REM Build and execute command
"%PYTHON_EXE%" "%DEV_MODULE%" %ACTION% --project "%FOLDER_PATH%"

set "RESULT=%ERRORLEVEL%"

echo.
echo ============================================
if %RESULT% EQU 0 (
    echo Action completed successfully
) else (
    echo Action completed with errors (exit code: %RESULT%)
)
echo ============================================

REM Keep window open for a few seconds to let user read output
timeout /t 3 /nobreak >nul

endlocal
exit /b %RESULT%