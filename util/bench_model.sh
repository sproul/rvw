#!/bin/bash
# Benchmark one already-loaded local model on this machine and print comparable
# numbers for doc/model_benchmarks.md. Load the model first (LM Studio or
# `lms load ...`), then run this with the served model id.

set -o pipefail

script_dir=$(cd "$(dirname "$BASH_SOURCE")" && pwd)
repo_dir=$(cd "$script_dir/.." && pwd)
venv_python=$repo_dir/.venv/bin/python

[[ -x $venv_python ]] || { echo "FAIL missing $venv_python; run util/init_local_models.sh first" >&2; exit 1; }

PYTHONPATH=$repo_dir/src exec "$venv_python" "$script_dir/bench_model.py" "$@"

exit
$dp/git/rvw/util/bench_model.sh qwen3.6-35b-a3b
$dp/git/rvw/util/bench_model.sh qwen3.6-35b-a3b --raw     # thinking left on, to show the failure
