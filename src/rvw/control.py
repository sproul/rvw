"""Unix socket front end for the command dispatcher.

A local socket keeps the hotkey agent (Hammerspoon plus the tiny rvwctl client)
completely separate from the assistant, which is the same split that Phase 7
needs when the hotkeys live on the other Mac.
"""

import logging
import os
import socket
import threading

from . import config

log = logging.getLogger(__name__)

max_command_bytes = 4096

# Commands polled on a timer (the menu bar's STATUS_FIELDS every few seconds), which
# would otherwise bury every command a person sent under one repeated line. Only
# their successes go unlogged: a poll that fails is news worth seeing.
quietly_polled_commands = frozenset({"STATUS_FIELDS"})


class ControlSocketServer:
    """Accept one command per connection and reply with a single line."""

    def __init__(self, dispatcher, socket_path=config.control_socket_path):
        self._dispatcher = dispatcher
        self._socket_path = socket_path
        self._server = None
        self._thread = None
        self._stopping = threading.Event()

    def start(self):
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._remove_stale_socket()
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(self._socket_path))
        self._server.listen(8)
        self._thread = threading.Thread(target=self._serve, name="rvw-control", daemon=True)
        self._thread.start()
        log.info("OK  listening for commands on %s", self._socket_path)

    def stop(self):
        self._stopping.set()
        if self._server is not None:
            self._server.close()
        self._remove_stale_socket()

    def _remove_stale_socket(self):
        try:
            os.unlink(self._socket_path)
        except FileNotFoundError:
            pass

    def _serve(self):
        while not self._stopping.is_set():
            try:
                connection, _ = self._server.accept()
            except OSError:
                return
            with connection:
                self._handle_connection(connection)

    def _handle_connection(self, connection):
        command_line = connection.recv(max_command_bytes).decode("utf-8", "replace")
        reply = self._dispatcher.dispatch(command_line)
        self._log_unless_a_successful_poll(command_line.strip(), reply)
        connection.sendall((reply + "\n").encode("utf-8"))

    def _log_unless_a_successful_poll(self, command_line, reply):
        """Log a served command and its reply, except a successful quiet poll."""
        if command_line in quietly_polled_commands and reply.startswith("OK"):
            return
        log.info("%s <- %s", reply, command_line)
