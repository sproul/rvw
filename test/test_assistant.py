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

expected_commands = ["CLARIFY", "EXPLAIN", "INTERPRET_SCREEN", "QUIT", "RECALL", "REINDEX",
                     "SCREENSHOT", "SEARCH", "START_CAPTURE", "START_RETAINING", "STATUS",
                     "STOP_CAPTURE", "STOP_RETAINING", "TOGGLE_CAPTURE", "TOGGLE_CONTINUOUS",
                     "TOGGLE_RETENTION"]

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
        self.served_models = [config.llm_model, config.vision_llm_model]
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
        self.saved_index_db = config.index_db_path
        config.archive_dir = self.root / "meetings"
        config.index_db_path = self.root / "index" / "meetings.db"
        self.addCleanup(self.restore_configuration)
        self.assistant = Assistant(["system"])
        self.llm = RecordingLlm()
        self.assistant._llm = self.llm
        self.assistant._vision_llm = self.llm

    def restore_configuration(self):
        config.archive_dir = self.saved_archive_dir
        config.screen_capture_helper_path = self.saved_helper_path
        config.index_db_path = self.saved_index_db
        self.temporary_directory.cleanup()

    def retain_a_meeting(self, text, started=None):
        """Write one retained meeting into the archive for the index to find."""
        from rvw import meeting_archive
        started = time.mktime((2026, 8, 20, 9, 0, 0, 0, 0, -1)) if started is None else started
        archive = meeting_archive.MeetingArchive(started, ["system"], retention_mode="retained")
        archive.record_segment(TranscriptSegment(stream="system", start_epoch=started + 5,
                                                 end_epoch=started + 9, text=text))
        archive.stop_retaining()

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

    def test_every_command_up_to_phase_3_is_registered(self):
        self.assertEqual(expected_commands, self.assistant._dispatcher.command_names())


class TranscriptRetentionTest(AssistantCommandTestCase):
    """Retention is off unless this session was asked for it, and a session that
    was never asked leaves nothing on the disk to be found later."""

    def archived_files(self):
        return sorted(path.name for path in config.archive_dir.rglob("*") if path.is_file())

    def transcript_text(self):
        return self.assistant._archive.transcript_path.read_text(encoding="utf-8")

    def test_a_session_starts_ephemeral_and_says_so(self):
        self.assertIn("ephemeral", self.dispatch("STATUS"))

    def test_speech_in_an_ephemeral_session_is_never_written(self):
        self.add_speech("this was said in confidence")
        self.assertEqual([], self.archived_files())

    def test_retaining_writes_the_speech_that_follows_it(self):
        self.assertTrue(self.dispatch("START_RETAINING").startswith("OK "))
        self.add_speech("the lease timeout was thirty seconds")
        self.assertIn("lease timeout", self.transcript_text())

    def test_speech_from_before_retaining_is_not_written_afterwards(self):
        """What was said while the session was ephemeral was said in confidence,
        so switching retention on is not retrospective."""
        self.add_speech("said while nobody was keeping it")
        self.dispatch("START_RETAINING")
        self.add_speech("said afterwards")
        transcript = self.transcript_text()
        self.assertNotIn("nobody was keeping it", transcript)
        self.assertIn("said afterwards", transcript)

    def test_status_reports_a_retained_session_and_where_it_is_kept(self):
        self.dispatch("START_RETAINING")
        self.add_speech("one utterance")
        reply = self.dispatch("STATUS")
        self.assertIn("retained", reply)
        self.assertIn(str(self.assistant._archive.directory), reply)

    def test_stopping_leaves_what_was_written_and_keeps_nothing_new(self):
        self.dispatch("START_RETAINING")
        self.add_speech("kept")
        self.dispatch("STOP_RETAINING")
        self.add_speech("not kept")
        transcript = self.transcript_text()
        self.assertIn("kept", transcript)
        self.assertNotIn("not kept", transcript)

    def test_the_toggle_turns_retention_on_and_off_again(self):
        self.dispatch("TOGGLE_RETENTION")
        self.assertTrue(self.assistant._archive.is_retaining)
        self.dispatch("TOGGLE_RETENTION")
        self.assertFalse(self.assistant._archive.is_retaining)

    def test_ending_a_retained_session_renders_the_markdown(self):
        self.dispatch("START_RETAINING")
        self.add_speech("the lease timeout was thirty seconds")
        self.assistant._finish_the_meeting_archive()
        self.assertIn("lease timeout",
                      self.assistant._archive.markdown_path.read_text(encoding="utf-8"))

    def test_a_screenshot_is_archived_beside_the_transcript(self):
        """Same directory, both timestamped: that is the whole association."""
        self.install_stub_capture_helper()
        self.dispatch("START_RETAINING")
        self.add_speech("look at this")
        self.dispatch("SCREENSHOT")
        images = sorted(config.archive_dir.rglob("*.png"))
        self.assertEqual(self.assistant._archive.directory, images[0].parent.parent)


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


