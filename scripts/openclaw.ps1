param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$OpenClawArgs
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$repoRootForWsl = $repoRoot -replace "\\", "/"
$wslRepo = (wsl -d Ubuntu -- wslpath -a "$repoRootForWsl").Trim()
if (-not $wslRepo) {
    throw "Unable to resolve WSL path for repo root."
}

$quotedArgs = @()
foreach ($item in $OpenClawArgs) {
    $quotedArgs += "'" + ($item -replace "'", "'""'""'") + "'"
}
$argText = ($quotedArgs -join " ").Trim()

$command = "cd '$wslRepo' && '$wslRepo/scripts/openclaw-wsl.sh'"
if ($argText) {
    $command += " $argText"
}

wsl -d Ubuntu -- bash -lc $command
exit $LASTEXITCODE
