"""Tests for supervision of the audio capture helper process."""

import os
import stat
import tempfile
import unittest
from pathlib import Path

from rvw import config
from rvw.audio_source import CaptureStream


def write_helper_script(directory, body):
    path = Path(directory) / "audio_capture"
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class CaptureStreamStartTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.original_helper_path = config.capture_helper_path

    def tearDown(self):
        config.capture_helper_path = self.original_helper_path

    def use_helper(self, body):
        config.capture_helper_path = write_helper_script(self.directory.name, body)

    def test_a_helper_that_refuses_to_start_is_reported_as_a_failure(self):
        # A denied microphone makes the helper print FAIL and exit at once; the
        # command must not answer OK for a stream that is already dead.
        self.use_helper('echo "FAIL microphone access is denied" >&2; exit 1')
        stream = CaptureStream("mic", lambda *_: None)
        with self.assertRaises(RuntimeError):
            stream.start()
        self.assertFalse(stream.is_running)

    def test_a_healthy_helper_keeps_running(self):
        self.use_helper("sleep 30")
        stream = CaptureStream("mic", lambda *_: None)
        self.addCleanup(stream.stop)
        self.assertTrue(stream.start())
        self.assertTrue(stream.is_running)

    def test_a_missing_helper_is_reported_before_anything_is_spawned(self):
        config.capture_helper_path = Path(self.directory.name) / "not_built_yet"
        with self.assertRaises(RuntimeError):
            CaptureStream("mic", lambda *_: None).start()


if __name__ == "__main__":
    unittest.main()
