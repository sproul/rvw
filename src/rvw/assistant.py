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

from . import config, meeting_index, prompts, recall, screenshot, session_log
from .asr import WhisperTranscriber
from .audio_source import CaptureStream
from .commands import CommandDispatcher
from .control import ControlSocketServer
from .llm import LocalLlm, LocalLlmError
from .meeting_archive import MeetingArchive
from .recognizer import RecognitionWorker
from .transcript import RollingTranscript

log = logging.getLogger(__name__)

all_stream_names = ["mic", "system"]


class Assistant:
    """Wires the components together and implements the hotkey commands."""

    def __init__(self, stream_names):
        self._session_started_epoch = time.time()
        self._archive = MeetingArchive(self._session_started_epoch, stream_names)
        self._transcript = RollingTranscript(on_segment_added=self._archive.record_segment)
        self._transcriber = WhisperTranscriber()
        self._recognizer = RecognitionWorker(self._transcriber, self._transcript)
        self._streams = {name: CaptureStream(name, self._recognizer.submit)
                         for name in stream_names}
        self._llm = LocalLlm()
        self._vision_llm = LocalLlm(model=config.vision_llm_model, loads_on_demand=False,
                                    suppress_reasoning=False)
        self._meeting_index = meeting_index.MeetingIndex()
        self._index_built = False
        self._dispatcher = self._build_dispatcher()
        self._control = ControlSocketServer(self._dispatcher)
        self._answering = threading.Lock()
        self._continuous_analysis = threading.Event()
        self._quit_requested = threading.Event()
        self._last_continuous_analysis = 0.0
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
        self._finish_the_meeting_archive()
        log.info("OK  assistant stopped; session log is %s", self._log_path)

    def _finish_the_meeting_archive(self):
        """Close the transcript of a retained session and render it; an ephemeral
        session has nothing to close and leaves nothing behind.

        The archive reports what it did, so there is nothing to log here.
        """
        self._archive.stop_retaining()

    def _report_llm_status(self):
        try:
            served = self._llm.available_models()
        except LocalLlmError as error:
            log.error("FAIL %s; run util/init_local_models.sh before asking for explanations",
                      error)
            return
        log.info("OK  local LLM at %s serving %s", config.llm_base_url,
                 ", ".join(served) or "no loaded model")
        self._note_whether_the_configured_model_is_loaded(served)
        self._note_whether_the_vision_model_is_loaded(served)

    @staticmethod
    def _note_whether_the_configured_model_is_loaded(served_models):
        """LM Studio unloads the model after an idle hour by design, so between
        questions an absent model is the ordinary state and not worth anybody's
        attention: the next question loads it again."""
        if config.llm_model in served_models:
            log.info("OK  %s is loaded and ready to answer", config.llm_model)
            return
        log.info("INFO %s is not loaded; it will be loaded when it is first needed, which "
                 "makes that one question slow", config.llm_model)

    @staticmethod
    def _note_whether_the_vision_model_is_loaded(served_models):
        """Interpretation is optional, so an absent vision model is news, not a failure."""
        if config.vision_llm_model in served_models:
            log.info("OK  vision model %s is loaded, so screenshots can be interpreted",
                     config.vision_llm_model)
            return
        log.info("INFO no vision model %s is loaded; alt-cmd-S still archives screenshots, "
                 "ctrl-alt-cmd-S will report the missing model",
                 config.vision_llm_model)

    def _print_ready_banner(self):
        log.info("OK  ready. Hotkeys: alt-cmd-R capture, ctrl-alt-cmd-R capture and analyse, "
                 "alt-cmd-E explain the last %ds, alt-cmd-C clarify the last %ds, "
                 "alt-cmd-S screenshot, ctrl-alt-cmd-S screenshot and interpret, "
                 "alt-cmd-T keep or stop keeping the transcript",
                 int(config.explain_window_seconds), int(config.clarify_window_seconds))
        log.info("OK  screenshots are archived under %s", self._archive.directory)
        log.info("OK  transcript retention: %s", self._archive.describe_state())

    # -- commands ----------------------------------------------------------

    def _build_dispatcher(self):
        dispatcher = CommandDispatcher()
        for name, handler in [("CLARIFY", self._command_clarify),
                              ("EXPLAIN", self._command_explain),
                              ("INTERPRET_SCREEN", self._command_interpret_screen),
                              ("RECALL", self._command_recall),
                              ("REINDEX", self._command_reindex),
                              ("SCREENSHOT", self._command_screenshot),
                              ("SEARCH", self._command_search),
                              ("START_CAPTURE", self._command_start_capture),
                              ("START_RETAINING", self._command_start_retaining),
                              ("STATUS", self._command_status),
                              ("STOP_CAPTURE", self._command_stop_capture),
                              ("STOP_RETAINING", self._command_stop_retaining),
                              ("TOGGLE_CAPTURE", self._command_toggle_capture),
                              ("TOGGLE_CONTINUOUS", self._command_toggle_continuous),
                              ("TOGGLE_RETENTION", self._command_toggle_retention),
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

    def _command_start_retaining(self, arguments):
        return self._archive.start_retaining()

    def _command_stop_retaining(self, arguments):
        return self._archive.stop_retaining()

    def _command_toggle_retention(self, arguments):
        return self._archive.toggle_retention()

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
        return "screenshot saved as %s; %s" % (saved.image_path.name,
                                               self._interpretation_of(saved, arguments))

    def _interpretation_of(self, saved, arguments):
        """The image is archived whatever happens here; interpreting it is optional."""
        unavailable = self._why_the_vision_model_cannot_answer()
        if unavailable:
            return unavailable
        window_seconds = self._requested_window_seconds(arguments,
                                                       config.interpret_window_seconds)
        transcript_text = self._transcript.render_window(window_seconds, now=time.time())
        messages = prompts.build_interpret_messages(
            transcript_text, screenshot.read_image_as_data_uri(saved.image_path), window_seconds)
        return self._start_answer(self._vision_llm, messages, transcript_text, "interpretation")

    def _why_the_vision_model_cannot_answer(self):
        """LM Studio answers a request for an identifier it does not serve with
        whatever model is loaded, so a screenshot sent to an absent vision model
        comes back described by the text model and looks like an interpretation.
        Asking here what is loaded is the only way to tell the two apart."""
        try:
            served = self._vision_llm.available_models()
        except LocalLlmError as error:
            return "not interpreted: %s" % error
        if config.vision_llm_model in served:
            return None
        return "not interpreted: no model is loaded as '%s'" % config.vision_llm_model

    # -- searchable meeting memory (Phase 4) -------------------------------

    def _command_search(self, arguments):
        """Full text search over retained conversations; hits trace back to them."""
        hits = self._ready_meeting_index().search(self._required_query(arguments, "SEARCH"))
        if not hits:
            return "no retained conversation matches %r" % " ".join(arguments)
        return "%d hit(s):\n%s" % (len(hits), "\n".join(recall.search_result_lines(hits)))

    def _command_recall(self, arguments):
        """Answer a question from a few retrieved passages, with references back."""
        question = self._required_query(arguments, "RECALL")
        hits = self._ready_meeting_index().search(question, config.recall_passage_count)
        if not hits:
            return "no retained conversation matches %r" % question
        return self._grounded_answer_to(question, hits)

    def _command_reindex(self, arguments):
        """Rebuild the disposable index from the canonical transcripts."""
        stats = self._meeting_index.rebuild()
        self._index_built = True
        return "indexed %d utterance(s) from %d meeting(s)" % (stats.utterance_count,
                                                               stats.meeting_count)

    def _grounded_answer_to(self, question, hits):
        """Ground the model on the passages, show me the sources, stream the answer."""
        passages = recall.numbered_passages(hits)
        messages = prompts.build_recall_messages(question, passages)
        self._print_recall_sources(hits)
        return "grounded answer in the assistant terminal from %d source(s): %s" % (
            len(hits), self._start_answer(self._llm, messages, passages, "recall"))

    def _print_recall_sources(self, hits):
        """The sources an answer's [n] references point at, in the assistant terminal."""
        sys.stdout.write("\n----- recall sources -----\n")
        sys.stdout.write("\n".join(recall.source_lines(hits)) + "\n")
        sys.stdout.flush()

    def _ready_meeting_index(self):
        """Build the index once on first use; REINDEX refreshes it afterwards."""
        if not self._index_built:
            self._meeting_index.rebuild()
            self._index_built = True
        return self._meeting_index

    @staticmethod
    def _required_query(arguments, command_name):
        query = " ".join(arguments).strip()
        if not query:
            raise ValueError("%s needs something to look for" % command_name)
        return query

    # -- status and shutdown -----------------------------------------------

    def _command_status(self, arguments):
        running = [name for name, stream in self._streams.items() if stream.is_running]
        return "capture: %s; continuous: %s; transcript segments: %d; retention: %s" % (
            ", ".join(running) or "idle",
            "on" if self._continuous_analysis.is_set() else "off",
            self._transcript.segment_count,
            self._archive.describe_state())

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
