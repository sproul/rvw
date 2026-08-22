"""Tests for util/init_llm_autostart.sh, which installs the login LaunchAgent.

The agent is what makes the LLM survive a reboot, and its only failure mode is
silence: a plist launchd rejects, or one naming a path that does not exist, is
noticed a reboot later when a question is answered by nothing. So the plist is
parsed here rather than matched as text, and the program it names has to be the
start script of this checkout.

The plist is generated instead of shipped because it must hold the absolute path
of the checkout and the checkout is allowed to live anywhere. Installation is
also rerun by util/init.sh every time, so booting out an agent that was never
loaded is the ordinary case and must not be treated as a failure.
"""

import os
import plistlib
import tempfile
import unittest
from pathlib import Path

from shell_script_testing import run_bash_using, sourceable_copy_of, write_stand_in_command

sourceable_script = None

label = "ai.rvw.llm_server"


def setUpModule():
    global sourceable_script
    sourceable_script = sourceable_copy_of("init_llm_autostart.sh")


class InitLlmAutostartTestCase(unittest.TestCase):
    """A stand in launchctl and a stand in home, so no real agent is touched."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.stand_in_bin = Path(self.directory.name) / "bin"
        self.stand_in_bin.mkdir()
        self.home = Path(self.directory.name) / "home"
        self.home.mkdir()
        self.record = Path(self.directory.name) / "launchctl_invocations"
        self.install_stand_in_launchctl()
        self.plist_path = self.home / "Library" / "LaunchAgents" / ("%s.plist" % label)
        self.repo_dir = Path(sourceable_script).parents[1]

    def install_stand_in_launchctl(self, bootout_status=0):
        write_stand_in_command(self.stand_in_bin, "launchctl", """
            echo "$*" >> %(record)s
            [[ $1 == bootout ]] && exit %(bootout_status)d
            exit 0
        """ % {"record": self.record, "bootout_status": bootout_status})

    def install(self, body="main"):
        """Installed under the umask of 0000 this user actually has, because a
        permissive umask is what makes launchd refuse the plist."""
        return run_bash_using(sourceable_script, "umask 0\n" + body,
                              path_prefix=str(self.stand_in_bin), home=self.home)

    def invocations(self):
        if not self.record.exists():
            return ""
        return self.record.read_text(encoding="utf-8")

    def installed_plist(self):
        self.assertTrue(self.plist_path.exists(), "no plist at %s" % self.plist_path)
        with self.plist_path.open("rb") as stream:
            return plistlib.load(stream)


class GeneratedPlistTest(InitLlmAutostartTestCase):
    """What launchd will read, parsed the way launchd reads it."""

    def setUp(self):
        super().setUp()
        self.completed = self.install()

    def test_the_plist_is_written_where_launchd_looks_for_it(self):
        self.assertEqual(0, self.completed.returncode, self.completed.stderr)
        self.assertEqual(label, self.installed_plist()["Label"])

    def test_it_runs_the_start_script_of_this_checkout(self):
        program = Path(self.installed_plist()["ProgramArguments"][0])
        self.assertTrue(program.is_absolute(), "launchd needs an absolute path")
        self.assertEqual(self.repo_dir / "util" / "start_llm_server.sh", program)

    def test_it_runs_at_login_and_is_not_kept_alive(self):
        """The script starts the server and exits; staying alive is the daemon's job."""
        plist = self.installed_plist()
        self.assertTrue(plist["RunAtLoad"])
        self.assertNotIn("KeepAlive", plist)

    def test_the_plist_is_not_left_writable_by_anyone_else(self):
        """launchd refuses a plist that others can write, with nothing but
        'Bootstrap failed: 5: Input/output error' to say why, and under a umask
        of 0000 a plain redirect writes exactly such a file."""
        self.assertEqual(0, self.plist_path.stat().st_mode & 0o022)

    def test_its_output_is_kept_in_the_repository_log_directory(self):
        plist = self.installed_plist()
        expected = str(self.repo_dir / "var" / "log" / "llm_server.launchagent.log")
        self.assertEqual(expected, plist["StandardOutPath"])
        self.assertEqual(expected, plist["StandardErrorPath"])


class LaunchctlTest(InitLlmAutostartTestCase):
    """Loading it, in the one way macOS 26 still supports."""

    def test_the_agent_is_booted_out_before_it_is_bootstrapped(self):
        self.install()
        booted_out = "bootout gui/%d/%s" % (os.getuid(), label)
        bootstrapped = "bootstrap gui/%d %s" % (os.getuid(), self.plist_path)
        self.assertIn(booted_out, self.invocations())
        self.assertIn(bootstrapped, self.invocations())
        self.assertLess(self.invocations().index(booted_out),
                        self.invocations().index(bootstrapped))

    def test_an_agent_that_was_never_loaded_is_not_a_failure(self):
        """The first install, and every install after a logout, boots out nothing."""
        self.install_stand_in_launchctl(bootout_status=113)
        completed = self.install()
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertNotIn("FAIL", completed.stdout + completed.stderr)

    def test_installing_it_again_changes_nothing(self):
        self.install()
        first = self.plist_path.read_bytes()
        completed = self.install()
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(first, self.plist_path.read_bytes())


class DryModeTest(InitLlmAutostartTestCase):
    """Preview has to be a preview, or nobody can use it to look before installing."""

    def test_dry_mode_writes_no_plist_and_calls_no_launchctl(self):
        completed = self.install("dry_mode=1\nmain")
        self.assertFalse(self.plist_path.exists())
        self.assertEqual("", self.invocations())
        self.assertIn("dry", completed.stdout)


if __name__ == "__main__":
    unittest.main()
