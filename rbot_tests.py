"""Run rbot's isolated unit tests without starting the Discord client."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


TEST_ENVIRONMENT = Path(tempfile.gettempdir()).resolve() / "rbot-self-tests"


def reset_test_environment():
    """Create an empty, runner-owned sandbox for each test invocation."""
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if TEST_ENVIRONMENT.parent != temporary_root:
        raise RuntimeError("Refusing to clear a test directory outside the system temporary directory")
    if TEST_ENVIRONMENT.exists():
        shutil.rmtree(TEST_ENVIRONMENT)
    TEST_ENVIRONMENT.mkdir()
    os.environ["RBOT_TEST_ENV"] = str(TEST_ENVIRONMENT)


def clear_test_environment():
    if TEST_ENVIRONMENT.exists():
        shutil.rmtree(TEST_ENVIRONMENT)


def main():
    reset_test_environment()
    try:
        test_dir = Path(__file__).with_name("tests")
        suite = unittest.defaultTestLoader.discover(str(test_dir), pattern="test_*.py")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    finally:
        clear_test_environment()


if __name__ == "__main__":
    sys.exit(main())
