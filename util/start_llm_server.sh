#!/bin/bash
# Bring up the local LLM endpoint, and do nothing if it is already up.
#
# 'lms server' has start, stop and status and no way to say "at login", and the
# installer starts the server once, at setup time. After a reboot the assistant
# therefore starts perfectly and every question fails until somebody types 'lms
# server start'. util/init_llm_autostart.sh installs a LaunchAgent that runs
# this script instead, and util/init_local_models.sh runs the same script rather
# than starting the server its own way.
#
# Two consequences of being run by launchd shape what is below. The environment
# is almost empty and holds nothing from a shell profile, so lms is invoked by
# the absolute path recorded in src/rvw/config.py rather than found on PATH; a
# bare 'lms' works in every test anyone runs by hand and is missing at the one
# moment that matters. And the script runs at every login as well as from the
# installer, so an already running daemon and an already listening server are
# the ordinary case and are left alone.

set -o pipefail

script_dir=$(cd "$(dirname "$BASH_SOURCE")" && pwd)
source "$script_dir/assistant_settings.sh"             # read_assistant_setting, read_llm_server_port

dry_mode=0                                             # set by -dry; suppresses every mutating command

log_ok()   { echo "OK   $*"; }
log_fail() { echo "FAIL $*" >&2; }
die()      { log_fail "$*"; exit 1; }
log_done() { [[ $dry_mode -eq 1 ]] || log_ok "$*"; }   # dry mode did it not, so it says so not

# Run a mutating command, honouring dry mode.
run_cmd() {
    if [[ $dry_mode -eq 1 ]]; then
        echo "dry: $*"
        return 0
    fi
    "$@"
}

lms_command=$(read_assistant_setting lms_command) || die "cannot read lms_command from src/rvw/config.py"
llm_server_port=$(read_llm_server_port) || die "cannot read the port from config.llm_base_url"

require_lms_command() {
    [[ -x $lms_command ]] || die "no lms at $lms_command; run util/init_local_models.sh"
}

start_llmster_daemon() {
    if "$lms_command" daemon status >/dev/null 2>&1; then
        log_ok "llmster daemon already running"
        return 0
    fi
    run_cmd "$lms_command" daemon up || die "could not start the llmster daemon"
    log_done "started the llmster daemon"
}

# A server listening on some other port is not a working server: the assistant
# asks at config.llm_base_url and nowhere else.
server_is_listening_on_the_configured_port() {
    "$lms_command" server status --json 2>/dev/null | python3 -c '
import json, sys
status = json.load(sys.stdin)
raise SystemExit(0 if status.get("running") and status.get("port") == int(sys.argv[1]) else 1)
' "$llm_server_port" 2>/dev/null
}

start_llm_server() {
    if server_is_listening_on_the_configured_port; then
        log_ok "OpenAI-compatible API already listening on port $llm_server_port"
        return 0
    fi
    run_cmd "$lms_command" server start --port "$llm_server_port" ||
        die "could not start the LM Studio server on port $llm_server_port"
    log_done "OpenAI-compatible API listening on port $llm_server_port"
}

main() {
    require_lms_command
    start_llmster_daemon
    start_llm_server
}

parse_command_line() {
    while (( $# >= 1 )); do
        case "$1" in
            -dry)
                dry_mode=1
            ;;
            -x)
                set -x
            ;;
            *)
                die "unrecognized flag $1"
            ;;
        esac
        shift
    done
    [[ $dry_mode -eq 1 ]] && log_ok "dry mode: no changes will be made"
    return 0
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
        parse_command_line "$@"
        main
fi

exit
$dp/git/rvw/util/start_llm_server.sh
$dp/git/rvw/util/start_llm_server.sh -dry
launchctl kickstart -k gui/$UID/ai.rvw.llm_server   # what login does, on demand
tail -f $dp/git/rvw/var/log/llm_server.launchagent.log
