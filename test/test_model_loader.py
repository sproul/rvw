"""Tests for loading the local LLM into LM Studio the moment it is first needed.

LM Studio unloads an idle model after config.llm_idle_ttl_seconds, which is
deliberate: the assistant spends most of its life listening rather than asking,
and twenty gigabytes of resident model is a poor way to spend that time. An
unloaded model is therefore the normal state between questions, not a fault, and
the daemon loads it when a question actually arrives.

Loading takes minutes and a great deal of memory, so nothing here runs lms; the
command it would have run is recorded instead.
"""

import json
import unittest

from rvw import config, model_loader

listing_of_downloaded_models = json.dumps([
    {"modelKey": "text-embedding-nomic-embed-text-v1.5", "path": "nomic/embed"},
    {"modelKey": "qwen3.6-35b-a3b", "path": "mlx-community/Qwen3.6-35B-A3B-4bit"},
])


class RecordingLms:
    """Stands in for the lms command line; records instead of loading."""

    def __init__(self, listing=listing_of_downloaded_models, succeeds=True):
        self.listing = listing
        self.succeeds = succeeds
        self.commands = []

    def __call__(self, arguments):
        self.commands.append(arguments)
        if arguments[:2] == ["ls", "--llm"]:
            return self.listing
        if not self.succeeds:
            raise model_loader.ModelLoadError("lms load failed")
        return ""

    @property
    def load_commands(self):
        return [command for command in self.commands if command[:1] == ["load"]]


class ModelLoaderTestCase(unittest.TestCase):

    def setUp(self):
        self.lms = RecordingLms()
        self.saved_runner = model_loader.run_lms
        model_loader.run_lms = self.lms
        self.addCleanup(self.restore_runner)

    def restore_runner(self):
        model_loader.run_lms = self.saved_runner


class ModelKeyTest(ModelLoaderTestCase):
    """'lms load' wants the local model key, not the Hugging Face repo id."""

    def test_the_repo_id_resolves_to_the_downloaded_model_key(self):
        self.assertEqual("qwen3.6-35b-a3b",
                         model_loader.resolve_model_key(listing_of_downloaded_models,
                                                        "mlx-community/Qwen3.6-35B-A3B-4bit"))

    def test_punctuation_and_case_do_not_matter(self):
        self.assertEqual("qwen3.6-35b-a3b",
                         model_loader.resolve_model_key(listing_of_downloaded_models,
                                                        "Qwen3_6-35B_A3B"))

    def test_a_model_that_was_never_downloaded_is_reported_rather_than_guessed(self):
        with self.assertRaises(model_loader.ModelLoadError):
            model_loader.resolve_model_key(listing_of_downloaded_models, "llama-3-70b")

    def test_an_empty_listing_is_reported_rather_than_guessed(self):
        with self.assertRaises(model_loader.ModelLoadError):
            model_loader.resolve_model_key("", "mlx-community/Qwen3.6-35B-A3B-4bit")


class LoadOnDemandTest(ModelLoaderTestCase):

    def test_a_model_that_is_already_served_is_not_loaded_again(self):
        self.assertFalse(model_loader.ensure_the_configured_model_is_loaded([config.llm_model]))
        self.assertEqual([], self.lms.load_commands)

    def test_a_missing_model_is_loaded_under_the_configured_identifier(self):
        self.assertTrue(model_loader.ensure_the_configured_model_is_loaded([]))
        self.assertEqual(1, len(self.lms.load_commands))
        command = self.lms.load_commands[0]
        self.assertIn("qwen3.6-35b-a3b", command)
        self.assertIn("--identifier", command)
        self.assertIn(config.llm_model, command)

    def test_the_load_carries_the_configured_context_length_and_idle_timeout(self):
        model_loader.ensure_the_configured_model_is_loaded([])
        command = self.lms.load_commands[0]
        self.assertIn(str(config.llm_context_length), command)
        self.assertIn(str(config.llm_idle_ttl_seconds), command)

    def test_a_load_that_fails_is_reported_and_not_swallowed(self):
        self.lms.succeeds = False
        with self.assertRaises(model_loader.ModelLoadError):
            model_loader.ensure_the_configured_model_is_loaded([])


if __name__ == "__main__":
    unittest.main()
