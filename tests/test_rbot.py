"""Unit tests for rbot.

External services are replaced with fakes: these tests do not connect to Discord,
run FFmpeg, or contact YouTube. The supplied Lighter.wav and URL are used as
the representative soundboard and music inputs.
"""

import array
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_DIR = PROJECT_DIR.parent
SAMPLE_FILE = REPOSITORY_DIR / "Samples" / "Lighter.wav"
YOUTUBE_URL = "https://www.youtube.com/watch?v=FLGJXbl6g8o"
TEST_ENVIRONMENT = Path(os.environ.get("RBOT_TEST_ENV", tempfile.gettempdir())).resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import rbot  # noqa: E402


class FakeSource:
    def __init__(self, frames=()):
        self.frames = deque(frames)
        self.cleaned = False

    def read(self):
        return self.frames.popleft() if self.frames else b""

    def cleanup(self):
        self.cleaned = True


class FakeVoiceClient:
    def __init__(self, channel="voice"):
        self.channel = channel
        self.played = []
        self.paused = False
        self.stopped = False
        self.disconnected = False
        self.listening = False

    def is_connected(self):
        return True

    def is_playing(self):
        return bool(self.played) and not self.paused

    def is_paused(self):
        return self.paused

    def play(self, source, after):
        self.played.append((source, after))

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop(self):
        self.stopped = True

    async def disconnect(self):
        self.disconnected = True


def pcm_frame(value, samples=rbot.AudioMixer.FRAME_SAMPLES * rbot.AudioMixer.CHANNELS):
    return array.array("h", [value] * samples).tobytes()


