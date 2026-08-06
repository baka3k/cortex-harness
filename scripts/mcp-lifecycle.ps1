[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("build", "install", "uninstall", "infra-up", "infra-down", "storage-layout", "storage-init", "storage-migrate-layout", "storage-backup", "storage-stop", "doctor", "start", "stop", "help")]
    [string]$Action = "help",
    [ValidateSet("all", "code", "doc")]
    [string]$Server = "all",
    [string]$Name = "",
    [string]$Project = "",
    [string]$Database = "",
    [string]$CodeDatabase = "",
    [string]$DocDatabase = "",
    [int]$Port = 0,
    [int]$CodePort = 0,
    [int]$DocPort = 0,
    [string]$BindHost = "",
    [string]$McpPath = "",
    [ValidateSet("", "falkordb", "neo4j")]
    [string]$Provider = "",
    [string]$Collection = "",
    [string]$CodeCollection = "",
    [string]$DocCollection = "",
    [string]$LegacyRoot = "",
    [switch]$Apply,
    [ValidateSet("code", "doc")]
    [string]$Owner = "code"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$StateDir = Join-Path (Join-Path $Root ".cache") "mcp"
$PidFile = Join-Path $StateDir "pids.json"

$Servers = @(
    [pscustomobject]@{
        Name = "code-tiny"
        WorkDir = Join-Path $Root "code-tiny"
        Script = Join-Path (Join-Path $Root "code-tiny") "mcp.sh"
        PythonScript = "mcp/unified_mcp.py"
        Arguments = @("--transport", "streamable-http", "--host", "127.0.0.1", "--port", "8788", "--path", "/mcp")
        Port = 8788
    },
    [pscustomobject]@{
        Name = "doc-tiny"
        WorkDir = Join-Path $Root "doc-tiny"
        Script = Join-Path (Join-Path $Root "doc-tiny") "mcp.sh"
        PythonScript = "mcp_graph_rag.py"
        Arguments = @("--host", "127.0.0.1", "--port", "8789", "--transport", "streamable-http", "--path", "/mcp")
        Port = 8789
    }
)

function Write-Usage {
    @"
Usage (equivalent forms):
  make build       | dev build       Create/sync virtualenvs and Python dependencies.
  make install     | dev install     Run build and install the global dev command.
  make uninstall   | dev uninstall   Remove the global dev command.
  make infra-up    | dev infra-up    Deprecated alias for storage initialization.
  make infra-down  | dev infra-down  Deprecated no-op for embedded storage.
  make storage-layout               Show centralized instance paths and leases.
  make storage-init                 Create the instance tree and manifest.
  make storage-migrate-layout       Dry-run legacy repository-local migration.
  make storage-backup               Create a verified owner backup.
  make doctor      | dev doctor      Check Python 3.12, local stores, and MCP ports.
  make start       | dev start       Open each MCP server in a separate terminal window.
  make stop        | dev stop        Stop MCP terminals/processes started by start.

Parameterized MCP instances:
  dev start --server code --name shop --project SHOP --port 8790
  dev start --name shop --project SHOP --code-port 8790 --doc-port 8791
  dev stop --name shop

Default MCP servers:
  code-tiny  http://127.0.0.1:8788/mcp
  doc-tiny   http://127.0.0.1:8789/mcp

Default local storage:
  data root     ~/.cortext-harness/v1/instances/default
  qdrant        <data-root>/qdrant/{code,doc}
  falkordb      <data-root>/falkordb/{code,doc}/data.rdb
"@ | Write-Host
}

function Test-IsWindows {
    if ($PSVersionTable.PSVersion.Major -ge 6) {
        return $IsWindows
    }

    return $true
}

function Get-CommandPath {
    param([string[]]$Names)

    foreach ($name in $Names) {
        $commands = @(Get-Command $name -ErrorAction SilentlyContinue)
        foreach ($cmd in $commands) {
            if ($cmd.CommandType -eq "Application" -and $cmd.Source) {
                return $cmd.Source
            }
        }
    }

    return $null
}

function Invoke-NativeQuiet {
    param(
        [string]$Command,
        [string[]]$Arguments
    )

    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Command @Arguments *> $null
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
}

function Get-PythonLauncher {
    $python = Get-CommandPath @("py.exe", "python.exe", "python3", "python")
    if (-not $python) {
        throw "Python was not found on PATH. Install Python 3.12+ before running make build."
    }
    return $python
}

function Get-RootVenvDir {
    return (Join-Path $Root ".venv")
}

