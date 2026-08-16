"""The Phase 1 listening assistant: capture, transcribe, explain on demand.

Everything runs in one process, but the seams that later phases need are
already in place: capture is a separate helper process, commands arrive
through a dispatcher, and the transcript records are timestamped.
"""

import argparse
import logging
import sys
import threading
import time

from . import config, prompts, screenshot, session_log
from .asr import WhisperTranscriber
from .audio_source import CaptureStream
from .commands import CommandDispatcher
from .control import ControlSocketServer
from .llm import LocalLlm, LocalLlmError
from .recognizer import RecognitionWorker
from .transcript import RollingTranscript

log = logging.getLogger(__name__)

all_stream_names = ["mic", "system"]


class Assistant:
    """Wires the components together and implements the hotkey commands."""

    def __init__(self, stream_names):
        self._transcript = RollingTranscript()
        self._transcriber = WhisperTranscriber()
        self._recognizer = RecognitionWorker(self._transcriber, self._transcript)
        self._streams = {name: CaptureStream(name, self._recognizer.submit)
                         for name in stream_names}
        self._llm = LocalLlm()
        self._vision_llm = LocalLlm(model=config.vision_llm_model)
        self._dispatcher = self._build_dispatcher()
        self._control = ControlSocketServer(self._dispatcher)
        self._answering = threading.Lock()
        self._continuous_analysis = threading.Event()
        self._quit_requested = threading.Event()
        self._last_continuous_analysis = 0.0
        self._session_started_epoch = time.time()
        self._log_path = None

    # -- lifecycle ---------------------------------------------------------

    def run(self, start_capture_immediately):
        self._log_path = session_log.start_session_log()
        log.info("OK  session log %s", self._log_path)
        self._report_llm_status()
        self._recognizer.start()
        self._transcriber.warm_up()
        self._control.start()
        self._print_ready_banner()
        if start_capture_immediately:
            self._start_capture_at_start_up()
        self._wait_for_quit()

    def _start_capture_at_start_up(self):
        """A stream that cannot start must not take the whole assistant down with it."""
        try:
            log.info("OK  %s", self._start_streams(self._requested_stream_names([])))
        except RuntimeError as error:
            log.error("FAIL %s; fix it and press the capture hotkey", error)

    def _wait_for_quit(self):
        while not self._quit_requested.wait(timeout=1.0):
            self._run_continuous_analysis_if_due()
        self.shut_down()

    def shut_down(self):
        self._continuous_analysis.clear()
        for stream in self._streams.values():
            stream.stop()
        self._recognizer.stop()
        self._control.stop()
        log.info("OK  assistant stopped; session log is %s", self._log_path)

    def _report_llm_status(self):
        try:
            served = self._llm.available_models()
        except LocalLlmError as error:
            log.error("FAIL %s; run util/init_local_models.sh before asking for explanations",
                      error)
            return
        log.info("OK  local LLM at %s serving %s", config.llm_base_url,
                 ", ".join(served) or "no loaded model")
        self._warn_if_the_configured_model_is_missing(served)
        self._note_whether_the_vision_model_is_loaded(served)

    @staticmethod
    def _warn_if_the_configured_model_is_missing(served_models):
        """Better to hear about it now than when the hotkey is pressed mid-conversation."""
        if config.llm_model in served_models:
            return
        log.error("FAIL %s is not loaded; load it with 'lms load <model> --identifier %s', "
                  "or point RVW_LLM_MODEL at one of: %s",
                  config.llm_model, config.llm_model, ", ".join(served_models) or "nothing")

    @staticmethod
    def _note_whether_the_vision_model_is_loaded(served_models):
        """Interpretation is optional, so an absent vision model is news, not a failure."""
        if config.vision_llm_model in served_models:
            log.info("OK  vision model %s is loaded, so screenshots can be interpreted",
                     config.vision_llm_model)
            return
        log.info("OK  no vision model %s is loaded; alt-cmd-S still archives screenshots, "
                 "ctrl-alt-cmd-S will report the missing model",
                 config.vision_llm_model)

    def _print_ready_banner(self):
        log.info("OK  ready. Hotkeys: alt-cmd-R capture, ctrl-alt-cmd-R capture and analyse, "
                 "alt-cmd-E explain the last %ds, alt-cmd-C clarify the last %ds, "
                 "alt-cmd-S screenshot, ctrl-alt-cmd-S screenshot and interpret",
                 int(config.explain_window_seconds), int(config.clarify_window_seconds))
        log.info("OK  screenshots are archived under %s",
                 screenshot.session_archive_dir(self._session_started_epoch))

    # -- commands ----------------------------------------------------------

    def _build_dispatcher(self):
        dispatcher = CommandDispatcher()
        for name, handler in [("CLARIFY", self._command_clarify),
                              ("EXPLAIN", self._command_explain),
                              ("INTERPRET_SCREEN", self._command_interpret_screen),
                              ("SCREENSHOT", self._command_screenshot),
                              ("START_CAPTURE", self._command_start_capture),
                              ("STATUS", self._command_status),
                              ("STOP_CAPTURE", self._command_stop_capture),
                              ("TOGGLE_CAPTURE", self._command_toggle_capture),
                              ("TOGGLE_CONTINUOUS", self._command_toggle_continuous),
                              ("QUIT", self._command_quit)]:
            dispatcher.register(name, handler)
        return dispatcher

    def _command_start_capture(self, arguments):
        return self._start_streams(self._requested_stream_names(arguments))

    def _command_stop_capture(self, arguments):
        stopped = [name for name, stream in self._streams.items() if stream.stop()]
        self._continuous_analysis.clear()
        return "capture stopped (%s)" % (", ".join(stopped) or "was not running")

    def _command_toggle_capture(self, arguments):
        if self._any_stream_running():
            return self._command_stop_capture(arguments)
        return self._command_start_capture(arguments)

    def _command_toggle_continuous(self, arguments):
        if self._continuous_analysis.is_set():
            self._continuous_analysis.clear()
            return "continuous analysis off"
        self._start_streams(self._requested_stream_names(arguments))
        self._last_continuous_analysis = time.monotonic()
        self._continuous_analysis.set()
        return "continuous analysis on, every %ds" % config.continuous_analysis_period_seconds

    def _command_explain(self, arguments):
        return self._start_transcript_answer(prompts.build_explain_messages, arguments,
                                             config.explain_window_seconds, "explanation")

    def _command_clarify(self, arguments):
        return self._start_transcript_answer(prompts.build_clarify_messages, arguments,
                                             config.clarify_window_seconds, "clarification")

    def _command_screenshot(self, arguments):
        """Archival only: no OCR, no model, no network, nothing on screen."""
        saved = screenshot.capture_screenshot(self._session_started_epoch)
        return "screenshot saved as %s" % saved.image_path.name

    def _command_interpret_screen(self, arguments):
        """The same archival save, then a private interpretation in this terminal."""
        saved = screenshot.capture_screenshot(self._session_started_epoch)
        window_seconds = self._requested_window_seconds(arguments,
                                                       config.interpret_window_seconds)
        transcript_text = self._transcript.render_window(window_seconds, now=time.time())
        messages = prompts.build_interpret_messages(
            transcript_text, screenshot.read_image_as_data_uri(saved.image_path), window_seconds)
        return "screenshot saved as %s; %s" % (
            saved.image_path.name,
            self._start_answer(self._vision_llm, messages, transcript_text, "interpretation"))

    def _command_status(self, arguments):
        running = [name for name, stream in self._streams.items() if stream.is_running]
        return "capture: %s; continuous: %s; transcript segments: %d" % (
            ", ".join(running) or "idle",
            "on" if self._continuous_analysis.is_set() else "off",
            self._transcript.segment_count)

    def _command_quit(self, arguments):
        self._quit_requested.set()
        return "shutting down"

    # -- helpers -----------------------------------------------------------

    def _start_transcript_answer(self, build_messages, arguments, default_window_seconds,
                                 heading):
        """Ask the text model about the recent transcript; EXPLAIN and CLARIFY differ only here."""
        window_seconds = self._requested_window_seconds(arguments, default_window_seconds)
        transcript_text = self._transcript.render_window(window_seconds, now=time.time())
        messages = build_messages(transcript_text, window_seconds)
        return "%s of the last %ds: %s" % (
            heading, window_seconds,
            self._start_answer(self._llm, messages, transcript_text, heading))

    @staticmethod
    def _requested_window_seconds(arguments, default_window_seconds):
        return float(arguments[0]) if arguments else default_window_seconds

    def _start_answer(self, llm, messages, context_text, heading):
        """One model request at a time; the GPU and the terminal are both single resources."""
        if not self._answering.acquire(blocking=False):
            return "an answer is already in progress"
        threading.Thread(target=self._answer, args=(llm, messages, context_text, heading),
                         name="rvw-answer", daemon=True).start()
        return "answering in the assistant terminal"

    def _requested_stream_names(self, arguments):
        """Streams named on the command line, or every stream this run offers."""
        return arguments or list(self._streams)

    def _any_stream_running(self):
        return any(stream.is_running for stream in self._streams.values())

    def _start_streams(self, stream_names):
        started = []
        for name in stream_names:
            stream = self._streams.get(name.lower())
            if stream is None:
                raise ValueError("unknown capture stream %s" % name)
            if stream.start():
                started.append(name.lower())
        return "capture running (%s)" % (", ".join(started) or "already running")

    def _run_continuous_analysis_if_due(self):
        if not self._continuous_analysis.is_set():
            return
        if time.monotonic() - self._last_continuous_analysis < config.continuous_analysis_period_seconds:
            return
        self._last_continuous_analysis = time.monotonic()
        log.info("%s", self._dispatcher.dispatch("EXPLAIN %d"
                                                 % config.continuous_analysis_period_seconds))

    def _answer(self, llm, messages, context_text, heading):
        """Stream one answer to the terminal and record it in the session log."""
        try:
            self._stream_answer_to_terminal(llm, messages, context_text, heading)
        except Exception as error:
            log.error("FAIL %s: %s", heading, error)
        finally:
            self._answering.release()

    def _stream_answer_to_terminal(self, llm, messages, context_text, heading):
        started = time.monotonic()
        sys.stdout.write("\n----- %s -----\n" % heading)
        answer = llm.stream_chat(messages, self._write_token)
        sys.stdout.write("\n%s\n" % ("-" * (len(heading) + 12)))
        sys.stdout.flush()
        log.debug("OK  %s produced in %.1fs", heading, time.monotonic() - started)
        session_log.write_answer_record(self._log_path, "transcript window", context_text)
        session_log.write_answer_record(self._log_path, heading, answer)

    @staticmethod
    def _write_token(token):
        sys.stdout.write(token)
        sys.stdout.flush()


def parse_arguments(argv):
    parser = argparse.ArgumentParser(description="local listening assistant")
    parser.add_argument("--source", default="both", choices=["mic", "system", "both"],
                        help="which capture streams to make available")
    parser.add_argument("--listen", action="store_true",
                        help="start capturing immediately instead of waiting for the hotkey")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    return parser.parse_args(argv)


def main(argv=None):
    arguments = parse_arguments(argv)
    config.debug_mode = arguments.debug
    stream_names = all_stream_names if arguments.source == "both" else [arguments.source]
    assistant = Assistant(stream_names)
    try:
        assistant.run(start_capture_immediately=arguments.listen)
    except KeyboardInterrupt:
        assistant.shut_down()
    return 0


if __name__ == "__main__":
    sys.exit(main())
