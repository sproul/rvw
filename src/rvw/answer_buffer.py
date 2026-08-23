"""The answer being written, or the last one, so that a window can show it.

Answers stream to the assistant's terminal a token at a time. A window cannot
watch a terminal, and polling has to be able to ask at any moment, including
halfway through an answer, so the tokens are collected here as well. This holds
one answer: the current one. Nothing here is history -- the session log is.
"""

import threading

no_answer_yet = "nothing has been asked yet"


class AnswerBuffer:
    """One answer, its heading and its state, written by the answering thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._heading = None
        self._tokens = []
        self._state = None

    def begin(self, heading):
        """Start a new answer, discarding the previous one."""
        with self._lock:
            self._heading = heading
            self._tokens = []
            self._state = "in progress"

    def append(self, token):
        with self._lock:
            self._tokens.append(token)

    def finish(self):
        with self._lock:
            self._state = "complete"

    def fail(self, reason):
        """Record why an answer stopped, so the window does not imply more is coming."""
        with self._lock:
            self._state = "FAIL %s" % reason

    def render(self):
        """The answer as a window should show it: one heading line, then the text."""
        with self._lock:
            if self._heading is None:
                return no_answer_yet
            return "%s (%s):\n%s" % (self._heading, self._state, "".join(self._tokens))
