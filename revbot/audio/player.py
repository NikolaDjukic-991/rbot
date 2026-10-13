"""Per-connection playback state and queue controls."""

import logging
import threading
from collections import deque

from discord.ext import voice_recv

from .capture import RecentVoiceSink
from .mixer import AudioMixer

MAX_MUSIC_QUEUE_ITEMS = 25
MAX_SOUNDBOARD_QUEUE_ITEMS = 20


class Player:
    def __init__(self, voice_client, event_loop, music_queue_limit=MAX_MUSIC_QUEUE_ITEMS, sound_queue_limit=MAX_SOUNDBOARD_QUEUE_ITEMS):
        self.voiceClient = voice_client
        self._loop = event_loop
        self._music_queue_limit = music_queue_limit
        self._sound_queue_limit = sound_queue_limit
        self.music_queue = deque()
        self.sound_queue = deque()
        self._lock = threading.RLock()
        self._mixer = None
        self._music_paused = False
        self._volume_percent = 100
        self.capture_sink = RecentVoiceSink()
        self.start_capture()

    def start_capture(self):
        if not isinstance(self.voiceClient, voice_recv.VoiceRecvClient):
            raise RuntimeError("Voice capture requires a receive-enabled voice connection")
        if not self.voiceClient.is_listening():
            self.voiceClient.listen(self.capture_sink)

    def capture_recent_voice(self):
        return self.capture_sink.snapshot()

    def _has_pending_audio(self):
        return bool(self.music_queue or self.sound_queue)

    def _start_if_needed(self):
        with self._lock:
            if self._mixer is not None or not self._has_pending_audio():
                return
            mixer = AudioMixer(self.music_queue, self.sound_queue, self._lock)
            mixer.set_master_volume(getattr(self, "_volume_percent", 100) / 100)
            if self._music_paused:
                mixer.pause_music()
            self._mixer = mixer
            try:
                self.voiceClient.play(mixer, after=lambda error: self._audio_finished(mixer, error))
            except Exception:
                self._mixer = None
                mixer.cleanup()
                raise

    def _audio_finished(self, mixer, error):
        if error:
            logging.error("Voice playback stopped with an error: %s", error)
        try:
            self._loop.call_soon_threadsafe(self._handle_audio_finished, mixer)
        except RuntimeError:
            logging.info("Event loop closed while audio playback was finishing")

    def _handle_audio_finished(self, mixer):
        with self._lock:
            if self._mixer is mixer:
                self._mixer = None
        self._start_if_needed()

    def queue_music(self, request):
        with self._lock:
            if len(self.music_queue) >= self._music_queue_limit:
                raise ValueError("The music queue is full ({} tracks maximum).".format(self._music_queue_limit))
            starts_now = not self.music_queue and (
                self._mixer is None or self._mixer.music_source is None
            )
            self.music_queue.append(request)
        self._start_if_needed()
        return starts_now

    def queue_sound(self, request):
        with self._lock:
            if len(self.sound_queue) >= self._sound_queue_limit:
                raise ValueError("The soundboard queue is full ({} clips maximum).".format(self._sound_queue_limit))
            self.sound_queue.append(request)
        self._start_if_needed()

    def queued_requests(self):
        with self._lock:
            return list(self.music_queue), list(self.sound_queue)

    def clear_queued_requests(self):
        with self._lock:
            music_count, sound_count = len(self.music_queue), len(self.sound_queue)
            self.music_queue.clear()
            self.sound_queue.clear()
        return music_count, sound_count

    def remove_queued_music(self, position):
        with self._lock:
            if position < 1 or position > len(self.music_queue):
                return None
            request = self.music_queue[position - 1]
            del self.music_queue[position - 1]
            return request

    def getVolume(self):
        with self._lock:
            return getattr(self, "_volume_percent", 100)

    def setVolume(self, percent):
        if percent < 0 or percent > 200:
            raise ValueError("Volume must be between 0 and 200.")
        with self._lock:
            self._volume_percent = percent
            if self._mixer is not None:
                self._mixer.set_master_volume(percent / 100)
        return percent

    def nowPlaying(self):
        with self._lock:
            mixer = self._mixer
            paused = self._music_paused
        if mixer is None:
            return None, None, paused
        music, sound = mixer.current_requests()
        return music, sound, paused

    def isPlaying(self):
        return self._mixer is not None

    def getPlayerChannel(self):
        return self.voiceClient.channel

    def pausePlayer(self):
        with self._lock:
            if not self.music_queue and (self._mixer is None or self._mixer.music_source is None):
                return
            self._music_paused = True
            if self._mixer is not None:
                self._mixer.pause_music()

    def resumePlayer(self):
        with self._lock:
            self._music_paused = False
            if self._mixer is not None:
                self._mixer.resume_music()

    def skipRequest(self):
        with self._lock:
            mixer = self._mixer
            if mixer is not None:
                mixer.skip_music()

    def stopMusic(self):
        with self._lock:
            self.music_queue.clear()
            self._music_paused = False
            mixer = self._mixer
            if mixer is not None:
                mixer.resume_music()
                mixer.skip_music()

    def stopAll(self):
        with self._lock:
            self.music_queue.clear()
            self.sound_queue.clear()
            mixer = self._mixer
            if mixer is not None:
                mixer.skip_music()
                mixer.skip_sound()
        if self.voiceClient.is_playing() or self.voiceClient.is_paused():
            if hasattr(self.voiceClient, "stop_playing"):
                self.voiceClient.stop_playing()
            else:
                self.voiceClient.stop()

    def stop(self):
        """Backward-compatible alias for stopping all playback."""
        self.stopAll()

    async def disconnect(self):
        self.stopAll()
        if isinstance(self.voiceClient, voice_recv.VoiceRecvClient):
            self.voiceClient.stop_listening()
        await self.voiceClient.disconnect()