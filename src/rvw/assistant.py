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
from .claude_code_llm import ClaudeCodeLlm
from .asr import WhisperTranscriber
from .audio_source import CaptureStream
from .commands import CommandDispatcher
from .console import Console
from .control import ControlSocketServer
from .llm import LocalLlm, LocalLlmError
from .meeting_archive import MeetingArchive
from .recognizer import RecognitionWorker
from .transcript import RollingTranscript

log = logging.getLogger(__name__)

all_stream_names = ["mic", "system"]


class Assistant:
    """Wires the components together and implements the hotkey commands."""

    # Every other command refuses arguments: `c remember this` typed out of an
    # old habit must fail loudly, not toggle capture and drop the words.
    commands_taking_arguments = frozenset({
        "AUDIO_CAPTURE_START", "AUDIO_CAPTURE_TOGGLE", "COMMENT", "COMMENT_SUBMIT",
        "EXPLAIN_SPEECH", "PROMPT_SET", "RECALL", "SCREEN_VISION", "SEARCH",
        "SET_LANGUAGE", "SET_MODEL", "TRANSCRIPT_SHOW", "UNGARBLE_SPEECH"})

    def __init__(self, stream_names):
        self._session_started_epoch = time.time()
        self._archive = MeetingArchive(self._session_started_epoch, stream_names)
        self._transcript = RollingTranscript(on_segment_added=self._archive.record_segment)
        self._transcriber = WhisperTranscriber()
        self._recognizer = RecognitionWorker(self._transcriber, self._transcript)
        self._streams = {name: CaptureStream(name, self._recognizer.submit)
                         for name in stream_names}
        self._llm = text_llm_for(config.text_model)
        self._vision_llm = LocalLlm(model=config.vision_llm_model, loads_on_demand=False,
                                    suppress_reasoning=False)
        self._meeting_index = meeting_index.MeetingIndex()
        self._index_built = False
        self._selected_prompt_key = prompts.read_selected_prompt_key(
            config.selected_prompt_path)
        self._answer_buffer = AnswerBuffer()
        # One Console whether or not --console runs its read loop: the pending
        # context the socket screen commands and the console lines share is
        # its, so it has to exist before either of them does.
        self._console = Console(self)
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
        ticker = threading.Thread(target=self._console_ticks, name="rvw-console-ticks",
                                  daemon=True)
        ticker.start()
        try:
            self._console.run()
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
        self._report_which_model_answers_questions()
        try:
            served = self._models_the_local_endpoint_serves()
        except LocalLlmError as error:
            self._report_the_local_endpoint_is_missing(error)
            return
        log.info("OK  local LLM at %s serving %s", config.llm_base_url,
                 ", ".join(served) or "no loaded model")
        self._note_whether_the_configured_model_is_loaded(served)
        self._note_whether_the_vision_model_is_loaded(served)

    def _report_which_model_answers_questions(self):
        """claude_code is checked here, because nothing else would notice a missing
        claude command before the first question failed in the middle of a meeting.
        A local model is checked by the endpoint report that follows."""
        if self._llm.model == config.claude_code_model:
            try:
                self._llm.available_models()
            except LocalLlmError as error:
                log.error("FAIL %s; questions go to %s and will fail", error,
                          config.claude_code_model)
                return
        log.info("OK  questions go to %s", self._llm.model)

    def _report_the_local_endpoint_is_missing(self, error):
        """Only a failure while a local model answers questions; otherwise only
        SCREEN_VISION needs the endpoint, and interpretation is optional."""
        if self._llm.model == config.claude_code_model:
            log.info("INFO %s; only SCREEN_VISION needs it while questions go to %s",
                     error, config.claude_code_model)
            return
        log.error("FAIL %s; run util/init_local_models.sh before asking for explanations",
                  error)

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
        log.info("OK  ready. EXPLAIN_SPEECH covers the last %ds, UNGARBLE_SPEECH the last %ds; "
                 "the menu bar "
                 "lists every hotkey and both windows",
                 int(config.explain_window_seconds), int(config.ungarble_window_seconds))
        log.info("OK  screenshots are archived under %s", self._archive.directory)
        log.info("OK  transcript retention: %s", self._archive.describe_state())

    # -- commands ----------------------------------------------------------

    def _build_dispatcher(self):
        dispatcher = CommandDispatcher()
        for name, handler, description, shortcut in [
                ("ANSWER", self._command_answer,
                 "show the answer being written or the latest answer", "A"),
                ("AUDIO_CAPTURE_START", self._command_start_capture,
                 "start the configured audio streams, or a named mic/system", "c+"),
                ("AUDIO_CAPTURE_STOP", self._command_stop_capture,
                 "stop audio capture", "c-"),
                ("AUDIO_CAPTURE_TOGGLE", self._command_toggle_capture,
                 "toggle audio capture", "c"),
                ("COMMENT", self._command_comment,
                 "queue a typed comment in the pending context", "n"),
                ("COMMENT_SUBMIT", self._command_comment_submit,
                 "the same, then submit the whole pending context under the "
                 "selected prompt", "N"),
                ("EXPLAIN_SPEECH", self._command_explain,
                 "explain recent speech with the text model (optional seconds)", "E"),
                ("MODELS", self._command_models,
                 "list the model identifiers the endpoint serves and which answers", "ml"),
                ("PROMPT_LIST", self._command_prompt_list,
                 "list the prompts a console submission can be sent under", "pl"),
                ("PROMPT_SET", self._command_prompt_set,
                 "select the prompt console submissions use, by one letter "
                 "(c, e, r, u, v)", "p"),
                ("QUIT", self._command_quit, "stop the assistant", "q"),
                ("RECALL", self._command_recall,
                 "answer a question from cited retained meeting passages", "r"),
                ("REINDEX", self._command_reindex,
                 "rebuild the retained transcript search index", "R"),
                ("SCREEN_ADD", self._command_screen_add,
                 "archive a screenshot, OCR it and queue the text in the "
                 "pending context", "sa"),
                ("SCREEN_ANALYZE", self._command_screen_analyze,
                 "the same, then submit the whole pending context under the "
                 "selected prompt", "sx"),
                ("SCREEN_SAVE", self._command_screen_save,
                 "archive a screenshot, without OCR or a model", "s"),
                ("SCREEN_VISION", self._command_interpret_screen,
                 "archive a screenshot and ask the vision model about it with "
                 "recent speech, if that model is served", "V"),
                ("SEARCH", self._command_search,
                 "search retained meeting transcripts for words", "f"),
                ("SET_LANGUAGE", self._command_set_language,
                 "set the recognition language for the next utterances", "l"),
                ("SET_MODEL", self._command_set_model,
                 "choose a model identifier the endpoint already serves "
                 "(does not load a new model)", "m"),
                ("STATUS", self._command_status,
                 "human readable session state", "S"),
                ("STATUS_FIELDS", self._command_status_fields,
                 "session state as machine readable key=value pairs", "F"),
                ("SUBMIT", self._command_submit,
                 "submit the pending context as it is under the selected prompt",
                 "send"),
                ("TRANSCRIPT_SHOW", self._command_transcript,
                 "show the recent speech transcript (optional seconds)", "T"),
                ("TRANSCRIPT_START", self._command_start_retaining,
                 "start keeping the transcript from now on", "t+"),
                ("TRANSCRIPT_STOP", self._command_stop_retaining,
                 "stop keeping the transcript", "t-"),
                ("TRANSCRIPT_TOGGLE", self._command_toggle_retention,
                 "toggle transcript retention", "t"),
                ("UNGARBLE_SPEECH", self._command_ungarble,
                 "ungarble recent speech with the text model (optional seconds)", "C")]:
            dispatcher.register(name, handler, description, shortcut=shortcut,
                                takes_arguments=name in self.commands_taking_arguments)
        return dispatcher

    def _command_start_capture(self, arguments):
        return self._start_streams(self._requested_stream_names(arguments))

    def _command_stop_capture(self, arguments):
        stopped = [name for name, stream in self._streams.items() if stream.stop()]
        self._continuous_analysis.clear()
        return "capture stopped (%s)" % (", ".join(stopped) or "was not running")

    def _command_toggle_capture(self, arguments):
        self._requested_stream_names(arguments)
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

    def _command_ungarble(self, arguments):
        return self._start_transcript_answer(prompts.build_ungarble_messages, arguments,
                                             config.ungarble_window_seconds, "ungarbling")

    def _command_screen_save(self, arguments):
        """Archival only: no OCR, no model, no network, nothing on screen."""
        saved = self._console.capture_screen()
        return "screenshot saved as %s" % saved.image_path.name

    # The pending context commands all act on the one Console, whichever
    # transport they arrived by.

    def _command_screen_add(self, arguments):
        """Archive a shot, OCR it and queue the text in the pending context."""
        return self._console.add_screenshot(submit=False)

    def _command_screen_analyze(self, arguments):
        """The same capture and queue, then submit all of the pending context."""
        return self._console.add_screenshot(submit=True)

    def _command_comment(self, arguments):
        return self._console.add_comment(" ".join(arguments), submit=False)

    def _command_comment_submit(self, arguments):
        return self._console.add_comment(" ".join(arguments), submit=True)

    def _command_submit(self, arguments):
        if arguments:
            raise ValueError("SUBMIT takes no arguments; queue text with COMMENT first")
        return self._console.submit()

    def _command_interpret_screen(self, arguments):
        """The same archival save, then a private interpretation in this terminal."""
        saved = self._console.capture_screen()
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
        selectable = self._selectable_text_models()
        fields = {"answering": self._llm.model, "serving": ",".join(selectable)}
        require_space_free_field_values(fields)
        return " ".join("%s=%s" % pair for pair in sorted(fields.items()))

    def _command_set_model(self, arguments):
        """Ask claude_code or a different one of the models this endpoint already serves.

        Loading a different model is Phase 8 work and is not this. The identifier
        has to be served now, because this endpoint answers for an identifier it
        does not serve with whatever is loaded: accepting an unserved one here
        would point the assistant at a model that never answers as itself.
        """
        identifier = self._required_query(arguments, "SET_MODEL")
        selectable = self._selectable_text_models()
        if identifier not in selectable:
            raise ValueError("%r cannot answer; choose one of %s"
                             % (identifier, ", ".join(selectable)))
        self._llm = text_llm_for(identifier)
        return "questions now go to %s" % identifier

    def _selectable_text_models(self):
        """claude_code, then whatever the endpoint serves; either one missing only
        shortens the list, because neither needs the other."""
        return (self._models_from(ClaudeCodeLlm().available_models)
                + self._models_from(self._models_the_local_endpoint_serves))

    @staticmethod
    def _models_from(list_models):
        try:
            return list_models()
        except LocalLlmError as error:
            log.info("INFO %s; its models cannot be chosen", error)
            return []

    def _models_the_local_endpoint_serves(self):
        """The vision client is always a local one, so it is the one to ask."""
        return self._vision_llm.available_models()

    def _command_prompt_list(self, arguments):
        """Every selectable prompt in full, the shared active one marked."""
        return "prompts:\n" + prompts.render_prompt_listing(self._selected_prompt_key)

    def _command_prompt_set(self, arguments):
        """Choose the prompt console submissions run under; kept across restarts.

        The file is written before the in-memory selection changes, so a
        failed write leaves the session exactly as it was.
        """
        if len(arguments) != 1 or len(arguments[0]) != 1:
            raise ValueError("PROMPT_SET takes exactly one letter argument "
                             "(%s)" % ", ".join(sorted(prompts.prompt_letters)))
        key = prompts.prompt_key_for_letter(arguments[0])
        prompts.write_selected_prompt_key(config.selected_prompt_path, key)
        self._selected_prompt_key = key
        return "console submissions now use prompt %s" % key

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
        """Ask the text model about the recent transcript; the two commands differ only here."""
        window_seconds = self._requested_window_seconds(arguments, default_window_seconds)
        transcript_text = self._transcript.render_window(window_seconds, now=time.time())
        messages = build_messages(transcript_text, window_seconds)
        return "%s of the last %ds: %s" % (
            heading, window_seconds,
            self._start_answer(self._llm, messages, transcript_text, heading))

    @staticmethod
    def _requested_window_seconds(arguments, default_window_seconds):
        if len(arguments) > 1:
            raise ValueError("a window length is one number of seconds; got %r"
                             % " ".join(arguments))
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
        unknown = [name for name in arguments if name.lower() not in self._streams]
        if unknown:
            raise ValueError("unknown capture stream %s" % " ".join(unknown))
        return [name.lower() for name in arguments] or list(self._streams)

    def _any_stream_running(self):
        return any(stream.is_running for stream in self._streams.values())

    def _start_streams(self, stream_names):
        if not self._streams:
            raise RuntimeError("no capture streams are configured (--source none)")
        started = []
        for name in stream_names:
            if self._streams[name].start():
                started.append(name)
        return "capture running (%s)" % (", ".join(started) or "already running")

    def _run_continuous_analysis_if_due(self):
        if not self._continuous_analysis.is_set():
            return
        if time.monotonic() - self._last_continuous_analysis < config.continuous_analysis_period_seconds:
            return
        self._last_continuous_analysis = time.monotonic()
        log.info("%s", self._dispatcher.dispatch("EXPLAIN_SPEECH %d"
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


def text_llm_for(identifier):
    """The client that answers questions as this identifier.

    Only the assistant's own local model is loaded on demand; see LocalLlm.
    """
    if identifier == config.claude_code_model:
        return ClaudeCodeLlm()
    return LocalLlm(model=identifier, loads_on_demand=identifier == config.llm_model)


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
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    return parser.parse_args(argv)


def main(argv=None):
    arguments = parse_arguments(argv)
    config.debug_mode = arguments.debug
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
