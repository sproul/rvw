"""Static configuration and repository relative paths for the listening assistant.

Every path is derived from the location of this file so that the checkout can
live anywhere. Wide reaching modes (debug, dry) are module level globals rather
than parameters threaded through the call graph.
"""

import os
from pathlib import Path

repo_dir = Path(__file__).resolve().parents[2]
bin_dir = repo_dir / "bin"
var_dir = repo_dir / "var"
log_dir = var_dir / "log"
run_dir = var_dir / "run"

capture_helper_path = bin_dir / "audio_capture"
screen_capture_helper_path = bin_dir / "screen_capture"
control_socket_path = run_dir / "rvw.sock"

# Canonical archive of saved screenshots, and in Phase 3 of saved transcripts.
archive_dir = Path(os.environ.get("RVW_ARCHIVE_DIR", var_dir / "meetings"))

debug_mode = False                      # set by --debug on the daemon command line

# Audio. The helper always delivers mono float32 at this rate, whatever the
# hardware was doing, so the recogniser never has to resample.
sample_rate = 16000
capture_read_seconds = 0.25

# Segmentation. Utterance sized chunks avoid overlapping recognition windows.
silence_rms_threshold = 0.006
min_segment_peak_amplitude = 0.02       # below this the recogniser invents filler phrases
min_silence_seconds = 0.6
min_speech_seconds = 0.6
max_segment_seconds = 20.0

# Transcript.
transcript_retention_seconds = 1800.0
explain_window_seconds = 60.0
clarify_window_seconds = 45.0            # short: clarify is about the words just spoken
interpret_window_seconds = 120.0         # context sent with a screenshot
continuous_analysis_period_seconds = 120.0

# Streams. "mic" is me, "system" is everything the Mac plays back.
stream_labels = {"mic": "me", "system": "them"}

# Speech recognition.
whisper_model = os.environ.get("RVW_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
whisper_language = os.environ.get("RVW_WHISPER_LANGUAGE", "en")

# Local LLM, served by the llmster/LM Studio OpenAI compatible endpoint.
llm_base_url = os.environ.get("RVW_LLM_URL", "http://127.0.0.1:1234/v1")
llm_model = os.environ.get("RVW_LLM_MODEL", "meeting-assistant")
llm_max_tokens = 3072                   # the reasoning model spends most of this on thinking
llm_temperature = 0.3
llm_request_timeout_seconds = 300.0
vision_llm_model = os.environ.get("RVW_VLM_MODEL", "meeting-vision")

# Qwen3.6 does think far longer than these questions deserve: measured at
# temperature 0, "what is a unit test" costs 489 reasoning tokens and twelve
# seconds to produce a twenty token answer. There is nothing here to turn that
# down with. This LM Studio build ignores every request level control, measured
# identical to the token across reasoning_effort low and high, reasoning.effort,
# chat_template_kwargs enable_thinking, thinking and reasoning_effort, on both
# /v1 and /api/v0, and 'lms load' has no reasoning option either. The model's
# own chat_template.jinja does honour enable_thinking=false, so the switch
# exists and only the MLX engine's plumbing is missing; if a later build starts
# forwarding chat_template_kwargs, that is the one to send. Until then the
# thinking is only a latency cost, because llm.py keeps reasoning tokens out of
# the answer, and a per model reasoning setting in the LM Studio UI would be
# applied at load time rather than from here.

# Loading the LLM on demand. llm_model above is the identifier LM Studio serves
# the model under; llm_source_model is the model loaded under that identifier.
# LM Studio unloads it again once it has been idle for llm_idle_ttl_seconds,
# which is deliberate: the assistant spends most of its life listening rather
# than asking, and a resident twenty gigabyte model is a poor way to spend that
# time. An unloaded model is therefore ordinary and not a fault.
llm_source_model = os.environ.get("RVW_LLM_SOURCE_MODEL", "mlx-community/Qwen3.6-35B-A3B-4bit")
llm_context_length = 32768
llm_idle_ttl_seconds = 3600
llm_load_timeout_seconds = 900.0        # loading 20 GB from a cold page cache is not quick

# Started through rvw.app the daemon inherits no shell PATH, so lms is absolute.
lms_command = Path.home() / ".lmstudio" / "bin" / "lms"

# Screen capture. "frontmost" captures the frontmost application window only;
# "display" captures the whole display and is therefore never chosen silently.
screenshot_target = os.environ.get("RVW_SCREENSHOT_TARGET", "frontmost")
screenshot_timeout_seconds = 20.0


def require_known_stream(stream_name):
    """Reject a capture stream name that the rest of the system cannot label."""
    if stream_name not in stream_labels:
        raise ValueError("unknown capture stream %r" % (stream_name,))


def stream_label(stream_name):
    """Human readable side of the conversation for a capture stream name."""
    require_known_stream(stream_name)
    return stream_labels[stream_name]
