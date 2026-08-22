"""Tests for the local LLM client, and for one dangerous habit of the server.

Measured against LM Studio on 2026-08-21: asked for a model identifier it has
never heard of, this build does not refuse. It answers with whatever model
happens to be loaded and reports that substitute in the response. A screenshot
sent to 'meeting-vision' when no vision model was loaded therefore came back
interpreted by the text model, and the assistant called it a success.

That is exactly the case the project treats as fatal: an assumption about our
own data was violated, so the client says so rather than passing off one model's
answer as another's. Both guards are tested here, the one before the question
and the one over the answer, because either alone can be defeated.
"""

import io
import json
import unittest
import urllib.request

from rvw import config, llm, model_loader

served_identifier = "meeting-assistant"
vision_identifier = "meeting-vision"


def stream_of(chunks, model):
    """The server sends one 'data:' line per chunk and then [DONE]."""
    lines = ["data: %s\n" % json.dumps({"model": model,
                                        "choices": [{"delta": delta}]}) for delta in chunks]
    return "".join(lines) + "data: [DONE]\n"


class FakeEndpoint:
    """Stands in for the OpenAI compatible endpoint, substitutions included."""

    def __init__(self, served_models=(served_identifier,), answering_model=None):
        self.served_models = list(served_models)
        self.answering_model = answering_model
        self.chunks = [{"content": "the answer"}]
        self.chat_requests = []

    def urlopen(self, request, timeout=None):
        """The listing is fetched by url, the chat request as a Request object."""
        if isinstance(request, str):
            return self.model_listing()
        self.chat_requests.append(json.loads(request.data.decode("utf-8")))
        return io.BytesIO(stream_of(self.chunks, self.model_that_answers()).encode("utf-8"))

    def model_that_answers(self):
        """Without an explicit substitute the server behaves honestly."""
        if self.answering_model is not None:
            return self.answering_model
        return self.chat_requests[-1]["model"]

    def model_listing(self):
        listing = {"data": [{"id": name} for name in self.served_models]}
        return io.BytesIO(json.dumps(listing).encode("utf-8"))


class LocalLlmTestCase(unittest.TestCase):

    def setUp(self):
        self.endpoint = FakeEndpoint()
        self.saved_urlopen = urllib.request.urlopen
        urllib.request.urlopen = self.endpoint.urlopen
        self.addCleanup(self.restore_urlopen)

    def restore_urlopen(self):
        urllib.request.urlopen = self.saved_urlopen

    def collected_answer(self, client):
        tokens = []
        answer = client.stream_chat([{"role": "user", "content": "hello"}], tokens.append)
        return answer, tokens


class StreamingTest(LocalLlmTestCase):
    """The ordinary path: the model that was asked answers."""

    def test_the_answer_is_returned_and_streamed_token_by_token(self):
        self.endpoint.chunks = [{"content": "one "}, {"content": "two"}]
        answer, tokens = self.collected_answer(llm.LocalLlm(model=served_identifier))
        self.assertEqual("one two", answer)
        self.assertEqual(["one ", "two"], tokens)

    def test_reasoning_tokens_are_kept_out_of_the_answer(self):
        self.endpoint.chunks = [{"reasoning_content": "thinking hard"}, {"content": "the answer"}]
        thoughts = []
        answer = llm.LocalLlm(model=served_identifier).stream_chat(
            [{"role": "user", "content": "hello"}], lambda token: None, thoughts.append)
        self.assertEqual("the answer", answer)
        self.assertEqual(["thinking hard"], thoughts)


class SubstitutedModelTest(LocalLlmTestCase):
    """A model that is not loaded must never be answered for by another one."""

    def vision_client(self):
        """The vision model is the one client that is never loaded on demand."""
        return llm.LocalLlm(model=vision_identifier, loads_on_demand=False)

    def test_an_unloaded_model_is_refused_before_the_question_is_asked(self):
        with self.assertRaises(llm.LocalLlmError):
            self.collected_answer(self.vision_client())
        self.assertEqual([], self.endpoint.chat_requests,
                         "the question was asked of a model that is not loaded")

    def test_the_refusal_names_the_missing_identifier_and_what_is_loaded(self):
        with self.assertRaises(llm.LocalLlmError) as refused:
            self.collected_answer(self.vision_client())
        self.assertIn(vision_identifier, str(refused.exception))
        self.assertIn(served_identifier, str(refused.exception))

    def test_a_loaded_model_is_asked_normally(self):
        self.endpoint.served_models = [served_identifier, vision_identifier]
        answer, _ = self.collected_answer(self.vision_client())
        self.assertEqual("the answer", answer)
        self.assertEqual(vision_identifier, self.endpoint.chat_requests[0]["model"])

    def test_an_answer_from_a_different_model_than_the_one_asked_is_reported(self):
        """The listing can say a model is there and the server still substitute."""
        self.endpoint.served_models = [served_identifier, vision_identifier]
        self.endpoint.answering_model = served_identifier
        with self.assertRaises(llm.LocalLlmError) as substituted:
            self.collected_answer(self.vision_client())
        self.assertIn(served_identifier, str(substituted.exception))
        self.assertIn(vision_identifier, str(substituted.exception))


class LoadOnDemandTest(LocalLlmTestCase):
    """The assistant's own model is loaded when a question needs it."""

    def setUp(self):
        super().setUp()
        self.load_requests = []
        self.saved_loader = model_loader.ensure_the_configured_model_is_loaded
        model_loader.ensure_the_configured_model_is_loaded = self.record_load
        self.addCleanup(self.restore_loader)

    def restore_loader(self):
        model_loader.ensure_the_configured_model_is_loaded = self.saved_loader

    def record_load(self, served_models):
        self.load_requests.append(served_models)
        self.endpoint.served_models = list(served_models) + [config.llm_model]
        return True

    def test_an_unloaded_model_is_loaded_rather_than_refused(self):
        self.endpoint.served_models = []
        answer, _ = self.collected_answer(llm.LocalLlm(model=config.llm_model))
        self.assertEqual("the answer", answer)
        self.assertEqual([[]], self.load_requests)

    def test_a_load_that_fails_is_reported_as_an_llm_error(self):
        def refuse_to_load(served_models):
            raise model_loader.ModelLoadError("out of memory")
        model_loader.ensure_the_configured_model_is_loaded = refuse_to_load
        with self.assertRaises(llm.LocalLlmError):
            self.collected_answer(llm.LocalLlm(model=config.llm_model))


if __name__ == "__main__":
    unittest.main()
