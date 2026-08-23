"""Tests for the settings that differ from one Mac to the next.

Only one setting does so far, and it is the one that decides whether the
assistant is usable at all: which model is loaded under the serving identifier.
The 96 GB m3 runs the 35B at 4-bit comfortably; on the 32 GB m4 that same model
is 20 GB resident and takes 24 seconds to the first token, so m4 runs the 3-bit
quant of it instead (doc/model_benchmarks.md holds the measurements).

This is settled in config.py rather than in the environment because the daemon
runs inside bin/rvw.app, started by LaunchServices, and so never sees a shell's
exported variables. A machine configured by an export would load the right model
when the installer was run by hand and the wrong one an hour later, when the
idle timeout expired and the daemon loaded it again by itself.
"""

import unittest

from rvw import config


class SourceModelForHostTest(unittest.TestCase):

    def test_m4_runs_the_three_bit_quant_that_fits_its_thirty_two_gigabytes(self):
        self.assertEqual("andrevp/Qwen3.6-35B-A3B-3bit-MLX",
                         config.source_model_for_host("m4"))

    def test_m3_runs_the_four_bit_model_its_ninety_six_gigabytes_afford(self):
        self.assertEqual("mlx-community/Qwen3.6-35B-A3B-4bit",
                         config.source_model_for_host("m3"))

    def test_a_machine_nobody_has_benchmarked_gets_the_four_bit_default(self):
        self.assertEqual(config.llm_source_model_default,
                         config.source_model_for_host("somebody-elses-mac"))

    def test_the_domain_suffix_and_the_case_of_the_name_do_not_matter(self):
        """macOS answers with m4, m4.local or M4.lan depending on the network."""
        for spelling in ("m4.local", "M4", "m4.lan", " m4 "):
            self.assertEqual(config.source_model_for_host("m4"),
                             config.source_model_for_host(spelling), spelling)

    def test_every_model_named_in_the_table_carries_its_publisher(self):
        """A bare name would resolve against whatever happened to be downloaded."""
        for host, model in config.llm_source_model_by_host.items():
            self.assertIn("/", model, host)


class ResolveLlmSourceModelTest(unittest.TestCase):

    def test_the_host_decides_when_the_environment_says_nothing(self):
        self.assertEqual("andrevp/Qwen3.6-35B-A3B-3bit-MLX",
                         config.resolve_llm_source_model({}, "m4"))

    def test_the_environment_overrides_the_host(self):
        self.assertEqual("mlx-community/some-other-model",
                         config.resolve_llm_source_model(
                             {"RVW_LLM_SOURCE_MODEL": "mlx-community/some-other-model"}, "m4"))

    def test_an_empty_environment_variable_is_not_an_answer(self):
        """Set but empty is how an unset variable reaches a launchd plist."""
        self.assertEqual("andrevp/Qwen3.6-35B-A3B-3bit-MLX",
                         config.resolve_llm_source_model({"RVW_LLM_SOURCE_MODEL": ""}, "m4"))


class ConfiguredSourceModelTest(unittest.TestCase):

    def test_this_machine_resolved_to_one_of_the_models_that_can_be_loaded(self):
        known = set(config.llm_source_model_by_host.values()) | {config.llm_source_model_default}
        self.assertIn(config.llm_source_model, known)


if __name__ == "__main__":
    unittest.main()
