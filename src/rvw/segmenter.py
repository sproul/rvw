"""Split a continuous PCM stream into utterance sized chunks.

Cutting on silence keeps every chunk independent, which avoids the duplicated
and contradictory text that overlapping fixed length recognition windows
produce. Timing is derived from the sample count rather than from wall clock
readings, so the offsets stay correct even when a block arrives late.
"""

from dataclasses import dataclass

import numpy as np

from . import config

analysis_frame_seconds = 0.05


@dataclass(frozen=True)
class AudioSegment:
    audio: np.ndarray
    start_epoch: float
    end_epoch: float

    @property
    def duration_seconds(self):
        return self.end_epoch - self.start_epoch


class SpeechSegmenter:
    """Emit an AudioSegment once speech is followed by enough silence."""

    def __init__(self, sample_rate=config.sample_rate,
                 silence_rms=config.silence_rms_threshold,
                 min_silence_seconds=config.min_silence_seconds,
                 min_speech_seconds=config.min_speech_seconds,
                 max_segment_seconds=config.max_segment_seconds,
                 min_peak_amplitude=config.min_segment_peak_amplitude):
        self._sample_rate = sample_rate
        self._silence_rms = silence_rms
        self._min_peak_amplitude = min_peak_amplitude
        self._min_silence_seconds = min_silence_seconds
        self._min_speech_seconds = min_speech_seconds
        self._max_segment_seconds = max_segment_seconds
        self._frame_samples = max(1, int(analysis_frame_seconds * sample_rate))
        self._clock_origin = None
        self._samples_consumed = 0
        self._carry = np.zeros(0, dtype=np.float32)
        self._reset_utterance()

    def _reset_utterance(self):
        self._utterance_frames = []
        self._utterance_start_sample = None
        self._trailing_silence_seconds = 0.0

    def feed(self, samples, arrival_epoch):
        """Consume one block of mono float32 audio and return finished segments."""
        if self._clock_origin is None:
            self._clock_origin = arrival_epoch
        self._carry = np.concatenate([self._carry, np.asarray(samples, dtype=np.float32)])
        finished = []
        while self._carry.size >= self._frame_samples:
            frame = self._carry[:self._frame_samples]
            self._carry = self._carry[self._frame_samples:]
            finished.extend(self._consume_frame(frame))
        return finished

    def flush(self):
        """Emit speech that never saw its trailing silence, at end of capture."""
        return self._close_utterance()

    def _consume_frame(self, frame):
        frame_start_sample = self._samples_consumed
        self._samples_consumed += frame.size
        if self._is_speech(frame):
            self._append_speech_frame(frame, frame_start_sample)
        elif self._utterance_start_sample is not None:
            self._utterance_frames.append(frame)
            self._trailing_silence_seconds += analysis_frame_seconds
            if self._trailing_silence_seconds >= self._min_silence_seconds:
                return self._close_utterance()
        if self._utterance_is_over_length():
            return self._close_utterance()
        return []

    def _is_speech(self, frame):
        return float(np.sqrt(np.mean(np.square(frame, dtype=np.float64)))) >= self._silence_rms

    def _append_speech_frame(self, frame, frame_start_sample):
        if self._utterance_start_sample is None:
            self._utterance_start_sample = frame_start_sample
        self._utterance_frames.append(frame)
        self._trailing_silence_seconds = 0.0

    def _utterance_is_over_length(self):
        if self._utterance_start_sample is None:
            return False
        collected_samples = sum(frame.size for frame in self._utterance_frames)
        return collected_samples / self._sample_rate >= self._max_segment_seconds

    def _close_utterance(self):
        if self._utterance_start_sample is None:
            return []
        audio = np.concatenate(self._utterance_frames)
        start_sample = self._utterance_start_sample
        speech_seconds = audio.size / self._sample_rate - self._trailing_silence_seconds
        self._reset_utterance()
        if speech_seconds < self._min_speech_seconds or self._is_too_quiet(audio):
            return []
        return [self._make_segment(audio, start_sample)]

    def _is_too_quiet(self, audio):
        """Speech peaks well above its own average; steady faint noise does not."""
        return float(np.abs(audio).max()) < self._min_peak_amplitude

    def _make_segment(self, audio, start_sample):
        start_epoch = self._clock_origin + start_sample / self._sample_rate
        return AudioSegment(audio=audio, start_epoch=start_epoch,
                            end_epoch=start_epoch + audio.size / self._sample_rate)