function Get-RootVenvPython {
    $venvDir = Get-RootVenvDir
    $windowsPython = Join-Path $venvDir "Scripts\python.exe"
    $unixPython = Join-Path $venvDir "bin\python"

    if (Test-Path -LiteralPath $windowsPython) {
        return $windowsPython
    }
    if (Test-Path -LiteralPath $unixPython) {
        return $unixPython
    }

    throw "Virtualenv Python not found under $venvDir."
}

function Install-Requirements {
    param(
        [string]$Python,
        [string]$RequirementsPath
    )

    if (Test-Path -LiteralPath $RequirementsPath) {
        Write-Host "[build] Installing requirements: $RequirementsPath"
        & $Python -m pip install -r $RequirementsPath
    }
}

function Invoke-Build {
    $venvDir = Get-RootVenvDir
    if (-not (Test-Path -LiteralPath $venvDir)) {
        $launcher = Get-PythonLauncher
        Write-Host "[build] Creating venv: $venvDir"
        & $launcher -m venv $venvDir
    }

    $python = Get-RootVenvPython

    Write-Host "[build] Upgrading pip in $venvDir"
    & $python -m pip install --upgrade pip

    Install-Requirements -Python $python -RequirementsPath (Join-Path $Root "requirements.txt")
    Install-Requirements -Python $python -RequirementsPath (Join-Path (Join-Path $Root "code-tiny") "requirements.txt")
    Install-Requirements -Python $python -RequirementsPath (Join-Path (Join-Path $Root "doc-tiny") "requirements.txt")

    Write-Host "[build] Installing editable root package"
    & $python -m pip install -e $Root

    Write-Host "[build] Dependency sync complete."
}

function Get-UserBinDir {
    if (-not (Test-IsWindows) -and $env:HOME) {
        return (Join-Path (Join-Path $env:HOME ".local") "bin")
    }

    if ($env:USERPROFILE) {
        return (Join-Path (Join-Path $env:USERPROFILE ".local") "bin")
    }

    throw "Neither HOME nor USERPROFILE is set; cannot choose a user-local install directory."
}

