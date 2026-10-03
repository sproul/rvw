"""Static configuration and repository relative paths for the listening assistant.

Every path is derived from the location of this file so that the checkout can
live anywhere. Wide reaching modes (debug, dry) are module level globals rather
than parameters threaded through the call graph.
"""

import os
import socket
from pathlib import Path

repo_dir = Path(__file__).resolve().parents[2]
bin_dir = repo_dir / "bin"
var_dir = repo_dir / "var"
log_dir = var_dir / "log"
run_dir = var_dir / "run"

# The console's selected prompt, persisted across restarts; written only when
# PROMPT_SET succeeds, read by every Assistant at start-up.
selected_prompt_path = var_dir / "selected_prompt"

capture_helper_path = bin_dir / "audio_capture"
screen_capture_helper_path = bin_dir / "screen_capture"
# RVW_CONTROL_SOCKET moves the socket for both ends; bin/rvwctl reads the same one.
control_socket_path = Path(os.environ.get("RVW_CONTROL_SOCKET", run_dir / "rvw.sock"))

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
transcript_display_seconds = 300.0       # what the transcript window shows by default
explain_window_seconds = 60.0
ungarble_window_seconds = 45.0           # short: ungarble is about the words just spoken
interpret_window_seconds = 120.0         # context sent with a screenshot
continuous_analysis_period_seconds = 120.0

# Streams. "mic" is me, "system" is everything the Mac plays back.
stream_labels = {"mic": "me", "system": "them"}

# Transcript retention. Ephemeral is the default because keeping a recording of
# somebody's conversation is a decision, and one nobody made is a decision not
# to: in that mode the rolling window above is all that exists and it is
# discarded as it ages. "retained" writes the transcript of the session it
# starts in; see rvw/meeting_archive.py.
retention_modes = ("ephemeral", "retained")
transcript_retention_mode = os.environ.get("RVW_RETENTION", "ephemeral")

# Searchable meeting memory (Phase 4). The index is derived data, rebuilt from
# the canonical transcripts, so it lives under var/ and can be deleted at any
# time; principle 7 forbids synchronising it between Macs, and rebuilding it
# locally is what replaces that. RVW_INDEX_DB moves it, as RVW_ARCHIVE_DIR moves
# the transcripts it is built from.
index_dir = var_dir / "index"
index_db_path = Path(os.environ.get("RVW_INDEX_DB", index_dir / "meetings.db"))
search_result_limit = 8                 # hits SEARCH shows for one query
recall_passage_count = 6                # passages retrieved to ground one RECALL answer
screenshot_association_seconds = 120.0  # a screenshot captured this near a passage is "associated"

