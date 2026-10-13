"""Commands that connect the bot to voice channels."""




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
    return {name: bot.get_command(name) for name in ['join', 'leave']}
