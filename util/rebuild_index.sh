#!/bin/bash
# Rebuild the searchable meeting index from the canonical transcripts.
#
# The index is derived data: it can be deleted at any time and rebuilt from the
# transcript.jsonl files alone, and principle 7 forbids synchronising it between
# Macs, so each Mac runs this over its own copy of the transcripts. The running
# assistant also rebuilds on demand with its REINDEX command; this script is the
# same operation without a running daemon, for a cron job or after copying an
# archive across.

set -o pipefail

script_dir=$(cd "$(dirname "$BASH_SOURCE")" && pwd)
repo_dir=$(cd "$script_dir/.." && pwd)
venv_python=$repo_dir/.venv/bin/python

[[ -x $venv_python ]] || { echo "FAIL missing $venv_python; run util/init_local_models.sh first" >&2; exit 1; }

PYTHONPATH=$repo_dir/src exec "$venv_python" -c '
from rvw import config, meeting_index
stats = meeting_index.MeetingIndex().rebuild()
print("OK   indexed %d utterance(s) from %d meeting(s) into %s"
      % (stats.utterance_count, stats.meeting_count, config.index_db_path))
'

exit
$dp/git/rvw/util/rebuild_index.sh
RVW_ARCHIVE_DIR=/path/to/meetings RVW_INDEX_DB=/tmp/meetings.db $dp/git/rvw/util/rebuild_index.sh
