@echo off
REM dev entrypoint - binary-only (phase-06 cutover; no Python rollback).
REM Resolution (D1): CORTEX_DEV_BIN -> %%USERPROFILE%%\.local\bin -> repo release build.
setlocal
set SCRIPT_DIR=%~dp0
if defined CORTEX_DEV_BIN if exist "%CORTEX_DEV_BIN%" (
    "%CORTEX_DEV_BIN%" %*
    goto :end
)
if exist "%USERPROFILE%\.local\bin\cortex-dev.exe" (
    "%USERPROFILE%\.local\bin\cortex-dev.exe" %*
    goto :end
)
if exist "%SCRIPT_DIR%rust\target\release\cortex-dev.exe" (
    "%SCRIPT_DIR%rust\target\release\cortex-dev.exe" %*
    goto :end
)
echo [error] cortex-dev binary not found. Install it ^(make install^) or set CORTEX_DEV_BIN. 1>&2
exit /b 1
:end
