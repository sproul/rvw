"""Tests for what the control socket server logs about the commands it serves.

The menu bar polls STATUS_FIELDS every few seconds, so logging that poll buried
every command a person actually sent under a status line repeated forever. A
successful poll is served silently; a failed one, and every other command, is
still logged with its reply.
"""

import socket
import tempfile
import unittest
from pathlib import Path

from rvw.commands import CommandDispatcher
from rvw.control import ControlSocketServer


class ControlServerLoggingTestCase(unittest.TestCase):

    def setUp(self):
        self.status_is_unavailable = False
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.socket_path = Path(temporary_directory.name) / "rvw.sock"
        self.server = ControlSocketServer(self.build_dispatcher_with_status_commands(),
                                          socket_path=self.socket_path)
        self.server.start()
        self.addCleanup(self.server.stop)

    def build_dispatcher_with_status_commands(self):
        dispatcher = CommandDispatcher()
        dispatcher.register("STATUS", lambda arguments: "idle")
        dispatcher.register("STATUS_FIELDS", self.report_status_fields)
        return dispatcher

    def report_status_fields(self, arguments):
        if self.status_is_unavailable:
            raise RuntimeError("status unavailable")
        return "capture=none"

    def send_command_and_read_reply(self, command_line):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(self.socket_path))
            client.sendall(command_line.encode("utf-8"))
            return client.makefile("r", encoding="utf-8").read()

    def test_status_fields_poll_is_answered_without_being_logged(self):
        with self.assertNoLogs("rvw.control", level="INFO"):
            reply = self.send_command_and_read_reply("STATUS_FIELDS\n")
        self.assertEqual(reply, "OK capture=none\n")

    def test_status_fields_poll_that_fails_is_still_logged(self):
        self.status_is_unavailable = True
        with self.assertLogs("rvw.control", level="INFO") as captured:
            self.send_command_and_read_reply("STATUS_FIELDS\n")
        self.assertEqual(captured.output, [
            "INFO:rvw.control:FAIL STATUS_FIELDS: status unavailable <- STATUS_FIELDS"])

    def test_other_commands_are_still_logged_with_their_reply(self):
        with self.assertLogs("rvw.control", level="INFO") as captured:
            self.send_command_and_read_reply("STATUS\n")
        self.assertEqual(captured.output, ["INFO:rvw.control:OK idle <- STATUS"])


if __name__ == "__main__":
    unittest.main()
