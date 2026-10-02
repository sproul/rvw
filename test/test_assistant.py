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
from unittest.mock import patch

from rvw import config, prompts
from rvw.assistant import Assistant
from rvw.llm import LocalLlmError
from rvw.transcript import TranscriptSegment

expected_commands = ["ANSWER", "AUDIO_CAPTURE_START", "AUDIO_CAPTURE_STOP",
                     "AUDIO_CAPTURE_TOGGLE", "EXPLAIN_SPEECH", "MODELS",
                     "PROMPT_LIST", "PROMPT_SET",
                     "QUIT", "RECALL", "REINDEX", "SCREENSHOT", "SCREEN_VISION", "SEARCH",
                     "SET_LANGUAGE", "SET_MODEL", "STATUS", "STATUS_FIELDS",
                     "TRANSCRIPT_SHOW", "TRANSCRIPT_START", "TRANSCRIPT_STOP",
                     "TRANSCRIPT_TOGGLE", "UNGARBLE_SPEECH"]

expected_shortcuts = {"ANSWER": "A", "AUDIO_CAPTURE_START": "c+",
                      "AUDIO_CAPTURE_STOP": "c-", "AUDIO_CAPTURE_TOGGLE": "c",
                      "EXPLAIN_SPEECH": "E", "MODELS": "ml",
                      "PROMPT_LIST": "pl", "PROMPT_SET": "p",
                      "QUIT": "q", "RECALL": "r", "REINDEX": "R", "SCREENSHOT": "s",
                      "SCREEN_VISION": "V", "SEARCH": "f", "SET_LANGUAGE": "l",
                      "SET_MODEL": "m", "STATUS": "S", "STATUS_FIELDS": "F",
                      "TRANSCRIPT_SHOW": "T", "TRANSCRIPT_START": "t+",
                      "TRANSCRIPT_STOP": "t-", "TRANSCRIPT_TOGGLE": "t",
                      "UNGARBLE_SPEECH": "C"}

former_commands = ["CLARIFY", "CLARIFY_SPEECH", "CONTINUOUS_TOGGLE", "EXPLAIN",
                   "INTERPRET_SCREEN",
                   "START_CAPTURE", "START_RETAINING", "STOP_CAPTURE", "STOP_RETAINING",
                   "TOGGLE_CAPTURE", "TOGGLE_CONTINUOUS", "TOGGLE_RETENTION", "TRANSCRIPT"]

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
        self.model = config.llm_model
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
        self.saved_selected_prompt_path = config.selected_prompt_path
        config.archive_dir = self.root / "meetings"
        config.index_db_path = self.root / "index" / "meetings.db"
        config.selected_prompt_path = self.root / "selected_prompt"
        self.addCleanup(self.restore_configuration)
        self.assistant = Assistant(["system"])
        # Answers write to the session log asynchronously; give the worker a
        # real file so a passing test does not log FAIL over a None path.
        self.assistant._log_path = self.root / "session.log"
        self.llm = RecordingLlm()
        self.assistant._llm = self.llm
        self.assistant._vision_llm = self.llm

    def restore_configuration(self):
        # An answer still streaming would outlive the temporary directory;
        # the lock is held for the whole answer, so taking it waits it out.
        with self.assistant._answering:
            pass
        config.archive_dir = self.saved_archive_dir
        config.screen_capture_helper_path = self.saved_helper_path
        config.index_db_path = self.saved_index_db
        config.selected_prompt_path = self.saved_selected_prompt_path
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

    def test_every_command_carries_its_documented_shortcut(self):
        help_by_name = {name: shortcut
                        for shortcut, name, _description
                        in self.assistant._dispatcher.command_help()}
        self.assertEqual(expected_shortcuts, help_by_name)

    def test_a_shortcut_runs_its_command_and_forwards_arguments(self):
        self.add_speech("the lease timeout was thirty seconds")
        reply = self.dispatch("C 30")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertIn("30", reply)

    def test_the_formerly_supported_command_names_are_rejected(self):
        for old_name in former_commands:
            self.assertTrue(self.dispatch(old_name).startswith("FAIL "), old_name)
            self.assertIn("unknown command", self.dispatch(old_name), old_name)

    def test_canonical_names_in_lower_case_are_rejected(self):
        self.assertTrue(self.dispatch("status").startswith("FAIL "))
        self.assertTrue(self.dispatch("explain_speech").startswith("FAIL "))


