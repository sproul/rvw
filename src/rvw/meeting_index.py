"""A disposable full text index over the canonical meeting transcripts.

The transcripts written by rvw/meeting_archive.py are the record; this is an
index over them and nothing more. It is derived data: it can be deleted at any
moment and rebuilt from the transcripts alone, and principle 7 of the
specification forbids ever synchronising it between Macs -- the transcripts are
synchronised and each Mac rebuilds its own index. Nothing here is canonical.

The index is SQLite FTS5, which ships with Python, ranks by BM25 and needs no
new dependency. One row is one utterance, carrying the words for searching and,
unindexed beside them, everything a hit needs to point back at the conversation
it came from: the meeting, its date, the utterance's own timestamp, the speaker,
and the directory that also holds the screenshots taken around it.

Only retained meetings are indexed, because an ephemeral session left no
transcript to authorise. A meeting kept but not for searching can opt out with
"index": false in its metadata.json, which is honoured here.
"""

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import config, meeting_archive

log = logging.getLogger(__name__)

table_name = "utterances"

# The words are indexed; everything else is stored beside them to trace a hit
# back to its source, and is declared UNINDEXED so FTS never tokenises it.
_unindexed_columns = ("meeting", "meeting_date", "start_epoch", "start_local",
                      "speaker", "stream", "meeting_dir")


@dataclass(frozen=True)
class Hit:
    """One matched utterance and the way back to the conversation it is from."""
    meeting: str
    meeting_date: str
    start_epoch: float
    start_local: str
    speaker: str
    stream: str
    text: str
    meeting_dir: Path
    screenshots: list = field(default_factory=list)


@dataclass(frozen=True)
class IndexStats:
    """What one rebuild covered, for the caller that asked for it."""
    meeting_count: int
    utterance_count: int


def authorized_meetings(archive_root=None):
    """Retained meetings cleared for indexing, oldest first.

    A meeting is a directory holding a canonical transcript; it is authorised
    unless its metadata.json opts out. Ephemeral sessions wrote no transcript
    and so are absent here rather than excluded.
    """
    root = Path(config.archive_dir if archive_root is None else archive_root)
    if not root.exists():
        return []
    meetings = [path.parent for path in root.rglob(meeting_archive.transcript_file_name)]
    return sorted(meeting for meeting in meetings if _meeting_allows_indexing(meeting))


def _meeting_allows_indexing(meeting_dir):
    """Honour an explicit opt out in a kept meeting's metadata."""
    metadata_path = meeting_dir / meeting_archive.metadata_file_name
    if not metadata_path.exists():
        return True
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    return metadata.get("index", True) is not False


def _fts_match_query(user_text):
    """A safe FTS5 MATCH string from arbitrary user text, or None if it has no words.

    Every word is quoted so that punctuation the user typed is searched for
    rather than interpreted as FTS query syntax, which would surface as an error.
    The words are joined with OR, so a passage need not contain every one to be a
    hit; BM25 then ranks a passage that matches more of them above one that
    matches fewer, which is recall without losing the ordering precision buys.
    """
    words = re.findall(r"\w+", user_text, flags=re.UNICODE)
    if not words:
        return None
    return " OR ".join('"%s"' % word for word in words)