function Test-PathListContains {
    param(
        [string]$PathList,
        [string]$Directory
    )

    if ([string]::IsNullOrWhiteSpace($PathList)) {
        return $false
    }

    $separator = if (Test-IsWindows) { ";" } else { ":" }
    $trimChars = [char[]]@("\", "/")
    $target = [System.IO.Path]::GetFullPath($Directory).TrimEnd($trimChars)
    foreach ($entry in ($PathList -split [regex]::Escape($separator))) {
        if ([string]::IsNullOrWhiteSpace($entry)) {
            continue
        }

        try {
            $normalized = [System.IO.Path]::GetFullPath($entry).TrimEnd($trimChars)
            if ([string]::Equals($normalized, $target, [System.StringComparison]::OrdinalIgnoreCase)) {
                return $true
            }
        } catch {
            if ([string]::Equals($entry.TrimEnd($trimChars), $Directory.TrimEnd($trimChars), [System.StringComparison]::OrdinalIgnoreCase)) {
                return $true
            }
        }
    }

    return $false
}

function Add-UserPathEntry {
    param([string]$Directory)

    if (-not (Test-IsWindows)) {
        if (-not (Test-PathListContains -PathList $env:PATH -Directory $Directory)) {
            $env:PATH = $env:PATH.TrimEnd(":") + ":" + $Directory
            Write-Host "[install] Add this to your shell profile if needed: export PATH=`"$Directory`":`$PATH"
            return $true
        }

        return $false
    }

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (Test-PathListContains -PathList $userPath -Directory $Directory) {
        return $false
    }

    $newPath = if ([string]::IsNullOrWhiteSpace($userPath)) {
        $Directory
    } else {
        $userPath.TrimEnd(";") + ";" + $Directory
    }

    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")

    if (-not (Test-PathListContains -PathList $env:Path -Directory $Directory)) {
        $env:Path = $env:Path.TrimEnd(";") + ";" + $Directory
    }

    return $true
}

function Install-DevCommand {
    $binDir = Get-UserBinDir
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null

    if (Test-IsWindows) {
        $source = Join-Path $Root "dev-global.cmd"
        $target = Join-Path $binDir "dev.cmd"

        if (-not (Test-Path -LiteralPath $source)) {
            throw "Global dev wrapper not found: $source"
        }

        Copy-Item -LiteralPath $source -Destination $target -Force
    } else {
        $target = Join-Path $binDir "dev"
        $rootForShell = $Root -replace "'", "'\''"
        $content = @"
#!/usr/bin/env bash
set -euo pipefail
CORTEX_HARNESS_DIR='$rootForShell'
PYTHON_EXE="`${CORTEX_HARNESS_DIR}/.venv/bin/python"
if [ ! -x "`$PYTHON_EXE" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_EXE="`$(command -v python3)"
  else
    PYTHON_EXE="`$(command -v python)"
  fi
fi
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
exec "`$PYTHON_EXE" "`${CORTEX_HARNESS_DIR}/cortex_harness/dev.py" "`$@"
"@
        Set-Content -LiteralPath $target -Value $content -NoNewline -Encoding UTF8
        $chmod = Get-CommandPath @("chmod")
        if ($chmod) {
            & $chmod +x $target
        }
    }

    $pathAdded = Add-UserPathEntry -Directory $binDir

    Write-Host "[install] Installed dev command: $target"
    if ($pathAdded) {
        Write-Host "[install] Added $binDir to User PATH. Open a new terminal if the current one does not see dev."
    }

    $command = Get-Command dev -ErrorAction SilentlyContinue
    if ($command) {
        Write-Host "[install] dev resolves to: $($command.Source)"
    } else {
        Write-Host "[install] dev will be available after opening a new terminal."
    }
}

function Invoke-Install {
    Invoke-Build
    Install-DevCommand
}

function Invoke-Uninstall {
    $commandName = if (Test-IsWindows) { "dev.cmd" } else { "dev" }
    $target = Join-Path (Get-UserBinDir) $commandName
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Force
        Write-Host "[uninstall] Removed dev command: $target"
    } else {
        Write-Host "[uninstall] dev command was not installed at: $target"
    }

    Write-Host "[uninstall] User PATH was left unchanged."
}

function Test-TcpPort {
    param(
        [string]$HostName,
        [int]$Port,
        [int]$TimeoutMs = 1000
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $result = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $result.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) {
            return $false
        }
        $client.EndConnect($result)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-TcpPort {
    param(
        [string]$HostName,
        [int]$Port,
        [int]$TimeoutSeconds = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPort -HostName $HostName -Port $Port -TimeoutMs 1000) {
            return $true
        }
        Start-Sleep -Seconds 1
    }

    return $false
}

function Test-HttpReady {
    param([string]$Url)

    if ([string]::IsNullOrWhiteSpace($Url)) {
        return $true
    }

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        return ([int]$response.StatusCode -ge 200 -and [int]$response.StatusCode -lt 400)
    } catch {
        return $false
    }
}

function Test-RedisPingReady {
    param(
        [string]$HostName,
        [int]$Port,
        [int]$TimeoutMs = 2000
    )

    # A listening socket is not enough; PING->PONG proves the server and its
    # modules (FalkorDB graph) finished loading and can serve commands.
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        $connected = $iar.AsyncWaitHandle.WaitOne($TimeoutMs)
        if (-not $connected -or -not $client.Connected) {
            $client.Close()
            return $false
        }
        $client.SendTimeout = $TimeoutMs
        $client.ReceiveTimeout = $TimeoutMs
        $stream = $client.GetStream()
        # RESP inline PING: "*1\r\n$4\r\nPING\r\n"
        $bytes = [System.Text.Encoding]::ASCII.GetBytes("*1`r`n`$4`r`nPING`r`n")
        $stream.Write($bytes, 0, $bytes.Length)
        $buffer = New-Object byte[] 64
        $read = $stream.Read($buffer, 0, 64)
        $client.Close()
        if ($read -le 0) { return $false }
        $response = [System.Text.Encoding]::ASCII.GetString($buffer, 0, $read)
        return $response.StartsWith("+PONG")
    } catch {
        return $false
    }
}

function Wait-RedisPingReady {
    param(
        [string]$HostName,
        [int]$Port,
        [int]$TimeoutSeconds = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-RedisPingReady -HostName $HostName -Port $Port -TimeoutMs 2000) {
            return $true
        }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Invoke-InfraUp {
    Write-Host "[warn] 'infra-up' is deprecated; initializing embedded storage instead."
    Invoke-StorageLifecycle -StorageAction "storage-init"
}

function Invoke-InfraDown {
    Write-Host "[warn] 'infra-down' is deprecated; embedded storage has no service to stop."
}

function Invoke-StorageLifecycle {
    param(
        [string]$StorageAction,
        [string[]]$StorageArguments = @()
    )
    $python = Get-RootVenvPython
    $lifecycle = Join-Path $Root "scripts/mcp-lifecycle.py"
    & $python $lifecycle $StorageAction @StorageArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Storage lifecycle action failed: $StorageAction"
    }
}

