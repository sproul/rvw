"""Tests for the silence based speech segmenter.

The segmenter turns a continuous stream of PCM frames into utterance sized
chunks so that the speech recogniser never has to deal with overlapping
windows or duplicated text.
"""

import unittest

import numpy as np

from rvw.segmenter import SpeechSegmenter

SAMPLE_RATE = 16000
STREAM_START_EPOCH = 1000.0


def make_tone(seconds, amplitude=0.3):
    sample_count = int(seconds * SAMPLE_RATE)
    times = np.arange(sample_count, dtype=np.float32) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * 440.0 * times)).astype(np.float32)


def make_silence(seconds):
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


def make_segmenter(**overrides):
    settings = dict(sample_rate=SAMPLE_RATE, min_silence_seconds=0.5,
                    min_speech_seconds=0.5, max_segment_seconds=5.0)
    settings.update(overrides)
    return SpeechSegmenter(**settings)


def feed_all(segmenter, blocks):
    """Feed every block and return the concatenated list of emitted segments."""
    emitted = []
    for block in blocks:
        emitted.extend(segmenter.feed(block, STREAM_START_EPOCH))
    return emitted


class SpeechSegmenterTest(unittest.TestCase):

    def test_silence_alone_produces_no_segments(self):
        segmenter = make_segmenter()
        self.assertEqual([], feed_all(segmenter, [make_silence(3.0)]))

    def test_speech_followed_by_silence_produces_one_segment(self):
        segmenter = make_segmenter()
        segments = feed_all(segmenter, [make_silence(1.0), make_tone(2.0), make_silence(1.0)])
        self.assertEqual(1, len(segments))
        self.assertAlmostEqual(STREAM_START_EPOCH + 1.0, segments[0].start_epoch, delta=0.2)
        self.assertAlmostEqual(STREAM_START_EPOCH + 3.0, segments[0].end_epoch, delta=0.6)

    def test_two_utterances_separated_by_silence_produce_two_segments(self):
        segmenter = make_segmenter()
        blocks = [make_tone(1.0), make_silence(1.0), make_tone(1.0), make_silence(1.0)]
        self.assertEqual(2, len(feed_all(segmenter, blocks)))

    def test_steady_low_level_noise_is_discarded(self):
        # Whisper invents phrases such as "Thank you." when handed near silence,
        # so a segment whose loudest sample is still quiet must never reach it.
        segmenter = make_segmenter(silence_rms=0.001)
        quiet = feed_all(segmenter, [make_tone(2.0, amplitude=0.004), make_silence(1.0)])
        self.assertEqual([], quiet)

    def test_short_blip_is_discarded_as_noise(self):
        segmenter = make_segmenter(min_speech_seconds=1.0)
        self.assertEqual([], feed_all(segmenter, [make_tone(0.2), make_silence(1.0)]))

    def test_uninterrupted_speech_is_split_at_the_maximum_length(self):
        segmenter = make_segmenter(max_segment_seconds=2.0)
        segments = feed_all(segmenter, [make_tone(7.0)])
        self.assertGreaterEqual(len(segments), 3)
        for segment in segments:
            self.assertLessEqual(segment.duration_seconds, 2.2)

    def test_segment_audio_is_returned_for_recognition(self):
        segmenter = make_segmenter()
        segments = feed_all(segmenter, [make_tone(1.5), make_silence(1.0)])
        self.assertEqual(1, len(segments))
        self.assertIsInstance(segments[0].audio, np.ndarray)
        self.assertGreater(segments[0].audio.size, SAMPLE_RATE)

    def test_block_sizes_do_not_change_the_result(self):
        odd_sized_blocks = np.array_split(np.concatenate([make_tone(1.5), make_silence(1.0)]), 37)
        self.assertEqual(1, len(feed_all(make_segmenter(), odd_sized_blocks)))

    def test_flush_emits_speech_that_never_saw_trailing_silence(self):
        segmenter = make_segmenter()
        self.assertEqual([], feed_all(segmenter, [make_tone(1.5)]))
        self.assertEqual(1, len(segmenter.flush()))
        self.assertEqual([], segmenter.flush())


if __name__ == "__main__":
    unittest.main()
