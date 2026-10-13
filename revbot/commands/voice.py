"""Commands that connect the bot to voice channels."""

import discord

from revbot.commands.permissions import require_revbot_admin




def register(bot, services):
    @bot.command()
    async def join(ctx):
        """Join your current voice channel."""
        try:
            await services.player_manager().createAndRegisterNewPlayer(ctx)
            await ctx.send(services.messages.getString("Join"))
        except ValueError as error:
            await ctx.send(str(error))

    @bot.command()
    async def leave(ctx):
        """Stop playback and leave the voice channel."""
        await services.player_manager().destroyPlayer(ctx.guild.id)
        await ctx.send(services.messages.getString("Leave"))

    @bot.command()
    async def channel(ctx, target: discord.TextChannel = None):
        """Set this server's allowed command text channel; no argument allows commands everywhere."""
        if not await require_revbot_admin(ctx, services):
            return
        if ctx.guild is None:
            await ctx.send("The command channel can only be configured in a server.")
            return
        if target is None:
            services.command_channels().set(ctx.guild.id, None)
            await ctx.send("Command listening is enabled in all text channels for this server.")
            return
        if target.guild.id != ctx.guild.id:
            await ctx.send("Choose a text channel from this server.")
            return
        try:
            services.command_channels().set(ctx.guild.id, target.id)
        except OSError as error:
            await ctx.send("Could not save the command channel setting: " + str(error))
            return
        await ctx.send(
            "I will listen for commands in {} for this server. Admins can use `!channel` anywhere to clear this setting.".format(target.mention)
        )

    return {name: bot.get_command(name) for name in ['join', 'leave', 'channel']}
