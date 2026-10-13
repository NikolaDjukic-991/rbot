"""Commands for music playback and queue controls."""

import asyncio
import json
import logging
import subprocess

from revbot.audio.sources import AudioRequest
from revbot.commands.permissions import require_revbot_admin



def register(bot, services):
    @bot.command()
    async def play(ctx, link: str):
        """Queue audio from a supported video or audio URL."""
        try:
            player = await services.player_manager().createAndRegisterNewPlayer(ctx)
            loop = asyncio.get_running_loop()
            track = await loop.run_in_executor(None, services.extract_track_info(), link)
            if track.duration is not None and track.duration > services.max_track_duration_seconds:
                raise ValueError("Tracks longer than {} minutes cannot be queued.".format(services.max_track_duration_seconds // 60))
            starts_now = player.queue_music(AudioRequest(track.requestedUrl, ctx.author, track.title, services.ffmpeg_executable()))
            prefix = services.messages.getString("SongPlay") if starts_now else services.messages.getString("Queued")
            await ctx.send(prefix + "*" + track.title + "*")
        except (ValueError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
            logging.exception("Could not queue requested music")
            await ctx.send("Could not queue that audio: " + str(error))

    @bot.command()
    async def volume(ctx, percent: int = None):
        """Show or set the server playback volume from 0 to 200 percent."""
        player = await services.player_for_command()(ctx)
        if player is None:
            await ctx.send("Join the bot's voice channel to view or change volume.")
            return
        if percent is None:
            await ctx.send("Current volume: {}%. Use `!volume <0-200>` to change it.".format(player.getVolume()))
            return
        try:
            player.setVolume(percent)
        except ValueError as error:
            await ctx.send(str(error))
            return
        await ctx.send("Volume set to {}%.".format(percent))

    @bot.command(name="now")
    async def now(ctx):
        """Show the music and soundboard audio currently playing."""
        player = await services.player_for_command()(ctx)
        if player is None:
            await ctx.send("Join the bot's voice channel to view what is playing.")
            return
        music, sound, music_paused = player.nowPlaying()
        if music is None and sound is None:
            await ctx.send("Nothing is playing right now.")
            return
        lines = ["Now playing:"]
        if music is not None:
            suffix = " (paused)" if music_paused else ""
            lines.append("Music: *{}*{}".format(music.trackInfo.title, suffix))
        elif music_paused:
            lines.append("Music: paused")
        if sound is not None:
            lines.append("Soundboard: *{}*".format(sound.trackInfo.title))
        await ctx.send("\n".join(lines))

    @bot.command()
    async def health(ctx):
        """Show bot and playback diagnostics (revbot_admin only)."""
        if not await require_revbot_admin(ctx, services):
            return
        ffmpeg_ready = services.ffmpeg_available(services.ffmpeg_executable())
        player = services.player_manager().getPlayerByGuildId(ctx.guild.id)
        lines = [
            "Health:",
            "Discord: {}".format("ready" if bot.is_ready() else "connecting"),
            "FFmpeg: {}".format("ready" if ffmpeg_ready else "not found"),
            "Sample directory: {}".format("ready" if services.sample_dir() and services.sample_dir().is_dir() else "unavailable"),
            "Temporary directory: {}".format("ready" if services.temp_dir() and services.temp_dir().is_dir() else "unavailable"),
            "Active guild players: {}".format(len(services.player_manager().players)),
        ]
        if player is None:
            lines.append("This server: not connected to voice")
        else:
            music, sound, paused = player.nowPlaying()
            state = "music paused" if paused else "playing" if player.isPlaying() else "idle"
            lines.append("This server: {} at {}% volume".format(state, player.getVolume()))
            lines.append("Current audio: {} music, {} soundboard".format(
                "yes" if music else "no", "yes" if sound else "no"
            ))
        await ctx.send("\n".join(lines))

    @bot.command(name="queue")
    async def showqueue(ctx):
        """Show upcoming music tracks and soundboard clips for this voice channel."""
        player = await services.player_for_command()(ctx)
        if player is None:
            await ctx.send("Join the bot's voice channel to view its queue.")
            return
        music, sounds = player.queued_requests()
        if not music and not sounds:
            await ctx.send("No audio is queued.")
            return

        lines = []
        if music:
            lines.append("Music queue:")
            lines.extend("{}. {}".format(index, request.trackInfo.title) for index, request in enumerate(music, 1))
        if sounds:
            if lines:
                lines.append("")
            lines.append("Soundboard queue:")
            lines.extend("- {}".format(request.trackInfo.title) for request in sounds)
        await ctx.send("\n".join(lines)[:1900])

    @bot.command()
    async def clear(ctx):
        """Clear queued audio without interrupting what is currently playing."""
        player = await services.player_for_command()(ctx)
        if player is None:
            await ctx.send("Join the bot's voice channel to clear its queue.")
            return
        music_count, sound_count = player.clear_queued_requests()
        await ctx.send("Cleared {} music track(s) and {} soundboard clip(s).".format(music_count, sound_count))

    @bot.command()
    async def remove(ctx, position: int):
        """Remove a queued music track by its one-based `!queue` position."""
        player = await services.player_for_command()(ctx)
        if player is None:
            await ctx.send("Join the bot's voice channel to remove queued music.")
            return
        request = player.remove_queued_music(position)
        if request is None:
            await ctx.send("There is no queued music track at position {}.".format(position))
            return
        await ctx.send("Removed queued track: *{}*".format(request.trackInfo.title))

    @bot.command()
    async def pause(ctx):
        """Pause music while allowing soundboard sounds to continue."""
        player = await services.player_for_command()(ctx)
        if player:
            player.pausePlayer()
            await ctx.send(services.messages.getString("Pause"))

    @bot.command()
    async def resume(ctx):
        """Resume paused playback."""
        player = await services.player_for_command()(ctx)
        if player:
            player.resumePlayer()
            await ctx.send(services.messages.getString("Resume"))

    @bot.command()
    async def skip(ctx):
        """Skip the current music track."""
        player = await services.player_for_command()(ctx)
        if player:
            player.skipRequest()
            await ctx.send(services.messages.getString("Skip"))

    @bot.command()
    async def stop(ctx):
        """Stop music and clear the music queue, leaving soundboard sounds playing."""
        player = await services.player_for_command()(ctx)
        if player:
            player.stopMusic()
            await ctx.send(services.messages.getString("Stop"))

    @bot.command()
    async def stopall(ctx):
        """Stop all music and soundboard playback and clear both queues."""
        player = await services.player_for_command()(ctx)
        if player:
            player.stopAll()
            await ctx.send(services.messages.getString("Stop"))
    return {
        "play": bot.get_command("play"),
        "volume": bot.get_command("volume"),
        "now": bot.get_command("now"),
        "health": bot.get_command("health"),
        "showqueue": bot.get_command("queue"),
        "clear": bot.get_command("clear"),
        "remove": bot.get_command("remove"),
        "pause": bot.get_command("pause"),
        "resume": bot.get_command("resume"),
        "skip": bot.get_command("skip"),
        "stop": bot.get_command("stop"),
        "stopall": bot.get_command("stopall"),
    }
