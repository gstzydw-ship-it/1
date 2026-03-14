param(
    [int]$Port = 8765,
    [switch]$NoBrowser,
    [switch]$Foreground,
    [switch]$ForceRestart
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$serverScript = Join-Path $projectRoot "scripts\openclaw_control_server.py"
$runtimeDir = Join-Path $projectRoot ".runtime\openclaw-ui"
$stdoutLog = Join-Path $runtimeDir "stdout.log"
$stderrLog = Join-Path $runtimeDir "stderr.log"
$url = "http://127.0.0.1:$Port/"

function Get-Runner {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @{
            FilePath = "py"
            Args = @("-3", $serverScript, "--port", "$Port")
            BrowserArgs = @("-3", "-c", "import webbrowser; webbrowser.open(r'$url')")
        }
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @{
            FilePath = "python"
            Args = @($serverScript, "--port", "$Port")
            BrowserArgs = @("-c", "import webbrowser; webbrowser.open(r'$url')")
        }
    }

    throw "Python was not found in PATH."
}

function Get-ServerConnection {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Wait-ForServer {
    param(
        [int]$TimeoutSeconds = 12
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 350
        $conn = Get-ServerConnection
        if ($conn) {
            return $conn
        }
    }

    return $null
}

function Open-UiInBrowser {
    param(
        [hashtable]$Runner
    )

    if ($NoBrowser) {
        return
    }

    & $Runner.FilePath @($Runner.BrowserArgs) | Out-Null
}

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

$existing = Get-ServerConnection
if ($existing -and $ForceRestart) {
    Stop-Process -Id $existing.OwningProcess -Force
    Start-Sleep -Seconds 1
    $existing = $null
}

$runner = Get-Runner

if ($Foreground) {
    if (-not $NoBrowser) {
        Start-Sleep -Milliseconds 300
        Open-UiInBrowser -Runner $runner
    }
    & $runner.FilePath @($runner.Args)
    exit $LASTEXITCODE
}

if (-not $existing) {
    if (Test-Path $stdoutLog) {
        Remove-Item $stdoutLog -Force
    }
    if (Test-Path $stderrLog) {
        Remove-Item $stderrLog -Force
    }

    $process = Start-Process `
        -FilePath $runner.FilePath `
        -ArgumentList $runner.Args `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -PassThru

    $existing = Wait-ForServer
    if (-not $existing) {
        $stderrText = ""
        if (Test-Path $stderrLog) {
            $stderrText = Get-Content -Path $stderrLog -Tail 40 | Out-String
        }
        throw "OpenClaw local console failed to start on $url`n$stderrText"
    }

    Write-Host "OpenClaw local console started on $url (PID: $($process.Id))"
} else {
    Write-Host "OpenClaw local console is already running on $url (PID: $($existing.OwningProcess))"
}

Open-UiInBrowser -Runner $runner
