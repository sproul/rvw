"""Tests for bin/rvwctl, the client every hotkey, menu item and window goes through.

The client read one 4 kB recv, which was enough while every reply was a single
line. It is not enough for a reply that carries the rolling transcript or a whole
answer: a truncated transcript in a window would look like a transcript, and a
truncated answer would look like an answer that had stopped. It now reads until
the server closes the connection, which is what the server does after replying.
"""

import os
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

repo_dir = Path(__file__).resolve().parents[1]
rvwctl = repo_dir / "bin" / "rvwctl"


class StandInAssistant:
    """A socket that answers one command with whatever reply the test wants."""

    def __init__(self, socket_path, reply):
        self._reply = reply
        self.received = None
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(socket_path))
        self._server.listen(1)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        connection, _ = self._server.accept()
        with connection:
            self.received = connection.recv(4096).decode("utf-8")
            connection.sendall(self._reply.encode("utf-8"))
        self._server.close()

    def wait(self):
        self._thread.join(timeout=5.0)


class ControlClientTestCase(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.socket_path = Path(self.temporary_directory.name) / "rvw.sock"

    def run_client(self, reply, arguments=("STATUS",)):
        """Run bin/rvwctl against a stand in assistant and return what it printed."""
        server = StandInAssistant(self.socket_path, reply)
        environment = dict(os.environ, RVW_CONTROL_SOCKET=str(self.socket_path))
        finished = subprocess.run([str(rvwctl)] + list(arguments), capture_output=True,
                                  text=True, timeout=30, env=environment)
        server.wait()
        return finished, server


class WholeReplyTest(ControlClientTestCase):

    def test_a_reply_larger_than_one_read_arrives_complete(self):
        """A rolling transcript is tens of kilobytes; a window must show all of it."""
        body = "\n".join("[00:%02d] them: utterance number %d" % (index % 60, index)
                         for index in range(2000))
        finished, _ = self.run_client("OK transcript:\n%s\n" % body)
        self.assertEqual(0, finished.returncode)
        self.assertIn("utterance number 1999", finished.stdout)
        self.assertGreater(len(finished.stdout), 4096)

    def test_a_short_reply_is_still_printed_once_and_unchanged(self):
        finished, _ = self.run_client("OK capture: none\n")
        self.assertEqual("OK capture: none", finished.stdout.strip())

    def test_the_command_and_its_arguments_reach_the_assistant(self):
        _, server = self.run_client("OK done\n", arguments=("RECALL", "what", "about", "reconnect"))
        self.assertEqual("RECALL what about reconnect", server.received)

    def test_a_failure_reply_is_reported_as_a_failure(self):
        finished, _ = self.run_client("FAIL unknown command WIBBLE\n")
        self.assertEqual(1, finished.returncode)

    def test_no_assistant_at_all_is_a_failure_and_says_where_it_looked(self):
        finished = subprocess.run([str(rvwctl), "STATUS"], capture_output=True, text=True,
                                  timeout=30,
                                  env=dict(os.environ, RVW_CONTROL_SOCKET=str(self.socket_path)))
        self.assertEqual(1, finished.returncode)
        self.assertIn(str(self.socket_path), finished.stderr)


if __name__ == "__main__":
    unittest.main()
