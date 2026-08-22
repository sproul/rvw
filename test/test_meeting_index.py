"""Tests for the Phase 4 searchable meeting memory: the disposable FTS index.

The index is derived data. Everything it returns has to be reconstructable from
the canonical transcript.jsonl files alone, and the index file itself has to be
deletable at any moment and rebuildable from those transcripts. The tests below
assert exactly that: a rebuilt index over the same transcripts gives the same
answers, a deleted index comes back, and an ephemeral meeting that left no
canonical file contributes nothing because there is nothing of it to authorise.

A hit has to link back to the conversation it came from -- the meeting, its date,
the passage's own timestamp, the words themselves and any screenshot taken around
them -- because a search result nobody can trace back to its source is a rumour.
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

from rvw import config, meeting_archive, meeting_index
from rvw.transcript import TranscriptSegment

# Two meetings on two different days, so date and directory are distinguishable.
MORNING_EPOCH = time.mktime((2026, 8, 20, 9, 0, 0, 0, 0, -1))
EVENING_EPOCH = time.mktime((2026, 8, 21, 22, 30, 0, 0, 0, -1))


def segment_at(epoch, text, stream="system", duration=4.0):
    return TranscriptSegment(stream=stream, start_epoch=epoch,
                             end_epoch=epoch + duration, text=text)


class IndexTestCase(unittest.TestCase):
    """Every index below is built over a temporary archive of real transcripts."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.saved_archive_dir = config.archive_dir
        self.saved_index_db = config.index_db_path
        config.archive_dir = self.root / "meetings"
        config.index_db_path = self.root / "index" / "meetings.db"
        self.addCleanup(self.restore_configuration)

    def restore_configuration(self):
        config.archive_dir = self.saved_archive_dir
        config.index_db_path = self.saved_index_db
        self.temporary_directory.cleanup()

    def retain_meeting(self, started_epoch, utterances, stream="system"):
        """Write one retained meeting's canonical transcript and return its dir."""
        archive = meeting_archive.MeetingArchive(started_epoch, ["mic", "system"],
                                                 retention_mode="retained")
        for offset, text in utterances:
            archive.record_segment(segment_at(started_epoch + offset, text, stream=stream))
        archive.stop_retaining()
        return archive.directory

    def add_screenshot(self, meeting_dir, captured_epoch):
        """Drop a screenshot and its canonical sidecar into a meeting directory."""
        shots = meeting_dir / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d_%H.%M.%S", time.localtime(captured_epoch))
        image = shots / (stamp + ".png")
        image.write_bytes(b"not a real png")
        image.with_suffix(".json").write_text(
            json.dumps({"image": image.name, "captured_epoch": round(captured_epoch, 3)}),
            encoding="utf-8")
        return image

    def new_index(self):
        return meeting_index.MeetingIndex()

    def rebuilt_index(self):
        index = self.new_index()
        index.rebuild()
        return index


class RebuildTest(IndexTestCase):
    """Building the index over the canonical transcripts."""

    def test_rebuild_counts_the_meetings_and_the_utterances(self):
        self.retain_meeting(MORNING_EPOCH, [(5, "the coordinator lease timeout was thirty seconds"),
                                            (20, "the client should reconnect after that")])
        self.retain_meeting(EVENING_EPOCH, [(5, "we will deploy the read path on friday")])
        stats = self.new_index().rebuild()
        self.assertEqual(2, stats.meeting_count)
        self.assertEqual(3, stats.utterance_count)

    def test_an_ephemeral_meeting_leaves_nothing_to_index(self):
        """Ephemeral sessions write no transcript, so there is nothing to authorise."""
        meeting_archive.MeetingArchive(MORNING_EPOCH, ["system"], retention_mode="ephemeral")
        stats = self.new_index().rebuild()
        self.assertEqual(0, stats.meeting_count)

    def test_a_meeting_marked_not_to_index_is_excluded(self):
        excluded = self.retain_meeting(MORNING_EPOCH, [(5, "this one is private")])
        self._mark_meeting_excluded(excluded)
        self.retain_meeting(EVENING_EPOCH, [(5, "this one is fine to index")])
        stats = self.rebuilt_index_stats()
        self.assertEqual(1, stats.meeting_count)
        self.assertEqual([], self.rebuilt_index().search("private"))

    def _mark_meeting_excluded(self, meeting_dir):
        metadata_path = meeting_dir / meeting_archive.metadata_file_name
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["index"] = False
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    def rebuilt_index_stats(self):
        index = self.new_index()
        return index.rebuild()