# Speech recognition. The language is what the recogniser is told to expect, and
# it can be changed while the assistant runs (SET_LANGUAGE); it takes effect on
# the next utterance recognised, not on the ones already queued. The offered set
# is deliberately short: these are the languages of Phase 0 and Phase 5, and a
# code nobody offers is far more likely to be a typo than a wish.
whisper_model = os.environ.get("RVW_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
whisper_language = os.environ.get("RVW_WHISPER_LANGUAGE", "en")
recognition_languages = ("de", "en", "fr")

# Local LLM, served by the llmster/LM Studio OpenAI compatible endpoint.
llm_base_url = os.environ.get("RVW_LLM_URL", "http://127.0.0.1:1234/v1")
llm_model = os.environ.get("RVW_LLM_MODEL", "meeting-assistant")
llm_max_tokens = 3072                   # the reasoning model spends most of this on thinking
llm_temperature = 0.3
llm_request_timeout_seconds = 300.0
vision_llm_model = os.environ.get("RVW_VLM_MODEL", "meeting-vision")

# The text model questions go to when the assistant starts; SET_MODEL changes it
# while it runs. "claude_code" is not served by the endpoint: it runs the local
# claude command line with that command's own default model. It is the default
# for now so that a weak local model is ruled out while the prompts are worked
# out; the local models are to be compared against it later. Vision stays local.
claude_code_model = "claude_code"
text_model = os.environ.get("RVW_TEXT_MODEL", claude_code_model)
# Absolute for the same reason as lms_command: rvw.app inherits no shell PATH.
claude_command = Path.home() / ".local" / "bin" / "claude"

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
#
# That latency is not small, though: measured on the M3 Max on 2026-08-22, the
# real EXPLAIN prompt with thinking left on spent all 1023 of its 1024 allowed
# tokens on reasoning and returned an empty answer, which is the timeout that made
# the model look unusable. The one lever that does work is not a request
# parameter at all: prefilling a closed, empty thinking block as the start of the
# assistant turn makes the model skip thinking and answer directly, measured at
# zero reasoning tokens and a full, good answer in about five seconds, streaming
# included. It works because the model's own chat template treats a supplied
# </think> as thinking already done, so no engine support is needed. llm.py
# appends this to the messages of a model asked to suppress reasoning.
reasoning_prefill = "<think>\n\n</think>\n\n"

# Loading the LLM on demand. llm_model above is the identifier LM Studio serves
# the model under; llm_source_model is the model loaded under that identifier.
# LM Studio unloads it again once it has been idle for llm_idle_ttl_seconds,
# which is deliberate: the assistant spends most of its life listening rather
# than asking, and a resident twenty gigabyte model is a poor way to spend that
# time. An unloaded model is therefore ordinary and not a fault.
#
# Which model that is, is the one setting that must differ from one Mac to the
# next, because it is the one the machine's memory decides. The 4-bit 35B is
# 20 GB resident: comfortable on the 96 GB m3, and on the 32 GB m4 it leaves the
# machine paging and takes 23.9 seconds to the first token of an EXPLAIN. The
# 3-bit quant of the same MoE is 14.2 GB there, answers as well, and starts in
# 1.2 seconds. doc/model_benchmarks.md holds those measurements and the runs of
# every other candidate; a new machine belongs in this table only once it has
# rows there, so the default is the model that is known to be good where there
# is memory for it.
#
# The table lives here rather than in the environment because the daemon runs
# inside bin/rvw.app, started by LaunchServices, and so inherits the launchd
# session's variables and never a shell's. A machine configured by an export in
# a shell profile would load the right model when the installer was run by hand
# and the wrong one an hour later, when the idle timeout expired and the daemon
# loaded it again by itself. RVW_LLM_SOURCE_MODEL still overrides, for trying a
# candidate out; to change what a machine does permanently, add its row.
llm_source_model_default = "mlx-community/Qwen3.6-35B-A3B-4bit"
llm_source_model_by_host = {
    "m3": llm_source_model_default,                  # M3 Max, 96 GB: 20 GB resident is comfortable
    "m4": "andrevp/Qwen3.6-35B-A3B-3bit-MLX",        # M4 Air, 32 GB: the 4-bit swaps, this fits in 14.2 GB
}


def short_host_name(host_name):
    """The machine's own name, without whatever domain the network appended.

    macOS answers gethostname() with m4, m4.local or m4.lan depending on where
    it is plugged in, and none of those is a different machine.
    """
    return host_name.strip().split(".")[0].lower()


def source_model_for_host(host_name):
    """The model this machine has the memory for; see llm_source_model_by_host."""
    return llm_source_model_by_host.get(short_host_name(host_name), llm_source_model_default)


def resolve_llm_source_model(environment, host_name):
    """RVW_LLM_SOURCE_MODEL if it names one, otherwise what the machine can run.

    Set-but-empty counts as saying nothing, because that is how an unset
    variable arrives from a launchd plist.
    """
    return environment.get("RVW_LLM_SOURCE_MODEL") or source_model_for_host(host_name)


llm_source_model = resolve_llm_source_model(os.environ, socket.gethostname())
llm_context_length = 32768
llm_idle_ttl_seconds = 3600
llm_load_timeout_seconds = 900.0        # loading 20 GB from a cold page cache is not quick

# Started through rvw.app the daemon inherits no shell PATH, so lms is absolute.
lms_command = Path.home() / ".lmstudio" / "bin" / "lms"

# Screen capture. "frontmost" captures the frontmost application window only;
# "display" captures the whole display and is therefore never chosen silently.
screenshot_target = os.environ.get("RVW_SCREENSHOT_TARGET", "frontmost")
screenshot_timeout_seconds = 20.0

# Where a screenshot comes from, a global mode set by `bin/rvw --screenshot-source`:
# "screen" is this Mac's own screen; "hdmi" is the other Mac's, read from the
# HDMI capture card named below, which is how the two-Mac arrangement sees the
# work machine. A command line flag, not an environment variable, because the
# daemon runs inside bin/rvw.app and never sees a shell's exports.
screenshot_sources = ("hdmi", "screen")
screenshot_source = "screen"
hdmi_capture_helper_path = bin_dir / "hdmi_capture"
hdmi_capture_device_name = "Elgato 4K X"

# Console OCR: the Vision helper reads an already archived PNG, so it needs no
# screen recording permission and no extra third-party software.
ocr_helper_path = bin_dir / "ocr_image"
ocr_timeout_seconds = 30.0

# Hammerspoon's command line client, which hides the window a typed command came
# from before a screenshot. Named in full because the daemon inside bin/rvw.app
# runs with LaunchServices' PATH, which does not include /usr/local/bin.
hammerspoon_cli_path = Path("/usr/local/bin/hs")


def require_known_stream(stream_name):
    """Reject a capture stream name that the rest of the system cannot label."""
    if stream_name not in stream_labels:
        raise ValueError("unknown capture stream %r" % (stream_name,))


def require_known_screenshot_source(source):
    """Reject a screenshot source with no helper behind it."""
    if source not in screenshot_sources:
        raise ValueError("unknown screenshot source %r; known: %s"
                         % (source, ", ".join(screenshot_sources)))


def require_known_recognition_language(language):
    """Reject a language the recogniser was not offered, rather than listening in it.

    Whisper would accept almost any code and quietly recognise badly, which reads
    as a broken microphone rather than as a wrong setting.
    """
    if language not in recognition_languages:
        raise ValueError("unknown recognition language %r; offered: %s"
                         % (language, ", ".join(recognition_languages)))


def require_known_retention_mode(mode):
    """Reject a retention mode nobody implements, rather than guessing at it.

    Guessing here would mean either keeping a conversation that was meant to be
    ephemeral or discarding one that was meant to be kept.
    """
    if mode not in retention_modes:
        raise ValueError("unknown transcript retention mode %r; known: %s"
                         % (mode, ", ".join(retention_modes)))


def stream_label(stream_name):
    """Human readable side of the conversation for a capture stream name."""
    require_known_stream(stream_name)
    return stream_labels[stream_name]
