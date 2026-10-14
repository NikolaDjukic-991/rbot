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

import asyncio
import logging
import os
from pathlib import Path
import subprocess  # Compatibility export for existing tests and integrations.
import sys
import threading
from types import SimpleNamespace

import discord
from discord.ext import commands
from discord.ext import tasks
from discord.ext import voice_recv

import strings as message

from revbot.audio.sources import AudioRequest, BufferedPCMSource, TrackInfo, extract_track_info
from revbot.audio.mixer import AudioMixer
from revbot.audio.capture import (
    RecentVoiceSink,
    capture_files as _capture_files_impl,
    delete_expired_unnamed_captures as _delete_expired_unnamed_captures_impl,
    make_room_for_capture as _make_room_for_capture_impl,
    write_capture as _write_capture_impl,
)
from revbot.audio.player import Player
from revbot.audio.manager import PlayerManager, find_voice_channel_by_member, join_voice_channel
from revbot.audio.storage import global_sound_directory, guild_sound_directory, has_revbot_admin_role
from revbot.command_channels import CommandChannelStore
from revbot.volume_settings import GuildVolumeStore
from revbot.config import (
    ConfigurationError,
    expand_config_path,
    is_configured_token,
    is_ffmpeg_available,
    load_settings,
    validate_settings,
)
from revbot.commands.general import register as register_general_commands
from revbot.commands.music import register as register_music_commands
from revbot.commands.soundboard import register as register_soundboard_commands
from revbot.commands.voice import register as register_voice_commands

RBOT_HOME = None
TOKEN = None
SAMPLE_DIR = None
TEMP_DIR = None
FFMPEG_EXECUTABLE = "ffmpeg"
LAST_CAPTURES = {}
SOUNDBOARD_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".webm", ".wma"}
MAX_MUSIC_QUEUE_ITEMS = 25
MAX_SOUNDBOARD_QUEUE_ITEMS = 20
MAX_TRACK_DURATION_SECONDS = 2 * 60 * 60
MAX_CAPTURE_STORAGE_BYTES = 250 * 1024 * 1024
CAPTURE_STORAGE_LOCK = threading.Lock()
CAPTURE_CODEC = "libopus"
CAPTURE_BITRATE = "64k"
CAPTURE_EXTENSIONS = {".ogg", ".wav"}
UNNAMED_CAPTURE_RETENTION_SECONDS = 7 * 24 * 60 * 60
COMMAND_CHANNELS = None
VOLUME_SETTINGS = None

intents = discord.Intents.all()
intents.members = True
bot = commands.Bot(command_prefix="!", description="revbot", intents=intents)


def should_process_command(ctx):
    """Filter commands by guild text channel, leaving admins able to manage the filter."""
    if ctx.guild is None or COMMAND_CHANNELS is None:
        return True
    allowed_channel_id = COMMAND_CHANNELS.get(ctx.guild.id)
    if allowed_channel_id is None or ctx.channel.id == allowed_channel_id:
        return True
    return bool(
        ctx.command is not None
        and ctx.command.name == "channel"
        and has_revbot_admin_role(ctx)
    )


@bot.event
async def on_message(incoming_message):
    if incoming_message.author.bot:
        return
    ctx = await bot.get_context(incoming_message)
    if should_process_command(ctx):
        await bot.invoke(ctx)


def _expand_config_path(value):
    return expand_config_path(value, RBOT_HOME)

def _ffmpeg_is_available(executable):
    return is_ffmpeg_available(executable)

def _is_configured_token(token):
    return is_configured_token(token)


def globalInit():
    global RBOT_HOME, TOKEN, SAMPLE_DIR, TEMP_DIR, FFMPEG_EXECUTABLE, COMMAND_CHANNELS, VOLUME_SETTINGS

    bot_home = Path(os.environ.get("RBOT_HOME", Path(__file__).resolve().parent)).expanduser().resolve()
    try:
        settings = load_settings(bot_home)
        global_sound_directory(settings.sample_dir)
    except ConfigurationError as error:
        logging.error("%s", error)
        return -1
    except OSError as error:
        logging.error("Could not load configuration or prepare bot directories: %s", error)
        return -1

    RBOT_HOME = settings.bot_home
    COMMAND_CHANNELS = CommandChannelStore(RBOT_HOME / "command_channels.json")
    COMMAND_CHANNELS.load()
    VOLUME_SETTINGS = GuildVolumeStore(RBOT_HOME / "guild_volumes.json")
    VOLUME_SETTINGS.load()
    playerManager.volume_store = VOLUME_SETTINGS
    TOKEN = settings.token
    SAMPLE_DIR = settings.sample_dir
    TEMP_DIR = settings.temp_dir
    FFMPEG_EXECUTABLE = settings.ffmpeg_executable
    try:
        validate_settings(settings, _ffmpeg_is_available)
    except ConfigurationError as error:
        logging.error("%s", error)
        return -1
    logging.basicConfig(level=logging.INFO)
    removed_captures = _delete_expired_unnamed_captures()
    logging.info("Bot home: %s", RBOT_HOME)
    logging.info("Samples: %s", SAMPLE_DIR)
    logging.info("Audio extractor: yt-dlp")
    if removed_captures:
        logging.info("Removed %d expired unnamed capture(s)", removed_captures)
    return 0


