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
from .answer_buffer import AnswerBuffer
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
        self._answer_buffer = AnswerBuffer()
        self._dispatcher = self._build_dispatcher()
        self._control = ControlSocketServer(self._dispatcher)
        self._answering = threading.Lock()
        self._continuous_analysis = threading.Event()
        self._quit_requested = threading.Event()
        self._last_continuous_analysis = 0.0
        self._log_path = None

    # -- lifecycle ---------------------------------------------------------

    def run(self, start_capture_immediately, console=False):
        self._log_path = session_log.start_session_log()
        log.info("OK  session log %s", self._log_path)
        self._report_llm_status()
        self._start_speech_pipeline()
        self._control.start()
        self._print_ready_banner()
        if start_capture_immediately:
            self._start_capture_at_start_up()
        if console:
            self._run_console()
        else:
            self._wait_for_quit()

    def _start_speech_pipeline(self):
        """No configured streams means no audio to recognise, so Whisper stays
        unloaded -- that is what makes `--source none` start instantly."""
        if not self._streams:
            log.info("INFO no capture streams configured; speech recognition is not started")
            return
        self._recognizer.start()
        self._transcriber.warm_up()

    def _run_console(self):
        """The in-process console: model answers stream to this same terminal.

        The read loop replaced `_wait_for_quit`, which is where the periodic
        continuous analysis ticked -- a small thread runs it here instead.
        """
        from .console import Console
        ticker = threading.Thread(target=self._console_ticks, name="rvw-console-ticks",
                                  daemon=True)
        ticker.start()
        try:
            Console(self).run()
        finally:
            # Whatever ended the loop -- q, EOF, an exception -- stops the
            # ticker; shutdown stays in exactly one place (here, or main's
            # KeyboardInterrupt handler when the interrupt propagates).
            self._quit_requested.set()
            ticker.join(timeout=5)
        self.shut_down()

    def _console_ticks(self):
        """Fire the periodic continuous analysis until the session ends."""
        while not self._quit_requested.wait(timeout=1.0):
            self._run_continuous_analysis_if_due()

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
        """What this run will do, not what the keys are: the keys and the menu are
        declared in hammerspoon/rvw_config.lua and the menu bar lists them, so
        repeating them here only creates a second version to keep right."""
        log.info("OK  ready. EXPLAIN covers the last %ds, CLARIFY the last %ds; the menu bar "
                 "lists every hotkey and both windows",
                 int(config.explain_window_seconds), int(config.clarify_window_seconds))
        log.info("OK  screenshots are archived under %s", self._archive.directory)
        log.info("OK  transcript retention: %s", self._archive.describe_state())

    # -- commands ----------------------------------------------------------

    def _build_dispatcher(self):
        dispatcher = CommandDispatcher()
        for name, handler in [("ANSWER", self._command_answer),
                              ("CLARIFY", self._command_clarify),
                              ("EXPLAIN", self._command_explain),
                              ("INTERPRET_SCREEN", self._command_interpret_screen),
                              ("MODELS", self._command_models),
                              ("RECALL", self._command_recall),
                              ("REINDEX", self._command_reindex),
                              ("SCREENSHOT", self._command_screenshot),
                              ("SEARCH", self._command_search),
                              ("SET_LANGUAGE", self._command_set_language),
                              ("SET_MODEL", self._command_set_model),
                              ("START_CAPTURE", self._command_start_capture),
                              ("START_RETAINING", self._command_start_retaining),
                              ("STATUS", self._command_status),
                              ("STATUS_FIELDS", self._command_status_fields),
                              ("STOP_CAPTURE", self._command_stop_capture),
                              ("STOP_RETAINING", self._command_stop_retaining),
                              ("TOGGLE_CAPTURE", self._command_toggle_capture),
                              ("TOGGLE_CONTINUOUS", self._command_toggle_continuous),
                              ("TOGGLE_RETENTION", self._command_toggle_retention),
                              ("TRANSCRIPT", self._command_transcript),
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

    # -- what a window shows (Phase 6) -------------------------------------

    def _command_transcript(self, arguments):
        """The rolling transcript, for the window that shows it during a meeting."""
        window_seconds = self._requested_window_seconds(arguments,
                                                        config.transcript_display_seconds)
        text = self._transcript.render_window(window_seconds, now=time.time())
        if not text:
            return "nothing has been transcribed in the last %ds" % window_seconds
        return "transcript of the last %ds:\n%s" % (window_seconds, text)

    def _command_answer(self, arguments):
        """The answer being written, or the last one; the answer window polls this."""
        return self._answer_buffer.render()

    # -- choosing the model and the language (Phase 6) ---------------------

    def _command_models(self, arguments):
        """What this endpoint serves and which of them answers, as key=value pairs.

        The menu builds its model list from this, so it is machine readable for the
        same reason STATUS_FIELDS is. It is asked only when the menu is opened,
        because unlike the rest of the status it costs a request to the endpoint.
        """
        served = self._llm.available_models()
        fields = {"answering": self._llm.model, "serving": ",".join(served) or "nothing"}
        require_space_free_field_values(fields)
        return " ".join("%s=%s" % pair for pair in sorted(fields.items()))

    def _command_set_model(self, arguments):
        """Ask a different one of the models this endpoint already serves.

        Loading a different model is Phase 8 work and is not this. The identifier
        has to be served now, because this endpoint answers for an identifier it
        does not serve with whatever is loaded: accepting an unserved one here
        would point the assistant at a model that never answers as itself.
        """
        identifier = self._required_query(arguments, "SET_MODEL")
        served = self._llm.available_models()
        if identifier not in served:
            raise ValueError("this endpoint does not serve %r; it serves %s"
                             % (identifier, ", ".join(served) or "nothing"))
        self._llm = LocalLlm(model=identifier,
                             loads_on_demand=identifier == config.llm_model)
        return "questions now go to %s" % identifier

    def _command_set_language(self, arguments):
        """Tell the recogniser which language it is listening to from now on."""
        language = self._required_query(arguments, "SET_LANGUAGE")
        self._transcriber.set_language(language)
        return "recognising %s from the next utterance onwards" % language

    # -- status and shutdown -----------------------------------------------

    def _command_status(self, arguments):
        """The session state in prose, for a person reading a reply."""
        fields = self._status_fields()
        return "capture: %s; continuous: %s; transcript segments: %s; retention: %s" % (
            fields["capture"], fields["continuous"], fields["segments"],
            self._archive.describe_state())

    def _command_status_fields(self, arguments):
        """The same state as key=value pairs, for the menu bar that polls it.

        The menu bar needs the state several times a minute and must not read the
        prose above: that wording is written for a person and improving it would
        break the indicator.
        """
        fields = self._status_fields()
        require_space_free_field_values(fields)
        return " ".join("%s=%s" % pair for pair in sorted(fields.items()))

    def _status_fields(self):
        """One description of the session, rendered by both STATUS commands."""
        return {"capture": ",".join(self._running_stream_names()) or "none",
                "continuous": "on" if self._continuous_analysis.is_set() else "off",
                "language": self._transcriber.language,
                "languages": ",".join(config.recognition_languages),
                "meeting": self._archive.directory.name,
                "model": self._llm.model,
                "retention": "retained" if self._archive.is_retaining else "ephemeral",
                "segments": str(self._transcript.segment_count),
                "streams": ",".join(self._streams),
                "vision_model": config.vision_llm_model}

    def _running_stream_names(self):
        return [name for name, stream in self._streams.items() if stream.is_running]

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
        if not self._queue_answer(llm, messages, context_text, heading):
            return "an answer is already in progress"
        return "answering in the assistant terminal"

    def ask_the_model(self, messages, context_text, heading):
        """Queue one answer on the text model; False while another is running.

        The console needs the boolean, not the reply text: whether a submit
        really was queued decides if the pending context may be cleared."""
        return self._queue_answer(self._llm, messages, context_text, heading)

    def _queue_answer(self, llm, messages, context_text, heading):
        if not self._answering.acquire(blocking=False):
            return False
        threading.Thread(target=self._answer, args=(llm, messages, context_text, heading),
                         name="rvw-answer", daemon=True).start()
        return True

    def _requested_stream_names(self, arguments):
        """Streams named on the command line, or every stream this run offers."""
        return arguments or list(self._streams)

    def _any_stream_running(self):
        return any(stream.is_running for stream in self._streams.values())

    def _start_streams(self, stream_names):
        if not self._streams:
            raise RuntimeError("no capture streams are configured (--source none)")
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
        """Stream one answer to the terminal, the buffer and the session log."""
        self._answer_buffer.begin(heading)
        try:
            self._stream_answer_to_terminal(llm, messages, context_text, heading)
            self._answer_buffer.finish()
        except Exception as error:
            log.error("FAIL %s: %s", heading, error)
            self._answer_buffer.fail(str(error))
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

    def _write_token(self, token):
        """Every token goes to the terminal and to whatever window is watching."""
        self._answer_buffer.append(token)
        sys.stdout.write(token)
        sys.stdout.flush()


def require_space_free_field_values(fields):
    """A space in a value would silently turn one field into two for the reader.

    The model identifiers come from the environment, so this is reachable by a
    typo in RVW_LLM_MODEL rather than only by a bug here.
    """
    offending = sorted(name for name, value in fields.items() if " " in value)
    if offending:
        raise ValueError("status field value(s) contain a space and cannot be reported as "
                         "key=value: %s" % ", ".join(offending))


def parse_arguments(argv):
    parser = argparse.ArgumentParser(description="local listening assistant")
    parser.add_argument("--source", default="both", choices=["mic", "system", "both", "none"],
                        help="which capture streams to make available; 'none' skips "
                             "speech recognition entirely")
    parser.add_argument("--listen", action="store_true",
                        help="start capturing immediately instead of waiting for the hotkey")
    parser.add_argument("--console", action="store_true",
                        help="read console commands from this terminal (rvw> prompt)")
    hide_flags = parser.add_mutually_exclusive_group()
    hide_flags.add_argument("--auto-hide-console", dest="auto_hide_console",
                            action="store_true", default=None,
                            help="with --console, minimize this terminal's window and "
                                 "capture the whole display (the default)")
    hide_flags.add_argument("--no-auto-hide-console", dest="auto_hide_console",
                            action="store_false",
                            help="with --console, keep the window and wait "
                                 "console_capture_delay_seconds for a Cmd-Tab instead")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    arguments = parser.parse_args(argv)
    if arguments.auto_hide_console is not None and not arguments.console:
        parser.error("--auto-hide-console/--no-auto-hide-console "
                     "only make sense with --console")
    if arguments.auto_hide_console is None:
        arguments.auto_hide_console = arguments.console
    return arguments


def main(argv=None):
    arguments = parse_arguments(argv)
    config.debug_mode = arguments.debug
    config.auto_hide_console = arguments.auto_hide_console
    stream_names = (all_stream_names if arguments.source == "both"
                    else [] if arguments.source == "none"
                    else [arguments.source])
    assistant = Assistant(stream_names)
    try:
        assistant.run(start_capture_immediately=arguments.listen, console=arguments.console)
    except KeyboardInterrupt:
        assistant.shut_down()
    return 0


if __name__ == "__main__":
    sys.exit(main())
