"""Tests for the buffer that lets a window show an answer as it is written.

Answers were only ever streamed to the assistant's terminal. A window has to be
able to ask for the answer at any moment, including while it is still arriving,
and has to be told the difference between an answer still being written, a
finished one, and no answer at all.
"""

import unittest

from rvw.answer_buffer import AnswerBuffer


class AnswerBufferTest(unittest.TestCase):

    def setUp(self):
        self.buffer = AnswerBuffer()

    def test_nothing_has_been_asked_yet(self):
        self.assertIn("nothing has been asked", self.buffer.render())

    def test_an_answer_in_progress_is_shown_while_it_is_written(self):
        self.buffer.begin("explanation")
        self.buffer.append("the lease ")
        self.buffer.append("timeout")
        rendered = self.buffer.render()
        self.assertIn("explanation", rendered)
        self.assertIn("in progress", rendered)
        self.assertIn("the lease timeout", rendered)

    def test_a_finished_answer_is_no_longer_in_progress(self):
        self.buffer.begin("ungarbling")
        self.buffer.append("they said backoff")
        self.buffer.finish()
        rendered = self.buffer.render()
        self.assertNotIn("in progress", rendered)
        self.assertIn("they said backoff", rendered)

    def test_a_new_question_replaces_the_previous_answer(self):
        self.buffer.begin("explanation")
        self.buffer.append("the first answer")
        self.buffer.finish()
        self.buffer.begin("ungarbling")
        self.buffer.append("the second answer")
        rendered = self.buffer.render()
        self.assertNotIn("the first answer", rendered)
        self.assertIn("the second answer", rendered)

    def test_a_failed_answer_says_so_rather_than_looking_unfinished(self):
        """An answer that stopped because the model failed must not sit in the
        window looking as though more of it were still coming."""
        self.buffer.begin("explanation")
        self.buffer.append("as far as it got")
        self.buffer.fail("no model is loaded as 'meeting-assistant'")
        rendered = self.buffer.render()
        self.assertIn("FAIL", rendered)
        self.assertIn("no model is loaded", rendered)
        self.assertIn("as far as it got", rendered)
        self.assertNotIn("in progress", rendered)

    def test_the_heading_and_the_text_are_separate_lines(self):
        """The window shows the text; the heading tells it what it is looking at."""
        self.buffer.begin("recall")
        self.buffer.append("line one\nline two")
        first_line, rest = self.buffer.render().split("\n", 1)
        self.assertIn("recall", first_line)
        self.assertEqual("line one\nline two", rest.strip())


if __name__ == "__main__":
    unittest.main()
