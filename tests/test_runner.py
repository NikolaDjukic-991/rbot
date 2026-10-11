"""Tests for the test-runner sandbox lifecycle."""

import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import rbot_tests  # noqa: E402


class TestRunnerSandboxTests(unittest.TestCase):
    def test_reset_removes_previous_artifacts(self):
        rbot_tests.reset_test_environment()
        artifact = rbot_tests.TEST_ENVIRONMENT / "previous-run-artifact.wav"
        artifact.write_bytes(b"test artifact")

        rbot_tests.reset_test_environment()

        self.assertTrue(rbot_tests.TEST_ENVIRONMENT.is_dir())
        self.assertFalse(artifact.exists())

    def tearDown(self):
        rbot_tests.clear_test_environment()


if __name__ == "__main__":
    unittest.main()
