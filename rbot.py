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

#
# External imports
#
import time
import subprocess
import discord
import logging
import hashlib
import threading
import json
import os
from discord.ext import commands

#
# My imports
#
import strings as message

#
# Globals
#
RBOT_HOME = None
TOKEN = None
SAMPLE_DIR = None
YTDL_DIR   = None
FFMPEG_OPTIONS = None

# TODO: Move this out
intents = discord.Intents.all()
intents.members = True
description = '''revbot'''

bot = commands.Bot(command_prefix='!', description=description, intents=intents)

# Initializes global settings and sets up bot configuration
def globalInit():
    global RBOT_HOME
    global TOKEN
    global SAMPLE_DIR
    global YTDL_DIR

    RBOT_HOME = os.environ.get("RBOT_HOME")
    if RBOT_HOME == None:
        RBOT_HOME="./"

    try:
        with open(RBOT_HOME + "/config.cfg", "r") as cfgFile:
            print("opened config.cfg")
            for line in cfgFile:
                keyValPair = line.split(":", 1)
                key = keyValPair[0].strip()
                value = keyValPair[1].strip()

                if key == "TOKEN":
                    TOKEN = os.path.expandvars(value)
                elif key == "SAMPLE_DIR":
                    SAMPLE_DIR = os.path.expandvars(value)
                elif key == "YTDL_DIR":
                    YTDL_DIR = os.path.expandvars(value)
    except IOError:
        print("Error reading config.cfg")
        return -1

    print("Home: " + RBOT_HOME)
    print("Samples: " + SAMPLE_DIR)
    print("Yydl dir: " + YTDL_DIR)

    FFMPEG_OPTIONS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5','options': '-vn'}

    logging.basicConfig(level=logging.INFO)

#
# Classes
#
class YTLinkInfo:
    def __init__(self):
        self.title = ""
        self.requestedUrl = None
        self.urls = []
        self.duration = -1


    def createYTLinkInfoFromJson(self, jsonObj):
        self.title = jsonObj["title"]
        self.requestedUrl = None
        self.urls = []
        self.duration = jsonObj["duration"]

        for fmt in jsonObj["formats"]:
            if fmt["format"].find("audio only"):
                self.urls.append(fmt["url"])

        for requestedFmt in jsonObj["requested_formats"]:
            if requestedFmt["format"].find("audio only"):
                self.requestedUrl = requestedFmt["url"]

class PlaylistRequest:
    """Represents a queable sound to be played by the bot"""
    def __init__(self, trackInfo, requester, soundDataStream):
        self.trackInfo  = trackInfo
        self.requester = requester
        self.soundDataStream = soundDataStream
        self.state     = "queued-unloaded"

    # Factory method to create PlaylistRequests from links
    @staticmethod
    def createPlaylistRequestFromLink(link, requester, preload = False):
        # TODO: Need error handling and validation for links
        # TODO: Need to transition to parsing the JSON object instead of only grabbing the URL.
        # TODO: Implement parsing for artist/title/length.
        ytdlProcessJson = subprocess.run(["python3", "/mnt/d/Nikola/Crap/rbot_git/youtube-dl/bin/youtube-dl", "--audio-quality", "0", "-j", link], capture_output=True, text=True)

        ytdlJson = json.loads(ytdlProcessJson.stdout)
        ytLinkInfo = YTLinkInfo()
        ytLinkInfo.createYTLinkInfoFromJson(jsonObj=ytdlJson)

        # TODO: Not sure if creating the object is going to preload when the source is a link.
        if preload == True:
            soundDataStream = discord.FFmpegOpusAudio(ytLinkInfo.requestedUrl, executable="ffmpeg", **FFMPEG_OPTIONS)
        else:
            soundDataStream = None
        return PlaylistRequest(ytLinkInfo, requester, soundDataStream)

    # Factory method to create PlaylistRequests from soundbyte paths
    @staticmethod
    def createPlaylistRequestFromPath(fname, requester, preload = True):
        sndAbsPath = SAMPLE_DIR + fname.strip()

        soundDataStream = discord.FFmpegOpusAudio(sndAbsPath, options='-vn')
        return PlaylistRequest(YTLinkInfo(), requester, soundDataStream)

    def isLoaded(self):
        return self.soundDataStream != None

    def load(self):
        if self.soundDataStream != None:
            return False
        self.soundDataStream = discord.FFmpegPCMAudio(self.trackInfo.requestedUrl, options="-vn")
        self.state = "queued-loaded"
        return True

    def getAudio(self):
        return self.soundDataStream

