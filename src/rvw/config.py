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
control_socket_path = run_dir / "rvw.sock"

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


def require_known_stream(stream_name):
    """Reject a capture stream name that the rest of the system cannot label."""
    if stream_name not in stream_labels:
        raise ValueError("unknown capture stream %r" % (stream_name,))


def stream_label(stream_name):
    """Human readable side of the conversation for a capture stream name."""
    require_known_stream(stream_name)
    return stream_labels[stream_name]
