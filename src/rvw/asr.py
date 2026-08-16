"""Local speech recognition, currently mlx-whisper on Apple Silicon.

Callers see only transcribe(); the backend can be replaced without touching
audio capture or the transcript.
"""

import logging
import threading
import time

import numpy as np

from . import config

log = logging.getLogger(__name__)


class WhisperTranscriber:
    """Recognise one utterance at a time; the GPU is a single shared resource."""

    def __init__(self, model=config.whisper_model, language=config.whisper_language):
        self._model = model
        self._language = language
        self._lock = threading.Lock()

    def warm_up(self):
        """Pay the model load cost before the first real utterance arrives."""
        started = time.monotonic()
        self.transcribe(np.zeros(config.sample_rate, dtype=np.float32))
        log.info("OK  speech model %s ready in %.1fs", self._model, time.monotonic() - started)

    def transcribe(self, audio):
        import mlx_whisper                       # imported late: loading mlx costs a second
        with self._lock:
            result = mlx_whisper.transcribe(audio, path_or_hf_repo=self._model,
                                            language=self._language, fp16=True)
        return result["text"].strip()
