"""Local LLM access over the OpenAI compatible endpoint served by llmster.

Only chat completion is used, so the same class will serve a different local
model, or eventually a model running on the companion Mac, unchanged.
"""

import json
import logging
import urllib.error
import urllib.request

from . import config, model_loader

log = logging.getLogger(__name__)


class LocalLlmError(RuntimeError):
    pass


class LocalLlm:
    """Streaming chat client for the local OpenAI compatible server."""

    def __init__(self, base_url=config.llm_base_url, model=config.llm_model,
                 loads_on_demand=True):
        self._base_url = base_url.rstrip("/")
        self._model = model
        # Only the assistant's own model is loaded on demand. The vision model
        # is a different model under a different identifier, and guessing which
        # one to load for it would be worse than saying it is not there.
        self._loads_on_demand = loads_on_demand

    def available_models(self):
        try:
            with urllib.request.urlopen(self._base_url + "/models", timeout=10) as response:
                return [entry["id"] for entry in json.load(response).get("data", [])]
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise LocalLlmError("no local LLM at %s (%s)" % (self._base_url, error))

    def stream_chat(self, messages, on_token, on_reasoning=None):
        """Send a chat request and feed answer tokens to on_token as they arrive.

        Returns the answer text. Reasoning tokens are kept out of the answer and
        reported through on_reasoning, so a reasoning model does not spray its
        scratch work over the explanation.
        """
        self._require_the_requested_model_is_ready()
        request = self._build_request(messages)
        answer, reasoning = [], []
        with urllib.request.urlopen(request, timeout=config.llm_request_timeout_seconds) as response:
            for line in response:
                chunk = self._parse_stream_line(line)
                if chunk is None:
                    continue
                self._require_the_model_that_answered_is_the_one_asked(chunk)
                self._collect_delta(chunk.get("delta") or {}, answer, reasoning,
                                    on_token, on_reasoning)
        if not answer and reasoning:
            log.info("OK  the model answered from its reasoning channel only")
            return "".join(reasoning).strip()
        return "".join(answer).strip()

    def _require_the_requested_model_is_ready(self):
        """Either bring the model back, or refuse to let another one answer for it."""
        if self._loads_on_demand:
            self._load_the_model_if_it_has_been_unloaded()
            return
        self._require_the_model_is_being_served()

    # Measured against LM Studio on 2026-08-21: asked for an identifier it has
    # never heard of, this build neither refuses nor loads anything. It answers
    # with whatever model is loaded and names that substitute in the response, so
    # a screenshot sent to the vision model came back interpreted by the text
    # model and looked like a success. Trusting the endpoint to serve what it was
    # asked for is therefore not safe, and both of these guards exist because
    # either one alone can be defeated: the listing can be right and the routing
    # wrong, and a substitution can be noticed only once the answer arrives.
    def _require_the_model_is_being_served(self):
        served = self.available_models()
        if self._model in served:
            return
        raise LocalLlmError("no model is loaded as '%s', and this endpoint would answer with "
                            "%s instead of refusing, so nothing was asked of it"
                            % (self._model, ", ".join(served) or "nothing"))

    def _require_the_model_that_answered_is_the_one_asked(self, chunk):
        answering_model = chunk.get("model")
        if not answering_model or answering_model == self._model:
            return
        raise LocalLlmError("'%s' answered a question meant for '%s'; one model's answer must "
                            "never be passed off as another's" % (answering_model, self._model))

    def _load_the_model_if_it_has_been_unloaded(self):
        """LM Studio unloads an idle model on purpose, so the first question
        after a quiet hour has to wait for it to come back."""
        try:
            model_loader.ensure_the_configured_model_is_loaded(self.available_models())
        except model_loader.ModelLoadError as error:
            raise LocalLlmError("%s is not loaded and could not be loaded (%s)"
                                % (self._model, error))

    def _build_request(self, messages):
        payload = {"model": self._model, "messages": messages, "stream": True,
                   "temperature": config.llm_temperature, "max_tokens": config.llm_max_tokens}
        return urllib.request.Request(self._base_url + "/chat/completions",
                                      data=json.dumps(payload).encode("utf-8"),
                                      headers={"Content-Type": "application/json"})

    @staticmethod
    def _parse_stream_line(raw_line):
        """One streamed chunk: the model that is answering, and its next tokens."""
        line = raw_line.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            return None
        body = line[len("data:"):].strip()
        if not body or body == "[DONE]":
            return None
        parsed = json.loads(body)
        choices = parsed.get("choices") or [{}]
        return {"model": parsed.get("model"), "delta": choices[0].get("delta") or {}}

    @staticmethod
    def _collect_delta(delta, answer, reasoning, on_token, on_reasoning):
        token = delta.get("content")
        if token:
            answer.append(token)
            on_token(token)
        thought = delta.get("reasoning_content")
        if thought:
            reasoning.append(thought)
            if on_reasoning is not None:
                on_reasoning(thought)
