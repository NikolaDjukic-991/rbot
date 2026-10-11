# Copyright (c) 2022 NikolaDjukic-991
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import array
import asyncio
from collections import deque
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse
import wave

import discord
from discord.ext import commands
from discord.ext import voice_recv

import strings as message


RBOT_HOME = None
TOKEN = None
SAMPLE_DIR = None
TEMP_DIR = None
FFMPEG_EXECUTABLE = "ffmpeg"
LAST_CAPTURES = {}
SOUNDBOARD_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".webm", ".wma"}

intents = discord.Intents.all()
intents.members = True
bot = commands.Bot(command_prefix="!", description="revbot", intents=intents)


def _expand_config_path(value):
    value = value.replace("${RBOT_HOME}", str(RBOT_HOME)).replace("$RBOT_HOME", str(RBOT_HOME))
    return Path(os.path.expandvars(value)).expanduser().resolve()


def globalInit():
    global RBOT_HOME, TOKEN, SAMPLE_DIR, TEMP_DIR, FFMPEG_EXECUTABLE

    RBOT_HOME = Path(os.environ.get("RBOT_HOME", Path(__file__).resolve().parent)).expanduser().resolve()
    config_path = RBOT_HOME / "config.cfg"
    values = {}
    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            for line in config_file:
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                key, value = line.split(":", 1)
                values[key.strip()] = value.strip()
    except OSError as error:
        logging.error("Could not read %s: %s", config_path, error)
        return -1

    TOKEN = os.path.expandvars(values.get("TOKEN", ""))
    SAMPLE_DIR = _expand_config_path(values.get("SAMPLE_DIR", "${RBOT_HOME}/../Samples"))
    TEMP_DIR = _expand_config_path(values.get("TEMP_DIR", "${RBOT_HOME}/../temp"))
    FFMPEG_EXECUTABLE = os.path.expandvars(values.get("FFMPEG", "ffmpeg"))

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO)
    logging.info("Bot home: %s", RBOT_HOME)
    logging.info("Samples: %s", SAMPLE_DIR)
    logging.info("Audio extractor: yt-dlp")
    return 0


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

    def __init__(self, source, requester, title="Audio"):
        self.source = str(source)
        self.requester = requester
        self.trackInfo = TrackInfo(title=title, url=self.source)

    def create_source(self):
        parsed = urlparse(self.source)
        before_options = None
        if parsed.scheme in ("http", "https"):
            before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
        return discord.FFmpegPCMAudio(
            self.source,
            executable=FFMPEG_EXECUTABLE,
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


def _extract_track_info(link):
    # Run yt-dlp as a module using this Python interpreter on both Windows and Linux.
    result = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--no-playlist", "-j", link],
        cwd=str(RBOT_HOME),
        capture_output=True,
        text=True,
    )
    if result.returncode:
        details = (result.stderr or result.stdout or "yt-dlp exited with status {}".format(result.returncode)).strip()
        raise ValueError("Audio extractor failed: " + details[-1000:])
    return TrackInfo.from_extractor_json(json.loads(result.stdout))


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
        mixed = bytearray(self.FRAME_BYTES)
        for index in range(self.FRAME_SAMPLES * self.CHANNELS):
            music_sample = music_samples[index] if index < len(music_samples) else 0
            sound_sample = sound_samples[index] if index < len(sound_samples) else 0
            sample = int(music_sample * self.MUSIC_GAIN + sound_sample * self.SOUNDBOARD_GAIN)
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
        self._cleanup(music)
        self._cleanup(sound)


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


def _write_capture(path, pcm):
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(RecentVoiceSink.CHANNELS)
        wav_file.setsampwidth(RecentVoiceSink.SAMPLE_WIDTH)
        wav_file.setframerate(RecentVoiceSink.SAMPLE_RATE)
        wav_file.writeframes(pcm)


class Player:
    def __init__(self, voice_client):
        self.voiceClient = voice_client
        self.music_queue = deque()
        self.sound_queue = deque()
        self._lock = threading.RLock()
        self._mixer = None
        self._music_paused = False
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
            bot.loop.call_soon_threadsafe(self._handle_audio_finished, mixer)
        except RuntimeError:
            logging.info("Event loop closed while audio playback was finishing")

    def _handle_audio_finished(self, mixer):
        with self._lock:
            if self._mixer is mixer:
                self._mixer = None
        self._start_if_needed()

    def queue_music(self, request):
        with self._lock:
            starts_now = not self.music_queue and (
                self._mixer is None or self._mixer.music_source is None
            )
            self.music_queue.append(request)
        self._start_if_needed()
        return starts_now

    def queue_sound(self, request):
        with self._lock:
            self.sound_queue.append(request)
        self._start_if_needed()

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


