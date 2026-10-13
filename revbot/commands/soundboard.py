"""Commands for soundboard playback and voice captures."""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
import re
import shutil

from revbot.audio.sources import AudioRequest



def register(bot, services):
    def is_within(path, root):
        return path != root and root in path.parents

    @bot.command()
    async def v(ctx, byteName: str):
        """Play a guild sound or a shared sound from `globals/`; admins can play any sound."""
        try:
            if ctx.guild is None:
                raise ValueError("Soundbytes can only be played in a server.")
            sample_root = services.sample_dir().resolve()
            is_admin = services.is_revbot_admin(ctx)
            if is_admin:
                sound_path = (sample_root / byteName).resolve()
                if not is_within(sound_path, sample_root):
                    raise ValueError("Invalid sound name")
            else:
                guild_root = services.guild_sound_dir(ctx.guild).resolve()
                global_root = services.global_sound_dir().resolve()
                sound_name = byteName.replace("\\", "/")
                if sound_name.casefold().startswith("globals/"):
                    sound_path = (global_root / sound_name[len("globals/"):]).resolve()
                    if not is_within(sound_path, global_root):
                        raise ValueError("Invalid sound name")
                else:
                    local_path = (guild_root / sound_name).resolve()
                    if not is_within(local_path, guild_root):
                        raise ValueError("Invalid sound name")
                    global_path = (global_root / sound_name).resolve()
                    if not is_within(global_path, global_root):
                        raise ValueError("Invalid sound name")
                    sound_path = local_path if local_path.is_file() else global_path
            if not sound_path.is_file():
                raise FileNotFoundError("Sound not found in this server's or the global soundboard.")
            if sound_path.suffix.lower() not in services.soundboard_extensions():
                raise ValueError("That file is not a supported soundbyte.")
            player = await services.player_manager().createAndRegisterNewPlayer(ctx)
            player.queue_sound(AudioRequest(sound_path, ctx.author, sound_path.stem, services.ffmpeg_executable()))
            await ctx.send(services.messages.getString("Soundbyte"))
        except (ValueError, OSError) as error:
            await ctx.send(str(error))

    @bot.command()
    async def cap(ctx):
        """Capture and save the last five seconds of voice-channel audio.

        The bot must already be in your voice channel for five seconds. The Ogg clip is
        saved in this server's soundboard directory and can be played with `!v <filename>`.
        """
        if ctx.guild is None:
            await ctx.send("Captures can only be saved in a server.")
            return
        player = services.player_manager().getPlayerByGuildId(ctx.guild.id)
        if player is None:
            await ctx.send("Join the bot to a voice channel first; capture starts when it joins.")
            return

        user_channel = await services.get_voice_channel()(ctx.guild, ctx.author)
        if user_channel is None or player.getPlayerChannel() != user_channel:
            await ctx.send("Join the bot's voice channel to capture a soundbyte.")
            return

        try:
            pcm = player.capture_recent_voice()
        except RuntimeError as error:
            await ctx.send(str(error))
            return

        guild_directory = services.guild_sound_dir(ctx.guild)
        global_directory = services.global_sound_dir()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = "cap_{}.ogg".format(stamp)
        collision_index = 1
        while (guild_directory / filename).exists() or (global_directory / filename).exists():
            filename = "cap_{}_{}.ogg".format(stamp, collision_index)
            collision_index += 1
        destination = guild_directory / filename
        temporary_capture = services.temp_dir() / "capture-encode" / guild_directory.name / filename
        loop = asyncio.get_running_loop()
        try:
            temporary_capture.parent.mkdir(parents=True, exist_ok=True)
            await loop.run_in_executor(None, services.write_capture(), temporary_capture, pcm)
            await loop.run_in_executor(None, services.make_room_for_capture(), temporary_capture.stat().st_size)
            await loop.run_in_executor(None, shutil.move, str(temporary_capture), str(destination))
        except (OSError, RuntimeError) as error:
            temporary_capture.unlink(missing_ok=True)
            await ctx.send("Could not save that capture: " + str(error))
            return
        services.last_captures[(ctx.guild.id, ctx.author.id)] = destination
        await ctx.send(
            "Saved the last five seconds. Name it with `!renamecap <name>`, then play it with `!v <name>.ogg`."
        )

    @bot.command()
    async def renamecap(ctx, *, new_name: str):
        """Rename your most recent capture while preserving its audio extension."""
        if ctx.guild is None:
            await ctx.send("Captures can only be renamed in the server where they were recorded.")
            return
        source = services.last_captures.get((ctx.guild.id, ctx.author.id))
        if source is None or not source.is_file():
            await ctx.send("You don't have a recent capture to rename. Run `!cap` first.")
            return

        new_name = new_name.strip()
        if not new_name or re.search(r'[<>:"/\\|?*\x00-\x1f]', new_name):
            await ctx.send("Choose a filename without Windows-invalid characters or paths.")
            return
        extension = source.suffix.lower()
        if Path(new_name).suffix and Path(new_name).suffix.lower() != extension:
            await ctx.send("Captures must keep the `{}` extension.".format(extension))
            return

        stem = Path(new_name).stem if Path(new_name).suffix else new_name
        stem = stem.rstrip(" .")
        if not stem or stem.upper() in {"CON", "PRN", "AUX", "NUL", *("COM{}".format(i) for i in range(1, 10)), *("LPT{}".format(i) for i in range(1, 10))}:
            await ctx.send("Choose a valid filename.")
            return

        guild_directory = services.guild_sound_dir(ctx.guild).resolve()
        global_directory = services.global_sound_dir().resolve()
        if source.resolve().parent != guild_directory:
            await ctx.send("Your recent capture is not in this server's soundboard.")
            return
        destination = (guild_directory / (stem + extension)).resolve()
        if destination.parent != guild_directory:
            await ctx.send("Choose a filename, not a path.")
            return
        if destination == source.resolve():
            await ctx.send("That capture already has that name.")
            return
        if destination.exists():
            await ctx.send("A soundbyte with that name already exists.")
            return
        if (global_directory / destination.name).exists():
            await ctx.send("A global soundbyte already uses that name; choose a different name.")
            return

        try:
            source.rename(destination)
        except OSError as error:
            logging.exception("Could not rename capture %s", source)
            await ctx.send("Could not rename that capture: " + str(error))
            return

        services.last_captures[(ctx.guild.id, ctx.author.id)] = destination
        await ctx.send("Renamed your latest capture to `{}`. Play it with `!v {}`.".format(destination.name, destination.name))

    @bot.command(aliases=["soundbytes", "sounds"])
    async def soundboard(ctx):
        """List playable guild and `globals/` sounds; admins also see all guild sounds."""
        if ctx.guild is None:
            await ctx.send("The soundboard is only available in a server.")
            return
        sample_root = services.sample_dir().resolve()
        is_admin = services.is_revbot_admin(ctx)
        sounds = []
        if is_admin:
            sound_root = sample_root
            for path in sound_root.rglob("*"):
                if path.is_file() and path.suffix.lower() in services.soundboard_extensions():
                    sounds.append(path.relative_to(sound_root).as_posix())
        else:
            guild_root = services.guild_sound_dir(ctx.guild).resolve()
            global_root = services.global_sound_dir().resolve()
            for path in guild_root.iterdir():
                if path.is_file() and path.suffix.lower() in services.soundboard_extensions():
                    sounds.append(path.name)
            for path in global_root.iterdir():
                if path.is_file() and path.suffix.lower() in services.soundboard_extensions():
                    sounds.append("globals/" + path.name)
        sounds.sort(key=str.casefold)
        if not sounds:
            await ctx.send("No soundbytes found in this server's soundboard.")
            return

        header = "Soundboard sounds (play one with `!v <filename>`; `globals/` sounds are shared):\n"
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
    return {name: bot.get_command(name) for name in ['v', 'cap', 'renamecap', 'soundboard']}
