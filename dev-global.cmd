@echo off
REM Global CLI wrapper for cortex-harness dev command
REM Copy this file to a directory in your PATH (e.g., C:\Users\baka3\.local\bin\)

set CORTEX_HARNESS_DIR=C:\ai\cortex-harness
set PYTHON_EXE=%CORTEX_HARNESS_DIR%\.venv\Scripts\python.exe
set DEV_MODULE=%CORTEX_HARNESS_DIR%\cortex_harness\dev.py

"%PYTHON_EXE%" "%DEV_MODULE%" %*