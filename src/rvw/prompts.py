"""Prompts sent to the local LLM.

Phase 1 has a single behaviour; later phases add language and comprehension
profiles here rather than in the callers.
"""

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


def build_explain_messages(transcript_text, window_seconds):
    """Chat messages asking for an explanation of the recent transcript window."""
    if not transcript_text.strip():
        raise ValueError("no transcript available for the requested window")
    request = (
        "Transcript of roughly the last %d seconds.\n"
        "Lines are labelled 'me' (my microphone) and 'them' (audio played by my Mac),\n"
        "with an offset from the start of the window.\n\n"
        "%s\n\n"
        "Explain this passage now." % (int(window_seconds), transcript_text.strip())
    )
    return [{"role": "system", "content": explain_system_prompt},
            {"role": "user", "content": request}]
