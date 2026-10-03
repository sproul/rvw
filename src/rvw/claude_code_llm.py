"""Answering through the local claude command line instead of the local endpoint.

Offers the same small interface as llm.LocalLlm (model, available_models,
stream_chat), so the assistant can switch between them with SET_MODEL. claude
runs once per question, non-interactively, with its own default model, and its
stream-json output is read as it arrives so the answer streams as the local
model's does.

claude is a coding agent unless told otherwise, so every run replaces its system
prompt with ours, gets no tools, keeps no session on disk, and runs in safe mode,
which keeps ~/.claude/CLAUDE.md and every other customisation, all written for
coding, out of a meeting answer.
"""

import json
import logging
import os
import signal
import subprocess
import tempfile
import threading

from . import config
from .llm import LocalLlmError

log = logging.getLogger(__name__)


class ClaudeCodeError(LocalLlmError):
    """A subclass so that every place that handles a failed local answer handles this."""


class ClaudeCodeLlm:
    """Streaming answers from `claude -p`, for messages of one system and one user turn."""

    @property
    def model(self):
        """The identifier the menu bar reports and SET_MODEL accepts."""
        return config.claude_code_model

    def available_models(self):
        """Only itself, and only if the command is there to run."""
        if not os.access(config.claude_command, os.X_OK):
            raise ClaudeCodeError("no claude command at %s" % config.claude_command)
        return [config.claude_code_model]

    def stream_chat(self, messages, on_token, on_reasoning=None):
        """Feed answer tokens to on_token as they arrive and return the whole answer."""
        system_prompt, question = system_prompt_and_question(messages)
        # stderr goes to a file, which unlike a pipe nobody has to drain while
        # the answer streams; a full pipe would stall claude until the timeout.
        with tempfile.TemporaryFile(mode="w+") as stderr_file:
            process = start_claude(system_prompt, stderr_file)
            timed_out = threading.Event()
            watchdog = threading.Timer(config.llm_request_timeout_seconds,
                                       kill_for_timeout, (process, timed_out))
            watchdog.start()
            try:
                answer = ask_and_read_the_answer(process, question, on_token, on_reasoning)
                require_a_clean_exit(process, stderr_file, timed_out)
            finally:
                watchdog.cancel()
                kill_claude_and_its_children(process)
        require_the_stream_ended_with_a_result(answer)
        return answer


def start_claude(system_prompt, stderr_file):
    """In a session of its own, so that a kill reaches everything claude started."""
    return subprocess.Popen(claude_arguments(system_prompt), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=stderr_file, text=True,
                            start_new_session=True)


def ask_and_read_the_answer(process, question, on_token, on_reasoning):
    process.stdin.write(question)
    process.stdin.close()
    return read_the_answer_stream(process.stdout, on_token, on_reasoning)


def kill_for_timeout(process, timed_out):
    timed_out.set()
    kill_claude_and_its_children(process)


def kill_claude_and_its_children(process):
    """Harmless once claude has exited by itself, which is the usual case."""
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def system_prompt_and_question(messages):
    """The one shape every prompt builder makes; any other means a bug upstream."""
    roles = [message.get("role") for message in messages]
    if roles != ["system", "user"]:
        raise ClaudeCodeError("claude_code takes one system and one user message, got roles %s"
                              % roles)
    if not all(isinstance(message.get("content"), str) for message in messages):
        raise ClaudeCodeError("claude_code takes text only; images go to the vision model")
    return messages[0]["content"], messages[1]["content"]


def claude_arguments(system_prompt):
    """No --model: claude answers with whatever its own default is."""
    return [str(config.claude_command), "-p", "--safe-mode",
            "--system-prompt", system_prompt, "--tools", "",
            "--no-session-persistence", "--strict-mcp-config",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages"]


def read_the_answer_stream(lines, on_token, on_reasoning):
    """Stream the text deltas; None if the stream ended without a result."""
    answer = []
    for raw_line in lines:
        if not raw_line.strip():
            continue
        event = parse_stream_line(raw_line)
        if event.get("type") == "result":
            require_a_successful_result(event)
            return require_the_streamed_answer_is_the_result("".join(answer).strip(), event)
        report_the_answering_model(event)
        deliver_delta(event, answer, on_token, on_reasoning)
    return None


def parse_stream_line(raw_line):
    try:
        return json.loads(raw_line)
    except ValueError:
        raise ClaudeCodeError("claude wrote a line that is not stream-json: %r"
                              % raw_line[:200])


def require_a_successful_result(event):
    if event.get("is_error"):
        raise ClaudeCodeError("claude reported an error: %s" % event.get("result"))


def require_the_streamed_answer_is_the_result(answer, event):
    """Without partial messages the stream carries no text deltas, and the answer
    would arrive empty and still look like a success."""
    result = (event.get("result") or "").strip()
    if answer != result:
        raise ClaudeCodeError("claude streamed %d characters but its result has %d; "
                              "the streamed answer is not the answer"
                              % (len(answer), len(result)))
    return answer


def report_the_answering_model(event):
    """claude chooses its own model, so say which one it chose."""
    if event.get("type") == "system" and event.get("subtype") == "init":
        log.debug("OK  claude_code is answering with %s", event.get("model"))


def deliver_delta(event, answer, on_token, on_reasoning):
    """Text goes into the answer; thinking, if anyone asked for it, goes beside it."""
    if event.get("type") != "stream_event":
        return
    inner = event.get("event") or {}
    if inner.get("type") != "content_block_delta":
        return
    delta = inner.get("delta") or {}
    if delta.get("type") == "text_delta":
        answer.append(delta["text"])
        on_token(delta["text"])
    elif delta.get("type") == "thinking_delta" and delta.get("thinking") and on_reasoning:
        on_reasoning(delta["thinking"])


def require_the_stream_ended_with_a_result(answer):
    """Checked after the exit status, which says more about why when there is one."""
    if answer is None:
        raise ClaudeCodeError("claude's stream ended without a result")


def require_a_clean_exit(process, stderr_file, timed_out):
    status = process.wait()
    if timed_out.is_set():
        raise ClaudeCodeError("claude timed out after %ds"
                              % config.llm_request_timeout_seconds)
    if status != 0:
        stderr_file.seek(0)
        raise ClaudeCodeError("claude exited with status %d: %s"
                              % (status, stderr_file.read().strip()))
