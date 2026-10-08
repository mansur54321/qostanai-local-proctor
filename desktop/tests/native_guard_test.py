"""Run only inside an isolated Xvfb server: xvfb-run -a python tests/native_guard_test.py."""
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import unittest


@unittest.skipUnless(os.environ.get("PROCTOR_TEST_XVFB") == "1" and os.environ.get("DISPLAY")
                     and not os.environ.get("WAYLAND_DISPLAY") and os.environ.get("XDG_SESSION_TYPE") == "x11",
                     "Native grabs run only with explicit PROCTOR_TEST_XVFB=1 inside isolated Xvfb")
class GuardReleaseTest(unittest.TestCase):
    def start_guard(self):
        guard = Path(__file__).resolve().parents[1] / "keyboard_guard.py"
        process = subprocess.Popen([sys.executable, str(guard)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            if not selector.select(5):
                process.kill()
                process.wait()
                self.fail("Guard did not report startup")
            return process, json.loads(process.stdout.readline())
        finally:
            selector.close()

    def test_grabs_conflict_then_release(self):
        self.assertTrue(os.environ.get("DISPLAY"), "Requires isolated Xvfb")
        first, ready = self.start_guard()
        try:
            self.assertEqual(ready["type"], "ready")
            second, rejected = self.start_guard()
            second.wait(timeout=5)
            self.assertEqual(rejected["type"], "error")
        finally:
            first.terminate()
            first.wait(timeout=5)
        third, restored = self.start_guard()
        try:
            self.assertEqual(restored["type"], "ready")
        finally:
            third.terminate()
            third.wait(timeout=5)
        for process in (first, second, third):
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()

    def test_parent_pipe_loss_releases_grabs(self):
        first, ready = self.start_guard()
        self.assertEqual(ready["type"], "ready")
        first.stdin.close()
        first.wait(timeout=5)
        second, restored = self.start_guard()
        try:
            self.assertEqual(restored["type"], "ready")
        finally:
            second.terminate()
            second.wait(timeout=5)
            second.stdin.close()
            for process in (first, second):
                process.stdout.close()
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
