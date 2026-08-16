"""Transport independent command dispatch.

Hotkeys, the control socket and (in a later phase) the companion Mac all
produce the same textual commands, so nothing above this layer needs to know
where a command came from.
"""

import logging
import traceback

from . import config

log = logging.getLogger(__name__)


class CommandDispatcher:
    """Map command names such as EXPLAIN onto handlers taking a list of arguments."""

    def __init__(self):
        self._handlers = {}

    def register(self, name, handler):
        key = name.strip().upper()
        if key in self._handlers:
            raise ValueError("command %s is already registered" % key)
        self._handlers[key] = handler

    def command_names(self):
        return sorted(self._handlers)

    def dispatch(self, command_line):
        """Run one command and return a single line 'OK ...' or 'FAIL ...' reply."""
        words = command_line.strip().split()
        if not words:
            return "FAIL empty command"
        name = words[0].upper()
        if name not in self._handlers:
            return "FAIL unknown command %s (known: %s)" % (name, ", ".join(self.command_names()))
        return self._run_handler(name, words[1:])

    def _run_handler(self, name, arguments):
        try:
            return "OK %s" % (self._handlers[name](arguments) or name)
        except Exception as error:
            if config.debug_mode:
                log.error("FAIL %s\n%s", name, traceback.format_exc())
            return "FAIL %s: %s" % (name, error)
