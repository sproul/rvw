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
# Every one of these grants except Accessibility goes to the application that
# launches the helper, not to the helper: the terminal, the editor or the IDE.
# Terminal.app, iTerm, Emacs.app and an IDE are four different applications and
# each prompts once, so run this script from whichever one will actually launch
# bin/rvw; it prints the application it resolved. Within one application the
# grant is permanent and survives reboots and rebuilds of the helpers.
#
# Not every application can hold every grant. An application signed with the
# hardened runtime needs com.apple.security.device.audio-input before macOS will
# even ask about the microphone, and Emacs.app is signed with the hardened
# runtime and without that entitlement: a helper launched from Emacs is refused
# the microphone in under a second, no prompt appears and Emacs never joins the
# Microphone list in System Settings. The other three permissions are not gated
# this way, which is why Emacs can tap system audio and capture the screen but
# never record a voice. Run this script from Terminal or iTerm, which are not
# hardened, or give the daemon an application bundle of its own.
#
# Hammerspoon's Accessibility grant is its own and is needed only once: the
# hotkeys reach the daemon through bin/rvwctl and a unix socket, and the daemon
# spawns the helpers, so Hammerspoon itself never needs the capture permissions.
# Starting the daemon from Hammerspoon or from a LaunchAgent instead would move
# the responsibility, and macOS would ask again.

set -o pipefail

script_dir=$(cd "$(dirname "$BASH_SOURCE")" && pwd)
repo_dir=$(cd "$script_dir/.." && pwd)
audio_helper=$repo_dir/bin/audio_capture
screen_helper=$repo_dir/bin/screen_capture

# A permission that has never been asked about raises a prompt, and a human has
# to walk to the keyboard and answer it. The probes therefore wait this long for
# the helper to say what happened, and stop the moment it has said it.
audio_probe_deadline_seconds=60

dry_mode=0                              # set by -dry; probes nothing, changes nothing
open_settings_mode=0                    # set by -open; opens the settings pane of each missing permission
reset_mode=0                            # set by -reset; clears the decisions so macOS asks again

failed_permissions=()

log_ok()   { echo "OK   $*"; }
log_fail() { echo "FAIL $*" >&2; }
die()      { log_fail "$*"; exit 1; }

