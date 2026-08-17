#!/bin/bash
# Download, install and run the local models used by the listening assistant:
#   1.) software analysis and generation:  Qwen3.6-35B-A3B 4-bit MLX
#                                          served by llmster (LM Studio MLX runtime)
#                                          over the OpenAI-compatible API
#   2.) interpretation of recorded conversations:
#                                          Whisper large-v3-turbo (MLX)  -- transcription
#                                          pyannote speaker-diarization-community-1 -- who spoke when
# See ../doc/models.summary and ../doc/models for the reasoning behind these choices.

set -o pipefail

script_dir=$(cd "$(dirname "$BASH_SOURCE")" && pwd)
repo_dir=$(cd "$script_dir/.." && pwd)
source "$script_dir/ffmpeg_env.sh"                     # add_ffmpeg_libs_to_dyld_path, shared with bin/rvw
venv_dir=$repo_dir/.venv
venv_python=$venv_dir/bin/python
python_request=cpython-3.12-macos-aarch64-none          # spelled out so an x86_64 build is never selected

llm_model=mlx-community/Qwen3.6-35B-A3B-4bit    # override with the first positional argument
whisper_model=mlx-community/whisper-large-v3-turbo
diarization_model=pyannote/speaker-diarization-community-1

llm_identifier=meeting-assistant
llm_context_length=32768
llm_idle_ttl_seconds=3600
llm_server_port=1234
llm_server_url=http://127.0.0.1:$llm_server_port/v1

hf_token_file=$HOME/.huggingface_token
lms_bin_dir=$HOME/.lmstudio/bin

dry_mode=0                                              # set by -dry; suppresses every mutating command
yes_mode=0                                              # set by -y/-yes; approves installs without prompting (like "yum -y")
ffmpeg_ready=0                                          # set once ensure_ffmpeg has installed ffmpeg and exposed its libs

log_ok()   { echo "OK   $*"; }
log_fail() { echo "FAIL $*" >&2; }
die()      { log_fail "$*"; exit 1; }

# Run a mutating command, honouring dry mode.
run_cmd() {
    if [[ $dry_mode -eq 1 ]]; then
        echo "dry: $*"
        return 0
    fi
    "$@"
}

# Emit an assume-yes flag for an install command when yes mode is active, so installs run unattended.
assume_yes_flag() {
    [[ $yes_mode -eq 1 ]] && printf '%s' "$1"
    return 0
}

require_apple_silicon_mac() {
    [[ $(uname -s) == Darwin ]] || die "this script supports macOS only (MLX requires Apple Silicon)"
    [[ $(uname -m) == arm64 ]]  || die "Apple Silicon required for the MLX runtime, found $(uname -m)"
    log_ok "host is an Apple Silicon Mac"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "$1 is required but not on PATH${2:+ ($2)}"
}

# The diarization model is gated on Hugging Face, so a token must exist before we start downloading.
read_hf_token() {
    [[ -f $hf_token_file ]] || die "missing $hf_token_file (put your Hugging Face token there, chmod 600)"
    export HF_TOKEN=$(tr -d ' \t\r\n' < "$hf_token_file")
    [[ -n $HF_TOKEN ]] || die "$hf_token_file is empty"
    log_ok "read Hugging Face token from $hf_token_file"
}

check_preconditions() {
    require_apple_silicon_mac
    require_command curl
    require_command python3
    require_native_uv
    read_hf_token
}

# An x86_64 uv running under Rosetta can only build x86_64 environments, and MLX has arm64 wheels only.
require_native_uv() {
    if command -v uv >/dev/null 2>&1 && file -b "$(command -v uv)" | grep -q arm64; then
        log_ok "uv is a native arm64 build"
        return 0
    fi
    run_cmd bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' || die "could not install a native uv"
    hash -r
    log_ok "installed a native arm64 uv"
}

install_ffmpeg_if_missing() {
    if command -v ffmpeg >/dev/null 2>&1; then
        log_ok "ffmpeg already installed"
        return 0
    fi
    require_command brew "needed to install ffmpeg"
    local -a brew_env=()
    [[ $yes_mode -eq 1 ]] && brew_env=(env NONINTERACTIVE=1)
    run_cmd "${brew_env[@]}" brew install ffmpeg || die "could not install ffmpeg"
    log_ok "installed ffmpeg"
}

# Expose ffmpeg's libraries for torchcodec (see util/ffmpeg_env.sh) and report where they were found.
export_ffmpeg_library_path() {
    [[ $dry_mode -eq 1 ]] && return 0
    add_ffmpeg_libs_to_dyld_path || die "could not locate ffmpeg's library directory"
    log_ok "ffmpeg libraries on DYLD_LIBRARY_PATH: $ffmpeg_lib_dir"
}

