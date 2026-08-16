"""Tests for the explanation prompt sent to the local LLM."""

import unittest

from rvw.prompts import build_explain_messages

TRANSCRIPT = "[00:03] them: we should use a bounded work queue\n[00:11] me: how bounded"


class BuildExplainMessagesTest(unittest.TestCase):

    def setUp(self):
        self.messages = build_explain_messages(TRANSCRIPT, window_seconds=60)

    def test_the_conversation_is_a_system_plus_user_pair(self):
        self.assertEqual(["system", "user"], [message["role"] for message in self.messages])

    def test_the_transcript_is_sent_verbatim(self):
        self.assertIn(TRANSCRIPT, self.messages[1]["content"])

    def test_the_window_length_is_stated_to_the_model(self):
        self.assertIn("60", self.messages[1]["content"])

    def test_the_system_prompt_states_the_required_behaviour(self):
        instructions = self.messages[0]["content"].lower()
        for expected in ["transcription", "terminology", "uncertain", "infer"]:
            self.assertIn(expected, instructions)

    def test_an_empty_transcript_is_refused(self):
        with self.assertRaises(ValueError):
            build_explain_messages("   ", window_seconds=60)


if __name__ == "__main__":
    unittest.main()
