"""Persistent per-guild command text-channel settings."""

import json
import logging
import os
import threading
from pathlib import Path

from discord.ext import commands


class CommandChannelStore:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._guild_channels = {}

    def load(self):
        with self._lock:
            try:
                values = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                self._guild_channels = {}
                return
            except (OSError, UnicodeError, json.JSONDecodeError):
                logging.exception("Could not load configured command channels from %s", self.path)
                self._guild_channels = {}
                return

            if not isinstance(values, dict):
                logging.error("Ignoring invalid command channel settings in %s", self.path)
                self._guild_channels = {}
                return
            self._guild_channels = {
                str(guild_id): int(channel_id)
                for guild_id, channel_id in values.items()
                if str(guild_id).isdigit() and str(channel_id).isdigit()
            }

    def get(self, guild_id):
        with self._lock:
            return self._guild_channels.get(str(guild_id))

    def set(self, guild_id, channel_id):
        guild_key = str(guild_id)
        with self._lock:
            updated = dict(self._guild_channels)
            if channel_id is None:
                updated.pop(guild_key, None)
            else:
                updated[guild_key] = int(channel_id)

            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.path.with_name(self.path.name + ".tmp")
            try:
                temporary_path.write_text(
                    json.dumps(updated, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                os.replace(str(temporary_path), str(self.path))
            except OSError:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
                raise
            self._guild_channels = updated


class RestrictedCommandChannel(commands.CheckFailure):
    """Raised to silently ignore commands sent outside the configured channel."""
