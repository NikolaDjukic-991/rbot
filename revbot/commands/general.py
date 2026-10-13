"""General information, diagnostics, and self-test commands."""

import asyncio
import subprocess
import sys



def register(bot, services):
    @bot.command()
    async def about(ctx):
        """Show information about the bot."""
        await ctx.send(services.messages.getAboutMessage())

    @bot.command(name="test")
    async def test(ctx):
        """Run the isolated rbot unit-test suite."""
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
    return {name: bot.get_command(name) for name in ['about', 'test']}
