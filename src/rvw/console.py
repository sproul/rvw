"""The interactive console: screenshots plus typed comments, submitted together.

`bin/rvw -here --source none --console` runs the assistant in this terminal and
reads one line at `rvw> `. Lower-case commands only accumulate an ordered
context; the upper-case forms submit it to the local text model:

    s         capture the screen now, OCR it, append the text to the context
    S         the same, then submit
    c TEXT    append TEXT as a comment
    C TEXT    the same, then submit
    pl        list the prompts; p X selects one by letter (c, e, r, u, v)
    :COMMAND  any dispatcher command or its shortcut, e.g. :STATUS or :S
    ?         this list plus every dispatcher command and its shortcut
    q         quit

The model is only ever sent OCR text, never the image: what it "sees" is the
text the Vision helper extracted, and the request header says so.
"""

import select
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

from . import config, ocr, prompts, screenshot

request_header = (
    "These observations were assembled in the order supplied. Screenshot text was "
    "extracted by OCR; you cannot see the underlying images. Treat OCR as fallible.\n\n"
)


@dataclass
class _PendingItem:
    """One queued observation: a screenshot's OCR text, or a typed comment."""
    kind: str              # "screenshot" or "comment"
    number: int            # per-kind sequence, for the model's citations
    name: str              # the archived image's file name, or "" for a comment
    text: str

    def rendered(self):
        if self.kind == "screenshot":
            return "Screenshot %d (%s):\n%s\n\n" % (self.number, self.name, self.text)
        return "Comment %d:\n%s\n\n" % (self.number, self.text)