function Test-SupportsColor {
    # Honor the de-facto NO_COLOR standard and disable colors when output is
    # redirected/piped, so logs stay clean and grep-friendly.
    if ($env:NO_COLOR) { return $false }
    return [Console]::IsOutputRedirected -eq $false
}

function Write-DoctorCheck {
    param(
        [string]$Name,
        [bool]$Ok,
        [string]$Message,
        [bool]$Required = $true
    )

    if ($Ok) {
        if (Test-SupportsColor) {
            Write-Host "[doctor]" -NoNewline
            Write-Host "[ok]" -ForegroundColor Green -NoNewline
            Write-Host "   $Name - $Message"
        } else {
            Write-Host "[doctor][ok]   $Name - $Message"
        }
        return
    }

    if ($Required) {
        $script:DoctorFailures += 1
        if (Test-SupportsColor) {
            Write-Host "[doctor]" -NoNewline
            Write-Host "[fail]" -ForegroundColor Red -NoNewline
            Write-Host " $Name - $Message"
        } else {
            Write-Host "[doctor][fail] $Name - $Message"
        }
    } else {
        if (Test-SupportsColor) {
            Write-Host "[doctor]" -NoNewline
            Write-Host "[warn]" -ForegroundColor Yellow -NoNewline
            Write-Host " $Name - $Message"
        } else {
            Write-Host "[doctor][warn] $Name - $Message"
        }
    }
}

function Invoke-Doctor {
    # The Python implementation owns the cross-platform local-store probes so
    # Windows and POSIX validate exactly the same dependencies and paths.
    Invoke-StorageLifecycle -StorageAction "doctor"
}

function Get-ShellRunner {
    $candidates = @(@(
        $env:GIT_BASH,
        (Join-Path ${env:ProgramFiles} "Git\bin\bash.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Git\bin\bash.exe")
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) })

    if ($candidates.Count -gt 0) {
        return [pscustomobject]@{
            Kind = "git-bash"
            Command = $candidates[0]
        }
    }

    $bash = Get-CommandPath @("bash.exe", "bash")
    if ($bash -and
        $bash -notmatch "\\Windows\\System32\\bash(\.exe)?$" -and
        $bash -notmatch "\\WindowsApps\\bash(\.exe)?$") {
        return [pscustomobject]@{
            Kind = "git-bash"
            Command = $bash
        }
    }

    $wsl = Get-CommandPath @("wsl.exe", "wsl")
    if ($wsl) {
        return [pscustomobject]@{
            Kind = "powershell"
            Command = "powershell.exe"
        }
    }

    if ($bash) {
        return [pscustomobject]@{
            Kind = "powershell"
            Command = "powershell.exe"
        }
    }

    return [pscustomobject]@{
        Kind = "powershell"
        Command = "powershell.exe"
    }
}

function Convert-ToShellPath {
    param(
        [string]$Path,
        [ValidateSet("git-bash", "wsl")]
        [string]$Kind
    )

    $resolved = [System.IO.Path]::GetFullPath($Path)
    if ($resolved -match "^([A-Za-z]):\\(.*)$") {
        $drive = $matches[1].ToLowerInvariant()
        $rest = $matches[2] -replace "\\", "/"
        if ($Kind -eq "wsl") {
            return "/mnt/$drive/$rest"
        }
        return "/$drive/$rest"
    }

    return ($resolved -replace "\\", "/")
}

function Quote-Bash {
    param([string]$Value)
    return "'" + ($Value -replace "'", "'\''") + "'"
}

