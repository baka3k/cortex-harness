[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("build", "install", "uninstall", "infra-up", "infra-down", "doctor", "start", "stop", "help")]
    [string]$Action = "help"
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

$InfraServices = @(
    [pscustomobject]@{
        Name = "qdrant"
        Container = "cortex-qdrant"
        Image = "qdrant/qdrant"
        Ports = @("6333:6333")
        Host = "127.0.0.1"
        Port = 6333
        ReadyUrl = "http://127.0.0.1:6333"
    },
    [pscustomobject]@{
        Name = "falkordb"
        Container = "cortex-falkordb"
        Image = "falkordb/falkordb"
        Ports = @("6379:6379")
        Host = "127.0.0.1"
        Port = 6379
        ReadyUrl = ""
    }
)

function Write-Usage {
    @"
Usage (equivalent forms):
  make build       | dev build       Create/sync virtualenvs and Python dependencies.
  make install     | dev install     Run build and install the global dev command.
  make uninstall   | dev uninstall   Remove the global dev command.
  make infra-up    | dev infra-up    Pull/start local Qdrant and FalkorDB containers.
  make infra-down  | dev infra-down  Stop the containers started by infra-up.
  make doctor      | dev doctor      Check Python deps, Docker, databases, and MCP ports.
  make start       | dev start       Open each MCP server in a separate terminal window.
  make stop        | dev stop        Stop MCP terminals/processes started by start.

Default MCP servers:
  code-tiny  http://127.0.0.1:8788/mcp
  doc-tiny   http://127.0.0.1:8789/mcp

Default local infrastructure:
  qdrant    http://127.0.0.1:6333
  falkordb  redis://127.0.0.1:6379
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
        throw "Python was not found on PATH. Install Python 3.10+ before running make build."
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

function Get-DockerCommand {
    $docker = Get-CommandPath @("docker.exe", "docker")
    if (-not $docker) {
        throw "Docker was not found on PATH. Install Docker Desktop before running make infra-up."
    }

    if ((Invoke-NativeQuiet -Command $docker -Arguments @("info")) -ne 0) {
        throw "Docker was found, but the Docker daemon is not running. Start Docker Desktop and retry."
    }

    return $docker
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

function Test-DockerContainerExists {
    param(
        [string]$Docker,
        [string]$Container
    )

    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $names = & $Docker ps -a --filter "name=^/$Container$" --format "{{.Names}}" 2>$null
        return (@($names) | Where-Object { $_ -eq $Container }).Count -gt 0
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
}

function Test-DockerContainerRunning {
    param(
        [string]$Docker,
        [string]$Container
    )

    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $running = & $Docker inspect -f "{{.State.Running}}" $Container 2>$null
        return (($running | Select-Object -First 1) -eq "true")
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
}

function Ensure-DockerImage {
    param(
        [string]$Docker,
        [string]$Image
    )

    if ((Invoke-NativeQuiet -Command $Docker -Arguments @("image", "inspect", $Image)) -eq 0) {
        return
    }

    Write-Host "[infra] Pulling image: $Image"
    & $Docker pull $Image
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to pull Docker image: $Image"
    }
}

function Start-InfraService {
    param(
        [string]$Docker,
        [object]$Service
    )

    if (Test-DockerContainerExists -Docker $Docker -Container $Service.Container) {
        if (Test-DockerContainerRunning -Docker $Docker -Container $Service.Container) {
            Write-Host "[infra] $($Service.Container) is already running."
        } else {
            Write-Host "[infra] Starting existing container: $($Service.Container)"
            & $Docker start $Service.Container | Out-Host
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to start Docker container: $($Service.Container)"
            }
        }
    } else {
        Ensure-DockerImage -Docker $Docker -Image $Service.Image

        $args = @("run", "-d", "--name", $Service.Container, "--restart", "unless-stopped")
        foreach ($port in @($Service.Ports)) {
            $args += @("-p", $port)
        }
        $args += $Service.Image

        Write-Host "[infra] Creating container: $($Service.Container)"
        & $Docker @args | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create Docker container: $($Service.Container)"
        }
    }

    if (-not (Wait-TcpPort -HostName $Service.Host -Port $Service.Port -TimeoutSeconds 30)) {
        throw "$($Service.Name) did not open $($Service.Host):$($Service.Port) within 30 seconds."
    }

    if (-not (Test-HttpReady -Url $Service.ReadyUrl)) {
        throw "$($Service.Name) is listening, but $($Service.ReadyUrl) did not return a healthy response."
    }

    Write-Host "[infra] $($Service.Name) ready on $($Service.Host):$($Service.Port)"
}

