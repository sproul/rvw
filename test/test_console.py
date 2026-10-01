"""Tests for the interactive console: pending context, prompt choice, submit.

The console runs in the assistant's own terminal, so what is tested here is the
line language (`s`, `S`, `c`, `C`, `pl`, `pN`, `?`, `:COMMAND`, `q`), the order
context items are assembled into the model request, and the rules around when
that context is cleared: only after a submit was actually queued.
"""

import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from rvw import config, screenshot
from rvw.assistant import Assistant, parse_arguments
from rvw.console import Console
from rvw.transcript import TranscriptSegment


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


def fake_screenshot(name="2026-08-15_23.41.07.123.png"):
    return screenshot.Screenshot(image_path=Path(name), metadata_path=Path(name + ".json"),
                                 captured_epoch=0.0, metadata={})


class ConsoleTestCase(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.saved_archive_dir = config.archive_dir
        self.saved_delay = config.console_capture_delay_seconds
        config.archive_dir = self.root / "meetings"
        config.console_capture_delay_seconds = 0.0
        self.addCleanup(self.restore_configuration)
        self.assistant = Assistant([])
        self.assistant._log_path = self.root / "session.log"
        self.llm = RecordingLlm()
        self.assistant._llm = self.llm
        self.captures = []
        self.ocr_calls = []
        self.console = Console(self.assistant, capture=self._capture, ocr_reader=self._ocr)

    def restore_configuration(self):
        config.archive_dir = self.saved_archive_dir
        config.console_capture_delay_seconds = self.saved_delay
        self.temporary_directory.cleanup()

    def _capture(self, session_epoch):
        shot = fake_screenshot("shot_%d.png" % (len(self.captures) + 1))
        self.captures.append(shot)
        return shot

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

    def test_a_lower_case_s_appends_a_screenshot_without_submitting(self):
        reply = self.console.handle_line("s")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertEqual(1, len(self.console.pending))
        time.sleep(0.1)
        self.assertEqual([], self.llm.requests)

    def test_an_upper_case_s_appends_and_submits(self):
        reply = self.console.handle_line("S")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertEqual(1, len(self.wait_for_one_answer()))

    def test_a_comment_is_kept_verbatim_as_one_item(self):
        reply = self.console.handle_line("c the field is called    x.y_z")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertIn("x.y_z", self.console.pending[0].rendered())

    def test_an_upper_case_c_appends_and_submits(self):
        self.assertTrue(self.console.handle_line("C look here").startswith("OK "))
        self.assertIn("look here", self.user_text())

    def test_send_submits_the_queued_context_as_it_is(self):
        self.console.handle_line("s")
        self.console.handle_line("c a remark")
        reply = self.console.handle_line("send")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertEqual(1, len(self.wait_for_one_answer()))
        text = self.user_text()
        self.assertIn("Screenshot 1 (shot_1.png)", text)
        self.assertIn("Comment 1:\na remark", text)
        self.assertEqual([], self.console.pending)

    def test_send_after_a_busy_failure_retries_without_new_items(self):
        self.console.handle_line("s")
        self.console.handle_line("c one")
        self.assistant._answering.acquire()
        self.assertTrue(self.console.handle_line("send").startswith("FAIL "))
        self.assistant._answering.release()
        self.assertEqual(2, len(self.console.pending))
        reply = self.console.handle_line("send")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertEqual(1, len(self.wait_for_one_answer()))
        self.assertEqual([], self.console.pending)

    def test_an_empty_comment_is_refused_and_keeps_nothing(self):
        for line in ["c", "c   ", "C"]:
            reply = self.console.handle_line(line)
            self.assertTrue(reply.startswith("FAIL "), line)
        self.assertEqual([], self.console.pending)

    def test_pl_lists_the_prompts_and_the_current_selection(self):
        reply = self.console.handle_line("pl")
        self.assertIn("1", reply)
        self.assertIn("2", reply)
        self.assertIn("Understand meeting", reply)
        self.assertIn("Critique code", reply)

    def test_p_selects_a_prompt_by_number(self):
        self.assertTrue(self.console.handle_line("p2").startswith("OK "))
        self.assertEqual(2, self.console.selected_prompt)

    def test_an_invalid_prompt_number_fails_without_changing_the_selection(self):
        for line in ["p0", "p3", "px", "p"]:
            self.assertTrue(self.console.handle_line(line).startswith("FAIL "), line)
        self.assertEqual(1, self.console.selected_prompt)

    def test_question_mark_lists_aliases_and_every_dispatcher_command(self):
        reply = self.console.handle_line("?")
        for alias in ["s", "S", "c", "C", "pl", "p1", "?", "q"]:
            self.assertIn(alias, reply)
        for name in self.assistant._dispatcher.command_names():
            self.assertIn(name, reply)

    def test_a_colon_dispatches_the_existing_command_set(self):
        reply = self.console.handle_line(":STATUS")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertIn("capture:", reply)

    def test_an_unknown_colon_command_is_a_fail_reply(self):
        self.assertTrue(self.console.handle_line(":DEFINITELY_NOT").startswith("FAIL "))

    def test_q_quits_through_the_existing_quit_command(self):
        reply = self.console.handle_line("q")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertTrue(self.assistant._quit_requested.is_set())

    def test_an_unknown_line_is_a_fail_reply(self):
        self.assertTrue(self.console.handle_line("wibble").startswith("FAIL "))


class RequestAssemblyTest(ConsoleTestCase):
    """The user message is the ordered context, verbatim framing included."""

    def test_screenshots_and_comments_are_assembled_in_order(self):
        self.console.handle_line("s")
        self.console.handle_line("c first remark")
        self.console.handle_line("s")
        self.console.handle_line("S")    # third screenshot, then submit
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

    def test_the_meeting_prompt_is_the_default_system_prompt(self):
        self.console.handle_line("C look at this")
        self.assertEqual("Understand meeting", self.console.prompt_label())
        self.assertIn("You help me understand a live meeting.",
                          self.request()[0]["content"])

    def test_the_code_review_prompt_replaces_the_system_prompt(self):
        self.console.handle_line("p2")
        self.console.handle_line("C review this diff")
        self.assertIn("You are a code-review partner", self.request()[0]["content"])

    def test_meeting_mode_includes_the_recent_transcript_when_there_is_one(self):
        self.add_speech("the lease timeout was thirty seconds")
        self.console.handle_line("C what did they say")
        self.assertIn("Recent transcript:\n", self.user_text())
        self.assertIn("lease timeout", self.user_text())

    def test_meeting_mode_omits_the_transcript_section_when_empty(self):
        self.console.handle_line("C anything")
        self.assertNotIn("Recent transcript:", self.user_text())

    def test_code_review_mode_never_includes_a_transcript(self):
        self.add_speech("the lease timeout was thirty seconds")
        self.console.handle_line("p2")
        self.console.handle_line("C review")
        self.assertNotIn("Recent transcript:", self.user_text())
        self.assertNotIn("lease timeout", self.user_text())


class SubmitSemanticsTest(ConsoleTestCase):

    def test_a_queued_submit_clears_the_pending_context(self):
        self.console.handle_line("s")
        self.console.handle_line("S")
        self.assertEqual([], self.console.pending)

    def test_a_submit_with_empty_context_is_refused(self):
        reply = self.console._submit()
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertEqual([], self.llm.requests)

    def test_a_busy_model_keeps_the_context_and_asks_nothing(self):
        self.assistant._answering.acquire()
        self.addCleanup(self.assistant._answering.release)
        self.console.handle_line("s")
        reply = self.console.handle_line("S")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertIn("progress", reply)
        # Nothing was queued, so nothing is cleared: the earlier item and the
        # screenshot just taken both stay pending for the retry.
        self.assertEqual(2, len(self.console.pending))
        time.sleep(0.1)
        self.assertEqual([], self.llm.requests)

    def test_the_item_counters_restart_after_a_successful_submit(self):
        self.console.handle_line("s")
        self.console.handle_line("send")
        self.console.handle_line("s")
        self.console.handle_line("c later")
        self.console.handle_line("send")
        text = self.llm.requests[-1][1]["content"]
        self.assertIn("Screenshot 1 (shot_2.png)", text)
        self.assertIn("Comment 1:\nlater", text)
        self.assertNotIn("Screenshot 2", text)

    def test_the_item_counters_survive_a_busy_submit(self):
        self.console.handle_line("s")
        self.console.handle_line("c one")
        self.assistant._answering.acquire()
        self.assertTrue(self.console.handle_line("send").startswith("FAIL "))
        self.assistant._answering.release()
        self.console.handle_line("s")
        self.console.handle_line("send")
        text = self.user_text()
        self.assertIn("Screenshot 2 (shot_2.png)", text)
        self.assertIn("Comment 1:\none", text)

    def test_an_ocr_failure_appends_nothing_but_still_archives_the_image(self):
        def failing_ocr(image_path):
            raise RuntimeError("unreadable")
        self.console = Console(self.assistant, capture=self._capture, ocr_reader=failing_ocr)
        reply = self.console.handle_line("s")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertIn("unreadable", reply)
        self.assertEqual([], self.console.pending)
        self.assertEqual(1, len(self.captures))

    def test_empty_ocr_appends_nothing(self):
        self.console = Console(self.assistant, capture=self._capture,
                               ocr_reader=lambda path: "   ")
        reply = self.console.handle_line("s")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertEqual([], self.console.pending)

    def test_a_capture_failure_is_reported_as_a_failure(self):
        def failing_capture(session_epoch):
            raise RuntimeError("screen capture failed: permission denied")
        self.console = Console(self.assistant, capture=failing_capture, ocr_reader=self._ocr)
        reply = self.console.handle_line("s")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertEqual([], self.ocr_calls)


class SourceNoneCaptureTest(unittest.TestCase):
    """With no streams there is nothing to start: the capture commands must say
    so instead of reporting 'capture running' over an empty set."""

    def setUp(self):
        self.assistant = Assistant([])

    def test_start_capture_fails_clearly(self):
        reply = self.assistant._dispatcher.dispatch("START_CAPTURE")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertIn("no capture streams", reply)

    def test_toggle_capture_fails_clearly(self):
        self.assertTrue(self.assistant._dispatcher.dispatch("TOGGLE_CAPTURE").startswith("FAIL "))

    def test_toggle_continuous_fails_and_does_not_arm_the_timer(self):
        reply = self.assistant._dispatcher.dispatch("TOGGLE_CONTINUOUS")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertFalse(self.assistant._continuous_analysis.is_set())


class ConsoleTickerTest(ConsoleTestCase):
    """In console mode the periodic continuous analysis must still tick: the
    read loop replaced the wait loop, so a small thread has to run it."""

    def setUp(self):
        super().setUp()
        self.saved_period = config.continuous_analysis_period_seconds
        # The period is also the transcript window EXPLAIN looks at, so it must
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
            self.assertTrue(self.wait_for_one_answer(), "EXPLAIN was never asked")
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
        return Console(self.assistant, capture=self._capture, ocr_reader=self._ocr,
                       input_stream=stream)

    def test_piped_lines_are_handled_in_order_and_quit_exits(self):
        console = self.piped_console("pl\nq\n")
        console.run()
        self.assertTrue(self.assistant._quit_requested.is_set())

    def test_a_socket_side_quit_wakes_the_read_loop_without_input(self):
        console = self.piped_console("")
        timer = threading.Timer(0.2, self.assistant._quit_requested.set)
        timer.start()
        started = time.monotonic()
        console.run()
        timer.cancel()
        self.assertLess(time.monotonic() - started, 5.0)


class SourceNoneTest(unittest.TestCase):
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
        console = Console(self.assistant, capture=self._capture)
        reply = console.handle_line("s")
        self.assertTrue(reply.startswith("OK "), reply)
        self.assertIn("stub helper saw the image", console.pending[0].rendered())

    def test_a_failing_real_helper_is_reported(self):
        self.install_stub_ocr_helper('#!/bin/sh\necho "FAIL broken" >&2\nexit 3\n')
        console = Console(self.assistant, capture=self._capture)
        reply = console.handle_line("s")
        self.assertTrue(reply.startswith("FAIL "), reply)
        self.assertIn("broken", reply)
        self.assertEqual([], console.pending)


class CaptureDelayTest(ConsoleTestCase):

    def test_the_console_waits_the_configured_delay_before_capturing(self):
        config.console_capture_delay_seconds = 0.05
        started = time.monotonic()
        self.console.handle_line("s")
        self.assertGreaterEqual(time.monotonic() - started, 0.05)


if __name__ == "__main__":
    unittest.main()
