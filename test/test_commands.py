"""Tests for the command dispatcher shared by the hotkey client and the daemon.

Commands are deliberately transport independent: today they arrive over a unix
socket, later they may arrive from the companion Mac over the network. Command
names and their shortcuts are case sensitive on purpose: `c` toggles capture and
`C` ungarbles, so folding case would make them collide.
"""

import unittest

from rvw.commands import CommandDispatcher


class CommandDispatcherTest(unittest.TestCase):

    def setUp(self):
        self.calls = []
        self.dispatcher = CommandDispatcher()
        self.dispatcher.register("EXPLAIN_SPEECH", self.record_call,
                                 description="explain recent speech", shortcut="E")
        self.dispatcher.register("UNGARBLE_SPEECH", self.ungarble_call,
                                 description="ungarble recent speech", shortcut="C")
        self.dispatcher.register("AUDIO_CAPTURE_TOGGLE", self.capture_call,
                                 description="toggle audio capture", shortcut="c")
        self.dispatcher.register("ANSWER", self.record_call)

    def record_call(self, arguments):
        self.calls.append(("explain", arguments))
        return "explained %d word(s)" % len(arguments)

    def ungarble_call(self, arguments):
        self.calls.append(("ungarble", arguments))
        return "ungarbled"

    def capture_call(self, arguments):
        self.calls.append(("capture", arguments))
        return "toggled"

    # -- dispatching -------------------------------------------------------

    def test_a_registered_command_is_invoked_and_reports_success(self):
        self.assertEqual("OK explained 0 word(s)", self.dispatcher.dispatch("EXPLAIN_SPEECH"))
        self.assertEqual([("explain", [])], self.calls)

    def test_arguments_are_passed_to_the_handler(self):
        self.dispatcher.dispatch("EXPLAIN_SPEECH 90 verbose")
        self.assertEqual([("explain", ["90", "verbose"])], self.calls)

    def test_command_names_are_case_sensitive_but_still_trimmed(self):
        self.assertTrue(self.dispatcher.dispatch("  EXPLAIN_SPEECH  ").startswith("OK "))
        for wrong_case in ["explain_speech", "Explain_Speech"]:
            self.assertTrue(self.dispatcher.dispatch(wrong_case).startswith("FAIL "),
                            wrong_case)

    def test_a_shortcut_runs_its_canonical_command_with_the_arguments(self):
        self.assertEqual("OK ungarbled", self.dispatcher.dispatch("C 45"))
        self.assertEqual([("ungarble", ["45"])], self.calls)

    def test_shortcuts_differing_only_in_case_are_different_commands(self):
        self.dispatcher.dispatch("c")
        self.dispatcher.dispatch("C")
        self.assertEqual([("capture", []), ("ungarble", [])], self.calls)

    def test_an_unknown_command_fails_without_raising(self):
        self.assertTrue(self.dispatcher.dispatch("DANCE").startswith("FAIL "))

    def test_an_unknown_command_names_the_canonical_commands_not_the_shortcuts(self):
        reply = self.dispatcher.dispatch("DANCE")
        known = reply.split("known: ", 1)[1].rstrip(")").split(", ")
        self.assertEqual(["ANSWER", "AUDIO_CAPTURE_TOGGLE", "EXPLAIN_SPEECH",
                          "UNGARBLE_SPEECH"], known)

    def test_an_empty_command_fails_without_raising(self):
        self.assertTrue(self.dispatcher.dispatch("").startswith("FAIL "))

    def test_a_handler_error_is_reported_as_a_failure(self):
        self.dispatcher.register("BOOM", self.raise_error)
        self.assertIn("kaboom", self.dispatcher.dispatch("BOOM"))
        self.assertTrue(self.dispatcher.dispatch("BOOM").startswith("FAIL "))

    def raise_error(self, arguments):
        raise RuntimeError("kaboom")

    # -- registration ------------------------------------------------------

    def test_registering_the_same_command_twice_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            self.dispatcher.register("EXPLAIN_SPEECH", self.record_call)

    def test_a_name_colliding_with_a_shortcut_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            self.dispatcher.register("E", self.record_call)

    def test_a_shortcut_colliding_with_a_name_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            self.dispatcher.register("NEW", self.record_call, shortcut="EXPLAIN_SPEECH")

    def test_a_shortcut_colliding_with_a_shortcut_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            self.dispatcher.register("NEW", self.record_call, shortcut="E")

    def test_a_shortcut_equal_to_its_own_name_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            self.dispatcher.register("NEW", self.record_call, shortcut="NEW")

    def test_a_blank_command_name_is_a_programming_error(self):
        for blank in ["", "   "]:
            with self.assertRaises(ValueError):
                self.dispatcher.register(blank, self.record_call)

    def test_a_blank_shortcut_is_a_programming_error(self):
        for blank in ["", "   "]:
            with self.assertRaises(ValueError):
                self.dispatcher.register("NEW", self.record_call, shortcut=blank)

    # -- recognising -------------------------------------------------------

    def test_a_canonical_name_and_a_shortcut_are_both_recognised(self):
        self.assertTrue(self.dispatcher.recognises("EXPLAIN_SPEECH"))
        self.assertTrue(self.dispatcher.recognises("E"))

    def test_an_unregistered_or_differently_cased_word_is_not_recognised(self):
        for word in ["explain_speech", "e", "WIBBLE", ""]:
            self.assertFalse(self.dispatcher.recognises(word), word)

    # -- listing -----------------------------------------------------------

    def test_known_commands_are_the_canonical_names_only(self):
        self.assertEqual(["ANSWER", "AUDIO_CAPTURE_TOGGLE", "EXPLAIN_SPEECH",
                          "UNGARBLE_SPEECH"],
                         self.dispatcher.command_names())

    def test_command_help_lists_shortcut_name_and_description_per_command(self):
        self.assertEqual(
            [("", "ANSWER", ""),
             ("c", "AUDIO_CAPTURE_TOGGLE", "toggle audio capture"),
             ("E", "EXPLAIN_SPEECH", "explain recent speech"),
             ("C", "UNGARBLE_SPEECH", "ungarble recent speech")],
            self.dispatcher.command_help())


if __name__ == "__main__":
    unittest.main()
