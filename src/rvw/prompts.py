"""Prompts sent to the local LLM and to the local vision model.

Three behaviours share one transcript framing: EXPLAIN teaches the concepts,
CLARIFY reconstructs the words, INTERPRET describes what is on the screen.
Later phases add language and comprehension profiles here rather than in the
callers.
"""

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
    "Answer in compact prose or short bullets. Do not pad the answer."
)


clarify_system_prompt = (
    "You help a listener who could not make out what was just said.\n"
    "You are given a machine transcription of the last part of a live conversation.\n"
    "Your task is to reconstruct the words themselves, not to teach the concepts.\n"
    "Assume the speech recognition misrecognised words, and that a strong accent or\n"
    "a non native speaker may have made the speech hard to parse.\n"
    "Give the most likely verbatim wording first, lightly repunctuated, and mark the\n"
    "words you changed.\n"
    "Then give one or two sentences saying what the speaker meant.\n"
    "Where a passage is genuinely ambiguous, offer the plausible readings instead of\n"
    "choosing one, and say which words you are uncertain about.\n"
    "Do not explain terminology unless the reconstruction depends on it.\n"
    "Do not pad the answer."
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


def build_explain_messages(transcript_text, window_seconds):
    """Chat messages asking for an explanation of the recent transcript window."""
    return _build_transcript_messages(explain_system_prompt, transcript_text, window_seconds,
                                      "Explain this passage now.")


def build_clarify_messages(transcript_text, window_seconds):
    """Chat messages asking what was actually said in the recent transcript window."""
    return _build_transcript_messages(clarify_system_prompt, transcript_text, window_seconds,
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