function Invoke-InfraUp {
    $docker = Get-DockerCommand

    foreach ($service in $InfraServices) {
        Start-InfraService -Docker $docker -Service $service
    }

    Write-Host "[infra] Local infrastructure is ready."
}

function Invoke-InfraDown {
    $docker = Get-DockerCommand

    foreach ($service in $InfraServices) {
        if (-not (Test-DockerContainerExists -Docker $docker -Container $service.Container)) {
            Write-Host "[infra] Container not found, skipping: $($service.Container)"
            continue
        }

        if (-not (Test-DockerContainerRunning -Docker $docker -Container $service.Container)) {
            Write-Host "[infra] Container already stopped: $($service.Container)"
            continue
        }

        Write-Host "[infra] Stopping container: $($service.Container)"
        & $docker stop $service.Container | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to stop Docker container: $($service.Container)"
        }
    }

    Write-Host "[infra] Local infrastructure stopped."
}

function Write-DoctorCheck {
    param(
        [string]$Name,
        [bool]$Ok,
        [string]$Message,
        [bool]$Required = $true
    )

    if ($Ok) {
        Write-Host "[doctor][ok]   $Name - $Message"
        return
    }

    if ($Required) {
        $script:DoctorFailures += 1
        Write-Host "[doctor][fail] $Name - $Message"
    } else {
        Write-Host "[doctor][warn] $Name - $Message"
    }
}

function Invoke-Doctor {
    $script:DoctorFailures = 0
    $python = $null

    try {
        $python = Get-RootVenvPython
        Write-DoctorCheck -Name "python venv" -Ok $true -Message $python
    } catch {
        Write-DoctorCheck -Name "python venv" -Ok $false -Message $_.Exception.Message
    }

    if ($python) {
        $depsOk = ((Invoke-NativeQuiet -Command $python -Arguments @("-c", "import neo4j, falkordb, qdrant_client, requests")) -eq 0)
        Write-DoctorCheck -Name "python deps" -Ok $depsOk -Message "neo4j, falkordb, qdrant_client, requests"
    }

    $docker = Get-CommandPath @("docker.exe", "docker")
    $dockerReady = $false
    if ($docker) {
        Write-DoctorCheck -Name "docker cli" -Ok $true -Message $docker
        $dockerReady = ((Invoke-NativeQuiet -Command $docker -Arguments @("info")) -eq 0)
        $dockerMessage = if ($dockerReady) { "Docker daemon reachable" } else { "Docker daemon not reachable" }
        Write-DoctorCheck -Name "docker daemon" -Ok $dockerReady -Message $dockerMessage
    } else {
        Write-DoctorCheck -Name "docker cli" -Ok $false -Message "Docker not found on PATH"
    }

    foreach ($service in $InfraServices) {
        $portOpen = Test-TcpPort -HostName $service.Host -Port $service.Port -TimeoutMs 1000
        Write-DoctorCheck -Name "$($service.Name) port" -Ok $portOpen -Message "$($service.Host):$($service.Port)"

        if ($service.ReadyUrl) {
            $ready = Test-HttpReady -Url $service.ReadyUrl
            Write-DoctorCheck -Name "$($service.Name) http" -Ok $ready -Message $service.ReadyUrl
        }

        if ($dockerReady) {
            $exists = Test-DockerContainerExists -Docker $docker -Container $service.Container
            $running = $exists -and (Test-DockerContainerRunning -Docker $docker -Container $service.Container)
            Write-DoctorCheck -Name "$($service.Name) container" -Ok $running -Message $service.Container -Required $false
        }
    }

    foreach ($server in $Servers) {
        $open = Test-TcpPort -HostName "127.0.0.1" -Port $server.Port -TimeoutMs 1000
        Write-DoctorCheck -Name "$($server.Name) mcp" -Ok $open -Message "127.0.0.1:$($server.Port)" -Required $false
    }

    if ($script:DoctorFailures -gt 0) {
        throw "Doctor found $script:DoctorFailures required check(s) failing."
    }

    Write-Host "[doctor] Required checks passed."
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
    $Records | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $PidFile -Encoding UTF8
}

