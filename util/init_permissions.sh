#!/bin/bash
# Provoke, check and report every macOS privacy permission the listening
# assistant needs. macOS has no API to grant a permission from a script: the
# only way to raise the system prompt is to perform the protected action once,
# which is exactly what this script does with the real helpers. Once a
# permission has been denied macOS stops prompting, so a denied permission is
# reported here with the System Settings pane to open and the tccutil command
# that makes macOS ask again.
#
# The permissions, and why each one is needed:
#
#   Microphone        bin/audio_capture --source mic records my own voice with
#                     AVAudioEngine. Without it the helper would be handed
#                     silence for ever, so it refuses to run instead.
#   Audio Recording   bin/audio_capture --source system installs a Core Audio
#                     process tap to hear what the Mac plays back, which is the
#                     other side of the conversation. The tap is observational:
#                     playback and headphones are unaffected.
#   Screen Recording  bin/screen_capture archives the frontmost window with
#                     ScreenCaptureKit for alt-cmd-S and ctrl-alt-cmd-S.
#   Accessibility     Hammerspoon owns the global hotkeys, so it needs this and
#                     the assistant itself does not.
#
# Every one of these grants except Accessibility goes to the application
# responsible for the helper, never to the helper itself, and that application
# is always bin/rvw.app. This script probes through the bundle for exactly that
# reason: a helper started any other way would put the grant on this terminal,
# where it would be worth nothing the next time the assistant is started from
# somewhere else.
#
# Answering these three prompts once is the whole of it. rvw.app is the only
# identity the assistant ever presents, so it no longer matters whether the
# daemon is started from a terminal, from Emacs or from a hotkey, and no other
# application ever needs a grant. That also settles a permission Emacs.app
# cannot hold at all: it is signed with the hardened runtime and without
# com.apple.security.device.audio-input, so macOS refuses it the microphone
# without ever asking and never lists it in System Settings.
#
# The one thing that voids these grants is re-signing the bundle, because an ad
# hoc signature is pinned to the bytes of the launcher inside it. Rebuilding the
# python daemon or either capture helper is free; helper/build_app.sh is the
# only thing that can cost a grant, and it rebuilds only when it must.
#
# Hammerspoon's Accessibility grant is its own and is needed only once: the
# hotkeys reach the daemon through bin/rvwctl and a unix socket, and the daemon
# spawns the helpers, so Hammerspoon itself never needs the capture permissions.

set -o pipefail

script_dir=$(cd "$(dirname "$BASH_SOURCE")" && pwd)
repo_dir=$(cd "$script_dir/.." && pwd)
# The helpers are named rather than pathed: rvw.app resolves them inside its own
# bin directory, and naming them twice would be one name too many.
audio_helper=audio_capture
screen_helper=screen_capture
app_bundle=$repo_dir/bin/rvw.app
app_bundle_id=ai.rvw.assistant
log_dir=$repo_dir/var/log

# A permission that has never been asked about raises a prompt, and a human has
# to walk to the keyboard and answer it. The probes therefore wait this long for
# the helper to say what happened, and stop the moment it has said it.
probe_deadline_seconds=60

# Stopping a helper is quick, and the launcher only has one line left to write.
exit_report_attempts=50
exit_report_poll_seconds=0.1

dry_mode=0                              # set by -dry; probes nothing, changes nothing
open_settings_mode=0                    # set by -open; opens the settings pane of each missing permission
reset_mode=0                            # set by -reset; clears the decisions so macOS asks again

failed_permissions=()

log_ok()   { echo "OK   $*"; }
log_fail() { echo "FAIL $*" >&2; }
die()      { log_fail "$*"; exit 1; }

report_the_permission_holder() {
    log_ok "permissions are held by $app_bundle ($app_bundle_id)"
    log_ok "start the assistant with bin/rvw from anywhere; it always presents this identity"
}

# Run a capture helper through rvw.app until it has said whether it can capture,
# then stop it. The helper keeps running once it succeeds, because capturing is
# its whole job, so the probe is the one that decides when enough is known.
#
# LaunchServices, not a plain fork, is what makes the bundle responsible for the
# helper; started any other way the grant would land on this terminal instead.
run_helper_through_the_app() {
    local program=$1 success_phrase=$2; shift 2
    local log=$log_dir/$program.launcher.log
    mkdir -p "$log_dir" || die "cannot create $log_dir"
    : > "$log"
    open -n -a "$app_bundle" --args --run "$program" "$@" || die "cannot start $app_bundle"
    wait_for_helper_verdict "$log" "$success_phrase"
    stop_the_helper_named_in "$log"
    cat "$log"
}

