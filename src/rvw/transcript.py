"""Rolling in-memory transcript shared by every capture stream.

Every recognised utterance passes through add(), which makes it the one place
retention can hang off: an accepted utterance is offered to the on_segment_added
sink, and whether anything is written is entirely the sink's decision. Nothing
here knows about files.
"""

import threading
from dataclasses import dataclass

from . import config


@dataclass(frozen=True)
class TranscriptSegment:
    stream: str
    start_epoch: float
    end_epoch: float
    text: str


def format_offset(seconds):
    """Render a within-window offset as mm:ss."""
    whole_seconds = max(0, int(seconds))
    return "[%02d:%02d]" % (whole_seconds // 60, whole_seconds % 60)


class RollingTranscript:
    """Timestamped recent speech from all streams, pruned to a retention window."""

    def __init__(self, retention_seconds=config.transcript_retention_seconds,
                 on_segment_added=None):
        self._retention_seconds = retention_seconds
        self._on_segment_added = on_segment_added
        self._segments = []
        self._lock = threading.Lock()

    @property
    def segment_count(self):
        with self._lock:
            return len(self._segments)

    def add(self, segment):
        """Store one recognised utterance. Blank recognitions are ignored."""
        config.require_known_stream(segment.stream)
        if not segment.text.strip():
            return False
        with self._lock:
            self._segments.append(segment)
            self._segments = self._segments_ending_after(
                segment.start_epoch - self._retention_seconds)
        self._offer_to_the_sink(segment)
        return True

    def _offer_to_the_sink(self, segment):
        """Called outside the lock: the sink writes to a file and the capture
        threads must not queue behind that."""
        if self._on_segment_added is not None:
            self._on_segment_added(segment)

    def _segments_ending_after(self, cutoff_epoch):
        return [segment for segment in self._segments if segment.end_epoch >= cutoff_epoch]

    def segments_in_window(self, window_seconds, now):
        """Segments that ended inside the window, oldest first."""
        with self._lock:
            recent = self._segments_ending_after(now - window_seconds)
        return sorted(recent, key=lambda segment: segment.start_epoch)

    def render_window(self, window_seconds, now):
        """Plain text rendering of the window, one labelled line per utterance."""
        segments = self.segments_in_window(window_seconds, now)
        window_start_epoch = now - window_seconds
        return "\n".join(self._render_segment(segment, window_start_epoch)
                         for segment in segments)

    def _render_segment(self, segment, window_start_epoch):
        return "%s %s: %s" % (format_offset(segment.start_epoch - window_start_epoch),
                              config.stream_label(segment.stream), segment.text.strip())
