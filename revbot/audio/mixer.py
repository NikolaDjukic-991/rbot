"""Thread-safe PCM mixing for music and soundboard playback."""

import array
import logging
import sys
import threading

import discord

from .sources import BufferedPCMSource


class AudioMixer(discord.AudioSource):
    """Mix one queued music source and one queued soundboard source into PCM."""

    FRAME_SAMPLES = 960
    CHANNELS = 2
    FRAME_BYTES = FRAME_SAMPLES * CHANNELS * 2
    MUSIC_GAIN = 0.72
    SOUNDBOARD_GAIN = 0.72

    def __init__(self, music_queue, sound_queue, lock):
        self.music_queue = music_queue
        self.sound_queue = sound_queue
        self.lock = lock
        self.music_source = None
        self.sound_source = None
        self.music_request = None
        self.sound_request = None
        self.music_starting = False
        self.sound_starting = False
        self.music_generation = 0
        self.sound_generation = 0
        self.master_volume = 1.0
        self.skip_music_requested = False
        self.skip_sound_requested = False
        self.music_paused = False
        self.closed = False

    @staticmethod
    def _cleanup(source):
        if source is not None:
            try:
                source.cleanup()
            except Exception:
                logging.exception("Failed to clean up an audio source")

    def _start_source(self, kind, request, generation):
        """Create FFmpeg and its decode buffer away from Discord's audio thread."""
        try:
            source = BufferedPCMSource(request.create_source())
        except Exception:
            logging.exception("Could not start queued %s audio", kind)
            source = None

        source_attr = "{}_source".format(kind)
        request_attr = "{}_request".format(kind)
        starting_attr = "{}_starting".format(kind)
        generation_attr = "{}_generation".format(kind)
        with self.lock:
            valid = (
                not self.closed
                and getattr(self, generation_attr) == generation
                and getattr(self, request_attr) is request
                and getattr(self, starting_attr)
            )
            if valid:
                setattr(self, source_attr, source)
                setattr(self, starting_attr, False)
                if source is None:
                    setattr(self, request_attr, None)
            else:
                source_to_cleanup = source
                source = None
        if not valid:
            self._cleanup(source_to_cleanup)

    def skip_music(self):
        with self.lock:
            self.skip_music_requested = True

    def skip_sound(self):
        with self.lock:
            self.skip_sound_requested = True

    def pause_music(self):
        with self.lock:
            self.music_paused = True

    def resume_music(self):
        with self.lock:
            self.music_paused = False

    def set_master_volume(self, volume):
        with self.lock:
            self.master_volume = volume

    def current_requests(self):
        with self.lock:
            return self.music_request, self.sound_request

    def _read_next_frame(self, kind):
        queue = self.music_queue if kind == "music" else self.sound_queue
        source_attr = "music_source" if kind == "music" else "sound_source"
        request_attr = "{}_request".format(kind)
        starting_attr = "{}_starting".format(kind)
        generation_attr = "{}_generation".format(kind)
        with self.lock:
            if self.closed:
                return None
            source = getattr(self, source_attr)
            skip_attr = "skip_{}_requested".format(kind)
            discarded = None
            if getattr(self, skip_attr):
                setattr(self, skip_attr, False)
                setattr(self, generation_attr, getattr(self, generation_attr) + 1)
                setattr(self, source_attr, None)
                setattr(self, request_attr, None)
                setattr(self, starting_attr, False)
                discarded = source
                source = None

            paused = kind == "music" and self.music_paused
            if not paused and source is None and not getattr(self, starting_attr) and queue:
                request = queue.popleft()
                setattr(self, request_attr, request)
                setattr(self, starting_attr, True)
                generation = getattr(self, generation_attr)
                threading.Thread(
                    target=self._start_source,
                    args=(kind, request, generation),
                    name="{}-audio-start".format(kind),
                    daemon=True,
                ).start()
                starting = True
            else:
                starting = getattr(self, starting_attr)
        self._cleanup(discarded)
        if paused:
            return None
        if source is None:
            if starting:
                return b""
            return None

        try:
            frame = source.read_frame(block=False)
        except Exception:
            logging.exception("Error reading %s audio", kind)
            frame = None
        if frame:
            return frame[: self.FRAME_BYTES]
        if frame == b"":
            return b""

        with self.lock:
            if getattr(self, source_attr) is source:
                setattr(self, source_attr, None)
                setattr(self, request_attr, None)
                setattr(self, generation_attr, getattr(self, generation_attr) + 1)
                if queue and not (kind == "music" and self.music_paused):
                    next_request = queue.popleft()
                    setattr(self, request_attr, next_request)
                    setattr(self, starting_attr, True)
                    generation = getattr(self, generation_attr)
                    threading.Thread(
                        target=self._start_source,
                        args=(kind, next_request, generation),
                        name="{}-audio-start".format(kind),
                        daemon=True,
                    ).start()
        self._cleanup(source)
        return b"" if getattr(self, starting_attr) else None

    @staticmethod
    def _as_samples(frame):
        samples = array.array("h")
        samples.frombytes(frame[: len(frame) - (len(frame) % 2)])
        if sys.byteorder != "little":
            samples.byteswap()
        return samples

    def read(self):
        if self.closed:
            return b""
        music = self._read_next_frame("music")
        sound = self._read_next_frame("sound")
        if music is None and sound is None:
            with self.lock:
                paused_music_pending = self.music_paused and (
                    self.music_source is not None or bool(self.music_queue)
                )
            if paused_music_pending:
                return bytes(self.FRAME_BYTES)
            return b""

        music_samples = self._as_samples(music or b"")
        sound_samples = self._as_samples(sound or b"")
        with self.lock:
            master_volume = self.master_volume
        mixed = bytearray(self.FRAME_BYTES)
        for index in range(self.FRAME_SAMPLES * self.CHANNELS):
            music_sample = music_samples[index] if index < len(music_samples) else 0
            sound_sample = sound_samples[index] if index < len(sound_samples) else 0
            sample = int((music_sample * self.MUSIC_GAIN + sound_sample * self.SOUNDBOARD_GAIN) * master_volume)
            sample = max(-32768, min(32767, sample))
            mixed[index * 2] = sample & 0xFF
            mixed[index * 2 + 1] = (sample >> 8) & 0xFF
        return bytes(mixed)

    def is_opus(self):
        return False

    def cleanup(self):
        self.closed = True
        with self.lock:
            music, sound = self.music_source, self.sound_source
            self.music_source = None
            self.sound_source = None
            self.music_request = None
            self.sound_request = None
        self._cleanup(music)
        self._cleanup(sound)
