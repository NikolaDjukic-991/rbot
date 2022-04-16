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

GLYPHS = {
     "play-button"      : "\U000025B6",
     "pause-button"     : "\U000023F8",
     "stop-button"      : "\U000023F9",
     "music-note"       : "\U0001F3B6",
     "glyph-zzz"        : "\U0001F4A4",
     "next-button"      : "\U000023E0",
     "playpause-button" : "\U000023EF",
     "glyph-headphone"  : "\U0001F3A7"
}

ST_STR =  {
    "Soundbyte" : GLYPHS["play-button"]      + " Playing Soundbyte.",
    "SongPlay"  : GLYPHS["play-button"]      + " Playing ",
    "Stop"      : GLYPHS["stop-button"]      + " Stopping.",
    "Join"      : GLYPHS["music-note"]       + " Joined channel ",
    "Leave"     : GLYPHS["glyph-zzz"]        + " Leaving.",
    "Skip"      : GLYPHS["next-button"]      + " Skipping.",
    "Pause"     : GLYPHS["pause-button"]     + " Paused playing.",
    "Resume"    : GLYPHS["playpause-button"] + " Resuming.",
    "Queued"    : GLYPHS["glyph-headphone"]  + " Queued "
}

ABOUT_BOT = "A simple discord music bot. Source can be found at: https://github.com/NikolaDjukic-991/rbot\n"

#
# This way of getting strings is preferred to direct access so
# in case the way we obtain strings is changed we won't need to change other code.
# 
def getString(key):
    return ST_STR[key]

def getAboutMessage():
    return ABOUT_BOT

