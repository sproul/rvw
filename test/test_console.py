"""Tests for the interactive console: pending context, prompt choice, submit.

The console runs in the assistant's own terminal and passes every line but `?`
to the dispatcher, so what is tested here is that line language, the order
context items are assembled into the model request, and the rules around when
that context is cleared: only after a submit was actually queued.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from rvw import config, prompts, screenshot
from rvw.assistant import Assistant, parse_arguments
from rvw.console import Console
from rvw.transcript import TranscriptSegment
from test_assistant import stub_helper

repo_dir = Path(__file__).resolve().parents[1]


class RecordingLlm:
    """Stands in for the local model; records requests instead of sending them."""

    def __init__(self):
        self.requests = []
        self.model = config.llm_model

    def stream_chat(self, messages, on_token, on_reasoning=None):
        self.requests.append(messages)
        on_token("stub answer")
        return "stub answer"

    def available_models(self):
        return [config.llm_model]


class BlockingLlm(RecordingLlm):
    """stream_chat holds the answer open until the test releases it."""

    def __init__(self):
        super().__init__()
        self.release = threading.Event()

    def stream_chat(self, messages, on_token, on_reasoning=None):
        self.requests.append(messages)
        self.release.wait(5)
        on_token("stub answer")
        return "stub answer"


def fake_screenshot(name="2026-08-15_23.41.07.123.png"):
    return screenshot.Screenshot(image_path=Path(name), metadata_path=Path(name + ".json"),
                                 captured_epoch=0.0, metadata={})


class ConsoleTestCase(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.saved_archive_dir = config.archive_dir
        self.saved_selected_prompt_path = config.selected_prompt_path
        config.archive_dir = self.root / "meetings"
        config.selected_prompt_path = self.root / "selected_prompt"
        self.addCleanup(self.restore_configuration)
        self.assistant = Assistant([])
        self.assistant._log_path = self.root / "session.log"
        self.llm = RecordingLlm()
        self.assistant._llm = self.llm
        self.captures = []
        self.ocr_calls = []
        self.events = []
        self.use_console()

    def use_console(self, **seams):
        """Install a Console with stub seams as the assistant's own, which is
        the one every dispatcher command reaches. No test may drive the real
        Hammerspoon: it would minimize whatever window is running the tests."""
        stubs = dict(capture=self._capture, ocr_reader=self._ocr, hs_runner=self._hs)
        stubs.update(seams)
        self.console = Console(self.assistant, **stubs)
        self.assistant._console = self.console
        return self.console

    def restore_configuration(self):
        config.archive_dir = self.saved_archive_dir
        config.selected_prompt_path = self.saved_selected_prompt_path
        self.wait_for_any_answer_thread_to_finish()
        self.temporary_directory.cleanup()

    def wait_for_any_answer_thread_to_finish(self):
        """An answer thread outlives the request the test checked and keeps
        writing under the temporary directory; removing it underneath that
        thread fails with 'Directory not empty'."""
        self.assertTrue(self.assistant._answering.acquire(timeout=5.0),
                        "an answer thread was still running after 5s")
        self.assistant._answering.release()

    def _capture(self, session_epoch, target=None, exclude_window_id=None):
        self.events.append(("capture", ""))
        shot = fake_screenshot("shot_%d.png" % (len(self.captures) + 1))
        self.captures.append((shot, target, exclude_window_id))
        return shot

    def _hs(self, script):
        """Stands in for `hs -c`: the frontmost window is 4242 and obliges."""
        self.events.append(("hs", script))
        if "minimize" in script and "unminimize" not in script:
            return "4242"
        return ""

    @property
    def hs_calls(self):
        return [script for event, script in self.events if event == "hs"]

    def _ocr(self, image_path):
        self.ocr_calls.append(image_path)
        return "text seen on %s" % image_path.name

    def wait_for_one_answer(self):
        deadline = time.monotonic() + 5.0
        while not self.llm.requests and time.monotonic() < deadline:
            time.sleep(0.02)
        return self.llm.requests

    def request(self):
        self.wait_for_one_answer()
        return self.llm.requests[0]

    def user_text(self):
        return self.request()[1]["content"]

    def add_speech(self, text):
        now = time.time()
        self.assistant._transcript.add(TranscriptSegment(stream="system",
                                                         start_epoch=now - 5.0,
                                                         end_epoch=now - 1.0, text=text))


class LineLanguageTest(ConsoleTestCase):
    """Every console line but `?` is a dispatcher command, typed exactly as
    bin/rvwctl would send it, so a shortcut means the same thing everywhere."""

    def test_a_command_name_is_dispatched_with_its_arguments(self):
        reply = self.console.handle_line("PROMPT_SET c")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertEqual("code_review", self.assistant._selected_prompt_key)

    def test_a_command_name_without_arguments_is_dispatched(self):
        reply = self.console.handle_line("STATUS")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertIn("capture:", reply)

    def test_a_shortcut_is_dispatched_with_its_arguments(self):
        reply = self.console.handle_line("t+")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertTrue(self.assistant._archive.is_retaining)

    def test_upper_case_s_is_the_status_shortcut_here_too(self):
        reply = self.console.handle_line("S")
        self.assertIn("capture:", reply)
        self.assertEqual([], self.captures)

    def test_lower_case_c_is_the_capture_toggle_here_too(self):
        reply = self.console.handle_line("c")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertIn("no capture streams", reply)
        self.assertEqual([], self.console.pending)

    def test_sa_queues_a_screenshot_without_submitting(self):
        reply = self.console.handle_line("sa")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertEqual(["screenshot"], [item.kind for item in self.console.pending])
        time.sleep(0.1)
        self.assertEqual([], self.llm.requests)

    def test_sx_queues_a_screenshot_and_submits(self):
        self.assertTrue(self.console.handle_line("sx").startswith("OK "))
        self.assertEqual(1, len(self.wait_for_one_answer()))

    def test_n_queues_a_comment_as_one_item(self):
        reply = self.console.handle_line("n the field is called x.y_z")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertEqual(["the field is called x.y_z"],
                         [item.text for item in self.console.pending])

    def test_upper_case_n_queues_a_comment_and_submits(self):
        self.assertTrue(self.console.handle_line("N look here").startswith("OK "))
        self.assertEqual(1, len(self.wait_for_one_answer()))

    def test_send_submits_the_queued_context_as_it_is(self):
        self.console.handle_line("sa")
        self.console.handle_line("n a remark")
        reply = self.console.handle_line("send")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertIn("2 item", reply)
        self.assertEqual(1, len(self.wait_for_one_answer()))
        self.assertEqual([], self.console.pending)

    def test_send_after_a_busy_failure_retries_without_new_items(self):
        self.console.handle_line("sa")
        self.console.handle_line("n one")
        self.assistant._answering.acquire()
        self.assertTrue(self.console.handle_line("send").startswith("FAIL "))
        self.assistant._answering.release()
        reply = self.console.handle_line("send")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertIn("2 item", reply)

    def test_an_empty_comment_is_refused_and_keeps_nothing(self):
        for line in ["n", "N", "n   "]:
            reply = self.console.handle_line(line)
            self.assertTrue(reply.startswith("FAIL "), line)
        self.assertEqual([], self.console.pending)

    def test_pl_lists_every_prompt_with_its_text_and_the_current_selection(self):
        reply = self.console.handle_line("pl")
        self.assertTrue(reply.startswith("OK "), reply)
        for key, prompt in prompts.prompts.items():
            self.assertIn(key, reply)
            self.assertIn(prompt, reply)
        self.assertIn("* explain", reply)

    def test_p_selects_a_prompt_by_its_letter(self):
        reply = self.console.handle_line("p c")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertEqual("code_review", self.assistant._selected_prompt_key)

    def test_an_invalid_prompt_letter_fails_without_changing_the_selection(self):
        for line in ["p", "p 1", "p x", "p E", "p explain", "p e x", "p1"]:
            self.assertTrue(self.console.handle_line(line).startswith("FAIL "), line)
        self.assertEqual("explain", self.assistant._selected_prompt_key)

    def test_question_mark_lists_each_command_once_with_its_shortcut_and_purpose(self):
        reply = self.console.handle_line("?")
        lines = reply.splitlines()
        self.assertIn("?  list these commands", lines)
        for shortcut, name, description in self.assistant._dispatcher.command_help():
            matches = [line for line in lines
                       if line.startswith("%s  %s  " % (shortcut, name))]
            self.assertEqual(1, len(matches), "expected one %s  %s line" % (shortcut, name))
            self.assertTrue(matches[0].split(name, 1)[1].strip(),
                            "%s carries no explanation" % name)
        self.assertEqual([], [line for line in lines if line.startswith(":")])

    def test_every_dispatcher_command_has_a_nonempty_description(self):
        for _shortcut, _name, description in self.assistant._dispatcher.command_help():
            self.assertTrue(description.strip(), _name)

    def test_help_distinguishes_archiving_from_queueing_a_screenshot(self):
        reply = self.console.handle_line("?")
        screenshot_lines = [line for line in reply.splitlines()
                            if line.startswith("s  SCREEN_SAVE  ")]
        self.assertEqual(1, len(screenshot_lines))
        self.assertIn("without", screenshot_lines[0].lower())

    def test_the_colon_prefix_is_no_longer_understood(self):
        self.assertTrue(self.console.handle_line(":STATUS").startswith("FAIL "))

    def test_command_names_stay_case_sensitive(self):
        self.assertTrue(self.console.handle_line("status").startswith("FAIL "))

    def test_q_quits_through_the_quit_command(self):
        reply = self.console.handle_line("q")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertTrue(self.assistant._quit_requested.is_set())

    def test_an_unknown_line_is_a_fail_reply(self):
        self.assertTrue(self.console.handle_line("wibble").startswith("FAIL "))

    def test_a_blank_line_says_nothing(self):
        self.assertEqual("", self.console.handle_line("   "))


class RequestAssemblyTest(ConsoleTestCase):
    """The user message is the ordered context, verbatim framing included."""

    def test_screenshots_and_comments_are_assembled_in_order(self):
        self.console.handle_line("sa")
        self.console.handle_line("n first remark")
        self.console.handle_line("sa")
        self.console.handle_line("sx")    # third screenshot, then submit
        text = self.user_text()
        self.assertTrue(text.startswith(
            "These observations were assembled in the order supplied. Screenshot text "
            "was extracted by OCR; you cannot see the underlying images. Treat OCR as "
            "fallible.\n\n"), text)
        expected = ("Screenshot 1 (shot_1.png):\ntext seen on shot_1.png\n\n"
                    "Comment 1:\nfirst remark\n\n"
                    "Screenshot 2 (shot_2.png):\ntext seen on shot_2.png\n\n"
                    "Screenshot 3 (shot_3.png):\ntext seen on shot_3.png\n\n")
        self.assertEqual(text.split("\n\n", 1)[1], expected)

    def test_the_explain_prompt_is_the_default_system_prompt(self):
        self.console.handle_line("N look at this")
        self.assertEqual("explain", self.assistant._selected_prompt_key)
        self.assertEqual(prompts.explain_system_prompt, self.request()[0]["content"])

    def test_the_code_review_prompt_replaces_the_system_prompt(self):
        self.console.handle_line("p c")
        self.console.handle_line("N review this diff")
        self.assertEqual(prompts.code_review_system_prompt,
                         self.request()[0]["content"])

    def test_explain_ungarble_and_visually_interpret_include_the_transcript(self):
        self.add_speech("the lease timeout was thirty seconds")
        for letter in ["e", "u", "v"]:
            self.console.handle_line("p " + letter)
            self.console.handle_line("N go")
            self.wait_for_any_answer_thread_to_finish()
            text = self.llm.requests[-1][1]["content"]
            self.assertIn("Recent transcript:\n", text, letter)
            self.assertIn("lease timeout", text, letter)

    def test_explain_omits_the_transcript_section_when_empty(self):
        self.console.handle_line("N anything")
        self.assertNotIn("Recent transcript:", self.user_text())

    def test_recall_and_code_review_never_include_a_transcript(self):
        self.add_speech("the lease timeout was thirty seconds")
        for letter in ["r", "c"]:
            self.console.handle_line("p " + letter)
            self.console.handle_line("N go")
            self.wait_for_any_answer_thread_to_finish()
            text = self.llm.requests[-1][1]["content"]
            self.assertNotIn("Recent transcript:", text, letter)
            self.assertNotIn("lease timeout", text, letter)


class SubmitSemanticsTest(ConsoleTestCase):

    def test_a_queued_submit_clears_the_pending_context(self):
        self.console.handle_line("sa")
        self.console.handle_line("sx")
        self.assertEqual([], self.console.pending)

    def test_a_submit_with_empty_context_is_refused(self):
        reply = self.console.handle_line("send")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertIn("nothing to submit", reply)
        self.assertEqual([], self.llm.requests)

    def test_a_busy_model_keeps_the_context_and_asks_nothing(self):
        self.assistant._answering.acquire()
        self.addCleanup(self.assistant._answering.release)
        self.console.handle_line("sa")
        reply = self.console.handle_line("sx")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertIn("progress", reply)
        # Nothing was queued, so nothing is cleared: the earlier item and the
        # screenshot just taken both stay pending for the retry.
        self.assertEqual(2, len(self.console.pending))
        time.sleep(0.1)
        self.assertEqual([], self.llm.requests)

    def test_the_item_counters_restart_after_a_successful_submit(self):
        self.console.handle_line("sa")
        self.console.handle_line("send")
        self.wait_for_any_answer_thread_to_finish()
        self.console.handle_line("sa")
        self.console.handle_line("n later")
        self.console.handle_line("send")
        self.wait_for_any_answer_thread_to_finish()
        text = self.llm.requests[-1][1]["content"]
        self.assertIn("Screenshot 1 (shot_2.png)", text)
        self.assertIn("Comment 1:\nlater", text)
        self.assertNotIn("Screenshot 2", text)

    def test_the_item_counters_survive_a_busy_submit(self):
        self.console.handle_line("sa")
        self.console.handle_line("n one")
        self.assistant._answering.acquire()
        self.assertTrue(self.console.handle_line("send").startswith("FAIL "))
        self.assistant._answering.release()
        self.console.handle_line("sa")
        self.console.handle_line("send")
        text = self.user_text()
        self.assertIn("Screenshot 2 (shot_2.png)", text)
        self.assertIn("Comment 1:\none", text)

    def test_an_ocr_failure_appends_nothing_but_still_archives_the_image(self):
        def failing_ocr(image_path):
            raise RuntimeError("unreadable")
        self.use_console(ocr_reader=failing_ocr)
        reply = self.console.handle_line("sa")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertIn("unreadable", reply)
        self.assertEqual([], self.console.pending)
        self.assertEqual(1, len(self.captures))

    def test_empty_ocr_appends_nothing(self):
        self.use_console(ocr_reader=lambda path: "   ")
        reply = self.console.handle_line("sa")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertEqual([], self.console.pending)

    def test_a_capture_failure_is_reported_as_a_failure(self):
        def failing_capture(session_epoch, target=None, exclude_window_id=None):
            raise RuntimeError("screen capture failed: permission denied")
        self.use_console(capture=failing_capture)
        reply = self.console.handle_line("sa")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertIn("permission denied", reply)
        self.assertEqual([], self.ocr_calls)


class RequestingWindowTest(ConsoleTestCase):
    """A screen command typed into a terminal would photograph that terminal:
    it is the frontmost window. So unless Hammerspoon marked the request as a
    hotkey, the frontmost window is minimized, the whole display captured with
    that window excluded, and the window put back -- even when the capture
    failed. A window that cannot be hidden means no capture at all."""

    def dispatch_as_a_hotkey(self, command_line):
        return self.assistant._dispatcher.dispatch(command_line)

    def assert_hidden_capture(self):
        self.assertEqual(["hs", "capture", "hs"], [event for event, _ in self.events])
        self.assertIn("minimize", self.hs_calls[0])
        self.assertIn("unminimize", self.hs_calls[1])
        self.assertIn("4242", self.hs_calls[1])
        _, target, excluded = self.captures[0]
        self.assertEqual("display", target)
        self.assertEqual(4242, excluded)

    def test_a_typed_screen_add_hides_the_console_for_the_capture(self):
        self.assertTrue(self.console.handle_line("sa").startswith("OK "))
        self.assert_hidden_capture()

    def test_a_typed_screen_save_hides_it_too(self):
        self.assertTrue(self.console.handle_line("s").startswith("OK "))
        self.assert_hidden_capture()

    def test_an_unmarked_socket_command_hides_the_window_that_sent_it(self):
        reply = self.assistant._dispatcher.dispatch("SCREEN_ANALYZE",
                                                    sent_from_frontmost_window=True)
        self.assertTrue(reply.startswith("OK "), reply)
        self.assert_hidden_capture()

    def test_a_hotkey_captures_the_frontmost_window_untouched(self):
        self.assertTrue(self.dispatch_as_a_hotkey("SCREEN_ADD").startswith("OK "))
        self.assertEqual([], self.hs_calls)
        _, target, excluded = self.captures[0]
        self.assertIsNone(target)
        self.assertIsNone(excluded)

    def test_a_typed_command_hides_nothing_when_the_screen_is_the_other_macs(self):
        """The capture card shows the work machine; this machine's windows are
        not in the picture, so minimizing one would only get in the way."""
        saved_source = config.screenshot_source
        self.addCleanup(setattr, config, "screenshot_source", saved_source)
        config.screenshot_source = "hdmi"
        self.assertTrue(self.console.handle_line("sa").startswith("OK "))
        self.assertEqual([], self.hs_calls)
        _, target, excluded = self.captures[0]
        self.assertIsNone(target)
        self.assertIsNone(excluded)

    def test_a_window_id_that_is_not_numeric_never_reaches_capture(self):
        def gibberish(script):
            self.events.append(("hs", script))
            return "not-a-window-id"
        self.use_console(hs_runner=gibberish)
        self.assertTrue(self.console.handle_line("sa").startswith("FAIL "))
        self.assertEqual(["hs"], [event for event, _ in self.events])
        self.assertEqual([], self.captures)

    def _restore_fails(self, script):
        self.events.append(("hs", script))
        if "unminimize" in script:
            raise RuntimeError("the window is gone")
        return "4242"

    def test_a_restore_failure_is_reported_not_claimed_as_success(self):
        self.use_console(hs_runner=self._restore_fails)
        reply = self.console.handle_line("sa")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertIn("restor", reply.lower())

    def test_a_restore_failure_is_reported_even_when_the_capture_failed(self):
        def failing(session_epoch, target=None, exclude_window_id=None):
            self.events.append(("capture", ""))
            raise RuntimeError("capture exploded")
        self.use_console(capture=failing, hs_runner=self._restore_fails)
        reply = self.console.handle_line("sa")
        self.assertIn("restor", reply.lower())
        self.assertEqual(["hs", "capture", "hs"], [event for event, _ in self.events])

    def test_a_capture_failure_still_restores_the_window(self):
        def failing(session_epoch, target=None, exclude_window_id=None):
            raise RuntimeError("capture exploded")
        self.use_console(capture=failing)
        reply = self.console.handle_line("sa")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertIn("capture exploded", reply)
        self.assertEqual(2, len(self.hs_calls), "the restore never ran")

    def test_without_hammerspoon_nothing_is_captured(self):
        def no_hs(script):
            raise RuntimeError("Hammerspoon's 'hs' command is not installed")
        self.use_console(hs_runner=no_hs)
        reply = self.console.handle_line("sa")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertIn("hs", reply)
        self.assertEqual([], self.captures, "captured anyway without hiding the window")
        self.assertEqual([], self.console.pending)

    def test_a_frontmost_window_that_cannot_minimize_captures_nothing(self):
        def stubborn(script):
            raise RuntimeError("the frontmost window cannot be minimized")
        self.use_console(hs_runner=stubborn)
        self.assertTrue(self.console.handle_line("sa").startswith("FAIL "))
        self.assertEqual([], self.captures)


class AssistantInitTestCase(unittest.TestCase):
    """Assistant([]) reads the persisted prompt selection, so isolate it."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.saved_selected_prompt_path = config.selected_prompt_path
        config.selected_prompt_path = (Path(self.temporary_directory.name)
                                       / "selected_prompt")
        self.addCleanup(self.restore_configuration)

    def restore_configuration(self):
        config.selected_prompt_path = self.saved_selected_prompt_path
        self.temporary_directory.cleanup()


