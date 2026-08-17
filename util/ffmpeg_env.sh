# Shared helper for exposing Homebrew's ffmpeg shared libraries to the dynamic linker.
#
# pyannote 4 decodes audio through torchcodec, which dlopens ffmpeg's libraries (libavutil.*.dylib)
# via @rpath and searches only the venv. macOS reads DYLD_* at process startup, so the path has to be
# set before python launches. Both the model installer and the app launcher source this file, so a
# single definition keeps the self test and the runtime in agreement.
#
# This file only defines a function; source it, then call the function. It is intentionally free of
# logging and dry-mode handling so each caller can react in its own idiom.

ffmpeg_lib_dir=""       # set by add_ffmpeg_libs_to_dyld_path for the caller to report

# Prepend the directory holding ffmpeg's shared libraries to DYLD_LIBRARY_PATH.
# Returns non-zero without changing the environment when ffmpeg is not on PATH.
add_ffmpeg_libs_to_dyld_path() {
    command -v ffmpeg >/dev/null 2>&1 || return 1
    ffmpeg_lib_dir=$(cd "$(dirname "$(command -v ffmpeg)")/../lib" && pwd) || return 1
    export DYLD_LIBRARY_PATH="$ffmpeg_lib_dir${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
}
