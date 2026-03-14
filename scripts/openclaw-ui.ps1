param(
    [int]$Port = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

if (Get-Command py -ErrorAction SilentlyContinue) {
    $runner = "py"
    $args = @("-3", "scripts/openclaw_control_server.py", "--port", "$Port")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $runner = "python"
    $args = @("scripts/openclaw_control_server.py", "--port", "$Port")
} else {
    throw "Python was not found in PATH."
}

if (-not $NoBrowser) {
    $args += "--open-browser"
}

& $runner @args
