"""Tests for optional transcript retention: the Phase 3 meeting archive.

Retention is a decision about somebody's private conversation, so the property
that matters most is the negative one: in ephemeral mode nothing whatever
reaches the disk, and a session that was never asked to be kept leaves no trace
of itself. The tests below assert that first.

What is written when retention is on is canonical data, which fixes the rest of
the requirements. The JSONL is the only source of truth; transcript.md is
derived from it and rebuilt from it, so a rendering can never disagree with the
record it came from. Each line carries its own speaker label and local time, so
the file is readable years later without this repository. And the transcript
lands in the same session directory the screenshots already use, which is what
associates an image with the speech around it.
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

from rvw import config, meeting_archive, screenshot
from rvw.transcript import TranscriptSegment

SESSION_EPOCH = time.mktime((2026, 8, 15, 21, 30, 0, 0, 0, -1))
FIRST_EPOCH = time.mktime((2026, 8, 15, 21, 31, 5, 0, 0, -1))


def segment_at(epoch, text, stream="system", duration=4.0):
    return TranscriptSegment(stream=stream, start_epoch=epoch,
                             end_epoch=epoch + duration, text=text)


class MeetingArchiveTestCase(unittest.TestCase):
    """Every archive below writes into a temporary meetings root."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.saved_archive_dir = config.archive_dir
        config.archive_dir = self.root / "meetings"
        self.addCleanup(self.restore_configuration)
        self.archive = self.new_archive()

    def restore_configuration(self):
        config.archive_dir = self.saved_archive_dir
        self.temporary_directory.cleanup()

    def new_archive(self, retention_mode="ephemeral"):
        return meeting_archive.MeetingArchive(SESSION_EPOCH, ["mic", "system"],
                                              retention_mode=retention_mode)

    def record(self, count=1, text="the lease timeout was thirty seconds"):
        for index in range(count):
            self.archive.record_segment(segment_at(FIRST_EPOCH + index * 10,
                                                   "%s %d" % (text, index)))

    def written_files(self):
        return sorted(path.name for path in config.archive_dir.rglob("*") if path.is_file())

    def transcript_records(self):
        return meeting_archive.read_transcript_records(self.archive.transcript_path)

    def recorded_metadata(self):
        return json.loads(self.archive.metadata_path.read_text(encoding="utf-8"))


class EphemeralModeTest(MeetingArchiveTestCase):
    """The default: enough transcript for the assistant, and nothing kept."""

    def test_nothing_at_all_is_written(self):
        self.record(count=3)
        self.assertEqual([], self.written_files())

    def test_recording_a_segment_reports_that_it_was_not_kept(self):
        self.assertFalse(self.archive.record_segment(segment_at(FIRST_EPOCH, "unkept")))

    def test_the_state_is_reported_as_ephemeral(self):
        self.assertFalse(self.archive.is_retaining)
        self.assertIn("ephemeral", self.archive.describe_state())

    def test_ending_an_ephemeral_session_writes_nothing_either(self):
        self.record(count=2)
        self.archive.stop_retaining()
        self.assertEqual([], self.written_files())


class RetainedTranscriptTest(MeetingArchiveTestCase):
    """One JSON object per utterance, in the meeting's own directory."""

    def setUp(self):
        super().setUp()
        self.archive.start_retaining()

    def test_the_transcript_lands_in_the_dated_session_directory(self):
        relative = self.archive.transcript_path.relative_to(config.archive_dir)
        self.assertEqual(("2026", "08", "2026-08-15_21.30", "transcript.jsonl"),
                         relative.parts)

    def test_it_shares_the_directory_the_screenshots_use(self):
        """This is what associates an image with the speech around it."""
        image_path = screenshot.screenshot_image_path(SESSION_EPOCH, FIRST_EPOCH)
        self.assertEqual(self.archive.directory, image_path.parent.parent)

    def test_each_segment_is_appended_as_one_line_of_json(self):
        self.record(count=3)
        self.assertEqual(3, len(self.transcript_records()))

    def test_a_line_carries_the_words_the_speaker_and_the_time(self):
        self.record()
        recorded = self.transcript_records()[0]
        self.assertIn("lease timeout", recorded["text"])
        self.assertEqual("system", recorded["stream"])
        self.assertEqual(config.stream_label("system"), recorded["speaker"])
        self.assertAlmostEqual(FIRST_EPOCH, recorded["start_epoch"], places=3)
        self.assertTrue(recorded["start_local"].startswith("2026-08-15T21:31:05"))

    def test_a_recorded_segment_reports_that_it_was_kept(self):
        self.assertTrue(self.archive.record_segment(segment_at(FIRST_EPOCH, "kept")))

    def test_an_unknown_stream_is_a_fatal_error_rather_than_a_written_line(self):
        with self.assertRaises(ValueError):
            self.archive.record_segment(segment_at(FIRST_EPOCH, "telepathy", stream="mind"))
        self.assertEqual([], self.transcript_records())

    def test_nothing_more_is_written_after_retention_is_switched_off(self):
        self.record()
        self.archive.stop_retaining()
        self.assertFalse(self.archive.record_segment(segment_at(FIRST_EPOCH, "later")))
        self.assertEqual(1, len(self.transcript_records()))

    def test_switching_retention_on_again_appends_rather_than_truncating(self):
        self.record()
        self.archive.stop_retaining()
        self.archive.start_retaining()
        self.record()
        self.assertEqual(2, len(self.transcript_records()))


