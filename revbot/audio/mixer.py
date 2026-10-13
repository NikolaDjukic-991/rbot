"""Thread-safe PCM mixing for music and soundboard playback."""

import array
import logging
import sys

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
        while not self.closed:
            with self.lock:
                source = getattr(self, source_attr)
                skip_attr = "skip_{}_requested".format(kind)
                if getattr(self, skip_attr):
                    setattr(self, skip_attr, False)
                    setattr(self, source_attr, None)
                    setattr(self, "{}_request".format(kind), None)
                    discarded = source
                    source = None
                else:
                    discarded = None
                if source is None and queue and not (kind == "music" and self.music_paused):
                    request = queue.popleft()
                    try:
                        source = BufferedPCMSource(request.create_source())
                    except Exception:
                        logging.exception("Could not start queued %s audio", kind)
                        source = None
                    setattr(self, source_attr, source)
                    if source is not None:
                        setattr(self, "{}_request".format(kind), request)
            self._cleanup(discarded)
            if kind == "music":
                with self.lock:
                    if self.music_paused:
                        return None
            if source is None:
                with self.lock:
                    if kind == "music" and self.music_paused:
                        return None
                    if not queue:
                        return None
                continue
            try:
                frame = source.read_frame()
            except Exception:
                logging.exception("Error reading %s audio", kind)
                frame = b""
            if frame:
                return frame[: self.FRAME_BYTES]
            with self.lock:
                if getattr(self, source_attr) is source:
                    setattr(self, source_attr, None)
                    setattr(self, "{}_request".format(kind), None)
            self._cleanup(source)
        return None

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