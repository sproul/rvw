"""Tests for util/start_llm_server.sh, the one place that brings the LLM up.

The script exists because 'lms server' has no boot option, so after a reboot the
assistant starts normally and every question fails until someone types 'lms
server start'. A LaunchAgent runs this script at login instead.

Two properties matter more than the rest and both are tested here. The script is
run by launchd, which gives it almost no environment and none of a shell
profile, so a bare 'lms' would simply not be found at login and the failure
would only ever be seen after a reboot: lms is therefore invoked by its absolute
path and a decoy on PATH proves it. And the script runs at every login and from
the installer, so finding the daemon and the server already up has to be a
no-op rather than a restart.
"""

import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse

from rvw import config

from shell_script_testing import run_bash_using, sourceable_copy_of, write_stand_in_command

sourceable_script = None

configured_port = urlparse(config.llm_base_url).port


def setUpModule():
    global sourceable_script
    sourceable_script = sourceable_copy_of("start_llm_server.sh")


class StartLlmServerTestCase(unittest.TestCase):
    """Every lms below is a stand in that records how it was called."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.stand_in_bin = Path(self.directory.name) / "bin"
        self.stand_in_bin.mkdir()
        self.home = Path(self.directory.name) / "home"
        self.record = Path(self.directory.name) / "lms_invocations"
        self.install_stand_in_lms()

    def install_stand_in_lms(self, daemon_running=True, server_running=True, port=None):
        """An lms where the daemon looks for it, reporting whatever this test wants."""
        lms_bin_dir = self.home / ".lmstudio" / "bin"
        lms_bin_dir.mkdir(parents=True, exist_ok=True)
        status = json.dumps({"running": server_running, "port": port or configured_port})
        write_stand_in_command(lms_bin_dir, "lms", """
            echo "$*" >> %(record)s
            case "$1 $2" in
                "daemon status") exit %(daemon_status)d ;;
                "server status") printf '%%s' '%(status)s' ;;
            esac
            exit 0
        """ % {"record": self.record, "daemon_status": 0 if daemon_running else 1,
               "status": status})

    def install_decoy_lms_on_the_path(self):
        """What a script that trusted PATH would find; it must never be run."""
        self.decoy_record = Path(self.directory.name) / "decoy_invocations"
        write_stand_in_command(self.stand_in_bin, "lms", """
            echo "$*" >> %s
            exit 1
        """ % self.decoy_record)

    def run_fragment(self, body, home=None):
        return run_bash_using(sourceable_script, body, path_prefix=str(self.stand_in_bin),
                              home=home or self.home)

    def invocations(self):
        if not self.record.exists():
            return ""
        return self.record.read_text(encoding="utf-8")


class LlmsterDaemonTest(StartLlmServerTestCase):
    """The daemon owns the models; nothing else works until it is up."""

    def test_a_running_daemon_is_left_alone(self):
        completed = self.run_fragment("start_llmster_daemon")
        self.assertNotIn("daemon up", self.invocations())
        self.assertIn("OK", completed.stdout)

    def test_a_stopped_daemon_is_started(self):
        self.install_stand_in_lms(daemon_running=False)
        self.run_fragment("start_llmster_daemon")
        self.assertIn("daemon up", self.invocations())


class LlmServerTest(StartLlmServerTestCase):
    """The endpoint the assistant asks its questions of."""

    def test_a_server_already_on_the_configured_port_is_left_alone(self):
        completed = self.run_fragment("start_llm_server")
        self.assertNotIn("server start", self.invocations())
        self.assertIn("OK", completed.stdout)

    def test_a_stopped_server_is_started_on_the_configured_port(self):
        self.install_stand_in_lms(server_running=False)
        self.run_fragment("start_llm_server")
        self.assertIn("server start --port %d" % configured_port, self.invocations())

    def test_a_server_listening_on_another_port_is_moved_to_the_configured_one(self):
        """Serving on the wrong port is indistinguishable, to the assistant, from
        serving nothing at all, so it is not a state to leave alone."""
        self.install_stand_in_lms(port=configured_port + 1)
        self.run_fragment("start_llm_server")
        self.assertIn("server start --port %d" % configured_port, self.invocations())


class AbsoluteLmsPathTest(StartLlmServerTestCase):
    """launchd hands the script a PATH that has never heard of lms."""

    def test_lms_is_run_from_its_absolute_path_and_not_from_the_path(self):
        self.install_decoy_lms_on_the_path()
        self.install_stand_in_lms(daemon_running=False, server_running=False)
        self.run_fragment("main")
        self.assertFalse(self.decoy_record.exists(), "the script trusted PATH to find lms")
        self.assertIn("daemon up", self.invocations())

    def test_a_missing_lms_is_reported_and_says_how_to_install_it(self):
        completed = self.run_fragment("main", home=Path(self.directory.name) / "empty_home")
        reported = completed.stdout + completed.stderr
        self.assertIn("FAIL", reported)
        self.assertIn("init_local_models.sh", reported)


class DryModeTest(StartLlmServerTestCase):
    """The installer passes -dry straight through, so it has to mean something."""

    def test_dry_mode_starts_nothing(self):
        self.install_stand_in_lms(daemon_running=False, server_running=False)
        completed = self.run_fragment("dry_mode=1\nmain")
        self.assertNotIn("daemon up", self.invocations())
        self.assertNotIn("server start", self.invocations())
        self.assertIn("dry", completed.stdout)


if __name__ == "__main__":
    unittest.main()