class SourceNoneCaptureTest(AssistantInitTestCase):
    """With no streams there is nothing to start: the capture commands must say
    so instead of reporting 'capture running' over an empty set."""

    def setUp(self):
        super().setUp()
        self.assistant = Assistant([])

    def test_start_capture_fails_clearly(self):
        reply = self.assistant._dispatcher.dispatch("AUDIO_CAPTURE_START")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertIn("no capture streams", reply)

    def test_toggle_capture_fails_clearly(self):
        self.assertTrue(self.assistant._dispatcher.dispatch("AUDIO_CAPTURE_TOGGLE")
                        .startswith("FAIL "))

    def test_continuous_analysis_is_no_longer_a_command(self):
        reply = self.assistant._dispatcher.dispatch("TOGGLE_CONTINUOUS")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertIn("unknown command", reply)
        self.assertFalse(self.assistant._continuous_analysis.is_set())


class ConsoleTickerTest(ConsoleTestCase):
    """In console mode the periodic continuous analysis must still tick: the
    read loop replaced the wait loop, so a small thread has to run it."""

    def setUp(self):
        super().setUp()
        self.saved_period = config.continuous_analysis_period_seconds
        # The period is also the transcript window EXPLAIN_SPEECH looks at, so it must
        # reach the utterance added below; _last_continuous_analysis starts at
        # 0, so the first tick is due immediately either way.
        config.continuous_analysis_period_seconds = 60.0
        self.addCleanup(self.restore_period)

    def restore_period(self):
        config.continuous_analysis_period_seconds = self.saved_period

    def test_the_ticker_fires_explain_while_continuous_is_on(self):
        self.add_speech("the lease timeout was thirty seconds")
        self.assistant._continuous_analysis.set()
        ticker = threading.Thread(target=self.assistant._console_ticks, daemon=True)
        ticker.start()
        try:
            self.assertTrue(self.wait_for_one_answer(), "EXPLAIN_SPEECH was never asked")
        finally:
            self.assistant._quit_requested.set()
            ticker.join(timeout=5)
        self.assertFalse(ticker.is_alive())
        self.assertIn("Explain this passage now.", self.user_text())

    def test_the_ticker_stops_on_quit_and_fires_nothing_more(self):
        self.add_speech("something said")
        self.assistant._continuous_analysis.set()
        ticker = threading.Thread(target=self.assistant._console_ticks, daemon=True)
        ticker.start()
        self.wait_for_one_answer()
        self.assistant._quit_requested.set()
        ticker.join(timeout=5)
        count = len(self.llm.requests)
        time.sleep(0.2)
        self.assertEqual(count, len(self.llm.requests))


