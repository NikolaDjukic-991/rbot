"""Shared command permission checks."""


async def require_revbot_admin(ctx, services):
    if services.is_revbot_admin(ctx):
        return True
    await ctx.send("This command is restricted to members with the `revbot_admin` role.")
    return False
