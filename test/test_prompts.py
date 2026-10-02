"""Tests for the prompts sent to the local LLM and the local vision model."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rvw import prompts
from rvw.prompts import (build_explain_messages, build_interpret_messages,
                         build_ungarble_messages)

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


class BuildUngarbleMessagesTest(unittest.TestCase):
    """Ungarble reconstructs the words themselves; it does not teach concepts."""

    def setUp(self):
        self.messages = build_ungarble_messages(TRANSCRIPT, window_seconds=45)

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

    def test_ungarble_does_not_reuse_the_explain_instructions(self):
        explain_instructions = build_explain_messages(TRANSCRIPT, window_seconds=45)[0]["content"]
        self.assertNotEqual(explain_instructions, self.messages[0]["content"])

    def test_an_empty_transcript_is_refused(self):
        with self.assertRaises(ValueError):
            build_ungarble_messages("", window_seconds=45)


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


class PromptRegistryTest(unittest.TestCase):
    """The selectable prompts, keyed for the console and the PROMPT commands."""

    def test_the_registry_offers_the_five_keys_in_order(self):
        self.assertEqual(["explain", "ungarble", "recall", "visually_interpret",
                          "code_review"], list(prompts.prompts))

    def test_the_registry_maps_to_the_existing_prompt_strings(self):
        self.assertIs(prompts.explain_system_prompt, prompts.prompts["explain"])
        self.assertIs(prompts.ungarble_system_prompt, prompts.prompts["ungarble"])
        self.assertIs(prompts.recall_system_prompt, prompts.prompts["recall"])
        self.assertIs(prompts.interpret_system_prompt, prompts.prompts["visually_interpret"])
        self.assertIs(prompts.code_review_system_prompt, prompts.prompts["code_review"])

    def test_transcript_context_is_offered_to_the_conversation_prompts_only(self):
        self.assertEqual(("explain", "ungarble", "visually_interpret"),
                         prompts.transcript_context_prompt_keys)

    def test_each_key_is_chosen_by_one_letter(self):
        for letter, key in [("c", "code_review"), ("e", "explain"), ("r", "recall"),
                            ("u", "ungarble"), ("v", "visually_interpret")]:
            self.assertEqual(key, prompts.prompt_key_for_letter(letter))

    def test_anything_but_a_known_letter_is_rejected(self):
        for bad in ["", "x", "E", "explain", "code_review", "1"]:
            with self.assertRaises(ValueError):
                prompts.prompt_key_for_letter(bad)


class SelectedPromptFileTest(unittest.TestCase):
    """The persisted selection: absent means the default, garbage is fatal."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "var" / "selected_prompt"
        self.addCleanup(self.temporary_directory.cleanup)

    def test_an_absent_file_means_the_default(self):
        self.assertEqual("explain", prompts.read_selected_prompt_key(self.path))

    def test_a_written_key_is_read_back(self):
        prompts.write_selected_prompt_key(self.path, "code_review")
        self.assertEqual("code_review", prompts.read_selected_prompt_key(self.path))

    def test_the_parent_directory_is_created_for_the_write(self):
        prompts.write_selected_prompt_key(self.path, "recall")
        self.assertTrue(self.path.is_file())

    def test_a_bare_key_without_the_newline_is_accepted(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("recall", encoding="utf-8")
        self.assertEqual("recall", prompts.read_selected_prompt_key(self.path))

    def test_only_the_exact_key_with_one_newline_is_accepted(self):
        """Saved data is ours, not typed input: padding is a violation, fatal."""
        self.path.parent.mkdir(parents=True)
        for content in [" explain\n", "explain \n", "explain\n\n", "\nexplain\n",
                        "explain\nmore\n", "explain\r\n"]:
            self.path.write_text(content, encoding="utf-8")
            with self.subTest(content=content), self.assertRaises(ValueError):
                prompts.read_selected_prompt_key(self.path)

    def test_an_invalid_or_empty_file_is_fatal(self):
        self.path.parent.mkdir(parents=True)
        for content in ["", "   \n", "nonsense\n"]:
            self.path.write_text(content, encoding="utf-8")
            with self.assertRaises(ValueError):
                prompts.read_selected_prompt_key(self.path)

    def test_a_failed_write_leaves_no_temp_file_and_the_old_value_intact(self):
        prompts.write_selected_prompt_key(self.path, "recall")
        with patch("rvw.prompts.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                prompts.write_selected_prompt_key(self.path, "code_review")
        self.assertEqual("recall", prompts.read_selected_prompt_key(self.path))
        self.assertEqual(["selected_prompt"],
                         [entry.name for entry in self.path.parent.iterdir()])

    def test_a_failure_inside_the_write_also_leaves_no_temp_file(self):
        """The temp file exists the moment NamedTemporaryFile opens it, so a
        write that fails after that point must still clean it up."""
        prompts.write_selected_prompt_key(self.path, "recall")
        real_named_temporary_file = tempfile.NamedTemporaryFile

        class WriteFailingTempFile:
            def __init__(self, *args, **kwargs):
                self._file = real_named_temporary_file(*args, **kwargs)
                self.name = self._file.name

            def write(self, data):
                raise OSError("disk full mid-write")

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                self._file.__exit__(*exc_info)
                return False

        with patch("rvw.prompts.tempfile.NamedTemporaryFile", WriteFailingTempFile):
            with self.assertRaises(OSError):
                prompts.write_selected_prompt_key(self.path, "code_review")
        self.assertEqual("recall", prompts.read_selected_prompt_key(self.path))
        self.assertEqual(["selected_prompt"],
                         [entry.name for entry in self.path.parent.iterdir()])


if __name__ == "__main__":
    unittest.main()
