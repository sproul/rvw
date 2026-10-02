"""Transport independent command dispatch.

Hotkeys, the control socket and (in a later phase) the companion Mac all
produce the same textual commands, so nothing above this layer needs to know
where a command came from. Names are case sensitive on purpose: `c` toggles
capture while `C` clarifies, so folding case would make them collide.
"""

import logging
import traceback

from . import config

log = logging.getLogger(__name__)


class CommandDispatcher:
    """Map command names such as EXPLAIN_SPEECH onto handlers taking arguments.

    Each command may also carry a shortcut (a few keystrokes, again case
    sensitive), which resolves to the canonical name at dispatch time.
    """

    def __init__(self):
        self._handlers = {}
        self._shortcuts = {}

    def register(self, name, handler, description="", shortcut=None):
        canonical = name.strip()
        if not canonical:
            raise ValueError("a command needs a name")
        self._reject_a_collision(canonical)
        if shortcut is not None:
            shortcut = shortcut.strip()
            if not shortcut or shortcut == canonical:
                raise ValueError("%r is not a usable shortcut for %s" % (shortcut, canonical))
            self._reject_a_collision(shortcut)
        self._handlers[canonical] = (handler, description)
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
                for name, (_handler, description) in sorted(self._handlers.items())]

    def dispatch(self, command_line):
        """Run one command and return a single line 'OK ...' or 'FAIL ...' reply."""
        words = command_line.strip().split()
        if not words:
            return "FAIL empty command"
        canonical = self._canonical_name(words[0])
        if canonical is None:
            return "FAIL unknown command %s (known: %s)" % (words[0],
                                                            ", ".join(self.command_names()))
        return self._run_handler(canonical, words[1:])

    def _canonical_name(self, word):
        if word in self._handlers:
            return word
        return self._shortcuts.get(word)

    def _run_handler(self, name, arguments):
        try:
            handler, _description = self._handlers[name]
            return "OK %s" % (handler(arguments) or name)
        except Exception as error:
            if config.debug_mode:
                log.error("FAIL %s\n%s", name, traceback.format_exc())
            return "FAIL %s: %s" % (name, error)
