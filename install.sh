#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"
export RBOT_HOME="$PROJECT_ROOT"

fail() {
    printf 'Setup error: %s\n' "$1" >&2
    exit 1
}

config_value() {
    local key="$1"
    awk -F: -v wanted="$key" '$1 == wanted { sub(/^[^:]*:/, ""); sub(/^[[:space:]]+/, ""); sub(/[[:space:]]+$/, ""); print; exit }' config.cfg
}

[[ -f config.cfg ]] || fail "config.cfg is missing. Copy config_example.cfg to config.cfg and set TOKEN before running this script."
[[ -f requirements.txt ]] || fail "requirements.txt is missing."

token="$(config_value TOKEN)"
case "$token" in
    ""|"<"*">") fail "Set a real Discord bot token in config.cfg before running this script." ;;
esac

ffmpeg="$(config_value FFMPEG)"
[[ -n "$ffmpeg" ]] || ffmpeg="ffmpeg"
if ! command -v "$ffmpeg" >/dev/null 2>&1 && [[ ! -x "$ffmpeg" ]]; then
    fail "FFmpeg was not found ('$ffmpeg'). Install FFmpeg and add it to PATH, or set FFMPEG in config.cfg to its executable path."
fi

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        version="$($candidate -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
        if [[ "$version" =~ ^[0-9]+\.[0-9]+$ ]] && (( ${version%%.*} > 3 || (${version%%.*} == 3 && ${version#*.} >= 8) )); then
            PYTHON="$(command -v "$candidate")"
            break
        fi
    fi
done
[[ -n "$PYTHON" ]] || fail "Python 3.8 or newer was not found. Install Python 3 and its venv support."

VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
    printf 'Creating the project virtual environment...\n'
    "$PYTHON" -m venv "$PROJECT_ROOT/.venv" || fail "Could not create .venv. Install the Python venv package (often python3-venv) and rerun."
fi

venv_version="$("$VENV_PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ ! "$venv_version" =~ ^[0-9]+\.[0-9]+$ ]] || (( ${venv_version%%.*} < 3 || (${venv_version%%.*} == 3 && ${venv_version#*.} < 8) )); then
    fail "The project virtual environment must use Python 3.8 or newer (found '$venv_version'). Remove .venv and rerun with a supported Python."
fi

printf 'Checking and installing Python dependencies...\n'
"$VENV_PYTHON" -m pip uninstall -y discord-ext-voice-recv || fail "Could not remove the old voice receive extension."
"$VENV_PYTHON" -m pip install -r "$PROJECT_ROOT/requirements.txt" || fail "Dependency installation failed."

printf 'Starting the bot. Press Ctrl+C to stop it.\n'
exec "$VENV_PYTHON" "$PROJECT_ROOT/rbot.py"
