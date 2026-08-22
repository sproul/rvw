#!/bin/bash
# Install the LaunchAgent that starts the local LLM endpoint at login.
#
# Without it the endpoint is up only until the machine reboots, and the symptom
# is not an error at startup: the assistant listens and transcribes exactly as
# usual, and only a question reveals that there is nothing to answer it. See
# util/start_llm_server.sh, which is what the agent runs.
#
# The plist is generated rather than kept in the repository because it has to
# name the absolute path of this checkout, and the checkout is allowed to live
# anywhere. It is not one of the plists in helper/: those are Info.plists built
# into binaries, an unrelated thing that shares a file extension.
#
# Running this again is expected, because util/init.sh runs it every time.

set -o pipefail

script_dir=$(cd "$(dirname "$BASH_SOURCE")" && pwd)
repo_dir=$(cd "$script_dir/.." && pwd)

label=ai.rvw.llm_server                                # matches the ai.rvw.assistant bundle identifier
plist_path=$HOME/Library/LaunchAgents/$label.plist
server_script=$script_dir/start_llm_server.sh
agent_log=$repo_dir/var/log/llm_server.launchagent.log
service_target=gui/$UID/$label

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

require_the_server_script() {
    [[ -x $server_script ]] || die "no executable $server_script"
}

# RunAtLoad and no KeepAlive: the script starts the server and exits, and
# keeping the server alive afterwards is the llmster daemon's business.
launch_agent_plist_text() {
    cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$label</string>
    <key>ProgramArguments</key>
    <array>
        <string>$server_script</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$agent_log</string>
    <key>StandardErrorPath</key>
    <string>$agent_log</string>
</dict>
</plist>
PLIST
}

# launchd will not bootstrap a plist that group or others can write, and says
# only "Bootstrap failed: 5: Input/output error" when it refuses. Under a
# permissive umask the redirect above writes exactly such a file, so the mode is
# set here rather than left to whatever the shell was configured with.
protect_the_plist_from_other_writers() {
    chmod go-w "$plist_path" || die "could not restrict the permissions of $plist_path"
}

write_launch_agent_plist() {
    run_cmd mkdir -p "$(dirname "$plist_path")" "$(dirname "$agent_log")" ||
        die "could not create the LaunchAgents or log directory"
    if [[ $dry_mode -eq 1 ]]; then
        echo "dry: write $plist_path running $server_script"
        return 0
    fi
    launch_agent_plist_text > "$plist_path" || die "could not write $plist_path"
    protect_the_plist_from_other_writers
    log_ok "wrote $plist_path"
}

# Booting out an agent that is not loaded is how the first install and every
# install after a logout go, so its failure says nothing and is discarded.
remove_any_previously_loaded_agent() {
    if [[ $dry_mode -eq 1 ]]; then
        echo "dry: launchctl bootout $service_target"
        return 0
    fi
    launchctl bootout "$service_target" >/dev/null 2>&1
    return 0
}

# 'launchctl load' is deprecated on this macOS; bootstrap into the gui domain
# is the supported way and the only one that reports a rejected plist.
load_launch_agent() {
    remove_any_previously_loaded_agent
    run_cmd launchctl bootstrap "gui/$UID" "$plist_path" ||
        die "launchd rejected $plist_path"
    log_done "loaded $label; the LLM endpoint will be started at every login"
}

main() {
    require_the_server_script
    write_launch_agent_plist
    load_launch_agent
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
$dp/git/rvw/util/init_llm_autostart.sh
$dp/git/rvw/util/init_llm_autostart.sh -dry
launchctl print gui/$UID/ai.rvw.llm_server | head -20
launchctl kickstart -k gui/$UID/ai.rvw.llm_server    # run it now, as login would
launchctl bootout gui/$UID/ai.rvw.llm_server         # stop starting it at login
