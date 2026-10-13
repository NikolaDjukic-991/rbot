"""Guild-local soundboard storage helpers."""

import re
from pathlib import Path


def guild_sound_directory(sample_dir, guild):
    """Return the stable, guild-name-labeled sound directory under Samples."""
    guild_id = getattr(guild, "id", None)
    if guild_id is None:
        raise ValueError("Soundbytes can only be saved or played in a server.")

    guild_name = getattr(guild, "name", "") or "guild"
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", guild_name.strip()).strip("-_ ").lower()
    slug = (slug or "guild")[:64].rstrip("-_") or "guild"

    root = Path(sample_dir).resolve()
    directory = (root / "guild-{}-{}".format(slug, guild_id)).resolve()
    if root not in directory.parents:
        raise ValueError("Invalid server sound directory.")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def global_sound_directory(sample_dir):
    """Create and return the soundboard directory shared with every guild."""
    root = Path(sample_dir).resolve()
    directory = (root / "globals").resolve()
    if root not in directory.parents:
        raise ValueError("Invalid global sound directory.")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def has_revbot_admin_role(ctx):
    """Whether the invoking member has the explicit bot-admin role."""
    roles = getattr(ctx.author, "roles", ()) or ()
    return any((getattr(role, "name", "") or "").casefold() == "revbot_admin" for role in roles)
