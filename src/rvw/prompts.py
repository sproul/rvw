"""Prompts sent to the local LLM and to the local vision model.

Three behaviours share one transcript framing: EXPLAIN teaches the concepts,
UNGARBLE reconstructs the words, INTERPRET describes what is on the screen.
Later phases add language and comprehension profiles here rather than in the
callers.
"""

import os
import tempfile

transcript_framing = (
    "Lines are labelled 'me' (my microphone) and 'them' (audio played by my Mac),\n"
    "with an offset from the start of the window."
)

explain_system_prompt = (
    "You help a technically competent listener follow a live conversation.\n"
    "You are given a machine transcription of the last part of that conversation.\n"
    "Explain what was just said.\n"
    "Reconstruct likely transcription errors when the context makes the intended\n"
    "meaning reasonably clear, and say which words you repaired.\n"
    "Identify specialized terminology, named systems and references that might make\n"
    "the discussion hard to follow, and explain them briefly.\n"
    "Keep what the speakers actually said clearly separate from anything you infer.\n"
    "If you are uncertain, say so instead of inventing details.\n"
    "Give the explanation directly; do not narrate your reasoning or reconsider it in\n"
    "the answer.\n"
    "Answer in compact prose or short bullets. Do not pad the answer."
)


ungarble_system_prompt = (
    "You help a listener who could not make out what was just said.\n"
    "You are given a machine transcription of the last part of a live conversation.\n"
    "Your task is to reconstruct the words themselves, not to teach the concepts.\n"
    "Assume the speech recognition misrecognised words, and that a strong accent or\n"
    "a non native speaker may have made the speech hard to parse.\n"
    "Decide on the most likely wording and commit to it. Do not think out loud, do\n"
    "not weigh the options in the answer, and do not reconsider a choice once it is\n"
    "made: give the result directly.\n"
    "Answer in exactly these three short parts and nothing else:\n"
    "1. The most likely verbatim wording, lightly repunctuated, with any word you\n"
    "   changed in bold.\n"
    "2. A line beginning 'Changed:' listing the substitutions, or 'Changed: nothing'.\n"
    "3. One or two sentences on what the speaker meant.\n"
    "If one word is genuinely uncertain, keep your best guess in part 1 and add at\n"
    "most one short clause naming the single alternative; do not enumerate your\n"
    "reasoning or list every possibility you considered."
)

recall_system_prompt = (
    "You answer a question about past conversations using only the numbered\n"
    "transcript passages you are given, which were retrieved from my own meeting\n"
    "archive.\n"
    "Base the answer only on those passages. Do not use anything else you know, and\n"
    "do not invent detail that is not in them.\n"
    "Cite the passages you used by their number in square brackets, for example [2],\n"
    "so the answer can be traced back to the conversation it came from.\n"
    "The passages are machine transcriptions and may contain misrecognised words;\n"
    "reconstruct the obvious ones and say when you have.\n"
    "If the passages do not answer the question, say so plainly instead of guessing,\n"
    "and do not pad the answer."
)

interpret_system_prompt = (
    "You describe a screenshot taken during a live technical conversation, for the\n"
    "person who is in that conversation.\n"
    "Report what is actually visible: the application, diagrams, code, tables, error\n"
    "messages and any text that matters.\n"
    "Relate it to the recent transcript when the connection is clear, and say when it\n"
    "is not.\n"
    "Keep what you can see clearly separate from anything you infer.\n"
    "If the image is unreadable, say so instead of guessing.\n"
    "Answer in compact prose or short bullets."
)

code_review_system_prompt = """Given a software task, source (including tests and possibly a transcript of how the software was tested) and patch, search the patch for defects. Note that this information will come in the form of screenshots (which may have been subject to OCR), so there could be overlap or gaps or odd formatting.

Read the task as a contract, closely. The patch quality is judged according to the task as written, including every constraint in it: files not to touch, tests that must keep passing, what to do if the request can't be met. A patch is valid only if it works in general, not just for an example in the task. E.g., an issue asking a date parser to accept a second format which includes one sample date: a patch that special-cases that one string would not be considered a valid general patch, even if the tests are green. Decide what a correct patch must do before judging the attempt.

Treat the patch and the tests as separate potential points of failure. They often agree, but the attempts where they don't are an important defect category. A correct patch can be rejected because the graders' tests demand something the issue never asked for (for example, an exact error-message wording). A wrong patch can pass because the tests check only one input, or because the agent hard-coded what the tests expect.

Answer each question on its own evidence.  For each test in graders-tests.diff, find what it calls and trace it to see if it is a valid test, both for common and uncommon cases.

Keep in mind how agents can cut corners. The Process answer asks how the agent made mistakes it made. Look for the usual shortcuts: copying the upstream fix, working against the graders' own tests, editing or deleting tests, hard-coding expected values, saying "tests pass" when the transcript never shows them running, and ignoring a rule the task set. An example would be a transcript that ends with "all tests pass" when no test command appears anywhere above it. When there is no transcript, the diff alone can still give it away.

The defect types are
        - critical: mostly broken, or a security problem
        - major: wrongness
        - minor: style
These are in priority order. If several apply, pick the first.

Review against the spec, not your taste. An issue is a place where the diff gets a requirement of the task (sometimes expressed by the contents of RFP.md) wrong or does something it forbids. The task or RFP is a short numbered list of requirements plus an "Out of scope" section. Read it first and note what each requirement demands.

Then go through each changed file on the after side, hunk by hunk, asking which requirement each hunk serves. Some code that looks suspicious is actually fine under the task/RFP; these look-alikes are planted, so check the task/RFP before you mark anything. An example would be a task/RFP that says existing settings must be preserved, and a diff that rewrites the settings file unconditionally. That's a critical issue even though the happy path works.

Check the paths nobody demos. Most issues aren't on the main path. Look at error handling and missing values, unusual inputs, configuration combinations, state shared across requests or threads, data the user already had, and anything that touches secrets or permissions. Use the brief's severity definitions.

Mark precisely, and explain every mark. Select only the lines that are wrong (only the first 6 selected lines count). If the same bug appears in several places, note the other instances in a single issue writeup. Any issue should note the test, hunk, transcript step or task/RFP clause that exposes the problem.

For each issue include a confidence level from 0 to 100.
"""

