#!/bin/bash
# Run the listening assistant unit tests in the project virtual environment.

set -o pipefail

script_dir=$(cd "$(dirname "$BASH_SOURCE")" && pwd)
repo_dir=$(cd "$script_dir/.." && pwd)
venv_python=$repo_dir/.venv/bin/python

[[ -x $venv_python ]] || { echo "FAIL missing $venv_python; run init_local_models.sh first" >&2; exit 1; }

PYTHONPATH=$repo_dir/src "$venv_python" -m unittest discover -s "$repo_dir/test" -t "$repo_dir/test" -v "$@"

exit
$dp/git/rvw/util/run_tests.sh
$dp/git/rvw/util/run_tests.sh -k segmenter
