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


class HelperInvocationTest(ScreenshotTestCase):
    """The python side must pass the chosen target and any window exclusion
    through to the helper verbatim."""

    def install_recording_helper(self):
        args_path = self.root / "helper_args.txt"
        source = """#!/bin/sh
printf '%%s\\n' "$@" > "%s"
output=""
while [ $# -gt 0 ]; do
  case "$1" in
    --output) output=$2; shift 2 ;;
    *) shift ;;
  esac
done
printf 'pretend png bytes' > "$output"
echo '{"target":"display","application":"Terminal","window_title":"rvw","display_id":1}'
""" % args_path
        self.use_helper(source)
        return args_path

    def test_a_display_capture_with_an_excluded_window_passes_both_flags(self):
        args_path = self.install_recording_helper()
        screenshot.capture_screenshot(SESSION_EPOCH, now=CAPTURE_EPOCH,
                                      target="display", exclude_window_id=4242)
        self.assertEqual(["--output", "--target", "display",
                          "--exclude-window-id", "4242"],
                         [a for a in args_path.read_text().splitlines()
                          if not a.endswith(".png")])

    def test_a_plain_capture_sends_the_configured_target_and_no_exclusion(self):
        args_path = self.install_recording_helper()
        screenshot.capture_screenshot(SESSION_EPOCH, now=CAPTURE_EPOCH)
        arguments = args_path.read_text().splitlines()
        self.assertIn("frontmost", arguments)
        self.assertNotIn("--exclude-window-id", arguments)


hdmi_helper_template = """#!/bin/sh
printf '%%s\\n' "$@" > "%(args_path)s"
output=""
while [ $# -gt 0 ]; do
  case "$1" in
    --output) output=$2; shift 2 ;;
    *) shift ;;
  esac
done
printf 'pretend png bytes' > "$output"
echo '{"target":"hdmi","device":"Elgato 4K X","width":3840,"height":2160}'
"""


class HdmiCaptureTest(ScreenshotTestCase):
    """With the HDMI source the image is the other Mac's screen, read from the
    capture card by bin/hdmi_capture; the archive is exactly the same."""

    def setUp(self):
        super().setUp()
        self.saved_source = config.screenshot_source
        self.saved_hdmi_helper_path = config.hdmi_capture_helper_path
        self.addCleanup(self.restore_hdmi_configuration)
        config.screenshot_source = "hdmi"
        self.args_path = self.root / "hdmi_args.txt"
        config.hdmi_capture_helper_path = self.root / "hdmi_capture"
        config.hdmi_capture_helper_path.write_text(
            hdmi_helper_template % {"args_path": self.args_path}, encoding="utf-8")
        config.hdmi_capture_helper_path.chmod(0o755)
        self.use_helper(failing_helper)          # the screen helper must not be used

    def restore_hdmi_configuration(self):
        config.screenshot_source = self.saved_source
        config.hdmi_capture_helper_path = self.saved_hdmi_helper_path

    def test_the_capture_card_helper_is_asked_for_the_configured_device(self):
        self.capture()
        arguments = self.args_path.read_text().splitlines()
        self.assertEqual(["--output", "--device", config.hdmi_capture_device_name],
                         [a for a in arguments if not a.endswith(".png")])

    def test_the_frame_is_archived_with_the_device_in_its_metadata(self):
        result = self.capture()
        recorded = json.loads(result.metadata_path.read_text(encoding="utf-8"))
        self.assertEqual("hdmi", recorded["target"])
        self.assertEqual("Elgato 4K X", recorded["device"])
        self.assertEqual(result.image_path.name, recorded["image"])

    def test_a_screen_target_makes_no_sense_for_the_capture_card(self):
        with self.assertRaises(ValueError):
            screenshot.capture_screenshot(SESSION_EPOCH, now=CAPTURE_EPOCH,
                                          target="display", exclude_window_id=4242)

    def test_a_missing_capture_card_helper_says_how_to_build_it(self):
        config.hdmi_capture_helper_path = self.root / "not_built"
        with self.assertRaises(RuntimeError) as raised:
            self.capture()
        self.assertIn("build.sh", str(raised.exception))


class ScreenshotSourceTest(unittest.TestCase):

    def test_an_unknown_source_is_refused(self):
        with self.assertRaises(ValueError):
            config.require_known_screenshot_source("hdmi2")

    def test_both_sources_are_known(self):
        for source in ["hdmi", "screen"]:
            config.require_known_screenshot_source(source)


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
