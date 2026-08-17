"""Tests for the assistant's command wiring.

Capture, recognition and the LLM are exercised elsewhere or need real hardware;
what matters here is that every hotkey command exists, that the answering
commands refuse to run on an empty transcript instead of asking the model
nonsense, and that SCREENSHOT reports where the image went.
"""

import stat
import tempfile
import time
import unittest
from pathlib import Path

from rvw import config
from rvw.assistant import Assistant
from rvw.llm import LocalLlmError
from rvw.transcript import TranscriptSegment

expected_commands = ["CLARIFY", "EXPLAIN", "INTERPRET_SCREEN", "QUIT", "SCREENSHOT",
                     "START_CAPTURE", "STATUS", "STOP_CAPTURE", "TOGGLE_CAPTURE",
                     "TOGGLE_CONTINUOUS"]

stub_helper = """#!/bin/sh
output=""
while [ $# -gt 0 ]; do
  case "$1" in
    --output) output=$2; shift 2 ;;
    *) shift ;;
  esac
done
printf 'pretend png bytes' > "$output"
echo '{"target":"display","application":"Terminal","window_title":"rvw","display_id":1}'
"""


class RecordingLlm:
    """Stands in for the local model; records requests instead of sending them."""

    def __init__(self):
        self.requests = []
        self.served_models = [config.llm_model]
        self.raise_on_available_models = False

    def stream_chat(self, messages, on_token, on_reasoning=None):
        self.requests.append(messages)
        on_token("stub answer")
        return "stub answer"

    def available_models(self):
        if self.raise_on_available_models:
            raise LocalLlmError("no local LLM at %s (refused)" % config.llm_base_url)
        return self.served_models


class AssistantCommandTestCase(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.saved_archive_dir = config.archive_dir
        self.saved_helper_path = config.screen_capture_helper_path
        config.archive_dir = self.root / "meetings"
        self.addCleanup(self.restore_configuration)
        self.assistant = Assistant(["system"])
        self.llm = RecordingLlm()
        self.assistant._llm = self.llm
        self.assistant._vision_llm = self.llm

    def restore_configuration(self):
        config.archive_dir = self.saved_archive_dir
        config.screen_capture_helper_path = self.saved_helper_path
        self.temporary_directory.cleanup()

    def install_stub_capture_helper(self):
        path = self.root / "screen_capture"
        path.write_text(stub_helper, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        config.screen_capture_helper_path = path

    def dispatch(self, command_line):
        return self.assistant._dispatcher.dispatch(command_line)

    def add_speech(self, text):
        now = time.time()
        self.assistant._transcript.add(TranscriptSegment(stream="system",
                                                         start_epoch=now - 5.0,
                                                         end_epoch=now - 1.0, text=text))

    def wait_for_one_answer(self):
        deadline = time.monotonic() + 5.0
        while not self.llm.requests and time.monotonic() < deadline:
            time.sleep(0.02)
        return self.llm.requests


class RegisteredCommandsTest(AssistantCommandTestCase):

    def test_every_phase_2_command_is_registered(self):
        self.assertEqual(expected_commands, self.assistant._dispatcher.command_names())


class LlmStatusReportTest(AssistantCommandTestCase):
    """LM Studio unloads the model once it has been idle for an hour, so an
    unloaded model is the ordinary state between questions. Reporting that as a
    failure spends the reader's attention on something already working."""

    def report_llm_status_with(self, served_models):
        self.llm.served_models = served_models
        with self.assertLogs("rvw.assistant", level="DEBUG") as captured:
            self.assistant._report_llm_status()
        return captured

    def test_an_unloaded_model_is_reported_as_news_not_as_a_failure(self):
        captured = self.report_llm_status_with([])
        self.assertNotIn("ERROR", [record.levelname for record in captured.records])
        self.assertTrue(any("loaded when it is first needed" in record.getMessage()
                            for record in captured.records), captured.output)

    def test_a_loaded_model_is_still_reported_as_loaded(self):
        captured = self.report_llm_status_with([config.llm_model])
        self.assertNotIn("ERROR", [record.levelname for record in captured.records])

    def test_a_server_that_is_not_running_is_still_a_failure(self):
        self.llm.raise_on_available_models = True
        with self.assertLogs("rvw.assistant", level="DEBUG") as captured:
            self.assistant._report_llm_status()
        self.assertIn("ERROR", [record.levelname for record in captured.records])


class ClarifyCommandTest(AssistantCommandTestCase):

    def test_clarify_sends_the_recent_transcript_to_the_model(self):
        self.add_speech("the lease timeout was thirty seconds")
        self.assertTrue(self.dispatch("CLARIFY").startswith("OK "))
        requests = self.wait_for_one_answer()
        self.assertEqual(1, len(requests))
        self.assertIn("lease timeout", requests[0][1]["content"])

    def test_clarify_accepts_an_explicit_window_length(self):
        self.add_speech("the lease timeout was thirty seconds")
        self.assertIn("30", self.dispatch("CLARIFY 30"))

    def test_clarify_without_speech_fails_instead_of_asking_the_model(self):
        self.assertTrue(self.dispatch("CLARIFY").startswith("FAIL "))
        self.assertEqual([], self.llm.requests)


class ScreenshotCommandTest(AssistantCommandTestCase):

    def test_screenshot_saves_an_image_and_reports_its_path(self):
        self.install_stub_capture_helper()
        reply = self.dispatch("SCREENSHOT")
        self.assertTrue(reply.startswith("OK "), reply)
        saved = sorted(config.archive_dir.rglob("*.png"))
        self.assertEqual(1, len(saved))
        self.assertIn(saved[0].name, reply)

    def test_screenshot_never_calls_a_model(self):
        self.install_stub_capture_helper()
        self.dispatch("SCREENSHOT")
        time.sleep(0.2)
        self.assertEqual([], self.llm.requests)

    def test_a_capture_failure_is_reported_as_a_failure(self):
        config.screen_capture_helper_path = self.root / "not_built"
        self.assertTrue(self.dispatch("SCREENSHOT").startswith("FAIL "))


class InterpretScreenCommandTest(AssistantCommandTestCase):

    def test_interpretation_saves_the_image_and_asks_the_vision_model(self):
        self.install_stub_capture_helper()
        self.add_speech("look at the diagram on the left")
        self.assertTrue(self.dispatch("INTERPRET_SCREEN").startswith("OK "))
        requests = self.wait_for_one_answer()
        parts = requests[0][1]["content"]
        self.assertTrue(any(part["type"] == "image_url" for part in parts))

    def test_interpretation_works_without_any_transcript(self):
        self.install_stub_capture_helper()
        self.assertTrue(self.dispatch("INTERPRET_SCREEN").startswith("OK "))
        self.assertEqual(1, len(self.wait_for_one_answer()))


if __name__ == "__main__":
    unittest.main()
