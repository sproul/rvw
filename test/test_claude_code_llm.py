"""Tests for answering through the local claude command line instead of the endpoint.

The real claude is not run here: a stub stands in for it, records the arguments
and the prompt it was given, and replays a canned stream-json transcript, so what
is tested is the client's half of the conversation and how it reads the stream.
"""

import json
import stat
import tempfile
import unittest
from pathlib import Path

from rvw import config
from rvw.claude_code_llm import ClaudeCodeError, ClaudeCodeLlm
from rvw.llm import LocalLlmError

stub_claude = """#!/bin/sh
here=$(dirname "$0")
printf '%s\\n' "$@" > "$here/argv"
cat > "$here/stdin"
cat "$here/stdout_lines"
echo "stub complaint" >&2
[ -f "$here/linger" ] && sleep "$(cat "$here/linger")"
exit $(cat "$here/exit_status")
"""

system_and_user = [{"role": "system", "content": "you explain meetings"},
                   {"role": "user", "content": "what did they mean by lease?"}]


def stream_event(delta):
    return {"type": "stream_event",
            "event": {"type": "content_block_delta", "index": 0, "delta": delta}}


def text_delta(text):
    return stream_event({"type": "text_delta", "text": text})


def thinking_delta(text):
    return stream_event({"type": "thinking_delta", "thinking": text})


def result_line(text, is_error=False):
    return {"type": "result", "subtype": "success", "is_error": is_error, "result": text}


init_line = {"type": "system", "subtype": "init", "model": "claude-opus-5-5", "tools": []}


