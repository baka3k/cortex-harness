#!/usr/bin/env pwsh
# dev entrypoint - binary-only (phase-06 cutover; no Python rollback).
# Resolution (D1): CORTEX_DEV_BIN -> ~/.local/bin -> repo release build.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($env:CORTEX_DEV_BIN -and (Test-Path $env:CORTEX_DEV_BIN)) {
    & $env:CORTEX_DEV_BIN @args
    exit $LASTEXITCODE
}
$localBin = Join-Path $env:USERPROFILE ".local/bin/cortex-dev.exe"
if (Test-Path $localBin) {
    & $localBin @args
    exit $LASTEXITCODE
}
$repoBuild = Join-Path $scriptDir "rust\target\release\cortex-dev.exe"
if (Test-Path $repoBuild) {
    & $repoBuild @args
    exit $LASTEXITCODE
}
Write-Error "[error] cortex-dev binary not found. Install it (make install) or set CORTEX_DEV_BIN."
exit 1