# Call this before anything that decodes audio through ffmpeg. It installs ffmpeg on first use and
# exposes its libraries; the guard makes repeated calls cheap so every ffmpeg-dependent step can gate on it.
ensure_ffmpeg() {
    [[ $ffmpeg_ready -eq 1 ]] && return 0
    install_ffmpeg_if_missing
    export_ffmpeg_library_path
    ffmpeg_ready=1
}

# llmster is the GUI-less LM Studio service; its CLI is 'lms'.
install_llmster_if_missing() {
    export PATH=$lms_bin_dir:$PATH
    if command -v lms >/dev/null 2>&1; then
        log_ok "lms already installed at $(command -v lms)"
        return 0
    fi
    run_cmd bash -c 'curl -fsSL https://lmstudio.ai/install.sh | bash -s -- --quiet' ||
        die "llmster installation failed"
    command -v lms >/dev/null 2>&1 || [[ $dry_mode -eq 1 ]] || die "lms not on PATH after installation; expected it in $lms_bin_dir"
    log_ok "installed llmster; add $lms_bin_dir to PATH in your shell profile"
}

start_llmster_daemon() {
    if lms daemon status >/dev/null 2>&1; then
        log_ok "llmster daemon already running"
        return 0
    fi
    run_cmd lms daemon up || die "could not start the llmster daemon"
    log_ok "started the llmster daemon"
}

# A bare "owner/name" is resolved against the LM Studio Hub, so the full URL is needed for Hugging Face.
download_llm_model() {
    run_cmd lms get "https://huggingface.co/$llm_model" --mlx $(assume_yes_flag --yes) || die "could not download $llm_model"
    log_ok "downloaded $llm_model"
}

# 'lms load' wants the local model key, which is not identical to the Hugging Face repo id.
resolve_llm_model_key() {
    lms ls --llm --json 2>/dev/null | python3 -c '
import json, re, sys

def normalize(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())

listing = sys.stdin.read().strip()
if not listing:
    sys.exit(0)
wanted = normalize(sys.argv[1].split("/")[-1])
for model in json.loads(listing):
    key = model.get("modelKey", "")
    if wanted in normalize(key) or wanted in normalize(model.get("path", "")):
        print(key)
        break
' "$llm_model"
}

report_llm_memory_estimate() {
    lms load --estimate-only "$1" --context-length "$llm_context_length" ||
        log_fail "memory estimate unavailable for $1 (continuing)"
}

load_llm_model() {
    local model_key
    model_key=$(resolve_llm_model_key)
    if [[ -z $model_key ]]; then
        [[ $dry_mode -eq 1 ]] && return 0
        die "could not find a downloaded model matching $llm_model in 'lms ls'"
    fi
    report_llm_memory_estimate "$model_key"
    if lms ps --json 2>/dev/null | grep -q "\"$llm_identifier\""; then
        log_ok "$llm_identifier already loaded"
        return 0
    fi
    run_cmd lms load "$model_key" --context-length "$llm_context_length" \
        --ttl "$llm_idle_ttl_seconds" --identifier "$llm_identifier" || die "could not load $model_key"
    log_ok "loaded $model_key as '$llm_identifier' (context $llm_context_length, ttl ${llm_idle_ttl_seconds}s)"
}

start_llm_server() {
    run_cmd lms server start --port "$llm_server_port" || die "could not start the LM Studio server"
    log_ok "OpenAI-compatible API listening on $llm_server_url"
}