class Playlist:
    """Playlist class used to queue sounds to be played"""
    def __init__(self):
        self.itemArray = []

    def queueRequest(self, playRequest):
        self.itemArray.append(playRequest)

    def getFirst(self):
        if len(self.itemArray) == 0:
            return None
        return self.itemArray[0]

    def deleteFirst(self):
        return self.itemArray.pop(0)

    def clearPlaylist(self):
        self.itemArray = []

    def isEmpty(self):
        return len(self.itemArray) == 0

class Player:
    def __init__(self, bForcePreload = False, voiceClient = None):
        self.playlist = Playlist()
        self.bForcePreload = bForcePreload
        self.voiceClient = voiceClient

    def addToPlaylistFromPath(self, fname, requester):
        firstItem = self.playlist.isEmpty()

        #
        # TODO: Consider implementing a caching mechanism for requests (in memory for start).
        # TODO: This is a shitty mechanism because it preloads everything on the playlist.
        #       Would be better to preload only the next request.
        #
        request = PlaylistRequest.createPlaylistRequestFromPath(fname, requester)

        if self.bForcePreload == True and request.isLoaded() == False:
            request.load()

        self.playlist.queueRequest(request)

    def addToPlaylistFromLink(self, link, requester):
        request = PlaylistRequest.createPlaylistRequestFromLink(link, requester)

        if self.bForcePreload == True and request.isLoaded() == False:
            request.load()

        self.playlist.queueRequest(request)
        return request

    def clearPlaylist(self):
        self.playlist.clearPlaylist()

    def isConnected(self):
        return self.voiceClient == None

    def setVoiceClient(self, voiceClient):
        self.voiceClient = voiceClient

    def isPlaying(self):
        if self.voiceClient == None:
            return False
        return self.voiceClient.is_playing()

    def getPlayerChannel(self):
        return self.voiceClient.channel

    def pausePlayer(self):
        if self.voiceClient.is_connected() == False:
            return

        if self.voiceClient.is_paused():
            return

        self.voiceClient.pause()

    def resumePlayer(self):
        if self.voiceClient.is_connected() == False:
            return
        if self.voiceClient.is_playing() == True:
            return
        self.voiceClient.resume()

    def skipRequest(self):
        self.voiceClient.stop()

    async def disconnect(self):
        await self.voiceClient.disconnect()

    def stop(self):
        self.playlist.clearPlaylist()
        self.voiceClient.stop()

    def play(self, request):
        if request == None:
            return

        if request.isLoaded() == False:
           request.load()

        if request.state == "paused":
            self.voiceClient.resume()
            request.state = "playing"
            return

        request.state = "playing"

        # WAR: NinaSleep. It seems loading the songs takes quite the amount of
        # resources causing the song that is queued to get stretched and change
        # tempo in the first 10s of playing.
        time.sleep(1)

        self.voiceClient.play(request.getAudio(), after=PlayerManager.playerFinishedCB)

    def startPlaying(self):
        nextRequest = self.playlist.getFirst()

        # TODO: Add exception handling all over the code
        if nextRequest == None:
            return

        self.play(nextRequest)

class PlayerManager:
    def __init__(self, bForcePreload):
        self.bForcePreload = bForcePreload
        self.players = dict()

    def hasPlayerByGuildId(self, guildId):
        if guildId in self.players:
            return True
        else:
            return False

    def getPlayerByGuildId(self, guildId):
        if self.hasPlayerByGuildId(guildId) == True:
            return self.players[guildId]
        else:
            return None

    # TODO: FUGLY CODE
    async def createAndRegisterNewPlayer(self, ctx):
        guildId = ctx.message.channel.guild.id

        voiceClient = await _joinHelper(ctx)

        # TODO: This needs cleaning up.
        if self.hasPlayerByGuildId(guildId):
            self.getPlayerByGuildId(guildId).setVoiceClient(voiceClient)
            return self.getPlayerByGuildId(guildId)

        if voiceClient == None:
            raise ValueException("voiceClient can't be null")

        newPlayer = Player(self.bForcePreload, voiceClient)
        self.players[guildId] = newPlayer
        return newPlayer

    async def destroyPlayer(self, guildId):
        if self.players[guildId] != None:
            await self.players[guildId].disconnect()
            del self.players[guildId]
        else:
            print("Player not found")

    @staticmethod
    def playerFinishedCB(error):
        #
        # TODO: Implement error checking. This code can be optimized, but with the number of servers
        #       we probably won't need to.
        #
        for player in playerManager.players.values():
                if player.isPlaying() == False and player.playlist.getFirst().state == "playing":
                    player.playlist.deleteFirst()
                    nextRequest = player.playlist.getFirst()
                    if nextRequest != None:
                        player.play(nextRequest)