function Quote-PowerShell {
    param([string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

function Read-PidRecords {
    if (-not (Test-Path -LiteralPath $PidFile)) {
        return @()
    }

    $json = Get-Content -Raw -LiteralPath $PidFile
    if ([string]::IsNullOrWhiteSpace($json)) {
        return @()
    }

    return @($json | ConvertFrom-Json)
}

function Write-PidRecords {
    param([object[]]$Records)

    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    if (@($Records).Count -eq 0) {
        if (Test-Path -LiteralPath $PidFile) {
            Remove-Item -LiteralPath $PidFile -Force
        }
        return
    }
    $Records | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $PidFile -Encoding UTF8
}

function Stop-SavedProcesses {
    param([string]$InstanceName = "")

    $records = Read-PidRecords
    $remaining = @()

    foreach ($record in $records) {
        if ($InstanceName -and $record.Instance -ne $InstanceName) {
            $remaining += $record
            continue
        }
        $pidToStop = [int]$record.Pid
        $proc = Get-Process -Id $pidToStop -ErrorAction SilentlyContinue
        if (-not $proc) {
            continue
        }

        $sameStart = $false
        try {
            if ($record.StartedAt) {
                $recordedStart = ([datetime]$record.StartedAt).ToUniversalTime()
                $actualStart = $proc.StartTime.ToUniversalTime()
                $sameStart = ([math]::Abs(($actualStart - $recordedStart).TotalSeconds) -lt 5)
            }
        } catch {
            $sameStart = $false
        }

        if ($sameStart) {
            Write-Host "[stop] Stopping saved process $pidToStop ($($record.Name))"
            Stop-Process -Id $pidToStop -Force -ErrorAction SilentlyContinue
        } else {
            Write-Host "[stop] Skipping stale PID record $pidToStop ($($record.Name))"
        }
    }

    return $remaining
}

function Stop-MarkerProcesses {
    $rootWin = $Root.ToLowerInvariant()
    $rootGitBash = (Convert-ToShellPath -Path $Root -Kind "git-bash").ToLowerInvariant()
    $rootWsl = (Convert-ToShellPath -Path $Root -Kind "wsl").ToLowerInvariant()
    $allowedProcessNames = @(
        "powershell.exe",
        "pwsh.exe",
        "bash.exe",
        "sh.exe",
        "python.exe",
        "python3.exe",
        "py.exe",
        "wsl.exe"
    )
    $markers = @(
        "code-tiny\mcp.sh",
        "doc-tiny\mcp.sh",
        "code-tiny/mcp.sh",
        "doc-tiny/mcp.sh",
        "mcp\unified_mcp.py",
        "mcp/unified_mcp.py",
        "mcp_graph_rag.py"
    )

    $processes = Get-CimInstance Win32_Process | Where-Object {
        if (-not $_.CommandLine -or $_.ProcessId -eq $PID) {
            return $false
        }

        if ($allowedProcessNames -notcontains $_.Name.ToLowerInvariant()) {
            return $false
        }

        $cmd = $_.CommandLine.ToLowerInvariant()
        $inRepo = $cmd.Contains($rootWin) -or $cmd.Contains($rootGitBash) -or $cmd.Contains($rootWsl)
        $hasMarker = $false
        foreach ($marker in $markers) {
            if ($cmd.Contains($marker.ToLowerInvariant())) {
                $hasMarker = $true
                break
            }
        }

        return ($inRepo -and $hasMarker)
    }

    foreach ($proc in $processes) {
        Write-Host "[stop] Stopping MCP process $($proc.ProcessId): $($proc.Name)"
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-Stop {
    param([string]$InstanceName = "")

    $remaining = @(Stop-SavedProcesses -InstanceName $InstanceName)
    if (-not $InstanceName) {
        Stop-MarkerProcesses
        $remaining = @()
    }
    Write-PidRecords -Records $remaining

    $scope = if ($InstanceName) { " instance '$InstanceName'" } else { "" }
    Write-Host "[stop] MCP$scope stop complete."
}

function Get-DefaultGraphEnvBash {
    param([string]$ServerName)

    $scopedProvider = if ($ServerName -eq "doc-tiny") { "DOC_GRAPH_PROVIDER" } else { "CODE_GRAPH_PROVIDER" }
    return @(
        'export GRAPH_PROVIDER="${GRAPH_PROVIDER:-falkordb}"',
        ('export ' + $scopedProvider + '="${' + $scopedProvider + ':-${GRAPH_PROVIDER}}"'),
        'export FALKORDB_GRAPH="${FALKORDB_GRAPH:-hyper_graph}"'
    ) -join "; "
}

function Get-DefaultGraphEnvPowerShell {
    param([string]$ServerName)

    $scopedProvider = if ($ServerName -eq "doc-tiny") { "DOC_GRAPH_PROVIDER" } else { "CODE_GRAPH_PROVIDER" }
    return @"
if (-not `$env:GRAPH_PROVIDER) { `$env:GRAPH_PROVIDER = 'falkordb' }
if (-not [Environment]::GetEnvironmentVariable('$scopedProvider', 'Process')) { [Environment]::SetEnvironmentVariable('$scopedProvider', `$env:GRAPH_PROVIDER, 'Process') }
if (-not `$env:FALKORDB_GRAPH) { `$env:FALKORDB_GRAPH = 'hyper_graph' }
"@
}

function Get-StartConfiguration {
    $custom = (
        $Server -ne "all" -or $Name -or $Project -or $Database -or $CodeDatabase -or $DocDatabase -or
        $Port -gt 0 -or $CodePort -gt 0 -or $DocPort -gt 0 -or $BindHost -or $McpPath -or $Provider -or
        $Collection -or $CodeCollection -or $DocCollection
    )
    if (-not $custom) {
        return [pscustomobject]@{
            Custom = $false
            Instance = "default"
            HostName = "127.0.0.1"
            Path = "/mcp"
            Servers = @($Servers)
        }
    }

    $instance = @($Name, $Project, $Database, $Server) | Where-Object { $_ } | Select-Object -First 1
    if ($instance -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$') {
        throw "Instance name must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63}. Use -Name to provide one."
    }
    if ($Port -gt 0 -and $Server -eq "all") {
        throw "-Port requires -Server code or -Server doc; use -CodePort/-DocPort for both."
    }
    if ($Server -eq "code" -and $DocPort -gt 0) {
        throw "-DocPort cannot be used with -Server code."
    }
    if ($Server -eq "doc" -and $CodePort -gt 0) {
        throw "-CodePort cannot be used with -Server doc."
    }

    $hostName = if ($BindHost) { $BindHost } else { "127.0.0.1" }
    $route = if ($McpPath) { $McpPath } else { "/mcp" }
    if (-not $route.StartsWith("/")) {
        $route = "/$route"
    }
    $selected = @()
    foreach ($source in $Servers) {
        if ($Server -ne "all" -and -not $source.Name.StartsWith($Server)) {
            continue
        }
        $serverPort = $source.Port
        if ($Port -gt 0) { $serverPort = $Port }
        if ($source.Name -eq "code-tiny" -and $CodePort -gt 0) {
            if ($Port -gt 0) { throw "Use either -Port or -CodePort, not both." }
            $serverPort = $CodePort
        }
        if ($source.Name -eq "doc-tiny" -and $DocPort -gt 0) {
            if ($Port -gt 0) { throw "Use either -Port or -DocPort, not both." }
            $serverPort = $DocPort
        }
        if ($serverPort -lt 1 -or $serverPort -gt 65535) {
            throw "Port must be between 1 and 65535: $serverPort"
        }
        $arguments = if ($source.Name -eq "code-tiny") {
            @("--transport", "streamable-http", "--host", $hostName, "--port", [string]$serverPort, "--path", $route)
        } else {
            @("--host", $hostName, "--port", [string]$serverPort, "--transport", "streamable-http", "--path", $route)
        }
        $selected += [pscustomobject]@{
            Name = $source.Name
            WorkDir = $source.WorkDir
            Script = $source.Script
            PythonScript = $source.PythonScript
            Arguments = $arguments
            Port = $serverPort
        }
    }
    if ((@($selected | ForEach-Object { $_.Port } | Select-Object -Unique)).Count -ne $selected.Count) {
        throw "Each selected MCP server must use a different port."
    }
    return [pscustomobject]@{
        Custom = $true
        Instance = $instance
        HostName = $hostName
        Path = $route
        Servers = $selected
    }
}

function Get-RuntimeOverrides {
    param(
        [object]$Config,
        [object]$ServerConfig
    )

    $isCode = $ServerConfig.Name -eq "code-tiny"
    $databaseName = if ($isCode -and $CodeDatabase) { $CodeDatabase } elseif (-not $isCode -and $DocDatabase) { $DocDatabase } elseif ($Database) { $Database } else { $Project }
    $collectionName = if ($isCode -and $CodeCollection) { $CodeCollection } elseif (-not $isCode -and $DocCollection) { $DocCollection } elseif ($Collection) { $Collection } else { $Project }
    $suffix = if ($isCode) { "code" } else { "doc" }
    $mcpName = if ($Config.Servers.Count -gt 1) { "$($Config.Instance)-$suffix" } else { $Config.Instance }
    $storageInstance = $Config.Instance.ToLowerInvariant().Replace('.', '-')
    $overrides = [ordered]@{
        MCP_SERVER_NAME = $mcpName
        CORTEX_STORAGE_INSTANCE = $storageInstance
        CORTEX_STORAGE_OWNER = $suffix
    }
    if ($Project) {
        $overrides.PROJECT_ID = $Project
        $overrides.PROJECT_NAME = $Project
    }
    if ($databaseName) {
        $overrides.FALKORDB_GRAPH = $databaseName
        $overrides.NEO4J_DB = $databaseName
    }
    if ($collectionName) {
        $collectionKey = if ($isCode) { "QDRANT_COLLECTION" } else { "QDRANT_COLLECTION_DOC" }
        $overrides[$collectionKey] = $collectionName
    }
    if ($Provider) {
        $overrides.GRAPH_PROVIDER = $Provider
        $providerKey = if ($isCode) { "CODE_GRAPH_PROVIDER" } else { "DOC_GRAPH_PROVIDER" }
        $overrides[$providerKey] = $Provider
    }
    return $overrides
}

function Invoke-Start {
    $config = Get-StartConfiguration
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    if ($config.Custom) {
        Invoke-Stop -InstanceName $config.Instance
        $records = @(Read-PidRecords)
        foreach ($server in $config.Servers) {
            if (Test-TcpPort -HostName $config.HostName -Port $server.Port -TimeoutMs 300) {
                throw "Port already in use: $($config.HostName):$($server.Port)"
            }
        }
    } else {
        Invoke-Stop
        $records = @()
    }

    $runner = Get-ShellRunner

    foreach ($server in $config.Servers) {
        if (-not (Test-Path -LiteralPath $server.Script)) {
            throw "MCP script not found: $($server.Script)"
        }

        $label = if ($config.Custom) { "$($config.Instance)/$($server.Name)" } else { $server.Name }
        $title = "MCP $label :$($server.Port)"
        $titleArg = Quote-PowerShell $title
        $scriptArg = Quote-PowerShell $server.Script
        $runtimeConfigScript = Join-Path $Root "scripts\mcp_runtime_config.py"
        $stateName = if ($config.Custom) { "$($config.Instance)-$($server.Name)" } else { $server.Name }
        $runtimeJsonPath = Join-Path $StateDir "$stateName.active.json"
        $runtimeBashPath = Join-Path $StateDir "$stateName.active.env"
        $rootPython = Get-RootVenvPython
        $runtimeJson = & $rootPython $runtimeConfigScript --root $Root --server $server.Name --format json
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to resolve active MCP environment for $($server.Name)."
        }
        $runtimeBash = & $rootPython $runtimeConfigScript --root $Root --server $server.Name --format bash
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to render active MCP environment for $($server.Name)."
        }
        if ($config.Custom) {
            $runtimeData = [ordered]@{}
            $parsedRuntime = ($runtimeJson -join [Environment]::NewLine) | ConvertFrom-Json
            $parsedRuntime.PSObject.Properties | ForEach-Object { $runtimeData[$_.Name] = [string]$_.Value }
            $overrides = Get-RuntimeOverrides -Config $config -ServerConfig $server
            foreach ($entry in $overrides.GetEnumerator()) {
                $runtimeData[$entry.Key] = [string]$entry.Value
                $runtimeBash += "export $($entry.Key)=$(Quote-Bash ([string]$entry.Value))"
            }
            $runtimeJson = @($runtimeData | ConvertTo-Json -Compress)
        }
        [System.IO.File]::WriteAllText($runtimeJsonPath, ($runtimeJson -join [Environment]::NewLine), [System.Text.UTF8Encoding]::new($false))
        [System.IO.File]::WriteAllText($runtimeBashPath, (($runtimeBash -join [Environment]::NewLine) + [Environment]::NewLine), [System.Text.UTF8Encoding]::new($false))

        if ($runner.Kind -eq "git-bash") {
            $workDir = Quote-Bash (Convert-ToShellPath -Path $server.WorkDir -Kind $runner.Kind)
            $rootVenv = Quote-Bash (Convert-ToShellPath -Path (Get-RootVenvDir) -Kind $runner.Kind)
            $scriptName = Quote-Bash (Split-Path -Leaf $server.Script)
            $runtimeEnvFile = Quote-Bash (Convert-ToShellPath -Path $runtimeBashPath -Kind $runner.Kind)
            $graphEnv = Get-DefaultGraphEnvBash -ServerName $server.Name
            $scriptOptions = if ($config.Custom) { " " + (($server.Arguments | ForEach-Object { Quote-Bash $_ }) -join " ") } else { "" }
            $bashCommand = "if [ -f $rootVenv/bin/activate ]; then source $rootVenv/bin/activate; elif [ -f $rootVenv/Scripts/activate ]; then source $rootVenv/Scripts/activate; fi; $graphEnv; export CORTEX_HARNESS_ENV_FILE=$runtimeEnvFile; cd $workDir && bash ./$scriptName$scriptOptions"
            $runnerArg = Quote-PowerShell $runner.Command
            $bashCommandArg = Quote-PowerShell $bashCommand
            $invokeLine = "& $runnerArg -lc $bashCommandArg"
        } else {
            $pythonArg = Quote-PowerShell (Get-RootVenvPython)
            $workDirArg = Quote-PowerShell $server.WorkDir
            $pythonScriptArg = Quote-PowerShell $server.PythonScript
            $runtimeJsonArg = Quote-PowerShell $runtimeJsonPath
            $argumentList = "@(" + (($server.Arguments | ForEach-Object { Quote-PowerShell $_ }) -join ", ") + ")"
            $graphEnv = Get-DefaultGraphEnvPowerShell -ServerName $server.Name
            $invokeLine = @"
Set-Location -LiteralPath $workDirArg
`$serverArgs = $argumentList
$graphEnv
`$envFile = Join-Path (Get-Location) '.env'
if (Test-Path -LiteralPath `$envFile) {
    Get-Content -LiteralPath `$envFile | ForEach-Object {
        `$line = `$_.Trim()
        if (-not `$line -or `$line.StartsWith('#') -or `$line -notmatch '=') { return }
        if (`$line.StartsWith('export ')) { `$line = `$line.Substring(7).Trim() }
        `$key, `$value = `$line.Split('=', 2)
        `$value = `$value.Trim()
        if ((`$value.StartsWith("'") -and `$value.EndsWith("'")) -or (`$value.StartsWith('"') -and `$value.EndsWith('"'))) {
            `$value = `$value.Substring(1, `$value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable(`$key.Trim(), `$value, 'Process')
    }
}
`$runtimeEnvironment = Get-Content -Raw -LiteralPath $runtimeJsonArg | ConvertFrom-Json
`$runtimeEnvironment.PSObject.Properties | ForEach-Object {
    [Environment]::SetEnvironmentVariable(`$_.Name, [string]`$_.Value, 'Process')
}
& $pythonArg $pythonScriptArg @serverArgs
"@
        }

        $terminalCommand = @"
`$host.UI.RawUI.WindowTitle = $titleArg
Write-Host '[start]' $titleArg
Write-Host '[start]' $scriptArg
$invokeLine
Write-Host ''
Write-Host '[start]' $titleArg 'exited.'
"@

        $encoded = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($terminalCommand))
        $proc = Start-Process `
            -FilePath "powershell.exe" `
            -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encoded) `
            -WorkingDirectory $Root `
            -WindowStyle Normal `
            -PassThru

        $record = [ordered]@{
            Name = $server.Name
            Pid = $proc.Id
            Script = $server.Script
            Port = $server.Port
            RuntimeConfig = $runtimeJsonPath
            StartedAt = $proc.StartTime.ToUniversalTime().ToString("o")
        }
        if ($config.Custom) {
            $record.Instance = $config.Instance
            $record.Host = $config.HostName
            $record.Path = $config.Path
            $record.Endpoint = "http://$($config.HostName):$($server.Port)$($config.Path)"
        }
        $records += [pscustomobject]$record

        if ($config.Custom) {
            Write-Host "[start] Started $label in terminal PID $($proc.Id) on $($server.Port)"
        } else {
            Write-Host "[start] Started $label in terminal PID $($proc.Id)"
        }
    }

    Write-PidRecords $records
    Write-Host "[start] MCP terminals opened. Logs are visible in their own windows."
}

try {
    switch ($Action) {
        "build" { Invoke-Build }
        "install" { Invoke-Install }
        "uninstall" { Invoke-Uninstall }
        "infra-up" { Invoke-InfraUp }
        "infra-down" { Invoke-InfraDown }
        "storage-layout" { Invoke-StorageLifecycle -StorageAction "storage-layout" }
        "storage-init" { Invoke-StorageLifecycle -StorageAction "storage-init" }
        "storage-migrate-layout" {
            $storageArgs = @()
            if ($LegacyRoot) { $storageArgs += @("--legacy-root", $LegacyRoot) }
            if ($Apply) { $storageArgs += "--apply" }
            Invoke-StorageLifecycle -StorageAction "storage-migrate-layout" -StorageArguments $storageArgs
        }
        "storage-backup" { Invoke-StorageLifecycle -StorageAction "storage-backup" -StorageArguments @("--owner", $Owner) }
        "storage-stop" { Write-Host "[storage-stop] Local storage has no lifecycle to stop." }
        "doctor" { Invoke-Doctor }
        "start" { Invoke-Start }
        "stop" { Invoke-Stop -InstanceName $Name }
        default { Write-Usage }
    }
} catch {
    Write-Host "[error] $($_.Exception.Message)"
    exit 1
}
