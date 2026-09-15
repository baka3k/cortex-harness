@echo off
REM Global CLI wrapper for the cortex-dev binary (binary-only, phase-06).
REM Copy this file to a directory in your PATH (e.g., C:\Users\<you>\.local\bin\)

set CORTEX_HARNESS_DIR=C:\ai\cortex-harness

if defined CORTEX_DEV_BIN if exist "%CORTEX_DEV_BIN%" (
    "%CORTEX_DEV_BIN%" %*
    goto :end
)
if exist "%USERPROFILE%\.local\bin\cortex-dev.exe" (
    "%USERPROFILE%\.local\bin\cortex-dev.exe" %*
    goto :end
)
if exist "%CORTEX_HARNESS_DIR%\rust\target\release\cortex-dev.exe" (
    "%CORTEX_HARNESS_DIR%\rust\target\release\cortex-dev.exe" %*
    goto :end
)
echo [error] cortex-dev binary not found. Install it ^(make install^) or set CORTEX_DEV_BIN. 1>&2
exit /b 1
:end
