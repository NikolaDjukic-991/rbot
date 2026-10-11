$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:RBOT_HOME = $ProjectRoot

function Stop-Setup([string]$Message) {
    Write-Error $Message
    exit 1
}

$ConfigPath = Join-Path $ProjectRoot "config.cfg"
$RequirementsPath = Join-Path $ProjectRoot "requirements.txt"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    Stop-Setup "config.cfg is missing. Copy config_example.cfg to config.cfg and set TOKEN before running this script."
}
if (-not (Test-Path -LiteralPath $RequirementsPath -PathType Leaf)) {
    Stop-Setup "requirements.txt is missing."
}

$Config = @{}
foreach ($Line in Get-Content -LiteralPath $ConfigPath) {
    if ($Line -match '^\s*#' -or $Line -notmatch ':') { continue }
    $Parts = $Line -split ':', 2
    $Config[$Parts[0].Trim()] = $Parts[1].Trim()
}

$Token = [Environment]::ExpandEnvironmentVariables($Config["TOKEN"])
if ([string]::IsNullOrWhiteSpace($Token) -or $Token -match '^<.*>$') {
    Stop-Setup "Set a real Discord bot token in config.cfg before running this script."
}

$Ffmpeg = $Config["FFMPEG"]
if ([string]::IsNullOrWhiteSpace($Ffmpeg)) { $Ffmpeg = "ffmpeg" }
$Ffmpeg = [Environment]::ExpandEnvironmentVariables($Ffmpeg)
if (-not (Get-Command -Name $Ffmpeg -ErrorAction SilentlyContinue) -and
    -not (Test-Path -LiteralPath $Ffmpeg -PathType Leaf)) {
    Stop-Setup "FFmpeg was not found ('$Ffmpeg'). Install FFmpeg and add it to PATH, or set FFMPEG in config.cfg to its executable path."
}

$PythonLauncher = Get-Command -Name "py" -CommandType Application -ErrorAction SilentlyContinue
if ($PythonLauncher) {
    $PythonExe = & $PythonLauncher.Source -3 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -ne 0) { $PythonExe = $null }
} else {
    $PythonExe = $null
}
if (-not $PythonExe) {
    $PythonCommand = Get-Command -Name "python" -CommandType Application -ErrorAction SilentlyContinue
    if ($PythonCommand) { $PythonExe = $PythonCommand.Source }
}
if (-not $PythonExe) {
    Stop-Setup "Python 3.8 or newer was not found. Install Python and enable the py launcher or add python.exe to PATH."
}

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    Write-Host "Creating the project virtual environment..."
    & $PythonExe -m venv (Join-Path $ProjectRoot ".venv")
    if ($LASTEXITCODE -ne 0) { Stop-Setup "Could not create .venv. Check that Python's venv support is installed." }
}

$PythonVersion = & $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or [version]$PythonVersion -lt [version]"3.8") {
    Stop-Setup "The project virtual environment must use Python 3.8 or newer (found '$PythonVersion'). Remove .venv and rerun this script with a supported Python."
}

Write-Host "Checking and installing Python dependencies..."
& $VenvPython -m pip uninstall -y discord-ext-voice-recv
if ($LASTEXITCODE -ne 0) { Stop-Setup "Could not remove the old voice receive extension." }
& $VenvPython -m pip install -r $RequirementsPath
if ($LASTEXITCODE -ne 0) { Stop-Setup "Dependency installation failed." }

Write-Host "Starting the bot. Press Ctrl+C to stop it."
& $VenvPython (Join-Path $ProjectRoot "rbot.py")
exit $LASTEXITCODE