class StatusFieldsTest(AssistantCommandTestCase):
    """The menu bar polls the session state several times a minute and cannot read
    prose: STATUS is written for a person and would break the menu the moment its
    wording improved. STATUS_FIELDS is the same state as key=value pairs."""

    def fields(self):
        reply = self.dispatch("STATUS_FIELDS")
        self.assertTrue(reply.startswith("OK "), reply)
        return dict(pair.split("=", 1) for pair in reply[len("OK "):].split())

    def test_a_new_session_is_idle_ephemeral_and_empty(self):
        fields = self.fields()
        self.assertEqual("none", fields["capture"])
        self.assertEqual("off", fields["continuous"])
        self.assertEqual("ephemeral", fields["retention"])
        self.assertEqual("0", fields["segments"])

    def test_the_available_streams_are_reported_so_the_menu_can_offer_them(self):
        self.assertEqual("system", self.fields()["streams"])

    def test_retention_and_the_utterance_count_follow_the_session(self):
        self.dispatch("TRANSCRIPT_START")
        self.add_speech("one utterance")
        fields = self.fields()
        self.assertEqual("retained", fields["retention"])
        self.assertEqual("1", fields["segments"])

    def test_the_meeting_and_the_models_are_named(self):
        fields = self.fields()
        self.assertEqual(self.assistant._archive.directory.name, fields["meeting"])
        self.assertEqual(config.llm_model, fields["model"])
        self.assertEqual(config.vision_llm_model, fields["vision_model"])

    def test_a_value_with_a_space_is_refused_rather_than_read_as_two_fields(self):
        """RVW_LLM_MODEL is whatever the environment said it was; a value with a
        space in it would silently turn one field into two for the reader."""
        self.llm.model = "two words"
        self.assertTrue(self.dispatch("STATUS_FIELDS").startswith("FAIL "))

    def test_the_prose_status_still_describes_the_same_session(self):
        self.dispatch("TRANSCRIPT_START")
        reply = self.dispatch("STATUS")
        self.assertIn("retained", reply)
        self.assertIn("continuous: off", reply)


class TranscriptCommandTest(AssistantCommandTestCase):
    """The rolling transcript, for the window that shows it while a meeting runs."""

    def test_the_recent_speech_is_returned_under_a_heading(self):
        self.add_speech("the lease timeout was thirty seconds")
        reply = self.dispatch("TRANSCRIPT_SHOW")
        heading, body = reply.split("\n", 1)
        self.assertTrue(heading.startswith("OK "), reply)
        self.assertIn("lease timeout", body)

    def test_an_explicit_window_length_is_honoured(self):
        self.add_speech("said a moment ago")
        self.assertIn("said a moment ago", self.dispatch("TRANSCRIPT_SHOW 300"))

    def test_a_silent_session_says_so_instead_of_returning_nothing(self):
        self.assertIn("nothing has been transcribed", self.dispatch("TRANSCRIPT_SHOW"))

    def test_speech_older_than_the_window_is_not_returned(self):
        self.add_speech("recent")
        self.assertNotIn("recent", self.dispatch("TRANSCRIPT_SHOW 0"))


class AnswerCommandTest(AssistantCommandTestCase):
    """The answer window polls this, so it has to work before, during and after."""

    def test_nothing_has_been_asked_yet(self):
        self.assertIn("nothing has been asked", self.dispatch("ANSWER"))

    def test_the_answer_to_the_last_question_is_returned_with_its_heading(self):
        self.add_speech("the lease timeout was thirty seconds")
        self.dispatch("EXPLAIN_SPEECH")
        self.wait_for_one_answer()
        reply = self.wait_for_the_answer_to_be_complete()
        self.assertIn("explanation", reply)
        self.assertIn("stub answer", reply)

    def wait_for_the_answer_to_be_complete(self):
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            reply = self.dispatch("ANSWER")
            if "complete" in reply:
                return reply
            time.sleep(0.02)
        return self.dispatch("ANSWER")


class ModelSelectionTest(AssistantCommandTestCase):
    """Choosing between the models this endpoint is already serving. Loading a
    different one is Phase 8 work; asking a different one is not."""

    def test_the_served_models_are_listed_with_the_one_answering_named(self):
        """Machine readable, for the same reason as STATUS_FIELDS: the menu builds
        its model list from this."""
        reply = self.dispatch("MODELS")
        self.assertIn("serving=%s,%s" % (config.llm_model, config.vision_llm_model), reply)
        self.assertIn("answering=%s" % config.llm_model, reply)

    def test_a_served_model_can_be_chosen_and_is_then_reported(self):
        self.assertTrue(self.dispatch("SET_MODEL %s" % config.vision_llm_model)
                        .startswith("OK "))
        self.assertIn("model=%s" % config.vision_llm_model, self.dispatch("STATUS_FIELDS"))

    def test_a_model_the_endpoint_does_not_serve_is_refused(self):
        """This endpoint answers for an identifier it does not serve with whatever is
        loaded, so accepting one would silently point the assistant at nothing."""
        reply = self.dispatch("SET_MODEL something-nobody-loaded")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertIn("model=%s" % config.llm_model, self.dispatch("STATUS_FIELDS"))

    def test_choosing_a_model_without_naming_one_is_refused(self):
        self.assertTrue(self.dispatch("SET_MODEL").startswith("FAIL "))