class ConsoleReadLoopTest(ConsoleTestCase):
    """The console must not sit in input() forever after QUIT came in over the
    control socket: the read loop polls stdin so the quit event can end it."""

    def piped_console(self, written):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, written.encode("utf-8"))
        os.close(write_fd)
        stream = os.fdopen(read_fd)
        self.addCleanup(stream.close)
        return self.use_console(input_stream=stream)

    def test_piped_lines_are_handled_in_order_and_quit_exits(self):
        console = self.piped_console("pl\nq\n")
        console.run()
        self.assertTrue(self.assistant._quit_requested.is_set())

    def test_the_next_line_waits_for_an_answer_the_last_one_started(self):
        """An answer streams to this terminal; reading on would put the next
        `rvw> ` prompt in the middle of it."""
        self.llm = self.assistant._llm = BlockingLlm()
        console = self.piped_console("N a remark\nq\n")
        runner = threading.Thread(target=console.run, daemon=True)
        runner.start()
        try:
            self.wait_for_one_answer()
            time.sleep(0.3)
            self.assertFalse(self.assistant._quit_requested.is_set(),
                             "q was read while the answer was still streaming")
        finally:
            self.llm.release.set()
        runner.join(timeout=5)
        self.assertTrue(self.assistant._quit_requested.is_set())

    def test_a_socket_side_quit_wakes_the_read_loop_without_input(self):
        console = self.piped_console("")
        timer = threading.Timer(0.2, self.assistant._quit_requested.set)
        timer.start()
        started = time.monotonic()
        console.run()
        timer.cancel()
        self.assertLess(time.monotonic() - started, 5.0)