def _extract_track_info(link):
    return extract_track_info(link, RBOT_HOME, sys.executable)


async def getVoiceChannelByUsername(guild, member):
    return await find_voice_channel_by_member(guild, member)


async def _joinHelper(ctx):
    return await join_voice_channel(ctx, bot)

def _write_capture(path, pcm):
    return _write_capture_impl(path, pcm, FFMPEG_EXECUTABLE, CAPTURE_CODEC, CAPTURE_BITRATE)

def _capture_files():
    return _capture_files_impl(SAMPLE_DIR, CAPTURE_EXTENSIONS)

def _make_room_for_capture(required_bytes):
    return _make_room_for_capture_impl(
        SAMPLE_DIR, required_bytes, MAX_CAPTURE_STORAGE_BYTES, LAST_CAPTURES,
        CAPTURE_STORAGE_LOCK, CAPTURE_EXTENSIONS,
    )

def _delete_expired_unnamed_captures(now=None):
    return _delete_expired_unnamed_captures_impl(
        SAMPLE_DIR, CAPTURE_EXTENSIONS, UNNAMED_CAPTURE_RETENTION_SECONDS,
        LAST_CAPTURES, CAPTURE_STORAGE_LOCK, now,
    )

playerManager = PlayerManager(
    bot,
    player_factory=lambda voice_client: Player(
        voice_client, bot.loop, MAX_MUSIC_QUEUE_ITEMS, MAX_SOUNDBOARD_QUEUE_ITEMS
    ),
)

@bot.event
async def on_ready():
    logging.info("Logged in as %s (%s)", bot.user.name, bot.user.id)
    await bot.change_presence(activity=discord.Game(name="!help"))
    if not cleanup_expired_captures.is_running():
        cleanup_expired_captures.start()

@tasks.loop(hours=7 * 24)
async def cleanup_expired_captures():
    """Periodically prune expired auto-generated voice captures."""
    loop = asyncio.get_running_loop()
    removed = await loop.run_in_executor(None, _delete_expired_unnamed_captures)
    if removed:
        logging.info("Removed %d expired unnamed capture(s)", removed)

@cleanup_expired_captures.before_loop
async def before_capture_cleanup():
    await bot.wait_until_ready()

async def _player_for_command(ctx):
    player = playerManager.getPlayerByGuildId(ctx.guild.id)
    if player is None:
        return None
    user_channel = await getVoiceChannelByUsername(ctx.guild, ctx.author)
    if user_channel is None or player.getPlayerChannel() != user_channel:
        return None
    return player


def _make_command_services():
    return SimpleNamespace(
        player_manager=lambda: playerManager,
        sample_dir=lambda: SAMPLE_DIR,
        guild_sound_dir=lambda guild: guild_sound_directory(SAMPLE_DIR, guild),
        global_sound_dir=lambda: global_sound_directory(SAMPLE_DIR),
        temp_dir=lambda: TEMP_DIR,
        ffmpeg_executable=lambda: FFMPEG_EXECUTABLE,
        last_captures=LAST_CAPTURES,
        soundboard_extensions=lambda: SOUNDBOARD_EXTENSIONS,
        max_track_duration_seconds=MAX_TRACK_DURATION_SECONDS,
        get_voice_channel=lambda: getVoiceChannelByUsername,
        write_capture=lambda: _write_capture,
        make_room_for_capture=lambda: _make_room_for_capture,
        player_for_command=lambda: _player_for_command,
        extract_track_info=lambda: _extract_track_info,
        ffmpeg_available=lambda executable: _ffmpeg_is_available(executable),
        messages=message,
        runner_path=lambda: Path(__file__).with_name("rbot_tests.py"),
        is_revbot_admin=has_revbot_admin_role,
        command_channels=lambda: COMMAND_CHANNELS,
    )


def _register_commands():
    services = _make_command_services()
    registered = {}
    for register in (
        register_soundboard_commands,
        register_music_commands,
        register_voice_commands,
        register_general_commands,
    ):
        registered.update(register(bot, services))
    globals().update(registered)


_register_commands()

def main():
    if globalInit() == 0:
        bot.run(TOKEN)

if __name__ == "__main__":
    main()
