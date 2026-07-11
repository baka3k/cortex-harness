#!/usr/bin/env pwsh
# Wrapper script for dev CLI that works from any directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$cortexHarnessPython = "$scriptDir\.venv\Scripts\python.exe"
$devModule = "$scriptDir\cortex_harness\dev.py"

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
& $cortexHarnessPython $devModule $args
