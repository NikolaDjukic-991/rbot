

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
    "Queued"    : GLYPHS["glyph-headphone"]  + "Queued "
}

#
# This way of getting strings is preferred to direct access so
# in case the way we obtain strings is changed we won't need to change other code.
# 
def getString(key):
    return ST_STR[key]
