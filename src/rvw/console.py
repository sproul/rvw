"""The interactive console and the ordered pending context it shares.

`bin/rvw --source none --console` runs the assistant in this terminal and
reads one line at `rvw> `. Every line but `?` is a dispatcher command, typed
exactly as `bin/rvwctl` would send it, so a shortcut means the same thing here
as on a hotkey. The commands that fill the context are, by shortcut:

    sa        capture the screen, OCR it, append the text (SCREEN_ADD)
    sx        the same, then submit (SCREEN_ANALYZE)
    n TEXT    append TEXT as a comment (COMMENT)
    N TEXT    the same, then submit (COMMENT_SUBMIT)
    send      submit the context as it is (SUBMIT)
    p X       choose the prompt it is submitted under (PROMPT_SET)
    ?         every command with its shortcut and purpose
    q         quit (QUIT)

The context belongs to this one Console, which the assistant creates whether or
not `--console` reads from a terminal, so the hotkeys fill it either way.

The model is only ever sent OCR text, never the image: what it "sees" is the
text the Vision helper extracted, and the request header says so.
"""

import select
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

from . import commands, config, ocr, prompts, screenshot

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
    """Read lines for the dispatcher and keep the ordered pending context."""

    def __init__(self, assistant, capture=None, ocr_reader=None, input_stream=None,
                 hs_runner=None):
        self._assistant = assistant
        # The capture seam is this one attribute: screenshot.capture_screenshot,
        # which reads this screen or the HDMI capture card according to
        # config.screenshot_source, and a stub in tests.
        self._capture = capture or screenshot.capture_screenshot
        self._ocr_reader = ocr_reader
        self._input_stream = input_stream or sys.stdin
        self._hs = hs_runner or run_hammerspoon
        # Socket commands and this read loop run on different threads and share
        # the pending context; every read-modify-write of it takes this lock.
        self._pending_lock = threading.Lock()
        self.pending = []
        self._screenshot_count = 0
        self._comment_count = 0

    def _ocr(self, image_path):
        """Resolved per call, so a test patching ocr.ocr_text_of reaches it."""
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
            self._wait_for_the_answer()

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

    def _wait_for_the_answer(self):
        """The next prompt must not interleave with an answer being streamed."""
        with self._assistant._answering:
            pass

    def handle_line(self, line):
        """Act on one console line and return the reply text ('' says nothing).

        A typed line comes from this console, which is the frontmost window.
        """
        line = line.strip()
        if not line:
            return ""
        if line == "?":
            return self._help()
        return self._assistant._dispatcher.dispatch(line, sent_from_frontmost_window=True)

    # -- accumulating context ----------------------------------------------
    #
    # These serve the dispatcher commands: each returns the text that follows
    # the dispatcher's "OK " and raises for anything the dispatcher must report
    # as FAIL. None of them waits for an answer, because the control socket
    # serves commands one at a time and a model that loads on demand would
    # stall STATUS and QUIT behind it for the better part of a minute.

    def add_comment(self, text, submit):
        if not text:
            raise ValueError("a comment needs text")
        with self._pending_lock:
            self._comment_count += 1
            number = self._comment_count
            self.pending.append(_PendingItem("comment", number, "", text))
        return self._maybe_submit(submit, "comment %d noted" % number)

    def add_screenshot(self, submit):
        """Archive a screenshot, OCR it, queue the text; a failure queues nothing."""
        saved = self.capture_screen()
        text = self._ocr(saved.image_path).strip()
        if not text:
            raise RuntimeError("OCR of %s produced no text; nothing was queued"
                               % saved.image_path.name)
        with self._pending_lock:
            self._screenshot_count += 1
            number = self._screenshot_count
            self.pending.append(_PendingItem("screenshot", number,
                                             saved.image_path.name, text))
        return self._maybe_submit(
            submit, "screenshot %d queued as %s" % (number, saved.image_path.name))

    def _maybe_submit(self, submit, append_reply):
        return self.submit() if submit else append_reply

    # -- capturing the screen ----------------------------------------------

    def capture_screen(self):
        """One archived screenshot for the request now being served.

        Every screen command captures through here, so the capture seam is one
        attribute whatever the command. A request typed into the frontmost
        window (this console, or a terminal running rvwctl) would photograph
        that window, so it is hidden for the capture; a hotkey is not. Nor is
        anything when the screen comes from the other Mac through the capture
        card, which shows none of this Mac's windows.
        """
        if config.screenshot_source == "screen" and commands.sent_from_frontmost_window():
            return self._capture_with_the_requesting_window_hidden()
        return self._capture(self._assistant._session_started_epoch)

    def _capture_with_the_requesting_window_hidden(self):
        """Minimize the frontmost window, capture the whole display, put it back.

        Hammerspoon does the window work over `hs -c`. No delay: the window is
        gone before the helper runs, and --exclude-window-id keeps it out even
        if it is still animating. Anything that cannot be hidden means no
        capture at all -- taking the shot anyway would photograph the very
        window the request came from.
        """
        window_id = self._minimize_frontmost_window()
        try:
            return self._capture(self._assistant._session_started_epoch,
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
            raise RuntimeError("the window %s that sent the command could not be "
                               "restored: %s" % (window_id, error))

    # -- submitting --------------------------------------------------------

    def submit(self):
        """Queue the pending context as one model request; clear it only on success."""
        with self._pending_lock:
            if not self.pending:
                raise RuntimeError("nothing to submit")
            prompt_key = self._assistant._selected_prompt_key
            messages = self._build_messages()
            context_text = messages[1]["content"]
            if not self._assistant.ask_the_model(messages, context_text, prompt_key):
                raise RuntimeError("an answer is already in progress; the context is kept")
            queued = len(self.pending)
            self.pending = []
            self._screenshot_count = 0
            self._comment_count = 0
        return "submitted %d item(s) under prompt %s" % (queued, prompt_key)

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
        command_lines = ["%s  %s  %s" % entry
                         for entry in self._assistant._dispatcher.command_help()]
        return ("OK console commands, as shortcut  NAME  purpose:\n?  list these commands\n%s"
                % "\n".join(command_lines))


def run_hammerspoon(script):
    """One `hs -c` snippet: stdout on success, RuntimeError on any failure."""
    try:
        finished = subprocess.run([str(config.hammerspoon_cli_path), "-c", script],
                                  capture_output=True, timeout=10)
    except FileNotFoundError:
        raise RuntimeError("Hammerspoon's 'hs' command is not at %s; without it the "
                           "window that sent the command cannot be hidden"
                           % config.hammerspoon_cli_path)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Hammerspoon did not answer within 10s")
    if finished.returncode != 0:
        detail = finished.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError("Hammerspoon refused: %s" % (detail or "no diagnostic"))
    return finished.stdout.decode("utf-8", "replace")
