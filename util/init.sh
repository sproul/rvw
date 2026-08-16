#!/bin/bash
script_dir=$(dirname $BASH_SOURCE|sed -e 's;^/$;;')      # if the containing dir is /, scripting goes better if script_dir is ''
export PATH=$script_dir:$PATH

set -o pipefail

init_local_models.sh

exit