class MeetingIndex:
    """A rebuildable FTS index over one archive of transcripts.

    Each public call opens and closes its own connection, so the control thread
    that searches and the thread that rebuilds never share one; the index is
    small enough that the cost is irrelevant.
    """

    def __init__(self, db_path=None, archive_root=None):
        self._db_path = Path(config.index_db_path if db_path is None else db_path)
        self._archive_root = archive_root

    # -- building ----------------------------------------------------------

    def rebuild(self):
        """Discard the index and build it again from the canonical transcripts."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._db_path)
        try:
            self._create_empty_table(connection)
            stats = self._insert_every_authorized_meeting(connection)
            connection.commit()
        finally:
            connection.close()
        log.info("OK  indexed %d utterance(s) from %d meeting(s) into %s",
                 stats.utterance_count, stats.meeting_count, self._db_path)
        return stats

    @staticmethod
    def _create_empty_table(connection):
        columns = ", ".join(("text",) + tuple("%s UNINDEXED" % name
                                               for name in _unindexed_columns))
        connection.execute("DROP TABLE IF EXISTS %s" % table_name)
        connection.execute("CREATE VIRTUAL TABLE %s USING fts5(%s)" % (table_name, columns))

    def _insert_every_authorized_meeting(self, connection):
        utterances = 0
        meetings = authorized_meetings(self._archive_root)
        for meeting in meetings:
            utterances += self._insert_one_meeting(connection, meeting)
        return IndexStats(meeting_count=len(meetings), utterance_count=utterances)

    def _insert_one_meeting(self, connection, meeting_dir):
        """A transcript line we cannot read back is fatal, by read_transcript_records."""
        records = meeting_archive.read_transcript_records(
            meeting_dir / meeting_archive.transcript_file_name)
        rows = [self._row_for_record(meeting_dir, record) for record in records]
        connection.executemany(
            "INSERT INTO %s (text, %s) VALUES (%s)"
            % (table_name, ", ".join(_unindexed_columns),
               ", ".join(["?"] * (1 + len(_unindexed_columns)))), rows)
        return len(rows)

    @staticmethod
    def _row_for_record(meeting_dir, record):
        return (record["text"], meeting_dir.name, meeting_dir.name[:10],
                str(record["start_epoch"]), record["start_local"], record["speaker"],
                record["stream"], str(meeting_dir))

    # -- searching ---------------------------------------------------------

    def search(self, query, limit=None):
        """Utterances matching the query, most relevant first, each traceable back."""
        match = _fts_match_query(query)
        if match is None or not self._db_path.exists():
            return []
        rows = self._matching_rows(match, config.search_result_limit if limit is None else limit)
        screenshots_by_meeting = {}
        return [self._hit_from_row(row, screenshots_by_meeting) for row in rows]

    def _matching_rows(self, match, limit):
        connection = sqlite3.connect(self._db_path)
        try:
            if not self._table_present(connection):
                return []
            selected = "text, " + ", ".join(_unindexed_columns)
            cursor = connection.execute(
                "SELECT %s FROM %s WHERE %s MATCH ? ORDER BY bm25(%s) LIMIT ?"
                % (selected, table_name, table_name, table_name), (match, limit))
            return cursor.fetchall()
        finally:
            connection.close()

    @staticmethod
    def _table_present(connection):
        found = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)).fetchone()
        return found is not None

    def _hit_from_row(self, row, screenshots_by_meeting):
        text, meeting, meeting_date, start_epoch, start_local, speaker, stream, meeting_dir = row
        directory = Path(meeting_dir)
        start = float(start_epoch)
        return Hit(meeting=meeting, meeting_date=meeting_date, start_epoch=start,
                   start_local=start_local, speaker=speaker, stream=stream, text=text,
                   meeting_dir=directory,
                   screenshots=self._screenshots_near(directory, start, screenshots_by_meeting))

    def _screenshots_near(self, meeting_dir, start_epoch, screenshots_by_meeting):
        """Images captured within the association window of this passage."""
        if meeting_dir not in screenshots_by_meeting:
            screenshots_by_meeting[meeting_dir] = _screenshot_times(meeting_dir)
        window = config.screenshot_association_seconds
        return [image for captured, image in screenshots_by_meeting[meeting_dir]
                if abs(captured - start_epoch) <= window]


def _screenshot_times(meeting_dir):
    """(captured_epoch, image_path) for every screenshot archived in a meeting."""
    shots_dir = meeting_dir / "screenshots"
    if not shots_dir.exists():
        return []
    return [pair for pair in (_screenshot_time(sidecar)
                              for sidecar in sorted(shots_dir.glob("*.json")))
            if pair is not None]


def _screenshot_time(sidecar_path):
    """When a screenshot was taken, from its canonical sidecar, or None if unusable.

    A damaged sidecar loses the association of that one image, which is a
    convenience, so it is logged and skipped rather than made to fail a search.
    """
    try:
        metadata = json.loads(sidecar_path.read_text(encoding="utf-8"))
        return float(metadata["captured_epoch"]), sidecar_path.with_suffix(".png")
    except (ValueError, KeyError, OSError) as error:
        log.error("FAIL cannot read screenshot time from %s (%s)", sidecar_path, error)
        return None