# The verdict is the success phrase, any FAIL line, or the helper exiting;
# anything else is a prompt still waiting to be answered.
wait_for_helper_verdict() {
    local log=$1 success_phrase=$2
    local waited=0
    while (( waited < probe_deadline_seconds )); do
        LC_ALL=C grep -q -e "$success_phrase" -e '^FAIL ' -e 'exited with status' "$log" && return 0
        sleep 1
        waited=$((waited + 1))
    done
    log_fail "the helper never said whether it could capture; assuming the permission is missing"
}

# The launcher records the helper's pid before anything else it says.
stop_the_helper_named_in() {
    local log=$1 pid
    pid=$(sed -n 's/^OK   started .* as pid \([0-9][0-9]*\)$/\1/p' "$log" | tail -1)
    [[ -n $pid ]] && kill "$pid" 2>/dev/null
    wait_until_the_launcher_reported_the_exit "$log"
    return 0
}

# Both audio probes read the same log, because the launcher names it after the
# program and there is only one audio helper. The launcher outlives its helper
# by the moment it takes to report the exit and holds that log open for
# appending, so returning as soon as the helper has spoken leaves the launcher
# free to append "exited with status 0" into the log the next probe has just
# emptied. The next probe reads that line as its own helper's verdict and calls
# a granted permission missing, which is exactly what happened to whichever
# audio probe ran second. Waiting here for the exit line makes the log quiet
# before anybody empties it again.
wait_until_the_launcher_reported_the_exit() {
    local log=$1 attempts=0
    while (( attempts < exit_report_attempts )); do
        LC_ALL=C grep -q 'exited with status' "$log" && return 0
        sleep "$exit_report_poll_seconds"
        attempts=$((attempts + 1))
    done
    log_fail "the launcher never reported the helper's exit, so $log may still be written to"
}

require_the_helpers_and_the_bundle_are_built() {
    [[ -x $repo_dir/bin/$audio_helper && -x $repo_dir/bin/$screen_helper && -d $app_bundle ]] &&
        return 0
    log_ok "building the capture helpers and rvw.app first"
    "$repo_dir/helper/build.sh" >/dev/null || die "helper/build.sh failed; build it by hand and rerun"
}

# The helpers report one line per event; the last one says why they stopped. The
# launcher's own bookkeeping frames those lines and would hide them, so it is
# reported only when the helper itself said nothing at all.
# LC_ALL=C throughout: a helper's diagnostics are external data and a stray byte
# must not stop sed and grep from reading the rest.
last_diagnostic_line() {
    local lines
    lines=$(printf '%s\n' "$1" | LC_ALL=C sed -e 's/^[[:space:]]*//' -e '/^$/d')
    printf '%s\n' "$lines" |
        LC_ALL=C grep -v -e 'started .* as pid [0-9]' -e 'exited with status' |
        tail -1 | LC_ALL=C grep . ||
        printf '%s\n' "$lines" | tail -1
}

# Accessibility belongs to Hammerspoon, every other permission to rvw.app.
permission_reset_target() {
    if [[ $1 == Accessibility ]]; then
        echo "org.hammerspoon.Hammerspoon"
        return 0
    fi
    echo "$app_bundle_id"
}

# record_result <name> <tcc service> <succeeded> <diagnostic>
record_result() {
    local name=$1 service=$2 succeeded=$3 diagnostic=$4
    if [[ $succeeded -eq 1 ]]; then
        log_ok "$name permission is granted"
        return 0
    fi
    log_fail "$name permission is missing: ${diagnostic:-no diagnostic}"
    log_fail "  open  $(settings_url_for_service "$service")"
    log_fail "  or make macOS ask again:  tccutil reset $service $(permission_reset_target "$service")"
    [[ $open_settings_mode -eq 1 ]] && open "$(settings_url_for_service "$service")"
    failed_permissions+=("$name")
    return 1
}

settings_url_for_service() {
    case "$1" in
        Accessibility) echo "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" ;;
        AudioCapture)  echo "x-apple.systempreferences:com.apple.preference.security?Privacy_AudioCapture" ;;
        Microphone)    echo "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone" ;;
        ScreenCapture) echo "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture" ;;
        *)             echo "x-apple.systempreferences:com.apple.preference.security?Privacy" ;;
    esac
}

