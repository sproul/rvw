"""Sourcing a util/ script for its functions, so the tests can drive them.

Every script in util/ keeps invocation notes after a bare 'exit' on a line of its
own. That is deliberate and harmless when the script is run, but the exit would
end the shell that sourced it before a single function had been called, and every
assertion would then pass without testing anything. The copy made here therefore
stops at that exit.

The scripts also guard their entry point with

    if [[ ${BASH_SOURCE[0]} == "$0" ]]; then ... fi

so that sourcing defines the functions and does nothing else. Both halves are
needed: without the guard, sourcing would run the whole script.
"""

import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

repo_dir = Path(__file__).resolve().parents[1]
util_dir = repo_dir / "util"
notes_separator = "\nexit\n"


def sourceable_copy_of(script_name):
    """A copy of util/<script_name> holding its function definitions and no more.

    The copy is put inside a stand in repository, because these scripts find
    everything they need relative to their own location, as they are meant to:
    the copy sources its neighbours in util/ and resolves src/ and .venv/ above
    it. Only util/ is copied; everything else is a symlink to the real thing.
    """
    root = Path(tempfile.mkdtemp(prefix="rvw_shell_functions_"))
    link_the_repository_into(root)
    copied_util = root / "util"
    copied_util.mkdir()
    for neighbour in util_dir.glob("*.sh"):
        shutil.copy2(neighbour, copied_util / neighbour.name)
    copy = copied_util / script_name
    copy.write_text(function_definitions_only(util_dir / script_name), encoding="utf-8")
    return copy


def link_the_repository_into(root):
    """Everything but util/ is the real thing, so the scripts see a real repository."""
    for entry in repo_dir.iterdir():
        if entry.name != "util":
            (root / entry.name).symlink_to(entry)


def function_definitions_only(script):
    """Everything above the bare 'exit' that ends the script."""
    text = script.read_text(encoding="utf-8")
    if notes_separator not in text:
        raise AssertionError("%s no longer ends with a bare exit" % script)
    return text.split(notes_separator, 1)[0] + "\n"


def run_bash_using(script, body, path_prefix=None, home=None, timeout=120):
    """Source the copied script and run one fragment of bash against its functions.

    path_prefix puts a directory of stand in commands ahead of the real ones, and
    home moves the home directory, which is how a test drives a script that shells
    out to lms or launchctl without touching either. A script that looks for a
    command at a fixed place under $HOME, as these do for lms, needs the second.
    """
    environment = None
    if path_prefix is not None:
        environment = {"PATH": "%s:/usr/bin:/bin:/usr/sbin:/sbin" % path_prefix,
                       "HOME": str(home or Path.home())}
    program = "source %s\n%s" % (script, textwrap.dedent(body))
    return subprocess.run(["bash", "-c", program], capture_output=True, text=True,
                          timeout=timeout, env=environment)


def write_stand_in_command(directory, name, body):
    """A fake command on PATH, so a test never runs the real lms or launchctl."""
    path = Path(directory) / name
    path.write_text("#!/bin/bash\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)
    return path
