#!/bin/bash
# Build the capture helpers, bin/audio_capture and bin/screen_capture, and the
# application bundle that owns their permissions, bin/rvw.app.
#
# The usage descriptions must be inside the binary itself: macOS refuses to let a
# command line tool record the microphone, tap system audio or capture the screen
# unless it can show the user a reason.
#
# Rebuilding the helpers is free. macOS grants the permissions to the
# application responsible for them, which is always rvw.app, and never looks at
# the helpers' own signatures. helper/build_app.sh is what has to be careful:
# re-signing the bundle is what would cost the grants, so it only rebuilds when
# its own sources have changed.

set -o pipefail

script_dir=$(cd "$(dirname "$BASH_SOURCE")" && pwd)
repo_dir=$(cd "$script_dir/.." && pwd)

log_ok()   { echo "OK   $*"; }
die()      { echo "FAIL $*" >&2; exit 1; }

# build_helper <name> <framework> [<framework> ...]
build_helper() {
    local name=$1; shift
    local output_binary=$repo_dir/bin/$name
    local -a frameworks=()
    local framework
    for framework in "$@"; do frameworks+=(-framework "$framework"); done

    swiftc -O -parse-as-library -o "$output_binary" "$script_dir/$name.swift" \
        "${frameworks[@]}" \
        -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist \
        -Xlinker "$script_dir/$name.plist" || die "compiling $name failed"
    log_ok "compiled $output_binary"

    codesign --force --sign - --identifier "ai.rvw.$name" "$output_binary" ||
        die "ad hoc code signing of $name failed"
    log_ok "signed $output_binary"
}

mkdir -p "$repo_dir/bin" || die "cannot create $repo_dir/bin"

build_helper audio_capture AVFoundation CoreAudio
build_helper screen_capture AppKit ScreenCaptureKit ImageIO

"$script_dir/build_app.sh" || die "building bin/rvw.app failed"

exit
$dp/git/rvw/helper/build.sh
$dp/git/rvw/bin/audio_capture --source mic > /tmp/mic.f32
$dp/git/rvw/bin/audio_capture --source system > /tmp/system.f32
$dp/git/rvw/bin/screen_capture --output /tmp/shot.png --target frontmost