class MeetingMetadataTest(MeetingArchiveTestCase):
    """What the transcript cannot say about itself."""

    def setUp(self):
        super().setUp()
        self.archive.start_retaining()

    def test_metadata_is_written_as_soon_as_retention_starts(self):
        recorded = self.recorded_metadata()
        self.assertEqual(["mic", "system"], recorded["streams"])
        self.assertTrue(recorded["session_started_local"].startswith("2026-08-15T21:30:00"))

    def test_metadata_records_the_models_that_produced_the_transcript(self):
        recorded = self.recorded_metadata()
        self.assertEqual(config.whisper_model, recorded["whisper_model"])
        self.assertEqual(config.llm_model, recorded["llm_model"])

    def test_metadata_counts_the_segments_when_the_session_ends(self):
        self.record(count=4)
        self.archive.stop_retaining()
        recorded = self.recorded_metadata()
        self.assertEqual(4, recorded["segment_count"])
        self.assertIn("ended_local", recorded)


class MarkdownRenderingTest(MeetingArchiveTestCase):
    """A convenience, derived from the JSONL and rebuildable from it alone."""

    def setUp(self):
        super().setUp()
        self.archive.start_retaining()
        self.record(count=2)
        self.archive.stop_retaining()

    def rendered(self):
        return self.archive.markdown_path.read_text(encoding="utf-8")

    def test_the_rendering_is_written_when_the_session_ends(self):
        self.assertIn("lease timeout", self.rendered())

    def test_it_names_the_speaker_and_the_time_of_each_line(self):
        rendered = self.rendered()
        self.assertIn(config.stream_label("system"), rendered)
        self.assertIn("21:31:05", rendered)

    def test_it_says_which_file_is_canonical(self):
        """Derived data that does not say so gets edited by hand eventually."""
        self.assertIn(meeting_archive.transcript_file_name, self.rendered())

    def test_it_can_be_rebuilt_from_the_transcript_alone(self):
        first = self.rendered()
        self.archive.markdown_path.unlink()
        meeting_archive.write_markdown_rendering(self.archive.directory)
        self.assertEqual(first, self.rendered())

    def test_a_damaged_transcript_line_is_fatal_and_says_where_it_is(self):
        """A record we wrote ourselves and cannot read means the archive is
        damaged, and quietly skipping the line would hide that."""
        with self.archive.transcript_path.open("a", encoding="utf-8") as transcript:
            transcript.write("this is not json\n")
        with self.assertRaises(meeting_archive.MeetingArchiveError) as raised:
            meeting_archive.write_markdown_rendering(self.archive.directory)
        self.assertIn("line 3", str(raised.exception))


class RetentionModeTest(MeetingArchiveTestCase):
    """The default mode is configuration, so a typo in it has to be caught."""

    def test_an_archive_can_start_out_retaining(self):
        archive = self.new_archive(retention_mode="retained")
        self.assertTrue(archive.is_retaining)
        self.assertTrue(archive.metadata_path.exists())

    def test_an_unknown_retention_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.new_archive(retention_mode="probably")

    def test_the_configured_default_is_a_known_mode(self):
        config.require_known_retention_mode(config.transcript_retention_mode)


if __name__ == "__main__":
    unittest.main()