# Qwen3.6 is a reasoning model, so the answer may arrive as reasoning_content when the budget is tight.
verify_llm_server() {
    [[ $dry_mode -eq 1 ]] && return 0
    local answer
    answer=$(curl -sS --max-time 600 "$llm_server_url/chat/completions" \
        -H 'Content-Type: application/json' \
        -d "{\"model\":\"$llm_identifier\",\"max_tokens\":512,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the single word: ready\"}]}" |
        python3 -c '
import json, sys
message = json.load(sys.stdin)["choices"][0]["message"]
print((message.get("content") or message.get("reasoning_content") or "").strip())
' 2>/dev/null)
    [[ -n $answer ]] || die "no usable answer from $llm_server_url"
    log_ok "LLM smoke test answered: $(echo "$answer" | tr -d '\n' | cut -c1-60)"
}

# MLX ships arm64 wheels only, so an x86_64 python found on PATH would make the install unsolvable.
create_python_env() {
    [[ -x $venv_python ]] ||
        run_cmd uv venv --python-preference only-managed --python "$python_request" "$venv_dir" ||
        die "could not create $venv_dir"
    require_arm64_python
    run_cmd uv pip install --python "$venv_dir" mlx-whisper 'pyannote.audio>=4.0' huggingface-hub ||
        die "could not install the speech packages into $venv_dir"
    log_ok "python environment ready at $venv_dir"
}

require_arm64_python() {
    [[ $dry_mode -eq 1 ]] && return 0
    local machine
    machine=$("$venv_python" -c 'import platform; print(platform.machine())')
    [[ $machine == arm64 ]] || die "$venv_python is $machine, not arm64; delete $venv_dir and rerun"
}

download_whisper_model() {
    run_cmd "$venv_python" -c '
import sys
from huggingface_hub import snapshot_download
print(snapshot_download(sys.argv[1]))
' "$whisper_model" || die "could not download $whisper_model"
    log_ok "downloaded $whisper_model"
}

# Instantiating the pipeline downloads every checkpoint it needs into the Hugging Face cache.
download_diarization_model() {
    run_cmd "$venv_python" -c '
import os, sys
from pyannote.audio import Pipeline
pipeline = Pipeline.from_pretrained(sys.argv[1], token=os.environ["HF_TOKEN"])
if pipeline is None:
    raise SystemExit("pipeline is None: accept the model conditions on its Hugging Face page")
' "$diarization_model" || die "could not download $diarization_model (accept its conditions at https://huggingface.co/$diarization_model)"
    log_ok "downloaded $diarization_model"
}

# 'say' gives us a deterministic offline sample so the speech stack can be exercised without a recording.
make_speech_test_clip() {
    local clip=$1
    say -o "$clip" --file-format=WAVE --data-format=LEI16@16000 \
        "This is a local transcription test of the meeting assistant." || die "could not synthesize $clip"
}

verify_speech_models() {
    ensure_ffmpeg
    [[ $dry_mode -eq 1 ]] && return 0
    local clip=$repo_dir/.speech_selftest.wav
    make_speech_test_clip "$clip"
    "$venv_python" - "$clip" "$whisper_model" "$diarization_model" <<'PY' || die "speech self test failed"
import os, sys
import mlx_whisper
from pyannote.audio import Pipeline

clip, whisper_model, diarization_model = sys.argv[1:4]
text = mlx_whisper.transcribe(clip, path_or_hf_repo=whisper_model)["text"].strip()
if not text:
    raise SystemExit("whisper returned an empty transcript")
print("OK   whisper transcript: " + text)

pipeline = Pipeline.from_pretrained(diarization_model, token=os.environ["HF_TOKEN"])
# pyannote 4 returns a DiarizeOutput; exclusive_speaker_diarization is the one to align with the transcript
turns = list(pipeline(clip).exclusive_speaker_diarization.itertracks(yield_label=True))
if not turns:
    raise SystemExit("diarization returned no speaker turns")
print("OK   diarization produced %d speaker turn(s)" % len(turns))
PY
    rm -f "$clip"
    log_ok "speech stack verified end to end"
}

print_summary() {
    cat <<SUMMARY

OK   local model stack ready
       reasoning/coding : $llm_model as '$llm_identifier' at $llm_server_url
       transcription    : $whisper_model
       diarization      : $diarization_model
       python env       : $venv_dir
       PATH addition    : $lms_bin_dir
SUMMARY
}

main() {
    check_preconditions
    install_llmster_if_missing
    start_llmster_daemon
    download_llm_model
    load_llm_model
    start_llm_server
    verify_llm_server
    create_python_env
    download_whisper_model
    download_diarization_model
    verify_speech_models
    print_summary
}

debug_mode=''
dry_mode=0
t=`mktemp`; trap "rm $t*" EXIT
verbose_mode=''
while (( $# >= 1 )); do
        case "$1" in
                -dry)
                        dry_mode=1
                ;;
                -q|-quiet)
                        verbose_mode=''
                ;;
                -v|-verbose)
                        verbose_mode=-v
                ;;
                -x)
                        set -x
                        debug_mode=-x
                ;;
                -y|-yes)
                        yes_mode=1
                ;;
                -*)
                        echo "FAIL unrecognized flag $1" 1>&2
                        exit 1
                ;;
                *)
                        break
                ;;
        esac
        shift
done

[[ $dry_mode -eq 1 ]] && log_ok "dry mode: no changes will be made"
[[ $# -ge 1 ]] && llm_model=$1

main

exit
$dp/git/rvw/util/init_local_models.sh mlx-community/Qwen3.6-35B-A3B-4bit
$dp/git/rvw/util/init_local_models.sh -dry
lms ps
lms unload --all
curl http://127.0.0.1:1234/v1/models
exit
$dp/git/rvw/util/init_local_models.sh -x -y
