"""The files one meeting leaves behind, and the choice of whether to leave any.

A session is either ephemeral or retained. Ephemeral is the default and writes
nothing: the rolling transcript in memory is all that exists and it is discarded
as it ages. Retained appends every recognised utterance to

    <archive>/YYYY/MM/YYYY-MM-DD_HH.MM/transcript.jsonl
                                      /metadata.json
                                      /transcript.md
                                      /screenshots/...

which is the directory the screenshots of that session already go into, so an
image is associated with the speech around it by where it sits and by its own
timestamp rather than by an index that could be lost.

transcript.jsonl is canonical: one JSON object per utterance, appended and never
rewritten, each line carrying its own local time and speaker label so the file
can be read years from now without this repository. transcript.md is derived
from it and rebuilt from it, so it can be deleted at any time and can never
disagree with the record. Nothing here is a database, by design: an index over
these files belongs in Phase 4 and has to be disposable.

Switching retention on part way through a session records the speech from that
moment on, and not the speech already in the rolling window. What was said while
the session was ephemeral was said in confidence.
"""

import json
import logging
import socket
import threading
import time
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

session_directory_format = "%Y-%m-%d_%H.%M"
local_time_format = "%Y-%m-%dT%H:%M:%S"

transcript_file_name = "transcript.jsonl"
metadata_file_name = "metadata.json"
markdown_file_name = "transcript.md"

required_record_keys = ("start_epoch", "start_local", "speaker", "stream", "text")


class MeetingArchiveError(RuntimeError):
    """The archive says something we wrote is unreadable, which is never routine."""


def session_archive_dir(session_started_epoch):
    """Directory holding everything archived from one assistant session."""
    started = time.localtime(session_started_epoch)
    return (config.archive_dir / time.strftime("%Y", started) / time.strftime("%m", started)
            / time.strftime(session_directory_format, started))


def local_time_text(epoch):
    """Wall clock time of an epoch, in the form the archive records it."""
    return time.strftime(local_time_format, time.localtime(epoch))


def transcript_record(segment):
    """One utterance as the canonical file stores it."""
    return {"stream": segment.stream,
            "speaker": config.stream_label(segment.stream),
            "start_epoch": round(segment.start_epoch, 3),
            "end_epoch": round(segment.end_epoch, 3),
            "start_local": local_time_text(segment.start_epoch),
            "text": segment.text.strip()}


def require_recordable_segment(segment):
    """Refuse to archive an utterance the archive could not honestly describe."""
    config.require_known_stream(segment.stream)
    if segment.end_epoch < segment.start_epoch:
        raise ValueError("segment ends %.3fs before it starts"
                         % (segment.start_epoch - segment.end_epoch))
    if not segment.text.strip():
        raise ValueError("refusing to archive an utterance with no words in it")


def read_transcript_records(transcript_path):
    """Every utterance in a canonical transcript, oldest first.

    A line we wrote and cannot read back means the archive is damaged, so it
    stops everything here rather than being skipped: a rendering silently short
    of one line would be believed.
    """
    path = Path(transcript_path)
    if not path.exists():
        return []
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            records.append(_parsed_record(line, path, line_number))
    return records


def _parsed_record(line, path, line_number):
    try:
        record = json.loads(line)
    except ValueError as error:
        raise MeetingArchiveError("%s line %d is not JSON (%s)" % (path, line_number, error))
    if not isinstance(record, dict):
        raise MeetingArchiveError("%s line %d is not a transcript record" % (path, line_number))
    missing = [key for key in required_record_keys if key not in record]
    if missing:
        raise MeetingArchiveError("%s line %d has no %s"
                                  % (path, line_number, ", ".join(missing)))
    return record


def write_markdown_rendering(meeting_dir):
    """Render the canonical transcript as Markdown and return the file written."""
    directory = Path(meeting_dir)
    records = read_transcript_records(directory / transcript_file_name)
    markdown_path = directory / markdown_file_name
    markdown_path.write_text(_markdown_text(directory.name, records), encoding="utf-8")
    return markdown_path


def _markdown_text(meeting_name, records):
    """One utterance per paragraph, because consecutive Markdown lines are one."""
    heading = "# Meeting %s\n\nRendered from %s, which is the canonical record; this file is " \
              "derived and is rewritten from it.\n" % (meeting_name, transcript_file_name)
    if not records:
        return heading + "\nNo speech was recorded while the transcript was retained.\n"
    return heading + "".join("\n%s\n" % _markdown_line(record) for record in records)


def _markdown_line(record):
    return "**%s %s:** %s" % (record["start_local"].split("T")[-1], record["speaker"],
                              record["text"])