class ClaudeCodeLlmTestCase(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.stub_dir = Path(self.temporary_directory.name)
        self.saved_claude_command = config.claude_command
        self.saved_timeout = config.llm_request_timeout_seconds
        self.addCleanup(self.restore_claude_command)
        config.claude_command = self.stub_dir / "claude"
        config.claude_command.write_text(stub_claude, encoding="utf-8")
        config.claude_command.chmod(config.claude_command.stat().st_mode | stat.S_IXUSR)
        self.replay([init_line, text_delta("a lease "), text_delta("is a lock"),
                     result_line("a lease is a lock")])

    def restore_claude_command(self):
        config.claude_command = self.saved_claude_command
        config.llm_request_timeout_seconds = self.saved_timeout

    def replay(self, lines, exit_status=0, raw_lines=()):
        rendered = [json.dumps(line) for line in lines] + list(raw_lines)
        (self.stub_dir / "stdout_lines").write_text("\n".join(rendered) + "\n",
                                                    encoding="utf-8")
        (self.stub_dir / "exit_status").write_text(str(exit_status), encoding="utf-8")

    def recorded_arguments(self):
        return (self.stub_dir / "argv").read_text(encoding="utf-8").split("\n")[:-1]

    def recorded_prompt(self):
        return (self.stub_dir / "stdin").read_text(encoding="utf-8")

    def ask(self, messages=system_and_user):
        tokens, thoughts = [], []
        answer = ClaudeCodeLlm().stream_chat(messages, tokens.append, thoughts.append)
        return answer, tokens, thoughts


class StreamingTest(ClaudeCodeLlmTestCase):

    def test_text_is_streamed_token_by_token_and_returned_whole(self):
        answer, tokens, _ = self.ask()
        self.assertEqual(["a lease ", "is a lock"], tokens)
        self.assertEqual("a lease is a lock", answer)

    def test_thinking_goes_to_the_reasoning_callback_and_not_into_the_answer(self):
        self.replay([thinking_delta("hmm"), text_delta("the answer"),
                     result_line("the answer")])
        answer, tokens, thoughts = self.ask()
        self.assertEqual("the answer", answer)
        self.assertEqual(["the answer"], tokens)
        self.assertEqual(["hmm"], thoughts)

    def test_the_identifier_is_claude_code_and_it_is_always_offered(self):
        self.assertEqual("claude_code", ClaudeCodeLlm().model)
        self.assertEqual(["claude_code"], ClaudeCodeLlm().available_models())


class InvocationTest(ClaudeCodeLlmTestCase):
    """claude is a coding agent by default; here it must be a plain answerer."""

    def test_the_system_prompt_replaces_claude_codes_own(self):
        self.ask()
        arguments = self.recorded_arguments()
        self.assertEqual("you explain meetings",
                         arguments[arguments.index("--system-prompt") + 1])

    def test_the_question_arrives_on_standard_input(self):
        self.ask()
        self.assertEqual("what did they mean by lease?", self.recorded_prompt())

    def test_no_tools_no_customisations_and_nothing_left_on_disk(self):
        """--safe-mode keeps ~/.claude/CLAUDE.md, written for coding, out of the answers."""
        self.ask()
        arguments = self.recorded_arguments()
        for flag in ("-p", "--safe-mode", "--no-session-persistence", "--strict-mcp-config",
                     "--include-partial-messages"):
            self.assertIn(flag, arguments)
        self.assertEqual("", arguments[arguments.index("--tools") + 1])
        self.assertEqual("stream-json", arguments[arguments.index("--output-format") + 1])

    def test_no_model_is_named_so_claude_uses_its_default(self):
        self.ask()
        self.assertNotIn("--model", self.recorded_arguments())


class RefusalTest(ClaudeCodeLlmTestCase):

    def test_an_error_result_is_raised_with_its_text(self):
        self.replay([result_line("rate limited", is_error=True)])
        with self.assertRaises(ClaudeCodeError) as refused:
            self.ask()
        self.assertIn("rate limited", str(refused.exception))

    def test_a_failed_exit_is_raised_with_what_claude_said_on_stderr(self):
        self.replay([], exit_status=3)
        with self.assertRaises(ClaudeCodeError) as refused:
            self.ask()
        self.assertIn("stub complaint", str(refused.exception))

    def test_a_stream_that_ends_without_a_result_is_raised(self):
        self.replay([text_delta("half an ans")])
        with self.assertRaises(ClaudeCodeError):
            self.ask()

    def test_a_result_that_differs_from_the_streamed_text_is_raised(self):
        """Without partial messages the answer would arrive empty and look like success."""
        self.replay([result_line("an answer nobody streamed")])
        with self.assertRaises(ClaudeCodeError):
            self.ask()

    def test_a_question_that_outlives_the_timeout_says_it_timed_out(self):
        config.llm_request_timeout_seconds = 0.5
        (self.stub_dir / "linger").write_text("30", encoding="utf-8")
        self.replay([text_delta("half")])
        with self.assertRaises(ClaudeCodeError) as refused:
            self.ask()
        self.assertIn("timed out", str(refused.exception))

    def test_a_flood_on_stderr_does_not_stall_the_answer(self):
        """A pipe holds about 64 KB; stderr must not be left to fill one."""
        stub = config.claude_command.read_text(encoding="utf-8").replace(
            'cat "$here/stdout_lines"',
            'head -c 200000 /dev/zero | tr "\\0" x >&2\ncat "$here/stdout_lines"')
        config.claude_command.write_text(stub, encoding="utf-8")
        config.llm_request_timeout_seconds = 10
        answer, _, _ = self.ask()
        self.assertEqual("a lease is a lock", answer)

    def test_a_line_that_is_not_json_is_raised(self):
        self.replay([], raw_lines=["this is not json"])
        with self.assertRaises(ClaudeCodeError):
            self.ask()

    def test_anything_but_one_system_and_one_user_message_is_refused_unasked(self):
        """Every prompt builder makes exactly that shape; another means a bug upstream."""
        for messages in ([system_and_user[1]],
                         system_and_user + [{"role": "user", "content": "and?"}],
                         [system_and_user[0], {"role": "user", "content": [{"type": "text"}]}]):
            with self.assertRaises(ClaudeCodeError):
                self.ask(messages)
        self.assertFalse((self.stub_dir / "argv").exists())

    def test_a_missing_claude_is_reported_by_the_listing(self):
        config.claude_command = self.stub_dir / "nowhere" / "claude"
        with self.assertRaises(ClaudeCodeError):
            ClaudeCodeLlm().available_models()

    def test_the_error_is_one_the_assistant_already_handles(self):
        self.assertTrue(issubclass(ClaudeCodeError, LocalLlmError))


if __name__ == "__main__":
    unittest.main()