# The prompt registry. The named commands (EXPLAIN_SPEECH and friends) choose
# their system prompt explicitly; this registry is what a console submission is
# sent under, selected by PROMPT_SET/PROMPT_LIST and persisted by the assistant.
prompts = {
    "explain": explain_system_prompt,
    "ungarble": ungarble_system_prompt,
    "recall": recall_system_prompt,
    "visually_interpret": interpret_system_prompt,
    "code_review": code_review_system_prompt,
}

default_prompt_key = "explain"

# PROMPT_SET takes exactly one letter argument: the key's first letter.
prompt_letters = {key[0]: key for key in prompts}

# The console appends the recent transcript only for the prompts that read a
# conversation; recall and code review are given material, not a transcript.
transcript_context_prompt_keys = ("explain", "ungarble", "visually_interpret")


def prompt_key_for_letter(letter):
    """The registry key one selection letter names; anything else is refused."""
    if letter in prompt_letters:
        return prompt_letters[letter]
    raise ValueError("no prompt %r; choose one letter from %s"
                     % (letter, ", ".join(sorted(prompt_letters))))


def read_selected_prompt_key(path):
    """The persisted selection; an absent file means the default.

    A file that exists but holds no known key is a violated assumption about
    our own data, so it is fatal rather than quietly defaulted.
    """
    try:
        # Bytes, not read_text: text mode would quietly translate \r\n into the
        # one newline the writer produces and hide the corruption being checked.
        content = path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return default_prompt_key
    # This file is our own saved data, not typed input: the writer appends
    # exactly one newline, so anything else in the file is corruption.
    if content.endswith("\n"):
        content = content[:-1]
    if content not in prompts:
        raise ValueError("%s does not name a known prompt (contains %r; known: %s); "
                         "fix or delete it" % (path, content, ", ".join(prompts)))
    return content


def write_selected_prompt_key(path, key):
    """Persist the selection atomically; a failure leaves neither a changed
    file nor a temp file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=str(path.parent), prefix=path.name + ".",
                                         delete=False, encoding="utf-8") as handle:
            temporary_name = handle.name
            handle.write(key + "\n")
        os.replace(temporary_name, path)
    except OSError:
        if temporary_name is not None:
            os.unlink(temporary_name)
        raise


def render_prompt_listing(selected_key):
    """Every key and its full prompt, the active one marked; registry order."""
    return "\n\n".join("%s%s\n%s" % ("* " if key == selected_key else "  ",
                                     key, prompt)
                       for key, prompt in prompts.items())


def build_explain_messages(transcript_text, window_seconds):
    """Chat messages asking for an explanation of the recent transcript window."""
    return _build_transcript_messages(explain_system_prompt, transcript_text, window_seconds,
                                      "Explain this passage now.")


def build_ungarble_messages(transcript_text, window_seconds):
    """Chat messages asking what was actually said in the recent transcript window."""
    return _build_transcript_messages(ungarble_system_prompt, transcript_text, window_seconds,
                                      "Reconstruct this passage now.")


def build_recall_messages(question, passages):
    """Chat messages asking the model to answer a question from retrieved passages."""
    if not question.strip():
        raise ValueError("no question was asked")
    if not passages.strip():
        raise ValueError("no passages were retrieved to answer from")
    request = ("Passages retrieved from my meeting archive:\n\n%s\n\n"
               "Question: %s\n\nAnswer from the passages above, and cite the ones you use."
               % (passages.strip(), question.strip()))
    return [{"role": "system", "content": recall_system_prompt},
            {"role": "user", "content": request}]


def build_interpret_messages(transcript_text, image_data_uri, window_seconds):
    """Chat messages asking the vision model about a screenshot plus recent speech."""
    if not image_data_uri:
        raise ValueError("no screenshot was supplied for interpretation")
    return [{"role": "system", "content": interpret_system_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": _interpret_request(transcript_text, window_seconds)},
                {"type": "image_url", "image_url": {"url": image_data_uri}}]}]


def _build_transcript_messages(system_prompt, transcript_text, window_seconds, instruction):
    if not transcript_text.strip():
        raise ValueError("no transcript available for the requested window")
    request = "%s\n\n%s\n\n%s" % (_transcript_heading(window_seconds),
                                  transcript_text.strip(), instruction)
    return [{"role": "system", "content": system_prompt},
            {"role": "user", "content": request}]


def _interpret_request(transcript_text, window_seconds):
    """A screenshot is worth interpreting even when nobody has said anything yet."""
    if not transcript_text.strip():
        return ("Screenshot taken during a conversation for which there is no transcript yet.\n"
                "Describe what is on the screen.")
    return "%s\n\n%s\n\nDescribe what is on the screen now." % (
        _transcript_heading(window_seconds), transcript_text.strip())


def _transcript_heading(window_seconds):
    return "Transcript of roughly the last %d seconds.\n%s" % (int(window_seconds),
                                                              transcript_framing)
