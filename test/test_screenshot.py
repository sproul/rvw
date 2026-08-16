"""Tests for archival screen capture: archive layout, metadata, failures.

The Swift helper itself needs a screen recording permission and a real window,
so these tests drive the python side against a stand-in helper script. What is
tested is what the assistant depends on: where the image lands, what metadata is
recorded beside it, and that a failing capture is reported rather than hidden.
"""

import json
import stat
import tempfile
import time
import unittest
from pathlib import Path

from rvw import config, screenshot

SESSION_EPOCH = time.mktime((2026, 8, 15, 21, 30, 0, 0, 0, -1))
CAPTURE_EPOCH = time.mktime((2026, 8, 15, 23, 41, 7, 0, 0, -1)) + 0.123

successful_helper = """#!/bin/sh
output=""
while [ $# -gt 0 ]; do
  case "$1" in
    --output) output=$2; shift 2 ;;
    *) shift ;;
  esac
done
printf 'pretend png bytes' > "$output"
echo '{"target":"window","application":"Zoom","window_title":"Weekly sync","display_id":1,"width":1512,"height":982}'
echo "OK   captured the frontmost window" >&2
"""

failing_helper = """#!/bin/sh
echo "FAIL screen recording permission denied" >&2
exit 3
"""


def install_helper(directory, source):
    path = Path(directory) / "screen_capture"
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class ScreenshotTestCase(unittest.TestCase):
    """Point the archive and the helper at a temporary directory."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.saved_archive_dir = config.archive_dir
        self.saved_helper_path = config.screen_capture_helper_path
        config.archive_dir = self.root / "meetings"
        self.addCleanup(self.restore_configuration)

    def restore_configuration(self):
        config.archive_dir = self.saved_archive_dir
        config.screen_capture_helper_path = self.saved_helper_path
        self.temporary_directory.cleanup()

    def use_helper(self, source):
        config.screen_capture_helper_path = install_helper(self.root, source)

    def capture(self):
        return screenshot.capture_screenshot(SESSION_EPOCH, now=CAPTURE_EPOCH)


class SuccessfulCaptureTest(ScreenshotTestCase):

    def setUp(self):
        super().setUp()
        self.use_helper(successful_helper)
        self.result = self.capture()

    def test_the_image_lands_in_the_dated_archive_layout(self):
        relative = self.result.image_path.relative_to(config.archive_dir)
        self.assertEqual(("2026", "08", "2026-08-15_21.30", "screenshots"),
                         relative.parts[:4])

    def test_the_image_file_name_carries_the_capture_timestamp(self):
        self.assertEqual("2026-08-15_23.41.07.123.png", self.result.image_path.name)

    def test_the_image_was_actually_written(self):
        self.assertEqual("pretend png bytes",
                         self.result.image_path.read_text(encoding="utf-8"))

    def test_metadata_is_written_beside_the_image(self):
        self.assertEqual(self.result.image_path.with_suffix(".json"),
                         self.result.metadata_path)
        self.assertTrue(self.result.metadata_path.exists())

    def test_metadata_records_the_application_window_and_timestamps(self):
        recorded = json.loads(self.result.metadata_path.read_text(encoding="utf-8"))
        self.assertEqual("Zoom", recorded["application"])
        self.assertEqual("Weekly sync", recorded["window_title"])
        self.assertEqual(self.result.image_path.name, recorded["image"])
        self.assertAlmostEqual(CAPTURE_EPOCH, recorded["captured_epoch"], places=3)
        self.assertTrue(recorded["captured_local"].startswith("2026-08-15T23:41:07"))

    def test_a_second_capture_in_the_same_session_reuses_the_session_directory(self):
        again = screenshot.capture_screenshot(SESSION_EPOCH, now=CAPTURE_EPOCH + 1)
        self.assertEqual(self.result.image_path.parent, again.image_path.parent)
        self.assertNotEqual(self.result.image_path, again.image_path)

    def test_the_image_can_be_read_back_as_a_data_uri_for_the_vision_model(self):
        data_uri = screenshot.read_image_as_data_uri(self.result.image_path)
        self.assertTrue(data_uri.startswith("data:image/png;base64,"))


class FailingCaptureTest(ScreenshotTestCase):

    def test_a_failing_helper_is_reported_with_its_own_diagnostic(self):
        self.use_helper(failing_helper)
        with self.assertRaises(RuntimeError) as raised:
            self.capture()
        self.assertIn("permission denied", str(raised.exception))

    def test_a_failing_helper_leaves_no_metadata_behind(self):
        self.use_helper(failing_helper)
        with self.assertRaises(RuntimeError):
            self.capture()
        self.assertEqual([], sorted(config.archive_dir.rglob("*.json")))

    def test_a_missing_helper_says_how_to_build_it(self):
        config.screen_capture_helper_path = self.root / "not_built"
        with self.assertRaises(RuntimeError) as raised:
            self.capture()
        self.assertIn("build.sh", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
