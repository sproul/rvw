"""Turning retrieved transcript passages into a citation for the model and for me.

meeting_index finds the passages; this formats them. The same numbering is used
in two places on purpose: the block handed to the model tags each passage [n] and
asks it to cite those tags, and the source list printed to me is tagged the same
way, so an answer that says "[2]" can be traced to the second source shown. A
citation names the meeting, the date and the time, because a reference that
cannot be found is not a reference.
"""

search_snippet_limit = 160              # characters of a passage SEARCH prints per hit


def numbered_passages(hits):
    """The passages block a recall prompt is built from, one [n] tag per hit."""
    return "\n".join("[%d] %s: %s" % (number, _passage_citation(hit), hit.text)
                     for number, hit in enumerate(hits, start=1))


def source_lines(hits):
    """One human readable line per hit, tagged to match the passages [n]."""
    return ["[%d] %s" % (number, _source_description(hit))
            for number, hit in enumerate(hits, start=1)]


def search_result_lines(hits):
    """One line per SEARCH hit: when, who, which meeting, and the words matched."""
    return ["%s %s (%s): %s%s" % (hit.start_local, hit.speaker, hit.meeting,
                                  _snippet(hit.text), _screenshot_note(hit.screenshots))
            for hit in hits]


def _snippet(text):
    """A passage is short, but a runaway one is truncated so a listing stays scannable."""
    text = text.strip()
    if len(text) <= search_snippet_limit:
        return text
    return text[:search_snippet_limit].rstrip() + "..."


def _passage_citation(hit):
    """Meeting and time, compact, for the citation the model reads."""
    return "%s %s, meeting %s" % (_clock_time(hit.start_local), hit.speaker, hit.meeting)


def _source_description(hit):
    """The citation plus where to find it, for the source list printed to me."""
    return "%s %s (%s)%s -> %s" % (hit.start_local, hit.speaker, hit.meeting,
                                   _screenshot_note(hit.screenshots), hit.meeting_dir)


def _screenshot_note(screenshots):
    count = len(screenshots)
    if not count:
        return ""
    return " +%d screenshot%s" % (count, "" if count == 1 else "s")


def _clock_time(start_local):
    """The wall clock part of an ISO local timestamp, for a compact citation."""
    return start_local.split("T")[-1] if "T" in start_local else start_local
