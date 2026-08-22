#!/bin/bash
# Read the assistant's own settings out of src/rvw/config.py, for the shell
# scripts that have to agree with the daemon about them.
#
# The installer, the model listing, the server starter and the daemon all need
# the same handful of facts: which identifier the model is served under, which
# identifier a screenshot interpretation asks for, and which port the endpoint
# listens on. Written down twice they would eventually disagree, and the failure
# would be a question quietly answered by the wrong model rather than an error.
# So config.py holds them and everything else asks.
#
# Sourced, never executed:
#   source "$script_dir/assistant_settings.sh"
#   identifier=$(read_assistant_setting llm_model) || die "..."

assistant_settings_repo_dir=$(cd "$(dirname "$BASH_SOURCE")/.." && pwd)

# read_assistant_setting <name of a module level variable in src/rvw/config.py>
read_assistant_setting() {
    PYTHONPATH=$assistant_settings_repo_dir/src python3 -c \
        'import sys; from rvw import config; print(getattr(config, sys.argv[1]))' "$1"
}

# The port is part of config.llm_base_url rather than a setting of its own, so it
# is taken from there instead of being written down a second time.
read_llm_server_port() {
    PYTHONPATH=$assistant_settings_repo_dir/src python3 -c \
        'from urllib.parse import urlparse; from rvw import config
print(urlparse(config.llm_base_url).port or 80)'
}
