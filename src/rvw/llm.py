"""Local LLM access over the OpenAI compatible endpoint served by llmster.

Only chat completion is used, so the same class will serve a different local
model, or eventually a model running on the companion Mac, unchanged.
"""

import json
import logging
import urllib.error
import urllib.request

from . import config

log = logging.getLogger(__name__)


class LocalLlmError(RuntimeError):
    pass


class LocalLlm:
    """Streaming chat client for the local OpenAI compatible server."""

    def __init__(self, base_url=config.llm_base_url, model=config.llm_model):
        self._base_url = base_url.rstrip("/")
        self._model = model

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
        request = self._build_request(messages)
        answer, reasoning = [], []
        with urllib.request.urlopen(request, timeout=config.llm_request_timeout_seconds) as response:
            for line in response:
                delta = self._parse_stream_line(line)
                if delta is None:
                    continue
                self._collect_delta(delta, answer, reasoning, on_token, on_reasoning)
        if not answer and reasoning:
            log.info("OK  the model answered from its reasoning channel only")
            return "".join(reasoning).strip()
        return "".join(answer).strip()

    def _build_request(self, messages):
        payload = {"model": self._model, "messages": messages, "stream": True,
                   "temperature": config.llm_temperature, "max_tokens": config.llm_max_tokens}
        return urllib.request.Request(self._base_url + "/chat/completions",
                                      data=json.dumps(payload).encode("utf-8"),
                                      headers={"Content-Type": "application/json"})

    @staticmethod
    def _parse_stream_line(raw_line):
        line = raw_line.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            return None
        body = line[len("data:"):].strip()
        if not body or body == "[DONE]":
            return None
        choices = json.loads(body).get("choices") or [{}]
        return choices[0].get("delta") or {}

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