function Stop-SavedProcesses {
    $records = Read-PidRecords

    foreach ($record in $records) {
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
    Stop-SavedProcesses
    Stop-MarkerProcesses

    if (Test-Path -LiteralPath $PidFile) {
        Remove-Item -LiteralPath $PidFile -Force
    }

    Write-Host "[stop] MCP stop complete."
}

function Get-DefaultGraphEnvBash {
    param([string]$ServerName)

    $scopedProvider = if ($ServerName -eq "doc-tiny") { "DOC_GRAPH_PROVIDER" } else { "CODE_GRAPH_PROVIDER" }
    return @(
        'export GRAPH_PROVIDER="${GRAPH_PROVIDER:-falkordb}"',
        ('export ' + $scopedProvider + '="${' + $scopedProvider + ':-${GRAPH_PROVIDER}}"'),
        'export FALKORDB_HOST="${FALKORDB_HOST:-localhost}"',
        'export FALKORDB_PORT="${FALKORDB_PORT:-6379}"',
        'export FALKORDB_URI="${FALKORDB_URI:-redis://${FALKORDB_HOST}:${FALKORDB_PORT}}"',
        'export FALKORDB_GRAPH="${FALKORDB_GRAPH:-neo4j}"',
        'export FALKORDB_PASSWORD="${FALKORDB_PASSWORD:-}"'
    ) -join "; "
}

function Get-DefaultGraphEnvPowerShell {
    param([string]$ServerName)

    $scopedProvider = if ($ServerName -eq "doc-tiny") { "DOC_GRAPH_PROVIDER" } else { "CODE_GRAPH_PROVIDER" }
    return @"
if (-not `$env:GRAPH_PROVIDER) { `$env:GRAPH_PROVIDER = 'falkordb' }
if (-not [Environment]::GetEnvironmentVariable('$scopedProvider', 'Process')) { [Environment]::SetEnvironmentVariable('$scopedProvider', `$env:GRAPH_PROVIDER, 'Process') }
if (-not `$env:FALKORDB_HOST) { `$env:FALKORDB_HOST = 'localhost' }
if (-not `$env:FALKORDB_PORT) { `$env:FALKORDB_PORT = '6379' }
if (-not `$env:FALKORDB_URI) { `$env:FALKORDB_URI = "redis://`$(`$env:FALKORDB_HOST):`$(`$env:FALKORDB_PORT)" }
if (-not `$env:FALKORDB_GRAPH) { `$env:FALKORDB_GRAPH = 'neo4j' }
if (-not `$env:FALKORDB_PASSWORD) { `$env:FALKORDB_PASSWORD = '' }
"@
}

function Invoke-Start {
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    Invoke-Stop

    $runner = Get-ShellRunner
    $records = @()

    foreach ($server in $Servers) {
        if (-not (Test-Path -LiteralPath $server.Script)) {
            throw "MCP script not found: $($server.Script)"
        }

        $title = "MCP $($server.Name) :$($server.Port)"
        $titleArg = Quote-PowerShell $title
        $scriptArg = Quote-PowerShell $server.Script

        if ($runner.Kind -eq "git-bash") {
            $workDir = Quote-Bash (Convert-ToShellPath -Path $server.WorkDir -Kind $runner.Kind)
            $rootVenv = Quote-Bash (Convert-ToShellPath -Path (Get-RootVenvDir) -Kind $runner.Kind)
            $scriptName = Quote-Bash (Split-Path -Leaf $server.Script)
            $graphEnv = Get-DefaultGraphEnvBash -ServerName $server.Name
            $bashCommand = "if [ -f $rootVenv/bin/activate ]; then source $rootVenv/bin/activate; elif [ -f $rootVenv/Scripts/activate ]; then source $rootVenv/Scripts/activate; fi; $graphEnv cd $workDir && bash ./$scriptName"
            $runnerArg = Quote-PowerShell $runner.Command
            $bashCommandArg = Quote-PowerShell $bashCommand
            $invokeLine = "& $runnerArg -lc $bashCommandArg"
        } else {
            $pythonArg = Quote-PowerShell (Get-RootVenvPython)
            $workDirArg = Quote-PowerShell $server.WorkDir
            $pythonScriptArg = Quote-PowerShell $server.PythonScript
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

        $records += [pscustomobject]@{
            Name = $server.Name
            Pid = $proc.Id
            Script = $server.Script
            Port = $server.Port
            StartedAt = $proc.StartTime.ToUniversalTime().ToString("o")
        }

        Write-Host "[start] Started $($server.Name) in terminal PID $($proc.Id)"
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
        "doctor" { Invoke-Doctor }
        "start" { Invoke-Start }
        "stop" { Invoke-Stop }
        default { Write-Usage }
    }
} catch {
    Write-Host "[error] $($_.Exception.Message)"
    exit 1
}