class InterpretWithoutAVisionModelTest(AssistantCommandTestCase):
    """LM Studio answers a request for an identifier it does not serve with
    whatever model is loaded, so an absent vision model has to be noticed here.
    Interpreting a screenshot with the text model and calling it an
    interpretation would be the one failure nobody could see."""

    def setUp(self):
        super().setUp()
        self.llm.served_models = [config.llm_model]
        self.install_stub_capture_helper()

    def test_the_image_is_still_archived_when_nothing_can_interpret_it(self):
        reply = self.dispatch("INTERPRET_SCREEN")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertEqual(1, len(sorted(config.archive_dir.rglob("*.png"))))

    def test_the_reply_says_the_screenshot_was_not_interpreted_and_why(self):
        reply = self.dispatch("INTERPRET_SCREEN")
        self.assertIn("not interpreted", reply)
        self.assertIn(config.vision_llm_model, reply)

    def test_no_model_is_asked_to_interpret_the_image(self):
        self.dispatch("INTERPRET_SCREEN")
        time.sleep(0.2)
        self.assertEqual([], self.llm.requests)


class SearchCommandTest(AssistantCommandTestCase):
    """Full text search over retained conversations, tracing hits back to them."""

    def test_search_finds_a_retained_passage_and_names_its_meeting(self):
        self.retain_a_meeting("the lease timeout was thirty seconds")
        reply = self.dispatch("SEARCH lease timeout")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertIn("lease timeout", reply)
        self.assertIn("2026-08-20_09.00", reply)

    def test_search_with_no_hits_says_so_without_failing(self):
        self.retain_a_meeting("the lease timeout was thirty seconds")
        self.assertIn("matches", self.dispatch("SEARCH kubernetes"))

    def test_search_without_a_query_is_refused(self):
        self.assertTrue(self.dispatch("SEARCH").startswith("FAIL "))


class ReindexCommandTest(AssistantCommandTestCase):
    """Rebuilding the disposable index from the canonical transcripts on demand."""

    def test_reindex_reports_what_it_covered(self):
        self.retain_a_meeting("the lease timeout was thirty seconds")
        reply = self.dispatch("REINDEX")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertIn("1 meeting", reply)

    def test_a_passage_retained_after_the_first_search_appears_once_reindexed(self):
        self.assertIn("matches", self.dispatch("SEARCH friday"))
        self.retain_a_meeting("we will deploy on friday", started=self.a_later_meeting_epoch())
        self.dispatch("REINDEX")
        self.assertIn("friday", self.dispatch("SEARCH friday"))

    @staticmethod
    def a_later_meeting_epoch():
        return time.mktime((2026, 8, 21, 14, 0, 0, 0, 0, -1))


class RecallCommandTest(AssistantCommandTestCase):
    """Retrieval augmented answers: a few passages, the model, references back."""

    def test_recall_grounds_the_model_on_the_retrieved_passages(self):
        self.retain_a_meeting("the lease timeout was thirty seconds")
        reply = self.dispatch("RECALL what was the lease timeout")
        self.assertTrue(reply.startswith("OK "), reply)
        content = self.wait_for_one_answer()[0][1]["content"]
        self.assertIn("lease timeout was thirty seconds", content)
        self.assertIn("what was the lease timeout", content)

    def test_recall_with_nothing_matching_never_calls_the_model(self):
        self.retain_a_meeting("the lease timeout was thirty seconds")
        self.assertIn("matches", self.dispatch("RECALL tell me about kubernetes"))
        time.sleep(0.2)
        self.assertEqual([], self.llm.requests)

    def test_recall_without_a_question_is_refused(self):
        self.assertTrue(self.dispatch("RECALL").startswith("FAIL "))


if __name__ == "__main__":
    unittest.main()
