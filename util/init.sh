#!/bin/bash
script_dir=$(dirname $BASH_SOURCE|sed -e 's;^/$;;')      # if the containing dir is /, scripting goes better if script_dir is ''
export PATH=$script_dir:$PATH

set -o pipefail

init_local_models.sh -x -y
init_llm_autostart.sh                   # the LM Studio server does not survive a reboot on its own
$script_dir/../helper/build.sh || exit 1  # the helpers, and rvw.app only when its sources changed
init_permissions.sh -open               # prompts for microphone, system audio, screen recording, camera

exit
$dp/git/rvw/util/init.sh