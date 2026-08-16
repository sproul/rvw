"""Tests for the command dispatcher shared by the hotkey client and the daemon.

Commands are deliberately transport independent: today they arrive over a unix
socket, later they may arrive from the companion Mac over the network.
"""

import unittest

from rvw.commands import CommandDispatcher


class CommandDispatcherTest(unittest.TestCase):

    def setUp(self):
        self.calls = []
        self.dispatcher = CommandDispatcher()
        self.dispatcher.register("EXPLAIN", self.record_call)

    def record_call(self, arguments):
        self.calls.append(arguments)
        return "explained %d word(s)" % len(arguments)

    def test_a_registered_command_is_invoked_and_reports_success(self):
        self.assertEqual("OK explained 0 word(s)", self.dispatcher.dispatch("EXPLAIN"))
        self.assertEqual([[]], self.calls)

    def test_arguments_are_passed_to_the_handler(self):
        self.dispatcher.dispatch("EXPLAIN 90 verbose")
        self.assertEqual([["90", "verbose"]], self.calls)

    def test_command_names_are_case_insensitive_and_trimmed(self):
        self.assertTrue(self.dispatcher.dispatch("  explain  ").startswith("OK "))

    def test_an_unknown_command_fails_without_raising(self):
        self.assertTrue(self.dispatcher.dispatch("DANCE").startswith("FAIL "))

    def test_an_empty_command_fails_without_raising(self):
        self.assertTrue(self.dispatcher.dispatch("").startswith("FAIL "))

    def test_a_handler_error_is_reported_as_a_failure(self):
        self.dispatcher.register("BOOM", self.raise_error)
        self.assertIn("kaboom", self.dispatcher.dispatch("BOOM"))
        self.assertTrue(self.dispatcher.dispatch("BOOM").startswith("FAIL "))

    def raise_error(self, arguments):
        raise RuntimeError("kaboom")

    def test_registering_the_same_command_twice_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            self.dispatcher.register("EXPLAIN", self.record_call)

    def test_known_commands_are_listable_for_diagnostics(self):
        self.assertEqual(["EXPLAIN"], self.dispatcher.command_names())


if __name__ == "__main__":
    unittest.main()
