@echo off
REM Global CLI wrapper for cortex-harness dev command
REM Copy this file to a directory in your PATH (e.g., %USERPROFILE%\.local\bin\)

if not defined CORTEX_HARNESS_DIR (
    if exist "%~dp0cortex_harness\dev.py" (
        set "CORTEX_HARNESS_DIR=%~dp0."
    ) else (
        set "CORTEX_HARNESS_DIR=C:\ai\cortex-harness"
    )
)
set "PYTHON_EXE=%CORTEX_HARNESS_DIR%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
set "DEV_MODULE=%CORTEX_HARNESS_DIR%\cortex_harness\dev.py"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

"%PYTHON_EXE%" "%DEV_MODULE%" %*