class SourceNoneTest(AssistantInitTestCase):
    """`--source none` exists for the console: no audio, no Whisper startup."""

    def test_the_argument_parser_accepts_source_none_and_console(self):
        arguments = parse_arguments(["--source", "none", "--console"])
        self.assertEqual("none", arguments.source)
        self.assertTrue(arguments.console)

    def test_source_none_builds_an_assistant_with_no_streams(self):
        arguments = parse_arguments(["--source", "none", "--console"])
        stream_names = [] if arguments.source == "none" else [arguments.source]
        assistant = Assistant(stream_names)
        self.assertEqual({}, assistant._streams)
        self.assertTrue(assistant._dispatcher.dispatch("STATUS").startswith("OK "))

    def test_the_retired_hide_flags_are_refused(self):
        for flag in ["--auto-hide-console", "--no-auto-hide-console"]:
            with self.assertRaises(SystemExit):
                parse_arguments(["--source", "none", "--console", flag])

    def test_source_none_does_not_start_the_speech_pipeline(self):
        assistant = Assistant([])
        assistant._recognizer = MagicMock()
        assistant._transcriber = MagicMock()
        assistant._start_speech_pipeline()
        assistant._recognizer.start.assert_not_called()
        assistant._transcriber.warm_up.assert_not_called()


