@echo off
setlocal
set SCRIPT_DIR=%~dp0
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
    "%SCRIPT_DIR%.venv\Scripts\python.exe" "%SCRIPT_DIR%cli\dev.py" %*
) else (
    python "%SCRIPT_DIR%cli\dev.py" %*
)
