"""Session logging: a readable terminal view plus a detailed file on disk."""

import logging
import time
from pathlib import Path

from . import config

console_format = "%(message)s"
file_format = "%(asctime)s %(levelname)-7s %(name)-14s %(message)s"

# Debug mode is for our own code; these libraries log every http frame.
noisy_libraries = ["filelock", "httpcore", "httpx", "huggingface_hub", "urllib3"]


def start_session_log():
    """Configure logging for one assistant run and return the log file path."""
    config.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = config.log_dir / ("rvw_%s.log" % time.strftime("%Y-%m-%d_%H.%M.%S"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(_make_console_handler())
    root.addHandler(_make_file_handler(log_path))
    for library in noisy_libraries:
        logging.getLogger(library).setLevel(logging.WARNING)
    return log_path


def _make_console_handler():
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG if config.debug_mode else logging.INFO)
    handler.setFormatter(logging.Formatter(console_format))
    return handler


def _make_file_handler(log_path):
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(file_format))
    return handler


def write_answer_record(log_path, heading, body):
    """Append the full text of one exchange to the session log file."""
    with Path(log_path).open("a", encoding="utf-8") as log_file:
        log_file.write("\n----- %s -----\n%s\n" % (heading, body.strip()))