class RealOcrPathTest(ConsoleTestCase):
    """No injected OCR stub: the Console must resolve the real wrapper, which
    runs the helper binary -- here a stub executable standing in for it."""

    def install_stub_ocr_helper(self, source):
        path = self.root / "ocr_image"
        path.write_text(source, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        self.saved_helper = config.ocr_helper_path
        config.ocr_helper_path = path
        self.addCleanup(self.restore_helper)

    def restore_helper(self):
        config.ocr_helper_path = self.saved_helper

    def test_a_console_with_no_injection_uses_the_real_ocr_wrapper(self):
        self.install_stub_ocr_helper('#!/bin/sh\necho "stub helper saw the image"\n')
        console = self.use_console(ocr_reader=None)
        reply = console.handle_line("sa")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertIn("stub helper saw the image", console.pending[0].rendered())

    def test_a_failing_real_helper_is_reported(self):
        self.install_stub_ocr_helper('#!/bin/sh\necho "FAIL broken" >&2\nexit 3\n')
        console = self.use_console(ocr_reader=None)
        reply = console.handle_line("sa")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertIn("broken", reply)
        self.assertEqual([], console.pending)


class LauncherRoutingTest(unittest.TestCase):
    """`bin/rvw` decides where the assistant runs. A console belongs in this
    terminal -- it reads this terminal's input and answers stream back into it --
    so `--console` anywhere in the arguments takes the in-terminal route just
    like `-here`."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="rvw-launcher-"))
        self.addCleanup(shutil.rmtree, self.root)
        for relative in ("bin", ".venv/bin", "util", "helper", "stub_bin"):
            (self.root / relative).mkdir(parents=True)
        shutil.copy(repo_dir / "bin/rvw", self.root / "bin/rvw")
        self._stand_in(".venv/bin/python", 'printf "PYTHON %s\\n" "$@"\n')
        self._stand_in("bin/audio_capture", "")
        self._stand_in("util/ffmpeg_env.sh",
                       "add_ffmpeg_libs_to_dyld_path() { return 0; }\n")
        self._stand_in("helper/build_app.sh", "exit 0\n")
        self._stand_in("stub_bin/open", """
            opened=""
            app=""
            while [ $# -gt 0 ]; do
              case "$1" in
                -a) app=$2; shift 2 ;;
                --args) shift; opened="$*"; break ;;
                *) shift ;;
              esac
            done
            echo "started rvw as pid 4242" > "$(dirname "$(dirname "$app")")/var/log/rvw.launcher.log"
            echo "OPENED $app $opened"
        """)

    def _stand_in(self, relative, body):
        path = self.root / relative
        path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def launch(self, *arguments):
        environment = dict(os.environ)
        environment["PATH"] = "%s:%s" % (self.root / "stub_bin", environment["PATH"])
        return subprocess.run(["bash", str(self.root / "bin/rvw"), *arguments],
                              capture_output=True, text=True, timeout=30,
                              env=environment)

    def test_a_plain_invocation_still_starts_inside_the_app_bundle(self):
        finished = self.launch("--listen")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("OPENED", finished.stdout)
        self.assertIn("rvw.app", finished.stdout)
        self.assertIn("--listen", finished.stdout)
        self.assertNotIn("PYTHON", finished.stdout)

    def test_dash_here_runs_the_daemon_in_this_terminal(self):
        finished = self.launch("-here", "--debug")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertNotIn("OPENED", finished.stdout)
        arguments = [line[7:] for line in finished.stdout.splitlines()
                     if line.startswith("PYTHON ")]
        self.assertEqual(["-m", "rvw.assistant", "--debug"], arguments)

    def test_console_runs_in_this_terminal_wherever_it_appears(self):
        finished = self.launch("--source", "none", "--console")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertNotIn("OPENED", finished.stdout)
        arguments = [line[7:] for line in finished.stdout.splitlines()
                     if line.startswith("PYTHON ")]
        self.assertEqual(["-m", "rvw.assistant", "--source", "none", "--console"],
                         arguments)

    def test_a_console_substring_is_not_a_console_flag(self):
        finished = self.launch("--consoled")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("OPENED", finished.stdout)
        self.assertNotIn("PYTHON", finished.stdout)


if __name__ == "__main__":
    unittest.main()
