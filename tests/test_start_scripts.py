"""Checks that each platform has a standalone bot startup script."""

import shutil
import subprocess
import unittest
import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


class StartScriptTests(unittest.TestCase):
    def test_windows_start_script_uses_project_venv_and_propagates_exit_code(self):
        script = (PROJECT_DIR / "start.ps1").read_text(encoding="utf-8")

        self.assertIn("$ProjectRoot = $PSScriptRoot", script)
        self.assertIn('$env:RBOT_HOME = $ProjectRoot', script)
        self.assertIn('".venv\\Scripts\\python.exe"', script)
        self.assertIn('"rbot.py"', script)
        self.assertIn('"config.cfg"', script)
        self.assertIn("& $PythonPath $BotPath", script)
        self.assertIn("exit $LASTEXITCODE", script)

    def test_linux_start_script_uses_project_venv_and_execs_bot(self):
        script_path = PROJECT_DIR / "start.sh"
        script = script_path.read_text(encoding="utf-8")

        self.assertIn('"${BASH_SOURCE[0]}"', script)
        self.assertIn('export RBOT_HOME="$PROJECT_ROOT"', script)
        self.assertIn('"$PROJECT_ROOT/.venv/bin/python"', script)
        self.assertIn('"$PROJECT_ROOT/rbot.py"', script)
        self.assertIn('"$PROJECT_ROOT/config.cfg"', script)
        self.assertIn('exec "$PYTHON_PATH" "$BOT_PATH"', script)

        bash = shutil.which("bash") if os.name != "nt" else None
        if bash:
            subprocess.run([bash, "-n", str(script_path)], check=True)


if __name__ == "__main__":
    unittest.main()
