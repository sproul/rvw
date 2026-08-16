"""Background recognition worker.

Capture threads must never block on the speech model, so finished utterances
are queued and recognised by a single worker; the model is a shared resource
and recognising two streams at once would only make both slower.
"""

import logging
import queue
import threading
import time

from . import config
from .transcript import TranscriptSegment

log = logging.getLogger(__name__)

max_pending_segments = 32


class RecognitionWorker:
    """Turn queued audio segments into transcript entries."""

    def __init__(self, transcriber, transcript):
        self._transcriber = transcriber
        self._transcript = transcript
        self._pending = queue.Queue(maxsize=max_pending_segments)
        self._thread = None
        self._stopping = threading.Event()

    def start(self):
        self._stopping.clear()
        self._thread = threading.Thread(target=self._work, name="rvw-recognizer", daemon=True)
        self._thread.start()

    def stop(self):
        self._stopping.set()
        self._pending.put(None)
        if self._thread is not None:
            self._thread.join(timeout=30)      # let the utterance in flight finish

    def submit(self, stream_name, audio_segment):
        """Called from a capture thread; drops audio rather than stalling capture."""
        try:
            self._pending.put_nowait((stream_name, audio_segment))
        except queue.Full:
            log.error("FAIL recognition backlog is full, dropped %.1fs of %s audio",
                      audio_segment.duration_seconds, stream_name)

    def _work(self):
        while not self._stopping.is_set():
            item = self._pending.get()
            if item is None:
                return
            self._recognize(*item)

    def _recognize(self, stream_name, audio_segment):
        started = time.monotonic()
        try:
            text = self._transcriber.transcribe(audio_segment.audio)
        except Exception as error:
            log.error("FAIL recognition of %s audio: %s", stream_name, error)
            return
        self._store(stream_name, audio_segment, text, time.monotonic() - started)

    def _store(self, stream_name, audio_segment, text, elapsed_seconds):
        segment = TranscriptSegment(stream=stream_name, start_epoch=audio_segment.start_epoch,
                                    end_epoch=audio_segment.end_epoch, text=text)
        if not self._transcript.add(segment):
            return
        log.info("%s: %s", config.stream_label(stream_name), text)
        log.debug("OK  recognised %.1fs of %s audio in %.1fs",
                  audio_segment.duration_seconds, stream_name, elapsed_seconds)
