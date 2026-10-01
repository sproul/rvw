"""Text extraction from an already saved screenshot, by the local Vision helper.

The image is read from a file that screenshot.py already archived, so OCR needs
no screen recording permission of its own and no network. What fails is said
aloud: an unreadable image or a missing helper raises rather than returning a
quiet empty string that would be mistaken for a blank screen.
"""

import subprocess
from pathlib import Path

from . import config


def ocr_text_of(image_path):
    """The recognised text of one image file; RuntimeError when OCR fails."""
    helper = Path(config.ocr_helper_path)
    if not helper.exists():
        raise RuntimeError("missing OCR helper %s; run helper/build.sh" % helper)
    finished = subprocess.run([str(helper), str(image_path)],
                              capture_output=True, timeout=config.ocr_timeout_seconds)
    if finished.returncode != 0:
        raise RuntimeError("OCR failed: %s" % _first_diagnostic_line(finished.stderr))
    return finished.stdout.decode("utf-8", "replace").strip()


def _first_diagnostic_line(helper_stderr):
    lines = [line.strip() for line in helper_stderr.decode("utf-8", "replace").splitlines()]
    reported = [line for line in lines if line]
    return reported[-1] if reported else "the helper said nothing"
