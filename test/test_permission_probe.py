"""Tests for the permission probes in util/init_permissions.sh.

The probes are the only way to find out what macOS has granted, because there
is no API that answers the question and no API that grants a permission: the
prompt appears only when the protected action is actually performed. That makes
these probes the single source of truth about the permissions, so a probe that
lies is worse than no probe at all.

The lie these tests exist to prevent: both audio probes read the same log,
var/log/audio_capture.launcher.log, because the launcher names the log after the
program and there is only one audio helper. The launcher outlives its helper by
the moment it takes to report the exit and holds that log open for appending, so
a probe that returns as soon as its helper has spoken leaves the previous
launcher free to append "exited with status 0" into the log the next probe has
just emptied. The next probe then reads that line as its own helper's verdict
and reports a granted permission as missing.

The script is therefore sourced rather than executed here, which is what the
guard around main() in it is for; see test/shell_script_testing.py.
"""

import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from shell_script_testing import run_bash_using, sourceable_copy_of, util_dir

permissions_script = util_dir / "init_permissions.sh"

sourceable_script = None


def setUpModule():
    """One sourceable copy of the script, shared by every test below."""
    global sourceable_script
    sourceable_script = sourceable_copy_of("init_permissions.sh")


def run_bash_using_the_script(body):
    """Source the probe script for its functions and run one fragment against them."""
    return run_bash_using(sourceable_script, body)


def process_is_alive(pid):
    """Asked about a process this test never fathered, so signal 0 is the only way."""
    return subprocess.run(["kill", "-0", str(pid)], capture_output=True).returncode == 0


# What the real launcher does, in the order it does it: start the helper, record
# its pid, let it say that it is capturing, then wait and report the exit.
fake_launcher_program = """
    sleep 600 &
    helper_pid=$!
    printf 'OK   started audio_capture as pid %%d\\nOK   capturing system audio\\n' \
        "$helper_pid" > %(log)s
    wait "$helper_pid"
    sleep %(delay)s
    echo 'OK   audio_capture exited with status 0' >> %(log)s
"""


class SourcingTest(unittest.TestCase):
    """Sourcing must give the tests the functions and nothing else."""

    def test_sourcing_the_script_probes_nothing_and_says_nothing(self):
        completed = run_bash_using_the_script("")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("", completed.stdout.strip())
        self.assertEqual("", completed.stderr.strip())

    def test_the_script_still_runs_its_probes_when_it_is_executed(self):
        completed = subprocess.run([str(permissions_script), "-dry"],
                                   capture_output=True, text=True, timeout=120)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("dry mode", completed.stdout)


class HelperVerdictTest(unittest.TestCase):
    """wait_for_helper_verdict stops as soon as the helper has said enough."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.log = Path(self.directory.name) / "audio_capture.launcher.log"
        self.addCleanup(self.directory.cleanup)

    def wait_for_verdict_in_the_log(self, success_phrase="capturing system audio"):
        started = time.monotonic()
        completed = run_bash_using_the_script("""
            wait_for_helper_verdict %s '%s'
        """ % (self.log, success_phrase))
        return completed, time.monotonic() - started

    def test_the_success_phrase_ends_the_wait_at_once(self):
        self.log.write_text("OK   started audio_capture as pid 1\n"
                            "OK   capturing system audio at 48000 Hz, 2 channel(s)\n")
        completed, took_seconds = self.wait_for_verdict_in_the_log()
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertLess(took_seconds, 5.0)
        self.assertEqual("", completed.stderr.strip())

    def test_a_refusal_ends_the_wait_at_once(self):
        self.log.write_text("FAIL cannot install a process tap\n")
        completed, took_seconds = self.wait_for_verdict_in_the_log()
        self.assertLess(took_seconds, 5.0)
        self.assertEqual("", completed.stderr.strip())


class StaleVerdictTest(unittest.TestCase):
    """A probe must never read a verdict left behind by the previous probe."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.log = Path(self.directory.name) / "audio_capture.launcher.log"
        self.addCleanup(self.directory.cleanup)

    def start_a_launcher_that_reports_the_exit_late(self, delay_seconds=2):
        """Stand in for rvw_launcher, which owns the helper and outlives it.

        The launcher has to own the helper for the same reason the real one
        does: only its parent learns that it exited, and only the parent can
        write the line that says so.
        """
        launcher = subprocess.Popen(["bash", "-c", fake_launcher_program
                                     % {"log": self.log, "delay": delay_seconds}],
                                    stderr=subprocess.DEVNULL)
        self.addCleanup(self.stop_the_launcher, launcher)
        return self.helper_pid_once_the_launcher_has_started_it()

    def stop_the_launcher(self, launcher):
        launcher.terminate()
        launcher.wait(timeout=30)

    def helper_pid_once_the_launcher_has_started_it(self):
        for _ in range(100):
            if self.log.exists() and "as pid" in self.log.read_text():
                return int(self.log.read_text().split("as pid ")[1].split("\n")[0])
            time.sleep(0.1)
        raise AssertionError("the stand in launcher never started a helper")

    def test_stopping_a_helper_waits_until_its_launcher_reported_the_exit(self):
        """Without this wait the exit line lands in the next probe's emptied log."""
        self.start_a_launcher_that_reports_the_exit_late()
        completed = run_bash_using_the_script("stop_the_helper_named_in %s" % self.log)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("exited with status", self.log.read_text(),
                      "the probe returned while the launcher could still write to this log")

    def test_the_helper_is_actually_stopped(self):
        helper_pid = self.start_a_launcher_that_reports_the_exit_late(delay_seconds=0)
        run_bash_using_the_script("stop_the_helper_named_in %s" % self.log)
        self.assertFalse(process_is_alive(helper_pid),
                         "the probe left its capture helper running")

    def test_a_helper_that_already_exited_needs_no_waiting(self):
        self.log.write_text("OK   started audio_capture as pid 1\n"
                            "FAIL cannot install a process tap\n"
                            "FAIL audio_capture exited with status 1\n")
        started = time.monotonic()
        completed = run_bash_using_the_script("stop_the_helper_named_in %s" % self.log)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertLess(time.monotonic() - started, 5.0)


if __name__ == "__main__":
    unittest.main()
