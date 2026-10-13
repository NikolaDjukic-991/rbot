$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:RBOT_HOME = $ProjectRoot

$ConfigPath = Join-Path $ProjectRoot "config.cfg"
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$BotPath = Join-Path $ProjectRoot "rbot.py"

if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    Write-Error "config.cfg is missing. Copy config_example.cfg to config.cfg and configure the bot token first."
    exit 1
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    Write-Error "The project virtual environment is missing. Run install.ps1 first."
    exit 1
}

Write-Host "Starting the bot. Press Ctrl+C to stop it."
& $PythonPath $BotPath
exit $LASTEXITCODE
