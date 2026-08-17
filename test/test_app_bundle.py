"""Tests for rvw.app, the application bundle that owns the macOS permissions.

macOS attributes microphone, system audio and screen recording to the
application responsible for the process that asks, never to the helper itself.
Launching the daemon from a terminal or an editor therefore made the permissions
belong to that terminal or editor, and Emacs.app can never hold a microphone
grant at all. rvw.app exists so that the assistant is its own responsible
application: granted once, permanent from then on.

That permanence is the whole point and it is fragile, because an ad hoc
signature is pinned to the exact bytes of the binary:

    # designated => cdhash H"3e6066c51eb7fef93e36317f1165c2a7a7b79077"

Rebuild the launcher and the cdhash changes, the stored grant no longer matches
and macOS asks again. The bundle therefore holds nothing but a frozen launcher,
and the test that matters most here is the one asserting that building twice
leaves the signature alone.
"""

import plistlib
import re
import subprocess
import unittest
from pathlib import Path

repo_dir = Path(__file__).resolve().parents[1]
build_app_script = repo_dir / "helper" / "build_app.sh"
app_bundle = repo_dir / "bin" / "rvw.app"
launcher_path = app_bundle / "Contents" / "MacOS" / "rvw_launcher"
information_property_list = app_bundle / "Contents" / "Info.plist"

required_usage_descriptions = ["NSMicrophoneUsageDescription",
                               "NSAudioCaptureUsageDescription",
                               "NSScreenCaptureUsageDescription"]


def build_the_bundle():
    """Building is idempotent by design, so the tests may always ask for it."""
    return subprocess.run([str(build_app_script)], capture_output=True, text=True)


def code_signature_hash(path):
    """The cdhash is the identity macOS pins an ad hoc permission grant to."""
    completed = subprocess.run(["codesign", "-d", "-r-", str(path)],
                               capture_output=True, text=True)
    found = re.search(r'cdhash H"([0-9a-f]+)"', completed.stdout + completed.stderr)
    if not found:
        raise AssertionError("no cdhash in: " + completed.stdout + completed.stderr)
    return found.group(1)


def run_launcher(*arguments):
    return subprocess.run([str(launcher_path), *arguments],
                          capture_output=True, text=True, timeout=60)


def launcher_log_for(program):
    """Launched through LaunchServices there is no terminal, so the program's
    own output only ever reaches this log."""
    return (repo_dir / "var" / "log" / ("%s.launcher.log" % program)).read_text(encoding="utf-8")


class AppBundleBuildTest(unittest.TestCase):
    """What helper/build_app.sh has to produce for macOS to accept the bundle."""

    @classmethod
    def setUpClass(cls):
        cls.build = build_the_bundle()

    def test_the_build_reports_success(self):
        self.assertEqual(0, self.build.returncode, self.build.stdout + self.build.stderr)
        self.assertNotIn("FAIL", self.build.stdout + self.build.stderr)

    def test_the_launcher_is_where_the_information_property_list_says_it_is(self):
        declared = plistlib.loads(information_property_list.read_bytes())["CFBundleExecutable"]
        self.assertEqual(launcher_path.name, declared)
        self.assertTrue(launcher_path.is_file(), "no launcher at %s" % launcher_path)

    def test_the_bundle_identifies_itself_as_a_background_application_named_rvw(self):
        declared = plistlib.loads(information_property_list.read_bytes())
        self.assertEqual("ai.rvw.assistant", declared["CFBundleIdentifier"])
        self.assertEqual("rvw", declared["CFBundleName"])
        self.assertEqual("APPL", declared["CFBundlePackageType"])
        self.assertTrue(declared["LSUIElement"], "a daemon must not take a dock icon")

    def test_every_permission_the_assistant_needs_has_a_usage_description(self):
        declared = plistlib.loads(information_property_list.read_bytes())
        for key in required_usage_descriptions:
            self.assertIn(key, declared)
            self.assertTrue(declared[key].strip(), "%s is empty" % key)

    def test_the_signature_verifies(self):
        completed = subprocess.run(["codesign", "--verify", "--strict", str(app_bundle)],
                                   capture_output=True, text=True)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_rebuilding_leaves_the_signature_alone_so_the_grants_survive(self):
        before = code_signature_hash(app_bundle)
        rebuild = build_the_bundle()
        self.assertEqual(0, rebuild.returncode, rebuild.stdout + rebuild.stderr)
        self.assertEqual(before, code_signature_hash(app_bundle),
                         "the bundle was re-signed, which voids every permission granted to it")


class LauncherTest(unittest.TestCase):
    """The launcher runs one program from bin/ and nothing else."""

    @classmethod
    def setUpClass(cls):
        build_the_bundle()

    def test_it_runs_the_named_program_from_the_bin_directory(self):
        run_launcher("--run", "screen_capture")
        self.assertIn("usage: screen_capture", launcher_log_for("screen_capture"))

    def test_it_passes_the_remaining_arguments_through(self):
        run_launcher("--run", "screen_capture", "--output", "/dev/null", "--target", "nonsense")
        self.assertIn("unknown target nonsense", launcher_log_for("screen_capture"))

    def test_it_records_the_pid_and_the_exit_status_in_the_log(self):
        completed = run_launcher("--run", "screen_capture")
        self.assertNotEqual(0, completed.returncode)
        log = launcher_log_for("screen_capture")
        self.assertRegex(log, r"OK   started screen_capture as pid [0-9]+")
        self.assertIn("FAIL screen_capture exited with status 1", log)

    def test_it_empties_the_log_at_each_launch(self):
        run_launcher("--run", "screen_capture", "--output", "/dev/null", "--target", "nonsense")
        run_launcher("--run", "screen_capture")
        self.assertNotIn("unknown target nonsense", launcher_log_for("screen_capture"))

    def test_it_refuses_a_program_name_that_escapes_the_bin_directory(self):
        completed = run_launcher("--run", "../../../../bin/echo", "escaped")
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("FAIL", completed.stdout + completed.stderr)
        self.assertNotIn("escaped", completed.stdout)

    def test_it_refuses_a_program_that_is_not_in_the_bin_directory(self):
        completed = run_launcher("--run", "no_such_program")
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("FAIL", completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
