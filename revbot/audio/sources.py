"""Audio request models, FFmpeg sources, and track extraction."""

import json
import logging
import queue
import subprocess
import sys
import threading
from urllib.parse import urlparse

import discord


class TrackInfo:
    def __init__(self, title="", url=None, duration=None):
        self.title = title
        self.requestedUrl = url
        self.duration = duration

    @classmethod
    def from_extractor_json(cls, data):
        formats = data.get("requested_formats") or data.get("formats") or []
        audio_format = next(
            (fmt for fmt in formats if "audio only" in fmt.get("format", "").lower()),
            None,
        )
        if audio_format is None and formats:
            audio_format = formats[-1]
        url = (audio_format or {}).get("url") or data.get("url")
        if not url:
            raise ValueError("The video extractor did not return an audio URL")
        return cls(data.get("title", "Untitled"), url, data.get("duration"))


class AudioRequest:
    """A queued audio item. Its FFmpeg process starts only when it reaches playback."""

    def __init__(self, source, requester, title="Audio", ffmpeg_executable="ffmpeg"):
        self.source = str(source)
        self.ffmpeg_executable = ffmpeg_executable
        self.requester = requester
        self.trackInfo = TrackInfo(title=title, url=self.source)

    def create_source(self):
        parsed = urlparse(self.source)
        before_options = None
        if parsed.scheme in ("http", "https"):
            before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
        return discord.FFmpegPCMAudio(
            self.source,
            executable=self.ffmpeg_executable,
            before_options=before_options,
            options="-vn",
        )


class BufferedPCMSource:
    """Decode on a worker so two streaming inputs do not block each other."""

    def __init__(self, source):
        self.source = source
        self.frames = queue.Queue(maxsize=8)
        self.stopped = threading.Event()
        self.worker = threading.Thread(target=self._produce, daemon=True)
        self.worker.start()

    def _put(self, frame):
        while not self.stopped.is_set():
            try:
                self.frames.put(frame, timeout=0.1)
                return
            except queue.Full:
                continue

    def _produce(self):
        try:
            while not self.stopped.is_set():
                frame = self.source.read()
                if not frame:
                    self._put(None)
                    return
                self._put(frame)
        except Exception:
            if not self.stopped.is_set():
                logging.exception("Error decoding audio")
                self._put(None)

    def read_frame(self):
        if self.stopped.is_set():
            return None
        return self.frames.get()

    def cleanup(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        self.source.cleanup()
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                break
        if self.worker is not threading.current_thread():
            self.worker.join(timeout=0.5)


def extract_track_info(link, home_directory, python_executable=None):
    # Run yt-dlp as a module using this Python interpreter on both Windows and Linux.
    result = subprocess.run(
        [python_executable or sys.executable, "-m", "yt_dlp", "--no-playlist", "-j", link],
        cwd=str(home_directory),
        capture_output=True,
        text=True,
    )
    if result.returncode:
        details = (result.stderr or result.stdout or "yt-dlp exited with status {}".format(result.returncode)).strip()
        raise ValueError("Audio extractor failed: " + details[-1000:])
    return TrackInfo.from_extractor_json(json.loads(result.stdout))