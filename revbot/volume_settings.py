"""Persistent per-guild playback volume settings."""

import json
import logging
import os
import threading
from pathlib import Path


class GuildVolumeStore:
    DEFAULT_VOLUME = 100
    MIN_VOLUME = 0
    MAX_VOLUME = 200

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._guild_volumes = {}

    @classmethod
    def _valid_volume(cls, value):
        return isinstance(value, int) and not isinstance(value, bool) and cls.MIN_VOLUME <= value <= cls.MAX_VOLUME

    def load(self):
        with self._lock:
            try:
                values = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                self._guild_volumes = {}
                return
            except (OSError, UnicodeError, json.JSONDecodeError):
                logging.exception("Could not load guild volume settings from %s", self.path)
                self._guild_volumes = {}
                return

            if not isinstance(values, dict):
                logging.error("Ignoring invalid guild volume settings in %s", self.path)
                self._guild_volumes = {}
                return
            self._guild_volumes = {
                str(guild_id): volume
                for guild_id, volume in values.items()
                if str(guild_id).isdigit() and self._valid_volume(volume)
            }

    def get(self, guild_id):
        with self._lock:
            return self._guild_volumes.get(str(guild_id), self.DEFAULT_VOLUME)

    def set(self, guild_id, volume):
        if not self._valid_volume(volume):
            raise ValueError("Volume must be between 0 and 200.")
        guild_key = str(guild_id)
        if not guild_key.isdigit():
            raise ValueError("Guild ID must be numeric.")

        with self._lock:
            updated = dict(self._guild_volumes)
            updated[guild_key] = volume
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
            self._guild_volumes = updated
