#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"
export RBOT_HOME="$PROJECT_ROOT"

CONFIG_PATH="$PROJECT_ROOT/config.cfg"
PYTHON_PATH="$PROJECT_ROOT/.venv/bin/python"
BOT_PATH="$PROJECT_ROOT/rbot.py"

if [[ ! -f "$CONFIG_PATH" ]]; then
    printf 'Startup error: config.cfg is missing. Copy config_example.cfg to config.cfg and configure the bot token first.\n' >&2
    exit 1
fi
if [[ ! -x "$PYTHON_PATH" ]]; then
    printf 'Startup error: the project virtual environment is missing. Run install.sh first.\n' >&2
    exit 1
fi

printf 'Starting the bot. Press Ctrl+C to stop it.\n'
exec "$PYTHON_PATH" "$BOT_PATH"
