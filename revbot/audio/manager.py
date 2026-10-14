"""Per-guild player lifecycle and Discord voice-channel connections."""

from discord.ext import voice_recv

from .player import Player


async def find_voice_channel_by_member(guild, member):
    for channel in guild.voice_channels:
        if member in channel.members:
            return channel
    return None


class PlayerManager:
    def __init__(self, bot_client, player_factory=None, volume_store=None):
        self.bot = bot_client
        self.player_factory = player_factory
        self.volume_store = volume_store
        self.players = {}

    def getPlayerByGuildId(self, guild_id):
        return self.players.get(guild_id)

    async def createAndRegisterNewPlayer(self, ctx):
        guild_id = ctx.guild.id
        voice_client = await join_voice_channel(ctx, self.bot)
        if voice_client is None:
            raise ValueError("Join a voice channel before requesting audio")
        player = self.players.get(guild_id)
        if player is None:
            player = self.player_factory(voice_client) if self.player_factory else Player(voice_client, self.bot.loop)
            if self.volume_store is not None:
                player._guild_id = guild_id
                player._volume_store = self.volume_store
                player._volume_percent = self.volume_store.get(guild_id)
            self.players[guild_id] = player
        else:
            player.voiceClient = voice_client
            player.start_capture()
        return player

    async def destroyPlayer(self, guild_id):
        player = self.players.pop(guild_id, None)
        if player is not None:
            await player.disconnect()

async def join_voice_channel(ctx, bot_client):
    target_channel = await find_voice_channel_by_member(ctx.guild, ctx.author)
    if target_channel is None:
        return None
    for voice_client in bot_client.voice_clients:
        if voice_client.guild.id == ctx.guild.id:
            if not isinstance(voice_client, voice_recv.VoiceRecvClient):
                await voice_client.disconnect()
                break
            if voice_client.channel != target_channel:
                await voice_client.move_to(target_channel)
            return voice_client
    return await target_channel.connect(cls=voice_recv.VoiceRecvClient)
