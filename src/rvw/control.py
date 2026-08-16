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
        log.info("%s <- %s", reply, command_line.strip())
        connection.sendall((reply + "\n").encode("utf-8"))
