# Sourced by helper/build.sh and helper/build_app.sh.
#
# The target is explicit because swiftc builds for the architecture of the shell
# that runs it: started from a terminal running under Rosetta it produced an
# x86_64 rvw_launcher, and macOS warns that such an application will stop
# running once Rosetta is withdrawn. arm64 is all the assistant supports, since
# MLX needs it throughout; the version matches LSMinimumSystemVersion in
# helper/rvw_app.plist.
swift_target=arm64-apple-macos14.4