# probe_audio_source <name> <tcc service> <--source value> <phrase the helper prints on success>
probe_audio_source() {
    local name=$1 service=$2 source=$3 success_phrase=$4
    local diagnostic succeeded=0
    log_ok "probing $name; answer the macOS prompt if one appears"
    diagnostic=$(run_helper_through_the_app "$audio_helper" "$success_phrase" --source "$source")
    [[ $diagnostic == *"$success_phrase"* ]] && succeeded=1
    record_result "$name" "$service" "$succeeded" "$(last_diagnostic_line "$diagnostic")"
}

check_microphone_permission() {
    probe_audio_source "Microphone" Microphone mic "capturing the microphone"
}

check_system_audio_permission() {
    probe_audio_source "Audio Recording (system audio tap)" AudioCapture system \
        "capturing system audio"
}

check_screen_recording_permission() {
    local diagnostic succeeded=0
    local image=$t.png
    log_ok "probing Screen Recording; answer the macOS prompt if one appears"
    diagnostic=$(run_helper_through_the_app "$screen_helper" "captured the" \
                     --output "$image" --target frontmost)
    [[ -s $image ]] && succeeded=1
    rm -f "$image"                       # the probe image is not archive material
    record_result "Screen Recording" ScreenCapture "$succeeded" \
        "$(last_diagnostic_line "$diagnostic")" ||
        explain_how_screen_recording_is_granted
}

# Screen recording is the one permission macOS will not grant from its own
# prompt. The first attempt always fails, whatever is clicked; all the prompt
# does is add the application to the Screen Recording list, and the switch there
# is what actually grants it. Probing again afterwards is enough, because each
# probe starts a fresh copy of rvw.app.
explain_how_screen_recording_is_granted() {
    log_fail "  macOS never grants this one from the prompt: switch rvw on in System Settings,"
    log_fail "  Privacy and Security, Screen Recording, then run this script again"
}

# Hammerspoon holds this one, and it can answer for itself.
check_hammerspoon_accessibility() {
    if ! command -v hs >/dev/null 2>&1; then
        log_fail "the Hammerspoon CLI 'hs' is not installed, so the hotkeys cannot be checked"
        log_fail "  install Hammerspoon, then enable its command line tool in its preferences"
        failed_permissions+=("Accessibility (Hammerspoon)")
        return 0
    fi
    local answer succeeded=0
    answer=$(hs -c 'print(hs.accessibilityState())' 2>&1)
    [[ $answer == *true* ]] && succeeded=1
    record_result "Accessibility (Hammerspoon hotkeys)" Accessibility "$succeeded" "$answer"
}

reset_permissions() {
    local service
    for service in AudioCapture Microphone ScreenCapture; do
        if tccutil reset "$service" "$app_bundle_id" >/dev/null 2>&1; then
            log_ok "reset $service for $app_bundle_id"
        else
            log_fail "cannot reset $service; macOS has no record of $app_bundle_id yet"
        fi
    done
    log_ok "run this script again to be asked afresh"
}

print_summary() {
    if [[ ${#failed_permissions[@]} -eq 0 ]]; then
        log_ok "every permission the assistant needs is granted"
        return 0
    fi
    log_fail "missing permission(s): ${failed_permissions[*]}"
    log_fail "grant them in System Settings, Privacy and Security, then run this script again"
    return 1
}

main() {
    report_the_permission_holder
    if [[ $dry_mode -eq 1 ]]; then
        log_ok "dry mode: not probing, because probing is what raises the prompts"
        return 0
    fi
    [[ $reset_mode -eq 1 ]] && { reset_permissions; return 0; }
    require_the_helpers_and_the_bundle_are_built
    check_microphone_permission
    check_system_audio_permission
    check_screen_recording_permission
    check_hammerspoon_accessibility
    print_summary
}

parse_command_line() {
        while (( $# >= 1 )); do
                case "$1" in
                        -dry)
                                dry_mode=1
                        ;;
                        -open)
                                open_settings_mode=1
                        ;;
                        -reset)
                                reset_mode=1
                        ;;
                        -x)
                                set -x
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
}

# Executed, this probes everything. Sourced, it only defines the functions, so
# that test/test_permission_probe.py can exercise them without touching a single
# macOS permission.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
        t=`mktemp`; trap "rm -f $t*" EXIT
        parse_command_line "$@"
        main
fi

exit
$dp/git/rvw/util/init_permissions.sh
$dp/git/rvw/util/init_permissions.sh -open       # open the settings pane of anything missing
$dp/git/rvw/util/init_permissions.sh -reset      # make macOS ask again after a refusal
tccutil reset ScreenCapture com.apple.Terminal
tccutil reset Microphone com.apple.Terminal
exit
$dp/git/rvw/util/init_permissions.sh -x -open