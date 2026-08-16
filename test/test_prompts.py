"""Tests for the prompts sent to the local LLM and the local vision model."""

import unittest

from rvw.prompts import (build_clarify_messages, build_explain_messages,
                         build_interpret_messages)

TRANSCRIPT = "[00:03] them: we should use a bounded work queue\n[00:11] me: how bounded"
IMAGE_DATA_URI = "data:image/png;base64,aW1hZ2U="


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


class BuildClarifyMessagesTest(unittest.TestCase):
    """Clarify reconstructs the words themselves; it does not teach concepts."""

    def setUp(self):
        self.messages = build_clarify_messages(TRANSCRIPT, window_seconds=45)

    def test_the_conversation_is_a_system_plus_user_pair(self):
        self.assertEqual(["system", "user"], [message["role"] for message in self.messages])

    def test_the_transcript_is_sent_verbatim(self):
        self.assertIn(TRANSCRIPT, self.messages[1]["content"])

    def test_the_window_length_is_stated_to_the_model(self):
        self.assertIn("45", self.messages[1]["content"])

    def test_the_system_prompt_asks_for_reconstruction_not_teaching(self):
        instructions = self.messages[0]["content"].lower()
        for expected in ["accent", "misrecogni", "uncertain", "verbatim"]:
            self.assertIn(expected, instructions)

    def test_clarify_does_not_reuse_the_explain_instructions(self):
        explain_instructions = build_explain_messages(TRANSCRIPT, window_seconds=45)[0]["content"]
        self.assertNotEqual(explain_instructions, self.messages[0]["content"])

    def test_an_empty_transcript_is_refused(self):
        with self.assertRaises(ValueError):
            build_clarify_messages("", window_seconds=45)


class BuildInterpretMessagesTest(unittest.TestCase):
    """The vision request carries the image plus whatever context exists."""

    def setUp(self):
        self.messages = build_interpret_messages(TRANSCRIPT, IMAGE_DATA_URI, window_seconds=120)

    def test_the_conversation_is_a_system_plus_user_pair(self):
        self.assertEqual(["system", "user"], [message["role"] for message in self.messages])

    def test_the_image_is_attached_as_an_image_url_part(self):
        parts = self.messages[1]["content"]
        image_parts = [part for part in parts if part["type"] == "image_url"]
        self.assertEqual([IMAGE_DATA_URI],
                         [part["image_url"]["url"] for part in image_parts])

    def test_the_transcript_is_attached_as_a_text_part(self):
        texts = [part["text"] for part in self.messages[1]["content"]
                 if part["type"] == "text"]
        self.assertTrue(any(TRANSCRIPT in text for text in texts))

    def test_an_absent_transcript_still_produces_a_usable_request(self):
        messages = build_interpret_messages("  ", IMAGE_DATA_URI, window_seconds=120)
        texts = [part["text"] for part in messages[1]["content"] if part["type"] == "text"]
        self.assertTrue(any("no transcript" in text.lower() for text in texts))

    def test_a_missing_image_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            build_interpret_messages(TRANSCRIPT, "", window_seconds=120)


if __name__ == "__main__":
    unittest.main()
