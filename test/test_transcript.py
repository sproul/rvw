"""Tests for the rolling in-memory transcript shared by all capture streams."""

import unittest

from rvw.transcript import RollingTranscript, TranscriptSegment

NOW = 2000.0


def segment_at(offset_seconds, text, stream="mic", duration=2.0):
    start = NOW - offset_seconds
    return TranscriptSegment(stream=stream, start_epoch=start,
                             end_epoch=start + duration, text=text)


class TranscriptSinkTest(unittest.TestCase):
    """Retention hangs off the one funnel every recognised utterance goes through.

    The rolling transcript knows nothing about files: it offers each utterance it
    accepted to a sink, and whether anything is written is the archive's decision.
    """

    def setUp(self):
        self.offered = []
        self.transcript = RollingTranscript(retention_seconds=600.0,
                                            on_segment_added=self.offered.append)

    def test_every_accepted_utterance_is_offered_to_the_sink(self):
        self.transcript.add(segment_at(10.0, "first thing"))
        self.transcript.add(segment_at(5.0, "second thing"))
        self.assertEqual(["first thing", "second thing"],
                         [segment.text for segment in self.offered])

    def test_a_blank_recognition_is_not_offered(self):
        self.transcript.add(segment_at(10.0, "   "))
        self.assertEqual([], self.offered)

    def test_an_utterance_from_an_unknown_stream_is_not_offered(self):
        with self.assertRaises(ValueError):
            self.transcript.add(segment_at(10.0, "hello", stream="telepathy"))
        self.assertEqual([], self.offered)


class RollingTranscriptTest(unittest.TestCase):

    def setUp(self):
        self.transcript = RollingTranscript(retention_seconds=600.0)

    def test_a_new_transcript_is_empty(self):
        self.assertEqual("", self.transcript.render_window(60.0, now=NOW))

    def test_blank_text_is_rejected(self):
        self.assertFalse(self.transcript.add(segment_at(10.0, "   ")))
        self.assertEqual("", self.transcript.render_window(60.0, now=NOW))

    def test_window_contains_only_recent_segments(self):
        self.transcript.add(segment_at(300.0, "ancient history"))
        self.transcript.add(segment_at(10.0, "recent remark"))
        rendered = self.transcript.render_window(60.0, now=NOW)
        self.assertIn("recent remark", rendered)
        self.assertNotIn("ancient history", rendered)

    def test_streams_are_interleaved_in_chronological_order(self):
        self.transcript.add(segment_at(30.0, "first thing", stream="system"))
        self.transcript.add(segment_at(20.0, "second thing", stream="mic"))
        self.transcript.add(segment_at(10.0, "third thing", stream="system"))
        rendered = self.transcript.render_window(60.0, now=NOW)
        self.assertLess(rendered.index("first thing"), rendered.index("second thing"))
        self.assertLess(rendered.index("second thing"), rendered.index("third thing"))

    def test_rendered_lines_identify_the_speaker_side_and_offset(self):
        self.transcript.add(segment_at(45.0, "what they said", stream="system"))
        self.transcript.add(segment_at(5.0, "what i said", stream="mic"))
        rendered = self.transcript.render_window(60.0, now=NOW)
        self.assertIn("them:", rendered)
        self.assertIn("me:", rendered)
        self.assertIn("[00:15]", rendered)

    def test_segments_older_than_the_retention_window_are_dropped(self):
        transcript = RollingTranscript(retention_seconds=60.0)
        transcript.add(segment_at(500.0, "long gone"))
        transcript.add(segment_at(1.0, "still here"))
        self.assertEqual(1, transcript.segment_count)

    def test_unknown_stream_names_are_rejected(self):
        with self.assertRaises(ValueError):
            self.transcript.add(segment_at(1.0, "hello", stream="telepathy"))


if __name__ == "__main__":
    unittest.main()