class PlayerManager:
    def __init__(self):
        self.players = {}

    def getPlayerByGuildId(self, guild_id):
        return self.players.get(guild_id)

    async def createAndRegisterNewPlayer(self, ctx):
        guild_id = ctx.guild.id
        voice_client = await _joinHelper(ctx)
        if voice_client is None:
            raise ValueError("Join a voice channel before requesting audio")
        player = self.players.get(guild_id)
        if player is None:
            player = Player(voice_client)
            self.players[guild_id] = player
        else:
            player.voiceClient = voice_client
            player.start_capture()
        return player

    async def destroyPlayer(self, guild_id):
        player = self.players.pop(guild_id, None)
        if player is not None:
            await player.disconnect()


async def getVoiceChannelByUsername(guild, member):
    for channel in guild.voice_channels:
        if member in channel.members:
            return channel
    return None


async def _joinHelper(ctx):
    target_channel = await getVoiceChannelByUsername(ctx.guild, ctx.author)
    if target_channel is None:
        return None
    for voice_client in bot.voice_clients:
        if voice_client.guild.id == ctx.guild.id:
            if not isinstance(voice_client, voice_recv.VoiceRecvClient):
                await voice_client.disconnect()
                break
            if voice_client.channel != target_channel:
                await voice_client.move_to(target_channel)
            return voice_client
    return await target_channel.connect(cls=voice_recv.VoiceRecvClient)


playerManager = PlayerManager()


@bot.event
async def on_ready():
    logging.info("Logged in as %s (%s)", bot.user.name, bot.user.id)


@bot.command()
async def v(ctx, byteName: str):
    """Queue a soundboard effect; effects play over music."""
    try:
        player = await playerManager.createAndRegisterNewPlayer(ctx)
        sound_path = (SAMPLE_DIR / byteName).resolve()
        if sound_path.parent != SAMPLE_DIR and SAMPLE_DIR not in sound_path.parents:
            raise ValueError("Invalid sound name")
        if not sound_path.is_file():
            raise FileNotFoundError("Sound not found")
        player.queue_sound(AudioRequest(sound_path, ctx.author, sound_path.stem))
        await ctx.send(message.getString("Soundbyte"))
    except (ValueError, OSError) as error:
        await ctx.send(str(error))


@bot.command()
async def cap(ctx):
    """Capture and save the last five seconds of voice-channel audio.

    The bot must already be in your voice channel for five seconds. The WAV is
    saved in the sample directory and can be played with `!v <filename>`.
    """
    player = playerManager.getPlayerByGuildId(ctx.guild.id)
    if player is None:
        await ctx.send("Join the bot to a voice channel first; capture starts when it joins.")
        return

    user_channel = await getVoiceChannelByUsername(ctx.guild, ctx.author)
    if user_channel is None or player.getPlayerChannel() != user_channel:
        await ctx.send("Join the bot's voice channel to capture a soundbyte.")
        return

    try:
        pcm = player.capture_recent_voice()
    except RuntimeError as error:
        await ctx.send(str(error))
        return

    filename = "cap_{}.wav".format(datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    destination = SAMPLE_DIR / filename
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _write_capture, destination, pcm)
    LAST_CAPTURES[(ctx.guild.id, ctx.author.id)] = destination
    await ctx.send("Saved the last five seconds as `{}`. Play it with `!v {}`.".format(filename, filename))


@bot.command()
async def renamecap(ctx, *, new_name: str):
    """Rename your most recent capture. The capture remains a WAV soundbyte."""
    source = LAST_CAPTURES.get((ctx.guild.id, ctx.author.id))
    if source is None or not source.is_file():
        recent_captures = list(SAMPLE_DIR.glob("cap_*.wav"))
        source = max(recent_captures, key=lambda path: path.stat().st_mtime, default=None)
        if source is None:
            await ctx.send("You don't have a recent capture to rename. Run `!cap` first.")
            return

    new_name = new_name.strip()
    if not new_name or re.search(r'[<>:"/\\|?*\x00-\x1f]', new_name):
        await ctx.send("Choose a filename without Windows-invalid characters or paths.")
        return
    if Path(new_name).suffix and Path(new_name).suffix.lower() != ".wav":
        await ctx.send("Captures must keep the `.wav` extension.")
        return

    stem = Path(new_name).stem if Path(new_name).suffix else new_name
    stem = stem.rstrip(" .")
    if not stem or stem.upper() in {"CON", "PRN", "AUX", "NUL", *("COM{}".format(i) for i in range(1, 10)), *("LPT{}".format(i) for i in range(1, 10))}:
        await ctx.send("Choose a valid filename.")
        return

    destination = (SAMPLE_DIR / (stem + ".wav")).resolve()
    if destination.parent != SAMPLE_DIR:
        await ctx.send("Choose a filename, not a path.")
        return
    if destination == source.resolve():
        await ctx.send("That capture already has that name.")
        return
    if destination.exists():
        await ctx.send("A soundbyte with that name already exists.")
        return

    try:
        source.rename(destination)
    except OSError as error:
        logging.exception("Could not rename capture %s", source)
        await ctx.send("Could not rename that capture: " + str(error))
        return

    LAST_CAPTURES[(ctx.guild.id, ctx.author.id)] = destination
    await ctx.send("Renamed your latest capture to `{}`. Play it with `!v {}`.".format(destination.name, destination.name))


