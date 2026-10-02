"""Tests for bin/rvw_view, the transcript and answer windows.

A window cannot be inspected from here, and what it draws has to be looked at. Two
things can still be tested, and they are the two that matter:

1. The window is excluded from screen capture. The viewer sets
   NSWindow.sharingType = .none, checks that the window server accepted it, and
   dies rather than showing a window that could be captured. Both halves are
   asserted in the source, because losing either one silently would put an LLM
   answer into somebody else's recording.
2. Against a real assistant it shows the transcript, and against no assistant it
   says so and then exits rather than leaving a stale transcript on the screen.
"""

import re
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

repo_dir = Path(__file__).resolve().parents[1]
viewer_source = repo_dir / "helper" / "rvw_view.swift"
viewer_binary = repo_dir / "bin" / "rvw_view"


class CaptureExclusionTest(unittest.TestCase):
    """The one property of this program that must never quietly stop being true."""

    def setUp(self):
        self.source = viewer_source.read_text(encoding="utf-8")

    def test_the_window_is_excluded_from_screen_capture(self):
        self.assertIn("window.sharingType = .none", self.source)

    def test_a_window_the_window_server_would_not_exclude_is_not_shown_at_all(self):
        self.assertIn("if window.sharingType != .none", self.source)
        refusal = self.source.split("if window.sharingType != .none", 1)[1][:200]
        self.assertIn("die(", refusal)

    def test_the_viewer_asks_macos_for_no_permission(self):
        """It draws text and reads a socket. A framework that needs a permission
        grant has no business in the one program here that needs none, and the
        imports are what decides that, not the prose about them."""
        imported = re.findall(r"^import (\w+)$", self.source, re.MULTILINE)
        self.assertEqual(["AppKit", "Darwin"], sorted(imported))

    def test_it_stays_out_of_the_dock_and_out_of_the_way(self):
        self.assertIn("setActivationPolicy(.accessory)", self.source)
        self.assertIn("window.level = .floating", self.source)


class StandInAssistant:
    """A socket that answers TRANSCRIPT until it is told to stop."""

    def __init__(self, socket_path, reply):
        self.reply = reply
        self.commands = []
        self._stopping = threading.Event()
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.settimeout(0.5)
        self._server.bind(str(socket_path))
        self._server.listen(4)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stopping.is_set():
            try:
                connection, _ = self._server.accept()
            except (socket.timeout, OSError):
                continue
            with connection:
                self.commands.append(connection.recv(4096).decode("utf-8"))
                connection.sendall(self.reply.encode("utf-8"))
        self._server.close()

    def stop(self):
        self._stopping.set()
        self._thread.join(timeout=5.0)


class ViewerAgainstAnAssistantTest(unittest.TestCase):
    """The viewer is a window, so this checks what it asks for rather than what it
    draws: that it polls the transcript, and that it does not outlive the daemon."""

    def setUp(self):
        if not viewer_binary.exists():
            self.skipTest("bin/rvw_view is not built; run helper/build.sh")
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.socket_path = Path(self.temporary_directory.name) / "rvw.sock"

    def start_viewer(self, window_kind):
        viewer = subprocess.Popen([str(viewer_binary), "--window", window_kind,
                                   "--socket", str(self.socket_path)],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(self.stop_viewer, viewer)
        return viewer

    @staticmethod
    def stop_viewer(viewer):
        if viewer.poll() is None:
            viewer.terminate()
        try:
            viewer.wait(timeout=5)
        except subprocess.TimeoutExpired:
            viewer.kill()
        viewer.stdout.close()

    def test_the_transcript_window_polls_the_transcript(self):
        assistant = StandInAssistant(self.socket_path, "OK transcript of the last 300s:\n"
                                     "[00:01] them: the lease timeout was thirty seconds\n")
        self.addCleanup(assistant.stop)
        self.start_viewer("transcript")
        self.wait_until(lambda: assistant.commands)
        self.assertEqual("TRANSCRIPT_SHOW", assistant.commands[0])

    def test_the_answer_window_polls_the_answer(self):
        assistant = StandInAssistant(self.socket_path, "OK explanation (complete):\nan answer\n")
        self.addCleanup(assistant.stop)
        self.start_viewer("answer")
        self.wait_until(lambda: assistant.commands)
        self.assertEqual("ANSWER", assistant.commands[0])

    def test_a_window_does_not_outlive_the_assistant_it_shows(self):
        """A transcript window left on screen after the session ended would show a
        conversation that has finished as though it were still going on."""
        viewer = self.start_viewer("answer")
        self.assertEqual(0, self.exit_code_within(30, viewer))

    def test_an_unknown_window_is_refused(self):
        finished = subprocess.run([str(viewer_binary), "--window", "wibble"],
                                  capture_output=True, text=True, timeout=30)
        self.assertEqual(1, finished.returncode)
        self.assertIn("FAIL", finished.stderr)

    @staticmethod
    def exit_code_within(seconds, viewer):
        try:
            return viewer.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            return None

    @staticmethod
    def wait_until(condition, seconds=15.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not condition():
            time.sleep(0.05)
        return condition()


if __name__ == "__main__":
    unittest.main()
