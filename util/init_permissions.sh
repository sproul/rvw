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

microphone_probe_seconds=3
system_audio_probe_seconds=3

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

# Run a helper just long enough to trigger the permission prompt, then stop it.
run_helper_briefly() {
    local seconds=$1; shift
    local diagnostics=$t.helper
    "$@" > /dev/null 2> "$diagnostics" &
    local helper_pid=$!
    sleep "$seconds"
    kill "$helper_pid" 2>/dev/null
    wait "$helper_pid" 2>/dev/null
    cat "$diagnostics"
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

check_microphone_permission() {
    local diagnostic succeeded=0
    diagnostic=$(run_helper_briefly "$microphone_probe_seconds" "$audio_helper" --source mic)
    [[ $diagnostic == *"capturing the microphone"* ]] && succeeded=1
    record_result "Microphone" Microphone "$succeeded" "$(last_diagnostic_line "$diagnostic")"
}

check_system_audio_permission() {
    local diagnostic succeeded=0
    diagnostic=$(run_helper_briefly "$system_audio_probe_seconds" "$audio_helper" --source system)
    [[ $diagnostic == *"capturing system audio"* ]] && succeeded=1
    record_result "Audio Recording (system audio tap)" AudioCapture "$succeeded" \
        "$(last_diagnostic_line "$diagnostic")"
}

check_screen_recording_permission() {
    local diagnostic succeeded=0
    local image=$t.png
    diagnostic=$("$screen_helper" --output "$image" --target frontmost 2>&1 >/dev/null)
    [[ -s $image ]] && succeeded=1
    rm -f "$image"                       # the probe image is not archive material
    record_result "Screen Recording" ScreenCapture "$succeeded" "$(last_diagnostic_line "$diagnostic")"
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