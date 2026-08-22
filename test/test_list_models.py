"""Tests for util/list_models.sh, which answers "what models does this machine have".

There are two model stores on this machine and LM Studio only knows about one of
them. The LLM lives in LM Studio; Whisper and the diarization model live in the
Hugging Face cache, where lms will never see them. A listing that showed only one
store would be worse than none, because it would look complete.

The part that earns the script its place, though, is the identifier check. The
endpoint answers a request for an identifier it does not serve with whatever
model happens to be loaded, so 'meeting-vision' missing is not an error anybody
sees at the time: it is a screenshot silently described by the text model. This
listing is where that has to be visible, so an absent identifier is reported as
FAIL and says what will happen because of it.
"""

import tempfile
import unittest
from pathlib import Path

from shell_script_testing import run_bash_using, sourceable_copy_of, write_stand_in_command

sourceable_script = None

model_listing_with = """{"data": [{"id": "meeting-assistant"}, {"id": "qwen3.6-35b-a3b"}]}"""
model_listing_with_vision = (
    """{"data": [{"id": "meeting-assistant"}, {"id": "meeting-vision"}]}""")


def setUpModule():
    global sourceable_script
    sourceable_script = sourceable_copy_of("list_models.sh")


class ListModelsTestCase(unittest.TestCase):
    """Neither lms nor curl is the real one here; both are stand ins on PATH."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.stand_in_bin = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.serve(model_listing_with)

    def serve(self, listing, exit_status=0):
        write_stand_in_command(self.stand_in_bin, "curl", """
            printf '%%s' '%s'
            exit %d
        """ % (listing, exit_status))

    def install_stand_in_lms(self, downloaded="qwen3.6-35b-a3b", loaded="meeting-assistant"):
        """The script looks for lms at $HOME/.lmstudio/bin and puts it first on
        PATH, so a stand in has to sit exactly there and the home has to move."""
        self.stand_in_home = Path(self.directory.name) / "home"
        lms_bin_dir = self.stand_in_home / ".lmstudio" / "bin"
        lms_bin_dir.mkdir(parents=True)
        write_stand_in_command(lms_bin_dir, "lms", """
            case "$1" in
                ls) echo 'downloaded: %s' ;;
                ps) echo 'loaded: %s' ;;
                *)  echo "unexpected lms $*" >&2; exit 1 ;;
            esac
        """ % (downloaded, loaded))
        return self.stand_in_home

    def run_fragment(self, body, home=None):
        return run_bash_using(sourceable_script, body, path_prefix=str(self.stand_in_bin),
                              home=home)


class ServedIdentifiersTest(ListModelsTestCase):
    """What the endpoint is serving decides whether a hotkey reaches a model."""

    def test_the_served_identifiers_are_read_from_the_endpoint(self):
        completed = self.run_fragment("served_identifiers")
        self.assertEqual(["meeting-assistant", "qwen3.6-35b-a3b"],
                         completed.stdout.split())

    def test_a_server_that_is_not_running_is_reported_rather_than_read_as_empty(self):
        self.serve("", exit_status=7)
        completed = self.run_fragment("report_the_identifiers_the_assistant_asks_for")
        self.assertIn("FAIL", completed.stdout + completed.stderr)
        self.assertIn("lms server start", completed.stdout + completed.stderr)


class IdentifierReportTest(ListModelsTestCase):
    """An identifier that is not served is the failure nobody sees at the time."""

    def report(self):
        completed = self.run_fragment("report_the_identifiers_the_assistant_asks_for")
        return completed.stdout + completed.stderr

    def test_a_served_identifier_is_reported_as_reachable(self):
        reported = self.report()
        self.assertRegex(reported, r"OK.*meeting-assistant")

    def test_a_missing_vision_identifier_is_reported_as_a_failure(self):
        reported = self.report()
        self.assertRegex(reported, r"FAIL.*meeting-vision")

    def test_the_missing_identifier_says_what_will_happen_because_of_it(self):
        self.assertIn("answered by whatever model is loaded", self.report())

    def test_nothing_fails_when_both_identifiers_are_served(self):
        self.serve(model_listing_with_vision)
        self.assertNotIn("FAIL", self.report())


class LmStudioListingTest(ListModelsTestCase):
    """The LM Studio half, driven by a stand in lms where the script looks for it."""

    def setUp(self):
        super().setUp()
        self.home = self.install_stand_in_lms()

    def test_it_reports_the_models_lm_studio_has_downloaded_and_loaded(self):
        completed = self.run_fragment("report_downloaded_llm_models; report_loaded_llm_models",
                                      home=self.home)
        self.assertIn("downloaded: qwen3.6-35b-a3b", completed.stdout)
        self.assertIn("loaded: meeting-assistant", completed.stdout)

    def test_a_missing_lms_is_reported_and_says_how_to_install_it(self):
        completed = self.run_fragment("report_downloaded_llm_models",
                                      home=Path(self.directory.name) / "empty_home")
        reported = completed.stdout + completed.stderr
        self.assertIn("FAIL", reported)
        self.assertIn("init_local_models.sh", reported)


class SpeechModelListingTest(ListModelsTestCase):
    """The half LM Studio cannot see, read from the real Hugging Face cache."""

    def test_it_reports_the_speech_models_lm_studio_cannot_see(self):
        completed = self.run_fragment("report_speech_models")
        self.assertIn("whisper", completed.stdout)
        self.assertIn("pyannote", completed.stdout)

    def test_one_broken_store_does_not_stop_the_other_being_listed(self):
        """Every section reports on its own, so a missing lms still lists speech.

        Both stores normally live under the home directory, so moving the home to
        take lms away would take the Hugging Face cache with it; HF_HOME points
        the second one back at the real cache.
        """
        completed = self.run_fragment(
            "export HF_HOME=%s/.cache/huggingface\nmain" % Path.home(),
            home=Path(self.directory.name) / "empty_home")
        self.assertIn("FAIL", completed.stdout + completed.stderr)
        self.assertIn("whisper", completed.stdout)


if __name__ == "__main__":
    unittest.main()
