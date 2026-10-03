"""Transport independent command dispatch.

Hotkeys, the control socket and (in a later phase) the companion Mac all
produce the same textual commands, so nothing above this layer needs to know
where a command came from. Names are case sensitive on purpose: `c` toggles
capture while `C` ungarbles, so folding case would make them collide.
"""

import logging
import threading
import traceback

from . import config

log = logging.getLogger(__name__)

# What the request now being served said about where it came from. Each
# transport serves its requests on its own thread, so this is per thread.
_current_request = threading.local()


def sent_from_frontmost_window():
    """Whether the request now being served was typed into the frontmost window.

    A screenshot taken for such a request would photograph the window that asked
    for it. Each transport decides: the console says yes for every line, and
    the control socket says yes unless Hammerspoon marked the request as a
    hotkey or menu click, which leave the window of interest frontmost. A
    direct dispatch() call, as in a test, says no unless told otherwise.
    """
    return getattr(_current_request, "from_frontmost_window", False)


class CommandDispatcher:
    """Map command names such as EXPLAIN_SPEECH onto handlers taking arguments.

    Each command may also carry a shortcut (a few keystrokes, again case
    sensitive), which resolves to the canonical name at dispatch time.
    """

    def __init__(self):
        self._handlers = {}
        self._shortcuts = {}

    def register(self, name, handler, description="", shortcut=None, takes_arguments=False):
        canonical = name.strip()
        if not canonical:
            raise ValueError("a command needs a name")
        self._reject_a_collision(canonical)
        if shortcut is not None:
            shortcut = shortcut.strip()
            if not shortcut or shortcut == canonical:
                raise ValueError("%r is not a usable shortcut for %s" % (shortcut, canonical))
            self._reject_a_collision(shortcut)
        self._handlers[canonical] = (handler, description, takes_arguments)
        if shortcut is not None:
            self._shortcuts[shortcut] = canonical

    def _reject_a_collision(self, word):
        if word in self._handlers or word in self._shortcuts:
            raise ValueError("command %s is already registered" % word)

    def command_names(self):
        return sorted(self._handlers)

    def command_help(self):
        """Sorted (shortcut, canonical_name, description) triples, by name."""
        shortcut_of = {canonical: shortcut
                       for shortcut, canonical in self._shortcuts.items()}
        return [(shortcut_of.get(name, ""), name, description)
                for name, (_handler, description, _takes) in sorted(self._handlers.items())]

    def dispatch(self, command_line, sent_from_frontmost_window=False):
        """Run one command and return a single line 'OK ...' or 'FAIL ...' reply.

        `sent_from_frontmost_window` is what the transport knows about the
        request's origin; handlers read it back through the module function.
        """
        words = command_line.strip().split()
        if not words:
            return "FAIL empty command"
        canonical = self._canonical_name(words[0])
        if canonical is None:
            return "FAIL unknown command %s (known: %s)" % (words[0],
                                                            ", ".join(self.command_names()))
        _current_request.from_frontmost_window = sent_from_frontmost_window
        try:
            return self._run_handler(canonical, words[1:])
        finally:
            _current_request.from_frontmost_window = False

    def _canonical_name(self, word):
        if word in self._handlers:
            return word
        return self._shortcuts.get(word)

    def _run_handler(self, name, arguments):
        handler, _description, takes_arguments = self._handlers[name]
        if arguments and not takes_arguments:
            return "FAIL %s takes no arguments; got %r" % (name, " ".join(arguments))
        try:
            return "OK %s" % (handler(arguments) or name)
        except Exception as error:
            if config.debug_mode:
                log.error("FAIL %s\n%s", name, traceback.format_exc())
            return "FAIL %s: %s" % (name, error)
