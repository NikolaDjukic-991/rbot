"""Voice capture buffering, encoding, and automatic capture storage."""

import array
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

from discord.ext import voice_recv


class RecentVoiceSink(voice_recv.AudioSink):
    """Keep a short, bounded rolling buffer of mixed voice-channel PCM."""

    SAMPLE_RATE = 48000
    CHANNELS = 2
    SAMPLE_WIDTH = 2
    FRAME_SAMPLES = 960
    FRAME_BYTES = FRAME_SAMPLES * CHANNELS * SAMPLE_WIDTH
    FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE
    BUFFER_SECONDS = 5
    BUFFER_FRAMES = int(BUFFER_SECONDS / FRAME_SECONDS)

    def __init__(self):
        super().__init__()
        self.started_at = time.monotonic()
        self.lock = threading.Lock()
        # Each time slot stores the sum of all speakers received in that slot.
        self.frames = {}

    def wants_opus(self):
        return False

    def write(self, user, data):
        pcm = getattr(data, "pcm", None)
        if not pcm:
            return

        samples = array.array("h")
        samples.frombytes(pcm[: len(pcm) - (len(pcm) % self.SAMPLE_WIDTH)])
        if sys.byteorder != "little":
            samples.byteswap()
        if not samples:
            return

        tick = int(time.monotonic() / self.FRAME_SECONDS)
        with self.lock:
            oldest_tick = tick - self.BUFFER_FRAMES - 2
            for expired_tick in [key for key in self.frames if key < oldest_tick]:
                del self.frames[expired_tick]

            mixed = self.frames.get(tick)
            if mixed is None:
                mixed = array.array("i", [0]) * len(samples)
                self.frames[tick] = mixed
            if len(mixed) < len(samples):
                mixed.extend([0] * (len(samples) - len(mixed)))
            for index, sample in enumerate(samples):
                mixed[index] += sample

    def snapshot(self):
        now = time.monotonic()
        if now - self.started_at < self.BUFFER_SECONDS:
            raise RuntimeError("The bot needs five seconds in the voice channel before it can capture.")

        current_tick = int(now / self.FRAME_SECONDS)
        first_tick = current_tick - self.BUFFER_FRAMES + 1
        with self.lock:
            frames = {tick: samples.tolist() for tick, samples in self.frames.items()
                      if first_tick <= tick <= current_tick}

        if not frames:
            raise RuntimeError("No voice audio was received during the last five seconds.")

        output = bytearray()
        sample_count = self.FRAME_SAMPLES * self.CHANNELS
        for tick in range(first_tick, current_tick + 1):
            samples = frames.get(tick, ())
            for index in range(sample_count):
                sample = samples[index] if index < len(samples) else 0
                sample = max(-32768, min(32767, sample))
                output.extend((sample & 0xFF, (sample >> 8) & 0xFF))
        return bytes(output)

    def cleanup(self):
        with self.lock:
            self.frames.clear()


def write_capture(path, pcm, ffmpeg_executable, codec="libopus", bitrate="64k"):
    """Encode captured PCM directly to a compact Ogg/Opus soundbyte."""
    result = subprocess.run(
        [
            str(ffmpeg_executable),
            "-f", "s16le",
            "-ar", str(RecentVoiceSink.SAMPLE_RATE),
            "-ac", str(RecentVoiceSink.CHANNELS),
            "-i", "pipe:0",
            "-c:a", codec,
            "-b:a", bitrate,
            "-ac", "1",
            "-f", "ogg",
            str(path),
        ],
        input=pcm,
        capture_output=True,
    )
    if result.returncode:
        details = result.stderr.decode(errors="replace").strip() if result.stderr else "unknown FFmpeg error"
        raise RuntimeError("Could not encode capture: " + details[-1000:])


def capture_files(sample_dir, extensions):
    return sorted(
        (path for path in sample_dir.rglob("cap_*") if path.is_file() and path.suffix.lower() in extensions),
        key=lambda path: path.stat().st_mtime,
    )


def make_room_for_capture(sample_dir, required_bytes, max_bytes, last_captures, lock, extensions):
    """Keep automatically named captures within their dedicated storage budget."""
    with lock:
        files = capture_files(sample_dir, extensions)
        used_bytes = sum(path.stat().st_size for path in files)
        while used_bytes + required_bytes > max_bytes and files:
            oldest = files.pop(0)
            size = oldest.stat().st_size
            oldest.unlink()
            for owner, capture in list(last_captures.items()):
                if capture == oldest:
                    del last_captures[owner]
            used_bytes -= size
        if used_bytes + required_bytes > max_bytes:
            raise RuntimeError("Capture storage is full. Remove old captures before recording another one.")


def delete_expired_unnamed_captures(sample_dir, extensions, retention_seconds, last_captures, lock, now=None):
    """Remove only auto-generated captures after their retention window."""
    cutoff = (time.time() if now is None else now) - retention_seconds
    removed = 0
    with lock:
        for capture in capture_files(sample_dir, extensions):
            try:
                if capture.stat().st_mtime >= cutoff:
                    continue
                capture.unlink()
            except OSError:
                logging.exception("Could not remove expired capture %s", capture)
                continue
            for owner, owned_capture in list(last_captures.items()):
                if owned_capture == capture:
                    del last_captures[owner]
            removed += 1
    return removed