class RbotUnitTests(unittest.TestCase):
    def setUp(self):
        rbot.RBOT_HOME = PROJECT_DIR
        rbot.SAMPLE_DIR = SAMPLE_FILE.parent.resolve()
        rbot.TEMP_DIR = REPOSITORY_DIR / "temp"
        rbot.FFMPEG_EXECUTABLE = "ffmpeg"
        rbot.LAST_CAPTURES.clear()

    def test_required_commands_are_exposed(self):
        expected = {
            "v", "cap", "renamecap", "soundboard", "play", "pause", "resume",
            "skip", "volume", "now", "health", "queue", "clear", "remove", "join", "leave", "stop", "stopall", "about", "test",
        }
        self.assertTrue(expected.issubset({command.name for command in rbot.bot.commands}))
        self.assertIs(rbot.bot.get_command("soundbytes"), rbot.soundboard)

    def test_audio_primitives_are_extracted_behind_compatible_entrypoint(self):
        self.assertEqual(rbot.TrackInfo.__module__, "revbot.audio.sources")
        self.assertEqual(rbot.AudioRequest.__module__, "revbot.audio.sources")
        self.assertEqual(rbot.BufferedPCMSource.__module__, "revbot.audio.sources")
        self.assertEqual(rbot.AudioMixer.__module__, "revbot.audio.mixer")
        self.assertEqual(rbot.RecentVoiceSink.__module__, "revbot.audio.capture")
        self.assertEqual(rbot.Player.__module__, "revbot.audio.player")
        self.assertEqual(rbot.PlayerManager.__module__, "revbot.audio.manager")
        self.assertEqual(rbot.cap.callback.__module__, "revbot.commands.soundboard")
        self.assertEqual(rbot.play.callback.__module__, "revbot.commands.music")
        self.assertEqual(rbot.join.callback.__module__, "revbot.commands.voice")
        self.assertEqual(rbot.about.callback.__module__, "revbot.commands.general")

    def test_config_expansion_and_initialization(self):
        with tempfile.TemporaryDirectory(dir=TEST_ENVIRONMENT) as directory:
            root = Path(directory)
            expired_capture = root / "samples" / "cap_expired.ogg"
            expired_capture.parent.mkdir()
            expired_capture.write_bytes(b"old capture")
            os.utime(expired_capture, (0, 0))
            (root / "config.cfg").write_text(
                "TOKEN:test-token\nSAMPLE_DIR:${RBOT_HOME}/samples\nTEMP_DIR:${RBOT_HOME}/temp\nFFMPEG:fake-ffmpeg\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"RBOT_HOME": str(root), "DISCORD_TOKEN": ""}, clear=False), \
                 patch.object(rbot, "_ffmpeg_is_available", return_value=True):
                self.assertEqual(rbot.globalInit(), 0)
            self.assertEqual(rbot.TOKEN, "test-token")
            self.assertEqual(rbot.SAMPLE_DIR, (root / "samples").resolve())
            self.assertEqual(rbot.TEMP_DIR, (root / "temp").resolve())
            self.assertEqual(rbot.FFMPEG_EXECUTABLE, "fake-ffmpeg")
            self.assertFalse(expired_capture.exists())

    def test_config_prefers_environment_token_and_rejects_missing_dependencies(self):
        with tempfile.TemporaryDirectory(dir=TEST_ENVIRONMENT) as directory:
            root = Path(directory)
            (root / "config.cfg").write_text("TOKEN:<Add-Token>\nFFMPEG:missing-ffmpeg\n", encoding="utf-8")
            with patch.dict("os.environ", {"RBOT_HOME": str(root), "DISCORD_TOKEN": "environment-token"}, clear=False), \
                 patch.object(rbot, "_ffmpeg_is_available", return_value=False):
                self.assertEqual(rbot.globalInit(), -1)
                self.assertEqual(rbot.TOKEN, "environment-token")
            with patch.dict("os.environ", {"RBOT_HOME": str(root), "DISCORD_TOKEN": ""}, clear=False):
                self.assertEqual(rbot.globalInit(), -1)

    def test_track_info_selects_audio_and_rejects_missing_url(self):
        track = rbot.TrackInfo.from_extractor_json({
            "title": "Representative video",
            "duration": 123,
            "formats": [{"format": "video only", "url": "video"}, {"format": "audio only", "url": "audio"}],
        })
        self.assertEqual((track.title, track.requestedUrl, track.duration), ("Representative video", "audio", 123))
        with self.assertRaisesRegex(ValueError, "audio URL"):
            rbot.TrackInfo.from_extractor_json({"formats": []})

    def test_soundbyte_request_uses_lighter_file_and_ffmpeg(self):
        self.assertTrue(SAMPLE_FILE.is_file(), "Samples/Lighter.wav is required by this test")
        request = rbot.AudioRequest(SAMPLE_FILE, requester="tester", title="Lighter")
        with patch.object(rbot.discord, "FFmpegPCMAudio", return_value="source") as ffmpeg:
            self.assertEqual(request.create_source(), "source")
        ffmpeg.assert_called_once_with(str(SAMPLE_FILE), executable="ffmpeg", before_options=None, options="-vn")

    def test_url_request_uses_reconnect_options(self):
        request = rbot.AudioRequest(YOUTUBE_URL, requester="tester")
        with patch.object(rbot.discord, "FFmpegPCMAudio", return_value="source") as ffmpeg:
            request.create_source()
        self.assertIn("-reconnect 1", ffmpeg.call_args.kwargs["before_options"])

    def test_track_extraction_invokes_yt_dlp_with_requested_url(self):
        payload = json.dumps({"title": "Test YouTube track", "url": "https://audio.example/stream", "duration": 9})
        completed = SimpleNamespace(returncode=0, stdout=payload, stderr="")
        with patch.object(rbot.subprocess, "run", return_value=completed) as run:
            track = rbot._extract_track_info(YOUTUBE_URL)
        self.assertEqual(track.requestedUrl, "https://audio.example/stream")
        self.assertEqual(run.call_args.args[0][-1], YOUTUBE_URL)
        self.assertEqual(run.call_args.args[0][2:4], ["yt_dlp", "--no-playlist"])

    def test_buffered_source_reads_and_cleans_up(self):
        source = FakeSource([b"frame"])
        buffered = rbot.BufferedPCMSource(source)
        self.assertEqual(buffered.read_frame(), b"frame")
        self.assertIsNone(buffered.read_frame())
        buffered.cleanup()
        self.assertTrue(source.cleaned)

    def test_audio_mixer_mixes_and_clamps_samples(self):
        music_request = Mock(create_source=Mock(return_value=FakeSource([pcm_frame(30000)])))
        sound_request = Mock(create_source=Mock(return_value=FakeSource([pcm_frame(30000)])))
        mixer = rbot.AudioMixer(deque([music_request]), deque([sound_request]), threading.RLock())
        samples = array.array("h")
        samples.frombytes(mixer.read())
        self.assertEqual(samples[0], 32767)
        self.assertEqual(len(samples), rbot.AudioMixer.FRAME_SAMPLES * rbot.AudioMixer.CHANNELS)
        mixer.cleanup()

    def test_audio_mixer_skips_current_music(self):
        first = Mock(create_source=Mock(return_value=FakeSource([pcm_frame(10), pcm_frame(10)])))
        second = Mock(create_source=Mock(return_value=FakeSource([pcm_frame(20)])))
        mixer = rbot.AudioMixer(deque([first, second]), deque(), threading.RLock())
        mixer.read()
        mixer.skip_music()
        frame = array.array("h")
        frame.frombytes(mixer.read())
        self.assertEqual(frame[0], int(20 * mixer.MUSIC_GAIN))
        mixer.cleanup()

    def test_audio_mixer_pauses_music_while_soundboard_continues(self):
        music = Mock(create_source=Mock(return_value=FakeSource([pcm_frame(100)])))
        sound = Mock(create_source=Mock(return_value=FakeSource([pcm_frame(200)])))
        mixer = rbot.AudioMixer(deque([music]), deque([sound]), threading.RLock())
        mixer.pause_music()

        paused_frame = array.array("h")
        paused_frame.frombytes(mixer.read())
        self.assertEqual(paused_frame[0], int(200 * mixer.SOUNDBOARD_GAIN))

        mixer.resume_music()
        resumed_frame = array.array("h")
        resumed_frame.frombytes(mixer.read())
        self.assertEqual(resumed_frame[0], int(100 * mixer.MUSIC_GAIN))
        mixer.cleanup()

    def test_audio_mixer_tracks_current_requests_and_applies_volume(self):
        music_request = SimpleNamespace(
            trackInfo=SimpleNamespace(title="Song"),
            create_source=Mock(return_value=FakeSource([pcm_frame(100)])),
        )
        mixer = rbot.AudioMixer(deque([music_request]), deque(), threading.RLock())
        mixer.set_master_volume(0.5)
        samples = array.array("h")
        samples.frombytes(mixer.read())
        self.assertEqual(samples[0], int(100 * mixer.MUSIC_GAIN * 0.5))
        self.assertIs(mixer.current_requests()[0], music_request)
        mixer.cleanup()

    def test_recent_voice_snapshot_encodes_ogg_opus_output(self):
        sink = rbot.RecentVoiceSink()
        sink.started_at = time.monotonic() - sink.BUFFER_SECONDS
        sink.write("speaker", SimpleNamespace(pcm=pcm_frame(100)))
        captured = sink.snapshot()
        self.assertEqual(len(captured), sink.BUFFER_FRAMES * sink.FRAME_BYTES)
        with tempfile.TemporaryDirectory(dir=TEST_ENVIRONMENT) as directory:
            output = Path(directory) / "capture.ogg"
            result = SimpleNamespace(returncode=0, stderr=b"")
            with patch.object(rbot.subprocess, "run", return_value=result) as run:
                rbot._write_capture(output, captured)
        command = run.call_args.args[0]
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("libopus", command)
        self.assertIn("ogg", command)
        self.assertEqual(command[-1], str(output))
        self.assertEqual(run.call_args.kwargs["input"], captured)
        sink.cleanup()

    def test_capture_encoder_reports_ffmpeg_failure(self):
        result = SimpleNamespace(returncode=1, stderr=b"encoder failed")
        with patch.object(rbot.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "encoder failed"):
                rbot._write_capture(Path("capture.ogg"), b"pcm")

    def test_player_queue_controls_and_disconnect(self):
        voice_client = FakeVoiceClient()
        player = rbot.Player.__new__(rbot.Player)
        player.voiceClient = voice_client
        player.music_queue = deque()
        player.sound_queue = deque()
        player._lock = threading.RLock()
        player._mixer = None
        player._music_paused = False
        player._music_queue_limit = rbot.MAX_MUSIC_QUEUE_ITEMS
        player._sound_queue_limit = rbot.MAX_SOUNDBOARD_QUEUE_ITEMS
        player.capture_sink = Mock()
        request = Mock()
        self.assertTrue(player.queue_music(request))
        self.assertTrue(voice_client.played)
        player.pausePlayer()
        self.assertTrue(player._music_paused)
        self.assertFalse(voice_client.paused)
        player.resumePlayer()
        self.assertFalse(player._music_paused)
        player.skipRequest()
        player.sound_queue.append(Mock())
        player.stopMusic()
        self.assertTrue(player.sound_queue)
        self.assertFalse(voice_client.stopped)
        player.stopAll()
        self.assertTrue(voice_client.stopped)
        self.assertFalse(player.music_queue)
        self.assertFalse(player.sound_queue)

        async def disconnect_player():
            with patch.object(rbot.voice_recv, "VoiceRecvClient", type("OtherClient", (), {})):
                await player.disconnect()
        asyncio.run(disconnect_player())
        self.assertTrue(voice_client.disconnected)

    def test_queue_limits_and_management_helpers(self):
        player = rbot.Player.__new__(rbot.Player)
        player.voiceClient = FakeVoiceClient()
        player.music_queue = deque([Mock()] * rbot.MAX_MUSIC_QUEUE_ITEMS)
        player.sound_queue = deque([Mock()] * rbot.MAX_SOUNDBOARD_QUEUE_ITEMS)
        player._lock = threading.RLock()
        player._mixer = None
        player._music_queue_limit = rbot.MAX_MUSIC_QUEUE_ITEMS
        player._sound_queue_limit = rbot.MAX_SOUNDBOARD_QUEUE_ITEMS

        with self.assertRaisesRegex(ValueError, "music queue is full"):
            player.queue_music(Mock())
        with self.assertRaisesRegex(ValueError, "soundboard queue is full"):
            player.queue_sound(Mock())

        first, second = SimpleNamespace(trackInfo=SimpleNamespace(title="First")), SimpleNamespace(trackInfo=SimpleNamespace(title="Second"))
        player.music_queue = deque([first, second])
        player.sound_queue = deque([Mock()])
        self.assertIs(player.remove_queued_music(2), second)
        self.assertIsNone(player.remove_queued_music(9))
        self.assertEqual(player.clear_queued_requests(), (1, 1))
        self.assertEqual(player.queued_requests(), ([], []))

    def test_capture_storage_evicts_oldest_automatic_captures(self):
        with tempfile.TemporaryDirectory(dir=TEST_ENVIRONMENT) as directory:
            sample_dir = Path(directory).resolve()
            oldest = sample_dir / "cap_oldest.wav"
            newest = sample_dir / "cap_newest.wav"
            oldest.write_bytes(b"12345")
            newest.write_bytes(b"67890")
            os.utime(oldest, (100, 100))
            os.utime(newest, (200, 200))
            rbot.SAMPLE_DIR = sample_dir
            with patch.object(rbot, "MAX_CAPTURE_STORAGE_BYTES", 10):
                rbot._make_room_for_capture(4)
            self.assertFalse(oldest.exists())
            self.assertTrue(newest.exists())
            with patch.object(rbot, "MAX_CAPTURE_STORAGE_BYTES", 3):
                with self.assertRaisesRegex(RuntimeError, "storage is full"):
                    rbot._make_room_for_capture(4)

    def test_expired_unnamed_captures_are_pruned_without_touching_named_soundbytes(self):
        with tempfile.TemporaryDirectory(dir=TEST_ENVIRONMENT) as directory:
            sample_dir = Path(directory).resolve()
            old_capture = sample_dir / "cap_old.ogg"
            fresh_capture = sample_dir / "cap_fresh.ogg"
            named_sound = sample_dir / "favorite.ogg"
            for sound in (old_capture, fresh_capture, named_sound):
                sound.write_bytes(b"audio")
            now = time.time()
            os.utime(old_capture, (now - rbot.UNNAMED_CAPTURE_RETENTION_SECONDS - 1, ) * 2)
            os.utime(named_sound, (now - rbot.UNNAMED_CAPTURE_RETENTION_SECONDS - 1, ) * 2)
            rbot.SAMPLE_DIR = sample_dir
            rbot.LAST_CAPTURES[(1, 2)] = old_capture

            self.assertEqual(rbot._delete_expired_unnamed_captures(now), 1)

            self.assertFalse(old_capture.exists())
            self.assertTrue(fresh_capture.exists())
            self.assertTrue(named_sound.exists())
            self.assertNotIn((1, 2), rbot.LAST_CAPTURES)

    def test_queue_management_commands(self):
        music = [SimpleNamespace(trackInfo=SimpleNamespace(title="Song"))]
        sounds = [SimpleNamespace(trackInfo=SimpleNamespace(title="Lighter"))]
        player = SimpleNamespace(
            queued_requests=Mock(return_value=(music, sounds)),
            clear_queued_requests=Mock(return_value=(1, 1)),
            remove_queued_music=Mock(return_value=music[0]),
        )
        context = SimpleNamespace(guild=SimpleNamespace(id=1), author="tester", send=AsyncMock())
        with patch.object(rbot, "_player_for_command", AsyncMock(return_value=player)):
            asyncio.run(rbot.showqueue.callback(context))
            asyncio.run(rbot.clear.callback(context))
            asyncio.run(rbot.remove.callback(context, 1))
        self.assertIn("Music queue", context.send.await_args_list[0].args[0])
        self.assertIn("Cleared 1 music", context.send.await_args_list[1].args[0])
        self.assertIn("Removed queued track: *Song*", context.send.await_args_list[2].args[0])

    def test_volume_command_shows_and_sets_volume(self):
        player = SimpleNamespace(getVolume=Mock(return_value=100), setVolume=Mock(return_value=75))
        context = SimpleNamespace(guild=SimpleNamespace(id=1), author="tester", send=AsyncMock())
        with patch.object(rbot, "_player_for_command", AsyncMock(return_value=player)):
            asyncio.run(rbot.volume.callback(context))
            asyncio.run(rbot.volume.callback(context, 75))
        self.assertIn("Current volume: 100%", context.send.await_args_list[0].args[0])
        player.setVolume.assert_called_once_with(75)
        self.assertEqual(context.send.await_args_list[1].args[0], "Volume set to 75%.")

    def test_now_command_lists_active_music_and_soundboard(self):
        music = SimpleNamespace(trackInfo=SimpleNamespace(title="Song"))
        sound = SimpleNamespace(trackInfo=SimpleNamespace(title="Lighter"))
        player = SimpleNamespace(nowPlaying=Mock(return_value=(music, sound, False)))
        context = SimpleNamespace(guild=SimpleNamespace(id=1), author="tester", send=AsyncMock())
        with patch.object(rbot, "_player_for_command", AsyncMock(return_value=player)):
            asyncio.run(rbot.now.callback(context))
        report = context.send.call_args.args[0]
        self.assertIn("Music: *Song*", report)
        self.assertIn("Soundboard: *Lighter*", report)

    def test_health_command_reports_readiness_without_sensitive_config(self):
        player = SimpleNamespace(
            nowPlaying=Mock(return_value=(None, None, False)),
            isPlaying=Mock(return_value=False),
            getVolume=Mock(return_value=100),
        )
        context = SimpleNamespace(guild=SimpleNamespace(id=1), send=AsyncMock())
        with patch.object(rbot, "_ffmpeg_is_available", return_value=True), \
             patch.object(rbot.playerManager, "getPlayerByGuildId", return_value=player), \
             patch.object(rbot.bot, "is_ready", return_value=True):
            asyncio.run(rbot.health.callback(context))
        report = context.send.call_args.args[0]
        self.assertIn("Discord: ready", report)
        self.assertIn("FFmpeg: ready", report)
        self.assertIn("100% volume", report)
        self.assertNotIn("TOKEN", report)

    def test_play_rejects_tracks_over_duration_limit(self):
        player = SimpleNamespace(queue_music=Mock())
        track = rbot.TrackInfo("Long track", "https://audio.example/stream", rbot.MAX_TRACK_DURATION_SECONDS + 1)
        context = SimpleNamespace(author="tester", send=AsyncMock())
        with patch.object(rbot.playerManager, "createAndRegisterNewPlayer", AsyncMock(return_value=player)), \
             patch.object(rbot, "_extract_track_info", return_value=track):
            asyncio.run(rbot.play.callback(context, YOUTUBE_URL))
        player.queue_music.assert_not_called()
        self.assertIn("longer than", context.send.call_args.args[0])

    def test_voice_channel_helpers(self):
        member = object()
        selected = SimpleNamespace(members=[member])
        guild = SimpleNamespace(voice_channels=[SimpleNamespace(members=[]), selected])
        self.assertIs(asyncio.run(rbot.getVoiceChannelByUsername(guild, member)), selected)
        self.assertIsNone(asyncio.run(rbot.getVoiceChannelByUsername(guild, object())))

    def test_about_command_sends_about_text(self):
        context = SimpleNamespace(send=AsyncMock())
        asyncio.run(rbot.about.callback(context))
        context.send.assert_awaited_once_with(rbot.message.getAboutMessage())

    def test_soundboard_command_queues_lighter(self):
        player = SimpleNamespace(queue_sound=Mock())
        context = SimpleNamespace(author="tester", send=AsyncMock())
        with patch.object(rbot.playerManager, "createAndRegisterNewPlayer", AsyncMock(return_value=player)):
            asyncio.run(rbot.v.callback(context, "Lighter.wav"))
        queued = player.queue_sound.call_args.args[0]
        self.assertEqual(Path(queued.source), SAMPLE_FILE)
        context.send.assert_awaited_once_with(rbot.message.getString("Soundbyte"))

    def test_capture_command_saves_snapshot(self):
        with tempfile.TemporaryDirectory(dir=TEST_ENVIRONMENT) as directory:
            root = Path(directory)
            rbot.SAMPLE_DIR = root / "samples"
            rbot.SAMPLE_DIR.mkdir()
            rbot.TEMP_DIR = root / "temp"
            player = SimpleNamespace(
                getPlayerChannel=Mock(return_value="voice"),
                capture_recent_voice=Mock(return_value=b"captured-pcm"),
            )
            context = SimpleNamespace(
                guild=SimpleNamespace(id=1), author=SimpleNamespace(id=2), send=AsyncMock()
            )

            def encode_capture(path, pcm):
                path.write_bytes(b"compressed-ogg")

            with patch.object(rbot.playerManager, "getPlayerByGuildId", return_value=player), \
                 patch.object(rbot, "getVoiceChannelByUsername", AsyncMock(return_value="voice")), \
                 patch.object(rbot, "_make_room_for_capture") as make_room, \
                 patch.object(rbot, "_write_capture", side_effect=encode_capture) as write_capture:
                asyncio.run(rbot.cap.callback(context))
            self.assertEqual(write_capture.call_args.args[1], b"captured-pcm")
            self.assertEqual(write_capture.call_args.args[0].suffix, ".ogg")
            make_room.assert_called_once_with(len(b"compressed-ogg"))
            self.assertIn("Saved the last five seconds", context.send.call_args.args[0])
            saved_capture = rbot.LAST_CAPTURES[(1, 2)]
            self.assertNotIn(saved_capture.name, context.send.call_args.args[0])
            self.assertIn("!renamecap <name>", context.send.call_args.args[0])
            self.assertTrue(saved_capture.is_file())
            self.assertEqual(saved_capture.suffix, ".ogg")

    def test_renamecap_renames_latest_capture_and_preserves_ogg_extension(self):
        with tempfile.TemporaryDirectory(dir=TEST_ENVIRONMENT) as directory:
            sample_dir = Path(directory).resolve()
            source = sample_dir / "cap_latest.ogg"
            source.write_bytes(b"captured audio")
            rbot.SAMPLE_DIR = sample_dir
            rbot.LAST_CAPTURES[(7, 42)] = source
            context = SimpleNamespace(guild=SimpleNamespace(id=7), author=SimpleNamespace(id=42), send=AsyncMock())

            asyncio.run(rbot.renamecap.callback(context, new_name="My Favorite Sound"))

            destination = sample_dir / "My Favorite Sound.ogg"
            self.assertFalse(source.exists())
            self.assertEqual(destination.read_bytes(), b"captured audio")
            self.assertEqual(rbot.LAST_CAPTURES[(7, 42)], destination)
            self.assertIn("!v My Favorite Sound.ogg", context.send.call_args.args[0])

    def test_renamecap_cannot_claim_a_capture_after_restart(self):
        with tempfile.TemporaryDirectory(dir=TEST_ENVIRONMENT) as directory:
            sample_dir = Path(directory).resolve()
            older = sample_dir / "cap_older.ogg"
            latest = sample_dir / "cap_latest.ogg"
            older.write_bytes(b"older")
            latest.write_bytes(b"latest")
            os.utime(older, (100, 100))
            os.utime(latest, (200, 200))
            rbot.SAMPLE_DIR = sample_dir
            context = SimpleNamespace(guild=SimpleNamespace(id=7), author=SimpleNamespace(id=42), send=AsyncMock())

            asyncio.run(rbot.renamecap.callback(context, new_name="Recovered"))

            self.assertTrue(older.exists())
            self.assertTrue(latest.exists())
            self.assertFalse((sample_dir / "Recovered.ogg").exists())
            context.send.assert_awaited_once_with("You don't have a recent capture to rename. Run `!cap` first.")

    def test_renamecap_rejects_invalid_names_and_existing_files(self):
        with tempfile.TemporaryDirectory(dir=TEST_ENVIRONMENT) as directory:
            sample_dir = Path(directory).resolve()
            source = sample_dir / "cap_latest.ogg"
            existing = sample_dir / "Taken.ogg"
            source.write_bytes(b"capture")
            existing.write_bytes(b"keep this")
            rbot.SAMPLE_DIR = sample_dir
            rbot.LAST_CAPTURES[(7, 42)] = source
            context = SimpleNamespace(guild=SimpleNamespace(id=7), author=SimpleNamespace(id=42), send=AsyncMock())

            asyncio.run(rbot.renamecap.callback(context, new_name="../escape"))
            self.assertTrue(source.exists())
            asyncio.run(rbot.renamecap.callback(context, new_name="Taken"))
            self.assertEqual(existing.read_bytes(), b"keep this")
            self.assertEqual(context.send.await_count, 2)

    def test_renamecap_rejects_invalid_filename_strings(self):
        invalid_names = [
            "",
            "   ",
            "../escape",
            "bad:name",
            "bad?name",
            "CON",
            "LPT1.ogg",
            "sound.mp3",
            "sound.wav",
        ]
        with tempfile.TemporaryDirectory(dir=TEST_ENVIRONMENT) as directory:
            sample_dir = Path(directory).resolve()
            source = sample_dir / "cap_latest.ogg"
            source.write_bytes(b"capture")
            rbot.SAMPLE_DIR = sample_dir
            rbot.LAST_CAPTURES[(7, 42)] = source
            context = SimpleNamespace(guild=SimpleNamespace(id=7), author=SimpleNamespace(id=42), send=AsyncMock())

            for invalid_name in invalid_names:
                with self.subTest(name=invalid_name):
                    asyncio.run(rbot.renamecap.callback(context, new_name=invalid_name))
                    self.assertTrue(source.exists())
                    self.assertEqual(source.read_bytes(), b"capture")

            self.assertEqual(context.send.await_count, len(invalid_names))

    def test_soundboard_lists_audio_files_and_excludes_other_files(self):
        with tempfile.TemporaryDirectory(dir=TEST_ENVIRONMENT) as directory:
            sample_dir = Path(directory).resolve()
            (sample_dir / "Zebra.ogg").write_bytes(b"ogg")
            (sample_dir / "alpha.wav").write_bytes(b"wav")
            (sample_dir / "cover.jpg").write_bytes(b"image")
            rbot.SAMPLE_DIR = sample_dir
            context = SimpleNamespace(send=AsyncMock())

            asyncio.run(rbot.soundboard.callback(context))

            listing = context.send.call_args.args[0]
            self.assertIn("alpha.wav", listing)
            self.assertIn("Zebra.ogg", listing)
            self.assertNotIn("cover.jpg", listing)
            self.assertLess(listing.index("alpha.wav"), listing.index("Zebra.ogg"))

    def test_capture_command_reports_when_no_voice_audio_is_available(self):
        player = SimpleNamespace(
            getPlayerChannel=Mock(return_value="voice"),
            capture_recent_voice=Mock(side_effect=RuntimeError("No voice audio was received during the last five seconds.")),
        )
        context = SimpleNamespace(
            guild=SimpleNamespace(id=1), author="tester", send=AsyncMock()
        )
        with patch.object(rbot.playerManager, "getPlayerByGuildId", return_value=player), \
             patch.object(rbot, "getVoiceChannelByUsername", AsyncMock(return_value="voice")):
            asyncio.run(rbot.cap.callback(context))

        context.send.assert_awaited_once_with("No voice audio was received during the last five seconds.")

    def test_play_command_queues_representative_youtube_url(self):
        player = SimpleNamespace(queue_music=Mock(return_value=True))
        track = rbot.TrackInfo("Test YouTube track", "https://audio.example/stream")
        context = SimpleNamespace(author="tester", send=AsyncMock())
        with patch.object(rbot.playerManager, "createAndRegisterNewPlayer", AsyncMock(return_value=player)), \
             patch.object(rbot, "_extract_track_info", return_value=track) as extract:
            asyncio.run(rbot.play.callback(context, YOUTUBE_URL))
        self.assertEqual(extract.call_args.args[0], YOUTUBE_URL)
        self.assertEqual(player.queue_music.call_args.args[0].source, track.requestedUrl)
        self.assertIn(track.title, context.send.call_args.args[0])

    def test_playback_control_commands_delegate_to_player(self):
        player = SimpleNamespace(
            pausePlayer=Mock(), resumePlayer=Mock(), skipRequest=Mock(),
            stopMusic=Mock(), stopAll=Mock(),
        )
        context = SimpleNamespace(guild=SimpleNamespace(id=1), send=AsyncMock())
        with patch.object(rbot, "_player_for_command", AsyncMock(return_value=player)):
            asyncio.run(rbot.pause.callback(context))
            asyncio.run(rbot.resume.callback(context))
            asyncio.run(rbot.skip.callback(context))
            asyncio.run(rbot.stop.callback(context))
            asyncio.run(rbot.stopall.callback(context))
        player.pausePlayer.assert_called_once()
        player.resumePlayer.assert_called_once()
        player.skipRequest.assert_called_once()
        player.stopMusic.assert_called_once()
        player.stopAll.assert_called_once()
        self.assertEqual(context.send.await_count, 5)

    def test_join_and_leave_commands_delegate_to_player_manager(self):
        context = SimpleNamespace(guild=SimpleNamespace(id=4), send=AsyncMock())
        with patch.object(rbot.playerManager, "createAndRegisterNewPlayer", AsyncMock()), \
             patch.object(rbot.playerManager, "destroyPlayer", AsyncMock()) as destroy:
            asyncio.run(rbot.join.callback(context))
            asyncio.run(rbot.leave.callback(context))
        destroy.assert_awaited_once_with(4)
        self.assertEqual(context.send.await_count, 2)

    def test_test_command_reports_runner_result(self):
        context = SimpleNamespace(send=AsyncMock())
        result = SimpleNamespace(returncode=0, stdout="Ran isolated tests", stderr="")
        with patch.object(rbot.subprocess, "run", return_value=result) as run:
            asyncio.run(rbot.test.callback(context))
        self.assertEqual(run.call_args.args[0][1], str(PROJECT_DIR / "rbot_tests.py"))
        self.assertIn("Self-tests passed", context.send.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
