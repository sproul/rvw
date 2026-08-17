#!/bin/bash
# Build bin/rvw.app, the application bundle that owns the assistant's macOS
# permissions.
#
# The bundle is the assistant's permanent identity to TCC, and an ad hoc
# signature pins that identity to the exact bytes of the launcher inside it. Any
# rebuild that changes those bytes gives rvw.app a new identity and macOS
# forgets every permission ever granted to it. This script therefore does
# nothing at all unless the launcher source or the property list has actually
# changed, and says plainly what a rebuild costs when one is needed.
#
# Rebuilding the python daemon or either capture helper is free: they live
# outside the bundle and macOS only ever looks at the responsible application.

set -o pipefail

script_dir=$(cd "$(dirname "$BASH_SOURCE")" && pwd)
repo_dir=$(cd "$script_dir/.." && pwd)

app_bundle=$repo_dir/bin/rvw.app
contents_dir=$app_bundle/Contents
launcher_binary=$contents_dir/MacOS/rvw_launcher
build_stamp=$contents_dir/Resources/build_id

launcher_source=$script_dir/rvw_launcher.swift
property_list=$script_dir/rvw_app.plist

launch_services_register=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister

log_ok() { echo "OK   $*"; }
die()    { echo "FAIL $*" >&2; exit 1; }

# What the bundle was built from. While this is unchanged there is nothing to
# gain from rebuilding and a permission grant to lose.
current_build_id() {
    cat "$launcher_source" "$property_list" | shasum -a 256 | cut -d' ' -f1
}

bundle_is_built_from_the_current_sources() {
    [[ -x $launcher_binary && -f $build_stamp ]] || return 1
    [[ $(cat "$build_stamp") == $(current_build_id) ]]
}

assemble_bundle() {
    rm -rf "$app_bundle"
    mkdir -p "$contents_dir/MacOS" "$contents_dir/Resources" || die "cannot create $contents_dir"
    cp "$property_list" "$contents_dir/Info.plist" || die "cannot install the property list"
    swiftc -O -parse-as-library -o "$launcher_binary" "$launcher_source" ||
        die "compiling the launcher failed"
    current_build_id > "$build_stamp" || die "cannot record what the bundle was built from"
}

# Ad hoc, because a Developer ID certificate would only matter for distributing
# this to somebody else. The identifier comes from CFBundleIdentifier.
sign_bundle() {
    codesign --force --sign - "$app_bundle" || die "ad hoc code signing of rvw.app failed"
    log_ok "signed $app_bundle"
}

# Until LaunchServices knows the rebuilt bundle, tccutil cannot resolve its
# identifier and util/init_permissions.sh -reset has nothing to reset.
register_bundle_with_launch_services() {
    "$launch_services_register" -f "$app_bundle" ||
        die "cannot register $app_bundle with LaunchServices"
    log_ok "registered $app_bundle with LaunchServices"
}

# The hardened runtime would demand com.apple.security.device.audio-input before
# macOS would even ask about the microphone, which is exactly what stops the
# assistant working under Emacs.app. Nothing here turns it on; this makes sure.
require_that_the_bundle_can_hold_a_microphone_grant() {
    codesign -dv "$app_bundle" 2>&1 | grep -q 'flags=0x[0-9a-f]*(.*runtime' &&
        die "$app_bundle is signed with the hardened runtime, so macOS will never grant it the microphone"
    return 0
}

report_what_the_rebuild_cost() {
    log_ok "any macOS permission granted to an earlier build of rvw.app is now void"
    log_ok "run util/init_permissions.sh once to grant them to this build"
}

if bundle_is_built_from_the_current_sources; then
    require_that_the_bundle_can_hold_a_microphone_grant
    log_ok "$app_bundle is current; leaving its signature and its permissions alone"
    exit 0
fi

assemble_bundle
sign_bundle
register_bundle_with_launch_services
require_that_the_bundle_can_hold_a_microphone_grant
report_what_the_rebuild_cost

exit
$dp/git/rvw/helper/build_app.sh
codesign -d -r- $dp/git/rvw/bin/rvw.app          # the cdhash the grants are pinned to