class MeetingArchive:
    """One session's canonical files, written only while retention is on.

    Retention is switched from the control socket while the recogniser is
    appending, so the mode, the counter and the file are guarded together: the
    utterance being written when retention stops is either in the file and
    counted or in neither.
    """

    def __init__(self, session_started_epoch, stream_names, retention_mode=None):
        mode = config.transcript_retention_mode if retention_mode is None else retention_mode
        config.require_known_retention_mode(mode)
        self._session_started_epoch = session_started_epoch
        self._stream_names = list(stream_names)
        self._segment_count = 0
        self._retaining = False
        self._lock = threading.Lock()
        if mode == "retained":
            self.start_retaining()

    @property
    def directory(self):
        return session_archive_dir(self._session_started_epoch)

    @property
    def transcript_path(self):
        return self.directory / transcript_file_name

    @property
    def metadata_path(self):
        return self.directory / metadata_file_name

    @property
    def markdown_path(self):
        return self.directory / markdown_file_name

    @property
    def is_retaining(self):
        with self._lock:
            return self._retaining

    # -- switching the state ----------------------------------------------

    def start_retaining(self):
        """Begin keeping the transcript, from this utterance onwards."""
        with self._lock:
            if self._retaining:
                return "already retaining the transcript in %s" % self.transcript_path
            self.directory.mkdir(parents=True, exist_ok=True)
            self._retaining = True
            self._write_metadata()
        log.info("OK  retaining the transcript in %s", self.transcript_path)
        return "retaining the transcript in %s" % self.transcript_path

    def stop_retaining(self):
        """Stop keeping the transcript and render what was kept.

        What is already written stays written: this is a decision about what
        happens next, not a way to unsay anything.
        """
        with self._lock:
            if not self._retaining:
                return self._ephemeral_state_description()
            self._retaining = False
            kept = self._segment_count
            self._write_metadata(ended_epoch=time.time())
        write_markdown_rendering(self.directory)
        log.info("OK  stopped retaining; %d utterance(s) kept in %s", kept, self.directory)
        return "stopped retaining; %d utterance(s) kept in %s" % (kept, self.directory)

    def toggle_retention(self):
        """One command for a hotkey, which cannot know the current state."""
        if self.is_retaining:
            return self.stop_retaining()
        return self.start_retaining()

    def describe_state(self):
        """One clause for the STATUS reply."""
        with self._lock:
            if not self._retaining:
                return self._ephemeral_state_description()
            return "retained (%d utterance(s) in %s)" % (self._segment_count, self.directory)

    @staticmethod
    def _ephemeral_state_description():
        return "ephemeral (the transcript is not being kept)"

    # -- writing ------------------------------------------------------------

    def record_segment(self, segment):
        """Append one recognised utterance; returns whether it was kept."""
        require_recordable_segment(segment)
        with self._lock:
            if not self._retaining:
                return False
            try:
                self._append(transcript_record(segment))
            except OSError as error:
                self._abandon_retention_after(error)
                return False
            self._segment_count += 1
            return True

    def _append(self, record):
        """Opened and closed per utterance, so a crash leaves a complete file.

        Utterances arrive every few seconds, which makes the cost of this
        irrelevant next to what it buys.
        """
        with self.transcript_path.open("a", encoding="utf-8") as transcript:
            transcript.write(json.dumps(record, sort_keys=True) + "\n")

    def _abandon_retention_after(self, error):
        """A disk that cannot be written to will not improve within the session,
        and one report of it is worth more than one per utterance."""
        self._retaining = False
        log.error("FAIL cannot write %s (%s); the transcript is no longer being kept",
                  self.transcript_path, error)

    def _write_metadata(self, ended_epoch=None):
        self.metadata_path.write_text(
            json.dumps(self._metadata(ended_epoch), indent=2, sort_keys=True) + "\n",
            encoding="utf-8")

    def _metadata(self, ended_epoch=None):
        """What the transcript cannot say about itself."""
        recorded = {
            "host": socket.gethostname(),
            "llm_model": config.llm_model,
            "segment_count": self._segment_count,
            "session_started_epoch": round(self._session_started_epoch, 3),
            "session_started_local": local_time_text(self._session_started_epoch),
            "stream_labels": {name: config.stream_label(name) for name in self._stream_names},
            "streams": self._stream_names,
            "transcript": transcript_file_name,
            "whisper_model": config.whisper_model,
        }
        if ended_epoch is not None:
            recorded["ended_local"] = local_time_text(ended_epoch)
        return recorded