class SearchTest(IndexTestCase):
    """Finding a passage and tracing it back to its conversation."""

    def setUp(self):
        super().setUp()
        self.morning = self.retain_meeting(
            MORNING_EPOCH, [(5, "the coordinator lease timeout was thirty seconds"),
                            (20, "the client should reconnect after a dropped connection")])
        self.evening = self.retain_meeting(
            EVENING_EPOCH, [(5, "we will deploy the read path on friday")])
        self.index = self.rebuilt_index()

    def test_a_term_finds_the_passage_that_contains_it(self):
        hits = self.index.search("reconnect")
        self.assertEqual(1, len(hits))
        self.assertIn("reconnect", hits[0].text)

    def test_a_hit_links_back_to_the_meeting_the_date_and_the_time(self):
        hit = self.index.search("lease timeout")[0]
        self.assertEqual(self.morning.name, hit.meeting)
        self.assertEqual("2026-08-20", hit.meeting_date)
        self.assertTrue(hit.start_local.startswith("2026-08-20T09:00:05"))
        self.assertEqual(self.morning, hit.meeting_dir)

    def test_a_hit_carries_the_speaker_and_the_verbatim_passage(self):
        hit = self.index.search("lease timeout")[0]
        self.assertEqual(config.stream_label("system"), hit.speaker)
        self.assertIn("thirty seconds", hit.text)

    def test_search_ranks_the_meeting_that_mentions_the_term_most(self):
        """A word said in two meetings returns the stronger match first."""
        self.retain_meeting(EVENING_EPOCH + 3600,
                            [(1, "reconnect reconnect reconnect after every reconnect")])
        index = self.rebuilt_index()
        hits = index.search("reconnect")
        self.assertGreaterEqual(len(hits), 2)
        self.assertIn("reconnect reconnect", hits[0].text)

    def test_a_query_with_no_searchable_words_finds_nothing_rather_than_raising(self):
        self.assertEqual([], self.index.search("!!! ???"))

    def test_punctuation_in_a_query_does_not_break_the_search(self):
        """User text reaches FTS, whose query syntax must not leak out as errors."""
        hits = self.index.search('"reconnect" (please)')
        self.assertIn("reconnect", hits[0].text)

    def test_a_missing_term_returns_no_hits(self):
        self.assertEqual([], self.index.search("kubernetes"))

    def test_the_result_count_is_capped(self):
        hits = self.index.search("the", limit=1)
        self.assertEqual(1, len(hits))


class ScreenshotAssociationTest(IndexTestCase):
    """A passage links to the screenshots taken around it in the same meeting."""

    def setUp(self):
        super().setUp()
        self.meeting = self.retain_meeting(
            MORNING_EPOCH, [(300, "look at this diagram of the read path")])

    def test_a_screenshot_taken_near_the_passage_is_associated(self):
        near = self.add_screenshot(self.meeting, MORNING_EPOCH + 305)
        hit = self.rebuilt_index().search("diagram")[0]
        self.assertIn(near, hit.screenshots)

    def test_a_screenshot_taken_far_from_the_passage_is_not_associated(self):
        self.add_screenshot(self.meeting, MORNING_EPOCH + 305 + config.screenshot_association_seconds * 3)
        hit = self.rebuilt_index().search("diagram")[0]
        self.assertEqual([], hit.screenshots)

    def test_a_passage_with_no_screenshots_has_an_empty_list(self):
        hit = self.rebuilt_index().search("diagram")[0]
        self.assertEqual([], hit.screenshots)


class DisposabilityTest(IndexTestCase):
    """The index file is derived data: deletable, and rebuildable from transcripts."""

    def setUp(self):
        super().setUp()
        self.retain_meeting(MORNING_EPOCH, [(5, "the coordinator lease timeout was thirty seconds")])

    def test_deleting_the_index_file_and_rebuilding_restores_the_answers(self):
        first = self.rebuilt_index().search("lease timeout")
        config.index_db_path.unlink()
        second = self.rebuilt_index().search("lease timeout")
        self.assertEqual([hit.text for hit in first], [hit.text for hit in second])

    def test_a_rebuild_reflects_transcripts_added_since_the_last_one(self):
        index = self.rebuilt_index()
        self.assertEqual([], index.search("friday"))
        self.retain_meeting(EVENING_EPOCH, [(5, "we will deploy on friday")])
        index.rebuild()
        self.assertEqual(1, len(index.search("friday")))

    def test_a_rebuild_replaces_rather_than_duplicates(self):
        index = self.rebuilt_index()
        index.rebuild()
        self.assertEqual(1, len(index.search("lease timeout")))


class DamagedTranscriptTest(IndexTestCase):
    """A canonical file we cannot read back is fatal, not silently skipped."""

    def test_a_transcript_line_that_is_not_json_stops_the_rebuild(self):
        meeting = self.retain_meeting(MORNING_EPOCH, [(5, "a good line")])
        with (meeting / meeting_archive.transcript_file_name).open("a", encoding="utf-8") as bad:
            bad.write("this is not json\n")
        with self.assertRaises(meeting_archive.MeetingArchiveError):
            self.new_index().rebuild()


if __name__ == "__main__":
    unittest.main()