@bot.command(aliases=["soundbytes", "sounds"])
async def soundboard(ctx):
    """List soundboard clips available to play with `!v <filename>`."""
    sounds = sorted(
        (path.name for path in SAMPLE_DIR.iterdir() if path.is_file() and path.suffix.lower() in SOUNDBOARD_EXTENSIONS),
        key=str.casefold,
    )
    if not sounds:
        await ctx.send("No soundbytes found in the soundboard.")
        return

    header = "Soundboard sounds (play one with `!v <filename>`):\n"
    chunks = []
    current = header
    for sound in sounds:
        line = "- `{}`\n".format(sound)
        if len(current) + len(line) > 1900:
            chunks.append(current.rstrip())
            current = ""
        current += line
    if current:
        chunks.append(current.rstrip())
    for chunk in chunks:
        await ctx.send(chunk)


@bot.command()
async def play(ctx, link: str):
    """Queue audio from a supported video or audio URL."""
    try:
        player = await playerManager.createAndRegisterNewPlayer(ctx)
        loop = asyncio.get_running_loop()
        track = await loop.run_in_executor(None, _extract_track_info, link)
        starts_now = player.queue_music(AudioRequest(track.requestedUrl, ctx.author, track.title))
        prefix = message.getString("SongPlay") if starts_now else message.getString("Queued")
        await ctx.send(prefix + "*" + track.title + "*")
    except (ValueError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        logging.exception("Could not queue requested music")
        await ctx.send("Could not queue that audio: " + str(error))


async def _player_for_command(ctx):
    player = playerManager.getPlayerByGuildId(ctx.guild.id)
    if player is None:
        return None
    user_channel = await getVoiceChannelByUsername(ctx.guild, ctx.author)
    if user_channel is None or player.getPlayerChannel() != user_channel:
        return None
    return player


@bot.command()
async def pause(ctx):
    """Pause music while allowing soundboard sounds to continue."""
    player = await _player_for_command(ctx)
    if player:
        player.pausePlayer()
        await ctx.send(message.getString("Pause"))


@bot.command()
async def resume(ctx):
    """Resume paused playback."""
    player = await _player_for_command(ctx)
    if player:
        player.resumePlayer()
        await ctx.send(message.getString("Resume"))


@bot.command()
async def skip(ctx):
    """Skip the current music track."""
    player = await _player_for_command(ctx)
    if player:
        player.skipRequest()
        await ctx.send(message.getString("Skip"))


@bot.command()
async def join(ctx):
    """Join your current voice channel."""
    try:
        await playerManager.createAndRegisterNewPlayer(ctx)
        await ctx.send(message.getString("Join"))
    except ValueError as error:
        await ctx.send(str(error))


@bot.command()
async def leave(ctx):
    """Stop playback and leave the voice channel."""
    await playerManager.destroyPlayer(ctx.guild.id)
    await ctx.send(message.getString("Leave"))


@bot.command()
async def stop(ctx):
    """Stop music and clear the music queue, leaving soundboard sounds playing."""
    player = await _player_for_command(ctx)
    if player:
        player.stopMusic()
        await ctx.send(message.getString("Stop"))


@bot.command()
async def stopall(ctx):
    """Stop all music and soundboard playback and clear both queues."""
    player = await _player_for_command(ctx)
    if player:
        player.stopAll()
        await ctx.send(message.getString("Stop"))


@bot.command()
async def about(ctx):
    """Show information about the bot."""
    await ctx.send(message.getAboutMessage())


@bot.command(name="test")
async def test(ctx):
    """Run the isolated rbot unit-test suite."""
    runner = Path(__file__).with_name("rbot_tests.py")
    loop = asyncio.get_running_loop()

    def run_tests():
        return subprocess.run(
            [sys.executable, str(runner)],
            cwd=str(runner.parent),
            capture_output=True,
            text=True,
        )

    result = await loop.run_in_executor(None, run_tests)
    output = (result.stdout + result.stderr).strip()
    # Keep a failed test report useful without exceeding Discord's message limit.
    output = output[-1500:] if output else "No test output was produced."
    if result.returncode == 0:
        await ctx.send("Self-tests passed.\n```\n{}\n```".format(output))
    else:
        await ctx.send("Self-tests failed.\n```\n{}\n```".format(output))


def main():
    if globalInit() == 0:
        bot.run(TOKEN)


if __name__ == "__main__":
    main()
