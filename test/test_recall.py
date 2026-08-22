"""Tests for retrieval augmented recall: formatting passages and grounding a prompt.

A recalled answer is only worth as much as its references. The passages handed to
the model carry numbered citations, the same numbers are shown to me so I can see
where an answer came from, and the citation names the meeting, the date and the
time so the source can actually be found. The system prompt has to hold the model
to those passages and make it admit when they do not answer the question, because
a confident answer with no source is exactly what this phase exists to avoid.
"""

import time
import unittest
from pathlib import Path

from rvw import config, recall
from rvw.meeting_index import Hit
from rvw.prompts import build_recall_messages

MEETING_DIR = Path("/archive/2026/08/2026-08-20_09.00")


def hit(text, start_local="2026-08-20T09:00:05", speaker="them", screenshots=()):
    return Hit(meeting=MEETING_DIR.name, meeting_date="2026-08-20",
               start_epoch=time.mktime((2026, 8, 20, 9, 0, 5, 0, 0, -1)),
               start_local=start_local, speaker=speaker, stream="system", text=text,
               meeting_dir=MEETING_DIR, screenshots=list(screenshots))


class NumberedPassagesTest(unittest.TestCase):
    """The block the model is asked to answer from."""

    def setUp(self):
        self.hits = [hit("the lease timeout was thirty seconds"),
                     hit("the client should reconnect", start_local="2026-08-20T09:01:10")]
        self.block = recall.numbered_passages(self.hits)

    def test_every_passage_is_numbered_from_one(self):
        self.assertIn("[1]", self.block)
        self.assertIn("[2]", self.block)

    def test_each_passage_carries_its_words(self):
        self.assertIn("lease timeout", self.block)
        self.assertIn("reconnect", self.block)

    def test_each_passage_names_its_meeting_and_time(self):
        self.assertIn(MEETING_DIR.name, self.block)
        self.assertIn("09:00:05", self.block)


class SourceLinesTest(unittest.TestCase):
    """What is shown to me, so an answer's [n] references can be traced."""

    def test_a_source_line_per_hit_tagged_to_match_the_passages(self):
        lines = recall.source_lines([hit("a"), hit("b")])
        self.assertEqual(2, len(lines))
        self.assertTrue(lines[0].startswith("[1]"))
        self.assertTrue(lines[1].startswith("[2]"))

    def test_a_source_line_points_at_the_meeting_directory(self):
        line = recall.source_lines([hit("a")])[0]
        self.assertIn(str(MEETING_DIR), line)

    def test_a_source_line_notes_associated_screenshots(self):
        line = recall.source_lines([hit("a", screenshots=[MEETING_DIR / "screenshots/x.png"])])[0]
        self.assertIn("screenshot", line.lower())

    def test_a_source_line_without_screenshots_does_not_mention_them(self):
        line = recall.source_lines([hit("a")])[0]
        self.assertNotIn("screenshot", line.lower())


class BuildRecallMessagesTest(unittest.TestCase):
    """Grounding the model on the retrieved passages and nothing else."""

    def setUp(self):
        self.passages = recall.numbered_passages([hit("the lease timeout was thirty seconds")])
        self.messages = build_recall_messages("what was the lease timeout", self.passages)

    def test_the_conversation_is_a_system_plus_user_pair(self):
        self.assertEqual(["system", "user"], [message["role"] for message in self.messages])

    def test_the_question_and_the_passages_reach_the_model(self):
        content = self.messages[1]["content"]
        self.assertIn("what was the lease timeout", content)
        self.assertIn("lease timeout was thirty seconds", content)

    def test_the_system_prompt_demands_grounding_and_citations(self):
        instructions = self.messages[0]["content"].lower()
        for expected in ["passage", "cite", "do not"]:
            self.assertIn(expected, instructions)

    def test_a_question_with_no_passages_is_refused(self):
        with self.assertRaises(ValueError):
            build_recall_messages("anything", "   ")

    def test_an_empty_question_is_refused(self):
        with self.assertRaises(ValueError):
            build_recall_messages("  ", self.passages)


if __name__ == "__main__":
    unittest.main()