class RecognitionLanguageTest(AssistantCommandTestCase):
    """Which language the recogniser is told it is listening to."""

    def test_the_current_language_is_part_of_the_status(self):
        self.assertIn("language=%s" % config.whisper_language, self.dispatch("STATUS_FIELDS"))

    def test_an_offered_language_is_accepted_and_reaches_the_recogniser(self):
        self.assertTrue(self.dispatch("SET_LANGUAGE de").startswith("OK "))
        self.assertEqual("de", self.assistant._transcriber.language)
        self.assertIn("language=de", self.dispatch("STATUS_FIELDS"))

    def test_a_language_nobody_offers_is_refused_rather_than_guessed_at(self):
        reply = self.dispatch("SET_LANGUAGE kl")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertEqual(config.whisper_language, self.assistant._transcriber.language)


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
        self.assertTrue(self.dispatch("TRANSCRIPT_START").startswith("OK "))
        self.add_speech("the lease timeout was thirty seconds")
        self.assertIn("lease timeout", self.transcript_text())

    def test_speech_from_before_retaining_is_not_written_afterwards(self):
        """What was said while the session was ephemeral was said in confidence,
        so switching retention on is not retrospective."""
        self.add_speech("said while nobody was keeping it")
        self.dispatch("TRANSCRIPT_START")
        self.add_speech("said afterwards")
        transcript = self.transcript_text()
        self.assertNotIn("nobody was keeping it", transcript)
        self.assertIn("said afterwards", transcript)

    def test_status_reports_a_retained_session_and_where_it_is_kept(self):
        self.dispatch("TRANSCRIPT_START")
        self.add_speech("one utterance")
        reply = self.dispatch("STATUS")
        self.assertIn("retained", reply)
        self.assertIn(str(self.assistant._archive.directory), reply)

    def test_stopping_leaves_what_was_written_and_keeps_nothing_new(self):
        self.dispatch("TRANSCRIPT_START")
        self.add_speech("kept")
        self.dispatch("TRANSCRIPT_STOP")
        self.add_speech("not kept")
        transcript = self.transcript_text()
        self.assertIn("kept", transcript)
        self.assertNotIn("not kept", transcript)

    def test_the_toggle_turns_retention_on_and_off_again(self):
        self.dispatch("TRANSCRIPT_TOGGLE")
        self.assertTrue(self.assistant._archive.is_retaining)
        self.dispatch("TRANSCRIPT_TOGGLE")
        self.assertFalse(self.assistant._archive.is_retaining)

    def test_ending_a_retained_session_renders_the_markdown(self):
        self.dispatch("TRANSCRIPT_START")
        self.add_speech("the lease timeout was thirty seconds")
        self.assistant._finish_the_meeting_archive()
        self.assertIn("lease timeout",
                      self.assistant._archive.markdown_path.read_text(encoding="utf-8"))

    def test_a_screenshot_is_archived_beside_the_transcript(self):
        """Same directory, both timestamped: that is the whole association."""
        self.install_stub_capture_helper()
        self.dispatch("TRANSCRIPT_START")
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