# Which application macOS will attribute the permissions to. TCC blames the
# outermost application bundle, not the helper process inside it, so the first
# ".app" in the path is the one that matters.
responsible_application_path() {
    local pid=$$
    local command_path
    while [[ $pid -gt 1 ]]; do
        command_path=$(ps -o comm= -p "$pid" 2>/dev/null)
        if [[ $command_path == *.app/* ]]; then
            echo "${command_path%%.app/*}.app"
            return 0
        fi
        pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
        [[ -n $pid ]] || break
    done
    return 1
}

responsible_bundle_id() {
    local app_path
    app_path=$(responsible_application_path) || return 1
    defaults read "$app_path/Contents/Info" CFBundleIdentifier 2>/dev/null
}

report_responsible_application() {
    local app_path bundle_id
    app_path=$(responsible_application_path)
    bundle_id=$(responsible_bundle_id)
    if [[ -z $bundle_id ]]; then
        log_fail "cannot tell which application is asking; grants will go to whatever launched this shell"
        return 0
    fi
    log_ok "permissions will be attributed to ${app_path:-this application} ($bundle_id)"
    log_ok "run bin/rvw from this same application, or grant the permissions again there"
}

# Run a capture helper until it has said whether it can capture, then stop it.
# The helper keeps running once it succeeds, because capturing is its whole job,
# so the probe is the one that decides when enough has been learned.
run_helper_until_it_reports() {
    local success_phrase=$1; shift
    local diagnostics=$t.helper
    : > "$diagnostics"
    "$@" > /dev/null 2> "$diagnostics" &
    local helper_pid=$!
    wait_for_helper_verdict "$helper_pid" "$diagnostics" "$success_phrase"
    kill "$helper_pid" 2>/dev/null
    wait "$helper_pid" 2>/dev/null
    cat "$diagnostics"
}

# The verdict is the success phrase, any FAIL line, or the helper giving up and
# exiting; anything else is a prompt still waiting to be answered.
wait_for_helper_verdict() {
    local helper_pid=$1 diagnostics=$2 success_phrase=$3
    local waited=0
    while (( waited < audio_probe_deadline_seconds )); do
        grep -q -e "$success_phrase" -e '^FAIL ' "$diagnostics" && return 0
        kill -0 "$helper_pid" 2>/dev/null || return 0
        sleep 1
        waited=$((waited + 1))
    done
    log_fail "the helper never said whether it could capture; assuming the permission is missing"
}

require_helpers_are_built() {
    [[ -x $audio_helper && -x $screen_helper ]] && return 0
    log_ok "building the capture helpers first"
    "$repo_dir/helper/build.sh" >/dev/null || die "helper/build.sh failed; build it by hand and rerun"
}

# The helpers report one line per event; the last one says why they stopped.
last_diagnostic_line() {
    printf '%s\n' "$1" | sed -e 's/^[[:space:]]*//' -e '/^$/d' | tail -1
}

# Accessibility belongs to Hammerspoon, every other permission to whatever
# application launches the helpers.
permission_reset_target() {
    if [[ $1 == Accessibility ]]; then
        echo "org.hammerspoon.Hammerspoon"
        return 0
    fi
    responsible_bundle_id
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
    diagnostic=$(run_helper_until_it_reports "$success_phrase" "$audio_helper" --source "$source")
    [[ $diagnostic == *"$success_phrase"* ]] && succeeded=1
    record_result "$name" "$service" "$succeeded" "$(last_diagnostic_line "$diagnostic")"
}

check_microphone_permission() {
    probe_audio_source "Microphone" Microphone mic "capturing the microphone" ||
        explain_a_microphone_that_can_never_be_granted
}

# macOS refuses the microphone outright to an application signed with the
# hardened runtime but without com.apple.security.device.audio-input: the
# request is denied in under a second, no prompt is shown, and the application
# never appears in the Microphone list in System Settings, so there is nothing
# there to switch on. Emacs.app is signed exactly that way; Terminal.app is not
# hardened at all and Chrome, Slack, Firefox and Zoom carry the entitlement.
explain_a_microphone_that_can_never_be_granted() {
    local app_path
    app_path=$(responsible_application_path) || return 0
    responsible_application_can_hold_a_microphone_grant "$app_path" && return 0
    log_fail "  $(basename "$app_path") is signed with the hardened runtime and without the"
    log_fail "  microphone entitlement, so macOS will never grant it and never list it there"
    log_fail "  run this script and bin/rvw from Terminal or iTerm instead"
}

responsible_application_can_hold_a_microphone_grant() {
    codesign -dv "$1" 2>&1 | grep -q 'flags=0x[0-9a-f]*(.*runtime' || return 0
    codesign -d --entitlements - "$1" 2>&1 |
        grep -q 'com.apple.security.device.audio-input'
}

check_system_audio_permission() {
    probe_audio_source "Audio Recording (system audio tap)" AudioCapture system \
        "capturing system audio"
}

check_screen_recording_permission() {
    local diagnostic succeeded=0 status
    local image=$t.png
    diagnostic=$("$screen_helper" --output "$image" --target frontmost 2>&1 >/dev/null)
    status=$?
    [[ -s $image ]] && succeeded=1
    rm -f "$image"                       # the probe image is not archive material
    record_result "Screen Recording" ScreenCapture "$succeeded" \
        "$(describe_helper_exit "$status" "$(last_diagnostic_line "$diagnostic")")"
}

# A helper killed by a signal was not denied anything, it broke; saying which
# signal keeps a crash from being read as a missing permission.
describe_helper_exit() {
    local status=$1 diagnostic=$2
    if (( status > 128 )); then
        echo "the helper crashed with signal $((status - 128)): ${diagnostic:-no diagnostic}"
        return 0
    fi
    echo "$diagnostic"
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
    local bundle_id service
    bundle_id=$(responsible_bundle_id) ||
        die "cannot identify this application, so its permissions cannot be reset"
    for service in AudioCapture Microphone ScreenCapture; do
        tccutil reset "$service" "$bundle_id" && log_ok "reset $service for $bundle_id"
    done
    log_ok "quit and reopen $bundle_id, then run this script again to be asked afresh"
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
    report_responsible_application
    if [[ $dry_mode -eq 1 ]]; then
        log_ok "dry mode: not probing, because probing is what raises the prompts"
        return 0
    fi
    [[ $reset_mode -eq 1 ]] && { reset_permissions; return 0; }
    require_helpers_are_built
    check_microphone_permission
    check_system_audio_permission
    check_screen_recording_permission
    check_hammerspoon_accessibility
    print_summary
}

t=`mktemp`; trap "rm -f $t*" EXIT

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

main

exit
$dp/git/rvw/util/init_permissions.sh
$dp/git/rvw/util/init_permissions.sh -open       # open the settings pane of anything missing
$dp/git/rvw/util/init_permissions.sh -reset      # make macOS ask again after a refusal
tccutil reset ScreenCapture com.apple.Terminal
tccutil reset Microphone com.apple.Terminal
exit
$dp/git/rvw/util/init_permissions.sh -x -open