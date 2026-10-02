#!/bin/bash
# Report every model this machine has, in both of the places they live.
#
# There are two model stores and LM Studio only knows about one of them:
#
#   LM Studio          the LLM, and any vision model. 'lms ls' is what is
#                      downloaded, 'lms ps' is what is loaded right now.
#   Hugging Face cache Whisper and the diarization model, downloaded by
#                      huggingface_hub into ~/.cache/huggingface. 'lms' will
#                      never show these, and a listing that omitted them would
#                      look complete without being complete.
#
# The last section is the one worth reading: what the endpoint serves is what a
# hotkey will actually reach, and the two lists above can both look healthy
# while it is empty.
#
# An absent identifier is reported as INFO, and this listing says exactly what
# the running assistant says about the same state. Neither absence is a fault.
# LM Studio unloads the LLM after an idle hour by design and model_loader loads
# it again on the first question; a vision model is optional and SCREEN_VISION
# declines cleanly without one. Spending FAIL on either would teach the reader to
# ignore the word. The one genuine failure here is an endpoint serving nothing.

set -o pipefail

script_dir=$(cd "$(dirname "$BASH_SOURCE")" && pwd)
repo_dir=$(cd "$script_dir/.." && pwd)
source "$script_dir/assistant_settings.sh"             # read_assistant_setting, read_llm_server_port

venv_python=$repo_dir/.venv/bin/python
lms_bin_dir=$HOME/.lmstudio/bin

log_ok()      { echo "OK   $*"; }
log_info()    { echo "INFO $*"; }
log_fail()    { echo "FAIL $*" >&2; }
log_heading() { echo; echo "$*"; }

have_lms() {
    export PATH=$lms_bin_dir:$PATH
    command -v lms >/dev/null 2>&1
}

report_downloaded_llm_models() {
    log_heading "LM Studio, downloaded"
    have_lms || { log_fail "lms is not installed; run util/init_local_models.sh"; return 1; }
    lms ls || log_fail "could not list the downloaded models"
}

report_loaded_llm_models() {
    log_heading "LM Studio, loaded right now"
    have_lms || return 1
    lms ps || log_fail "could not list the loaded models"
}

# One identifier per line, as the assistant itself would see them.
served_identifiers() {
    curl -sS --max-time 10 "http://127.0.0.1:$(read_llm_server_port)/v1/models" |
        python3 -c '
import json, sys
for entry in json.load(sys.stdin).get("data", []):
    print(entry["id"])
' 2>/dev/null
}

report_the_identifiers_the_assistant_asks_for() {
    log_heading "Identifiers the assistant asks for"
    local served
    served=$(served_identifiers)
    if [[ -z $served ]]; then
        log_fail "nothing is served at port $(read_llm_server_port); start it with 'lms server start'"
        return 1
    fi
    report_one_identifier "$(read_assistant_setting llm_model)" "EXPLAIN_SPEECH and UNGARBLE_SPEECH" "$served" \
        "it is loaded when it is first needed, which makes that one question slow"
    report_one_identifier "$(read_assistant_setting vision_llm_model)" "SCREEN_VISION" "$served" \
        "SCREEN_VISION declines instead of answering, and alt-cmd-S still archives screenshots"
}

# Each identifier carries its own account of what an absence means, because the
# two absences mean different things and neither is a fault. What must not be
# said is that another model will answer instead: llm.py refuses an identifier
# it cannot see before asking, so no user ever receives that substitution.
report_one_identifier() {
    local identifier=$1 used_by=$2 served=$3 when_absent=$4
    if grep -qxF "$identifier" <<< "$served"; then
        log_ok "$identifier is served, so $used_by will reach it"
        return 0
    fi
    log_info "$identifier is not served; $when_absent"
}

report_speech_models() {
    log_heading "Hugging Face cache, which LM Studio never sees"
    [[ -x $venv_python ]] ||
        { log_fail "no python environment at $venv_python; run util/init_local_models.sh"; return 1; }
    "$venv_python" -c '
from huggingface_hub import scan_cache_dir
cache = scan_cache_dir()
for repo in sorted(cache.repos, key=lambda repo: repo.repo_id):
    print("     %-52s %10s" % (repo.repo_id, repo.size_on_disk_str))
print("     %-52s %10s" % ("total", cache.size_on_disk_str))
' || log_fail "could not scan the Hugging Face cache"
}

# Every section reports on its own, so one broken store still lists the other.
main() {
    report_downloaded_llm_models
    report_loaded_llm_models
    report_the_identifiers_the_assistant_asks_for
    report_speech_models
    echo
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
        main "$@"
fi

exit
$dp/git/rvw/util/list_models.sh
lms ls                                  # downloaded LLMs only
lms ps                                  # loaded right now, with identifier and ttl
curl -s http://127.0.0.1:1234/v1/models # served identifiers only, not what is downloaded