class UngarbleCommandTest(AssistantCommandTestCase):

    def test_ungarble_sends_the_recent_transcript_to_the_model(self):
        self.add_speech("the lease timeout was thirty seconds")
        self.assertTrue(self.dispatch("UNGARBLE_SPEECH").startswith("OK "))
        requests = self.wait_for_one_answer()
        self.assertEqual(1, len(requests))
        self.assertIn("lease timeout", requests[0][1]["content"])

    def test_ungarble_accepts_an_explicit_window_length(self):
        self.add_speech("the lease timeout was thirty seconds")
        self.assertIn("30", self.dispatch("UNGARBLE_SPEECH 30"))

    def test_ungarble_uses_its_own_shorter_default_window(self):
        self.add_speech("the lease timeout was thirty seconds")
        reply = self.dispatch("UNGARBLE_SPEECH")
        self.assertIn("last 45s", reply)
        requests = self.wait_for_one_answer()
        self.assertIn("last 45 seconds", requests[0][1]["content"])

    def test_ungarble_without_speech_fails_instead_of_asking_the_model(self):
        self.assertTrue(self.dispatch("UNGARBLE_SPEECH").startswith("FAIL "))
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
        self.assertTrue(self.dispatch("SCREEN_VISION").startswith("OK "))
        requests = self.wait_for_one_answer()
        parts = requests[0][1]["content"]
        self.assertTrue(any(part["type"] == "image_url" for part in parts))

    def test_interpretation_works_without_any_transcript(self):
        self.install_stub_capture_helper()
        self.assertTrue(self.dispatch("SCREEN_VISION").startswith("OK "))
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
        reply = self.dispatch("SCREEN_VISION")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertEqual(1, len(sorted(config.archive_dir.rglob("*.png"))))

    def test_the_reply_says_the_screenshot_was_not_interpreted_and_why(self):
        reply = self.dispatch("SCREEN_VISION")
        self.assertIn("not interpreted", reply)
        self.assertIn(config.vision_llm_model, reply)

    def test_no_model_is_asked_to_interpret_the_image(self):
        self.dispatch("SCREEN_VISION")
        time.sleep(0.2)
        self.assertEqual([], self.llm.requests)


class PromptSelectionTest(AssistantCommandTestCase):
    """One shared prompt selection for the console and the dispatcher, kept in
    var/selected_prompt so it survives a restart."""

    def test_the_default_selection_is_explain_and_writes_nothing(self):
        self.assertEqual("explain", self.assistant._selected_prompt_key)
        self.assertFalse(config.selected_prompt_path.exists())

    def test_p_selects_a_prompt_and_persists_it(self):
        reply = self.dispatch("p c")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertIn("code_review", reply)
        self.assertEqual("code_review", self.assistant._selected_prompt_key)
        self.assertEqual("code_review",
                         config.selected_prompt_path.read_text(encoding="utf-8").strip())

    def test_the_selection_survives_into_a_new_assistant(self):
        self.dispatch("p v")
        self.assertEqual("visually_interpret", Assistant([])._selected_prompt_key)

    def test_an_invalid_persisted_value_is_fatal_at_startup(self):
        config.selected_prompt_path.write_text("bogus\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            Assistant([])

    def test_a_failed_write_leaves_the_selection_unchanged(self):
        blocker = self.root / "not_a_directory"
        blocker.write_text("i am a file", encoding="utf-8")
        config.selected_prompt_path = blocker / "selected_prompt"
        reply = self.dispatch("p c")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertEqual("explain", self.assistant._selected_prompt_key)

    def test_a_replace_failure_is_a_fail_reply_and_leaves_everything_intact(self):
        self.dispatch("p r")
        with patch("rvw.prompts.os.replace", side_effect=OSError("disk full")):
            reply = self.dispatch("p c")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertEqual("recall", self.assistant._selected_prompt_key)
        self.assertEqual("recall", prompts.read_selected_prompt_key(
            config.selected_prompt_path))
        leftovers = [entry.name for entry in self.root.iterdir()
                     if entry.name.startswith("selected_prompt")]
        self.assertEqual(["selected_prompt"], leftovers)

    def test_pl_lists_every_key_with_its_full_prompt_and_marks_the_active(self):
        reply = self.dispatch("pl")
        self.assertTrue(reply.startswith("OK "), reply)
        for key, prompt in prompts.prompts.items():
            self.assertIn(key, reply)
            self.assertIn(prompt, reply)
        self.assertIn("* explain", reply)
        self.dispatch("p u")
        self.assertIn("* ungarble", self.dispatch("pl"))

    def test_invalid_selection_requests_change_nothing(self):
        for command in ["p", "PROMPT_SET", "p e x", "p explain", "p x", "p E", "p 1"]:
            reply = self.dispatch(command)
            self.assertTrue(reply.startswith("FAIL "), command)
            self.assertEqual("explain", self.assistant._selected_prompt_key)
        self.assertFalse(config.selected_prompt_path.exists())

    def test_the_named_commands_keep_their_own_prompts(self):
        """EXPLAIN_SPEECH and friends name their prompt explicitly; the selection
        only governs console submissions."""
        self.dispatch("p c")
        self.add_speech("the lease timeout was thirty seconds")
        self.dispatch("UNGARBLE_SPEECH")
        self.assertIn("reconstruct",
                      self.wait_for_one_answer()[0][0]["content"].lower())


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
