"""Configuration loading and startup validation for the bot."""

import os
from dataclasses import dataclass
from pathlib import Path
import shutil


class ConfigurationError(ValueError):
    """Raised when required bot configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    bot_home: Path
    token: str
    sample_dir: Path
    temp_dir: Path
    ffmpeg_executable: str


def expand_config_path(value, bot_home, environ=None):
    """Expand project-root and environment variables into an absolute path."""
    expanded = value.replace("${RBOT_HOME}", str(bot_home)).replace("$RBOT_HOME", str(bot_home))
    expanded = expand_environment_variables(expanded, environ)
    return Path(expanded).expanduser().resolve()


def expand_environment_variables(value, environ=None):
    """Expand common environment-variable syntaxes on both supported OSes."""
    if environ is None:
        return os.path.expandvars(value)
    expanded = value
    for name, replacement in environ.items():
        expanded = expanded.replace("${" + name + "}", replacement)
        expanded = expanded.replace("$" + name, replacement)
        expanded = expanded.replace("%" + name + "%", replacement)
    return expanded


def is_configured_token(token):
    return bool(token and not (token.startswith("<") and token.endswith(">")))


def is_ffmpeg_available(executable):
    return shutil.which(executable) is not None or Path(executable).expanduser().is_file()


def load_settings(bot_home, environ=None):
    """Read config.cfg, validate runtime requirements, and prepare data folders."""
    environ = os.environ if environ is None else environ
    bot_home = Path(bot_home).expanduser().resolve()
    values = {}
    config_path = bot_home / "config.cfg"
    with config_path.open("r", encoding="utf-8") as config_file:
        for line in config_file:
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()

    token = environ.get("DISCORD_TOKEN") or expand_environment_variables(values.get("TOKEN", ""), environ)
    if not is_configured_token(token):
        raise ConfigurationError("Configure TOKEN or the DISCORD_TOKEN environment variable before starting the bot")

    sample_dir = expand_config_path(values.get("SAMPLE_DIR", "${RBOT_HOME}/../Samples"), bot_home, environ)
    temp_dir = expand_config_path(values.get("TEMP_DIR", "${RBOT_HOME}/../temp"), bot_home, environ)
    ffmpeg_executable = expand_environment_variables(values.get("FFMPEG", "ffmpeg"), environ)

    temp_dir.mkdir(parents=True, exist_ok=True)
    sample_dir.mkdir(parents=True, exist_ok=True)
    return Settings(bot_home, token, sample_dir, temp_dir, ffmpeg_executable)


def validate_settings(settings, ffmpeg_check=None):
    """Check runtime executables after settings have been loaded and exposed."""
    ffmpeg_check = is_ffmpeg_available if ffmpeg_check is None else ffmpeg_check
    if not ffmpeg_check(settings.ffmpeg_executable):
        raise ConfigurationError("FFmpeg was not found: {}".format(settings.ffmpeg_executable))
