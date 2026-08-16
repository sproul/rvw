"""Run one audio_capture helper and turn its PCM stream into utterances.

Capture lives in a separate process on purpose: the helper owns the Core Audio
tap and the microphone permission, and the assistant only ever sees mono
float32 samples, whatever the hardware was doing.
"""

import logging
import subprocess
import threading
import time

import numpy as np

from . import config
from .segmenter import SpeechSegmenter

log = logging.getLogger(__name__)

bytes_per_sample = 4
start_up_grace_seconds = 0.6


class CaptureStream:
    """A named capture stream: spawn the helper, segment it, report utterances."""

    def __init__(self, stream_name, on_segment):
        config.require_known_stream(stream_name)
        self._stream_name = stream_name
        self._on_segment = on_segment
        self._process = None
        self._started_at = 0.0
        self._threads = []
        self._stopping = threading.Event()

    @property
    def stream_name(self):
        return self._stream_name

    @property
    def is_running(self):
        return self._process is not None and self._process.poll() is None

    def start(self):
        if self.is_running:
            return False
        if not config.capture_helper_path.exists():
            raise RuntimeError("missing capture helper %s; run helper/build.sh"
                               % config.capture_helper_path)
        self._stopping.clear()
        self._started_at = time.time()
        self._process = subprocess.Popen(
            [str(config.capture_helper_path), "--source", self._stream_name],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self._threads = [self._spawn(self._read_audio, "rvw-audio-" + self._stream_name),
                         self._spawn(self._relay_helper_log, "rvw-log-" + self._stream_name)]
        self._require_helper_survived_start_up()
        return True

    def _require_helper_survived_start_up(self):
        """A helper denied its permission exits at once; never report that as success."""
        try:
            self._process.wait(timeout=start_up_grace_seconds)
        except subprocess.TimeoutExpired:
            return
        self._stopping.set()                   # the reason is already in the relayed log
        raise RuntimeError("the %s capture helper exited immediately with status %d; "
                           "see the session log for the reason"
                           % (self._stream_name, self._process.returncode))

    def stop(self):
        if not self.is_running:
            return False
        self._stopping.set()
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)
        for thread in self._threads:
            thread.join(timeout=2)
        log.info("OK  stopped the %s capture stream", self._stream_name)
        return True

    def _spawn(self, target, name):
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        return thread

    def _read_audio(self):
        segmenter = SpeechSegmenter()
        block_bytes = int(config.capture_read_seconds * config.sample_rate) * bytes_per_sample
        while not self._stopping.is_set():
            block = self._process.stdout.read(block_bytes)
            if not block:
                break
            samples = np.frombuffer(block, dtype=np.float32)
            block_start_epoch = time.time() - samples.size / config.sample_rate
            self._emit_segments(segmenter.feed(samples, block_start_epoch))
        self._emit_segments(segmenter.flush())
        self._report_unexpected_exit()

    def _emit_segments(self, segments):
        for segment in segments:
            self._on_segment(self._stream_name, segment)

    def _report_unexpected_exit(self):
        """A death during start-up is already reported, with its exit status, by start()."""
        if self._stopping.is_set() or time.time() - self._started_at < start_up_grace_seconds:
            return
        log.error("FAIL the %s capture helper exited unexpectedly", self._stream_name)

    def _relay_helper_log(self):
        for raw_line in self._process.stderr:
            line = raw_line.decode("utf-8", "replace").rstrip()
            if line:
                log.info("[%s helper] %s", self._stream_name, line)
