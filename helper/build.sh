#!/bin/bash
# Build bin/audio_capture from audio_capture.swift.
#
# The usage descriptions must be inside the binary itself: macOS refuses to let a
# command line tool record the microphone or tap system audio unless it can show
# the user a reason, and an ad hoc signature gives the tool a stable identity so
# the permission grant survives rebuilds.

set -o pipefail

script_dir=$(cd "$(dirname "$BASH_SOURCE")" && pwd)
repo_dir=$(cd "$script_dir/.." && pwd)
source_file=$script_dir/audio_capture.swift
info_plist=$script_dir/audio_capture.plist
output_binary=$repo_dir/bin/audio_capture

log_ok()   { echo "OK   $*"; }
die()      { echo "FAIL $*" >&2; exit 1; }

mkdir -p "$repo_dir/bin" || die "cannot create $repo_dir/bin"

swiftc -O -parse-as-library -o "$output_binary" "$source_file" \
    -framework AVFoundation -framework CoreAudio \
    -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker "$info_plist" ||
    die "compilation failed"
log_ok "compiled $output_binary"

codesign --force --sign - --identifier ai.rvw.audio_capture "$output_binary" ||
    die "ad hoc code signing failed"
log_ok "signed $output_binary"

exit
$dp/git/rvw/helper/build.sh
$dp/git/rvw/bin/audio_capture --source mic > /tmp/mic.f32
$dp/git/rvw/bin/audio_capture --source system > /tmp/system.f32
