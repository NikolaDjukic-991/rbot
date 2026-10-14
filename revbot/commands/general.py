"""General information, diagnostics, and self-test commands."""

import asyncio
import subprocess
import sys

import discord

from revbot.commands.permissions import require_revbot_admin


COMMAND_GROUPS = (
    ("Music", ("play", "pause", "resume", "skip", "stop", "stopall", "volume", "now", "queue", "clear", "remove")),
    ("Soundboard", ("v", "cap", "renamecap", "soundboard")),
    ("Voice", ("join", "leave")),
    ("General", ("about", "help")),
    ("Admin · revbot_admin role required", ("channel", "health", "test")),
)

COMMAND_USAGE = {
    "play": "!play <URL>",
    "volume": "!volume [0-200]",
    "remove": "!remove <position>",
    "v": "!v <sound>",
    "renamecap": "!renamecap <name>",
    "channel": "!channel [#text-channel]",
    "help": "!help [command]",
}


def register(bot, services):
    bot.remove_command("help")

    @bot.command()
    async def about(ctx):
        """Show information about the bot."""
        await ctx.send(services.messages.getAboutMessage())

    @bot.command()
    async def help(ctx, *, command_name: str = None):
        """Show commands by category, or details for one command."""
        if command_name:
            command = bot.get_command(command_name.strip())
            if command is None:
                await ctx.send("I couldn't find the command `{}`. Try `!help` for the command list.".format(command_name))
                return
            usage = COMMAND_USAGE.get(command.name, "!" + command.name)
            description = command.help or command.short_doc or "No description is available."
            if command.name in {"channel", "health", "test"}:
                description += "\n\nRequires the `revbot_admin` role."
            embed = discord.Embed(title=usage, description=description, color=discord.Color.blurple())
            if command.aliases:
                embed.add_field(name="Aliases", value=", ".join("`{}`".format(alias) for alias in command.aliases), inline=False)
            await ctx.send(embed=embed)
            return

        embed = discord.Embed(
            title="Revbot commands",
            description="Use `!help <command>` for details. Commands work in the configured text channel.",
            color=discord.Color.blurple(),
        )
        for category, command_names in COMMAND_GROUPS:
            lines = []
            for name in command_names:
                command = bot.get_command(name)
                if command is None:
                    continue
                usage = COMMAND_USAGE.get(name, "!" + name)
                description = command.short_doc or ""
                lines.append("`{}`{}".format(usage, " — " + description if description else ""))
            if lines:
                embed.add_field(name=category, value="\n".join(lines), inline=False)
        await ctx.send(embed=embed)

    @bot.command(name="test")
    async def test(ctx):
        """Run the isolated rbot unit-test suite (revbot_admin only)."""
        if not await require_revbot_admin(ctx, services):
            return
        runner = services.runner_path()
        loop = asyncio.get_running_loop()

        def run_tests():
            return subprocess.run(
                [sys.executable, str(runner)],
                cwd=str(runner.parent),
                capture_output=True,
                text=True,
            )

        result = await loop.run_in_executor(None, run_tests)
        output = (result.stdout + result.stderr).strip()
        # Keep a failed test report useful without exceeding Discord's message limit.
        output = output[-1500:] if output else "No test output was produced."
        if result.returncode == 0:
            await ctx.send("Self-tests passed.\n```\n{}\n```".format(output))
        else:
            await ctx.send("Self-tests failed.\n```\n{}\n```".format(output))
    return {name: bot.get_command(name) for name in ['about', 'help', 'test']}