class Console:
    """Read lines, keep an ordered pending context, submit it on the capitals."""

    def __init__(self, assistant, capture=None, ocr_reader=None, input_stream=None,
                 hs_runner=None):
        self._assistant = assistant
        # The capture seam is this one attribute: software capture today, a HDMI
        # UVC grabber later, and a stub in tests.
        self._capture = capture or self._software_capture
        self._ocr_reader = ocr_reader
        self._input_stream = input_stream or sys.stdin
        self._hs = hs_runner or self._run_hammerspoon
        # Socket commands and this read loop run on different threads and share
        # the pending context; every read-modify-write of it takes this lock.
        self._pending_lock = threading.Lock()
        self.pending = []
        self._screenshot_count = 0
        self._comment_count = 0

    def _ocr(self, image_path):
        return (self._ocr_reader or ocr.ocr_text_of)(image_path)

    # -- the read loop -----------------------------------------------------

    def run(self):
        print("console ready: '?' lists commands, 'q' quits; screenshots need this "
              "terminal's Screen Recording permission")
        while not self._assistant._quit_requested.is_set():
            sys.stdout.write("rvw> ")
            sys.stdout.flush()
            line = self._read_line()
            if line is None:
                break
            reply = self.handle_line(line)
            if reply:
                print(reply)

    def _read_line(self):
        """One stdin line, or None on EOF/quit.

        `input()` cannot be interrupted, and QUIT can also arrive over the
        control socket while this thread is sitting in it -- so the loop polls
        stdin and notices `_quit_requested` within a fraction of a second.
        """
        while not self._assistant._quit_requested.is_set():
            ready, _, _ = select.select([self._input_stream], [], [], 0.2)
            if not ready:
                continue
            line = self._input_stream.readline()
            return line if line else None
        return None

    # -- one line ----------------------------------------------------------

    def handle_line(self, line):
        """Act on one console line and return the reply text ('' says nothing)."""
        line = line.strip()
        if not line:
            return ""
        if line == "s":
            return self._capture_and_append(submit=False)
        if line == "S":
            return self._capture_and_append(submit=True)
        if line.startswith("c ") or line == "c":
            return self._comment(line[1:], submit=False)
        if line.startswith("C ") or line == "C":
            return self._comment(line[1:], submit=True)
        if line == "pl":
            return self._assistant._dispatcher.dispatch("PROMPT_LIST")
        if line == "send":
            return self._submit()
        if line.split()[0] == "p":
            return self._assistant._dispatcher.dispatch(line)
        if line == "?":
            return self._help()
        if line.startswith(":"):
            return self._assistant._dispatcher.dispatch(line[1:])
        if line == "q":
            return self._assistant._dispatcher.dispatch("QUIT")
        return "FAIL unknown console line %r; '?' lists what works" % line

    # -- accumulating context ----------------------------------------------

    def _comment(self, text, submit):
        text = text.strip()
        if not text:
            return "FAIL a comment needs text"
        with self._pending_lock:
            self._comment_count += 1
            number = self._comment_count
            self.pending.append(_PendingItem("comment", number, "", text))
        return self._maybe_submit(submit, "comment %d noted" % number)

    def capture_and_append_for_command(self, submit):
        """A dispatcher screen command: the plain archiver, no hiding or delay.

        The console window is not frontmost when a hotkey arrives over the
        control socket, so there is nothing to hide and nothing to wait for.
        The reply must not wait for the answer either: the socket serves
        commands serially, and a model that loads on demand would stall STATUS
        and QUIT behind it for the better part of a minute.
        """
        return self._capture_and_append(submit, capture=self._software_capture,
                                        wait_for_answer=False)

    def _capture_and_append(self, submit, capture=None, wait_for_answer=True):
        """Archive a screenshot, OCR it, queue the text; a failure queues nothing."""
        capture = capture or self._capture_for_console
        try:
            saved = capture(self._assistant._session_started_epoch)
            text = self._ocr(saved.image_path).strip()
        except Exception as error:
            return "FAIL %s" % error
        if not text:
            return "FAIL OCR of %s produced no text; nothing was queued" % saved.image_path.name
        with self._pending_lock:
            self._screenshot_count += 1
            number = self._screenshot_count
            self.pending.append(_PendingItem("screenshot", number,
                                             saved.image_path.name, text))
        return self._maybe_submit(
            submit, "screenshot %d queued as %s" % (number, saved.image_path.name),
            wait_for_answer=wait_for_answer)

    def _capture_for_console(self, session_epoch):
        if config.auto_hide_console:
            return self._capture_with_hidden_console(session_epoch)
        return self._capture_with_switch_delay(session_epoch)

    def _capture_with_switch_delay(self, session_epoch):
        """Typing in this terminal makes it frontmost; give Cmd-Tab a moment."""
        delay = config.console_capture_delay_seconds
        if delay > 0:
            print("switch to the window to capture (Cmd-Tab); capturing in %gs" % delay)
            sys.stdout.flush()
            time.sleep(delay)
        return self._capture(session_epoch)

    def _capture_with_hidden_console(self, session_epoch):
        """Minimize only this window, capture the whole display, put it back.

        Hammerspoon does the window work over `hs -c`. No delay: the window is
        gone before the helper runs, and --exclude-window-id keeps it out even
        if it is still animating. Anything that cannot be hidden means no
        capture at all -- taking the shot anyway would photograph the console
        the user explicitly asked to hide.
        """
        window_id = self._minimize_frontmost_window()
        try:
            return self._capture(session_epoch,
                                 target="display", exclude_window_id=window_id)
        finally:
            self._restore_minimized_window(window_id)

    def _minimize_frontmost_window(self):
        output = self._hs(
            'local w = hs.window.frontmostWindow(); '
            'assert(w ~= nil, "no frontmost window"); '
            'local id = w:id(); '
            'assert(type(id) == "number" and id > 0, "the frontmost window has no usable id"); '
            'assert(w:minimize(), "the frontmost window cannot be minimized"); '
            'print(id)')
        try:
            return int(output.strip())
        except ValueError:
            raise RuntimeError("Hammerspoon minimized a window but reported %r, "
                               "not its window id" % output.strip()[:100])

    def _restore_minimized_window(self, window_id):
        """Unminimize and refocus; a failure here is raised, not claimed away."""
        try:
            self._hs(
                'local w = hs.window.get(%d); '
                'assert(w ~= nil, "the window is gone"); '
                'assert(w:unminimize(), "the window would not unminimize"); '
                'w:focus()' % window_id)
        except Exception as error:
            raise RuntimeError("the console window %s could not be restored: %s"
                               % (window_id, error))

    @staticmethod
    def _run_hammerspoon(script):
        """One `hs -c` snippet: stdout on success, RuntimeError on any failure."""
        try:
            finished = subprocess.run(["hs", "-c", script], capture_output=True,
                                      timeout=10)
        except FileNotFoundError:
            raise RuntimeError("Hammerspoon's 'hs' command is not installed; "
                               "without it the console cannot hide its own window")
        except subprocess.TimeoutExpired:
            raise RuntimeError("Hammerspoon did not answer within 10s")
        if finished.returncode != 0:
            detail = finished.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError("Hammerspoon refused: %s" % (detail or "no diagnostic"))
        return finished.stdout.decode("utf-8", "replace")

    @staticmethod
    def _software_capture(session_epoch, target=None, exclude_window_id=None):
        return screenshot.capture_screenshot(session_epoch, target=target,
                                             exclude_window_id=exclude_window_id)

    def _maybe_submit(self, submit, append_reply, wait_for_answer=True):
        if not submit:
            return "OK %s" % append_reply
        return self._submit(wait_for_answer)

    # -- submitting --------------------------------------------------------

    def _submit(self, wait_for_answer=True):
        """Queue the pending context as one model request; clear it only on success.

        `wait_for_answer` keeps a typed `rvw> ` prompt from interleaving with
        the answer being streamed; a socket command has no prompt to protect,
        so it returns as soon as the request is queued.
        """
        with self._pending_lock:
            if not self.pending:
                return "FAIL nothing to submit"
            prompt_key = self._assistant._selected_prompt_key
            messages = self._build_messages()
            context_text = messages[1]["content"]
            if not self._assistant.ask_the_model(messages, context_text, prompt_key):
                return "FAIL an answer is already in progress; the context is kept"
            queued = len(self.pending)
            self.pending = []
            self._screenshot_count = 0
            self._comment_count = 0
        if wait_for_answer:
            self._wait_for_the_answer()
        return "OK submitted %d item(s) under prompt %s" % (queued, prompt_key)

    def _wait_for_the_answer(self):
        """The next prompt must not interleave with the answer being streamed."""
        with self._assistant._answering:
            pass

    def _build_messages(self):
        key = self._assistant._selected_prompt_key
        body = request_header + "".join(item.rendered() for item in self.pending)
        if key in prompts.transcript_context_prompt_keys:
            transcript_text = self._recent_transcript()
            if transcript_text:
                body += "Recent transcript:\n%s\n\n" % transcript_text
        return [{"role": "system", "content": prompts.prompts[key]},
                {"role": "user", "content": body}]

    def _recent_transcript(self):
        return self._assistant._transcript.render_window(
            config.interpret_window_seconds, now=time.time()).strip()

    # -- help ---------------------------------------------------------------

    def _help(self):
        aliases = [
            "s  capture a screenshot, OCR it, queue the text",
            "S  the same, then submit the whole context",
            "c TEXT / C TEXT  queue a comment / queue and submit",
            "pl list prompts; p X select prompt X (one letter: c e r u v)",
            "send  submit the queued context as it is",
            ":CMD run a dispatcher command or its shortcut, e.g. :STATUS :S",
            "?  this list",
            "q  quit",
        ]
        dispatcher_lines = [":%s  %s  %s" % (shortcut, name, description)
                            for shortcut, name, description
                            in self._assistant._dispatcher.command_help()]
        return ("OK console commands:\n%s\n\ndispatcher commands; canonical commands also "
                "work through :NAME:\n%s"
                % ("\n".join(aliases), "\n".join(dispatcher_lines)))