#
# Helper functions
#

async def getVoiceChannelByUsername(guild, name):
    await guild.chunk()

    for channel in guild.voice_channels:
        for member in channel.members:
            if member == name:
                return channel

    return None

async def _joinHelper(ctx):
    cmdRequester = ctx.message.author
    fromGuild = ctx.message.channel.guild

    targetChannel = await getVoiceChannelByUsername(fromGuild, cmdRequester)

    # User not connected
    if targetChannel == None:
        # Switch this out for an exception
        return

    # if bot is already connected just return the vClient
    for botVClient in bot.voice_clients:
        if botVClient.channel == targetChannel:
            return botVClient

    return await targetChannel.connect()

#
# Bot Commands/Events
#

@bot.event
async def on_ready():
    print('Logged in as')
    print(bot.user.name)
    print(bot.user.id)
    print('------')

@bot.command()
async def v(ctx, byteName : str):
    """Play soundboard effect."""
    player = await playerManager.createAndRegisterNewPlayer(ctx)
    rqs = PlaylistRequest.createPlaylistRequestFromPath(byteName, ctx.message.author, True)
    rqs.load()
    audioStream = rqs.getAudio()
    audioData = audioStream.read()
    while len(audioData) != 0:
        player.voiceClient.send_audio_packet(audioData, encode=False)
        audioData = audioStream.read()

@bot.command()
async def play(ctx, link : str):
    """Play audio from youtube link."""
    player = await playerManager.createAndRegisterNewPlayer(ctx)

    playlistRequest = player.addToPlaylistFromLink(link, ctx.message.author)

    if player.isPlaying() == False:
        await ctx.send(message.getString("SongPlay") + "*" + playlistRequest.trackInfo.title + "*")
        player.startPlaying()
    else:
        await ctx.send(message.getString("Queued") + playlistRequest.trackInfo.title)

@bot.command()
async def pause(ctx):
    """Pause playing."""
    player = playerManager.getPlayerByGuildId(ctx.message.channel.guild.id)

    userVoiceChannel = await getVoiceChannelByUsername(ctx.message.channel.guild, ctx.message.author)

    for botVoiceClient in bot.voice_clients:
        if botVoiceClient.channel == userVoiceChannel:
            await ctx.send(message.getString("Pause"))
            player.pausePlayer()
        else:
            return

@bot.command()
async def resume(ctx):
    """Resume playing."""
    player = playerManager.getPlayerByGuildId(ctx.message.channel.guild.id)

    userVoiceChannel = await getVoiceChannelByUsername(ctx.message.channel.guild, ctx.message.author)

    for botVoiceClient in bot.voice_clients:
        if botVoiceClient.channel == userVoiceChannel:
            await ctx.send(message.getString("Resume"))
            player.resumePlayer()
        else:
            return

@bot.command()
async def skip(ctx):
    """Skip current playlist item."""
    player = playerManager.getPlayerByGuildId(ctx.message.channel.guild.id)

    userVoiceChannel = await getVoiceChannelByUsername(ctx.message.channel.guild, ctx.message.author)

    for botVoiceClient in bot.voice_clients:
        if botVoiceClient.channel == userVoiceChannel:
            await ctx.send(message.getString("Skip"))
            player.skipRequest()

@bot.command()
async def join(ctx):
    """Request bot to join voice channel."""
    await ctx.send(message.getString("Join"))
    await playerManager.createAndRegisterNewPlayer(ctx)

@bot.command()
async def leave(ctx):
    """Leave channel"""
    await playerManager.destroyPlayer(ctx.message.channel.guild.id)

@bot.command()
async def stop(ctx):
    """Leave channel"""
    await playerManager.destroyPlayer(ctx.message.channel.guild.id)

@bot.command()
async def about(ctx):
    """About this bot"""
    await ctx.send(message.getAboutMessage())



#
# Create PlayerManager singleton and run the bot
#
globalInit()
playerManager = PlayerManager(True)
bot.run(TOKEN)
