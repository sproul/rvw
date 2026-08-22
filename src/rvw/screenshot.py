"""Archival screen capture.

The image and the metadata beside it are canonical data: no OCR, no model and
no network are involved in saving them, and they are archived inside the meeting
directory that rvw/meeting_archive.py owns,

    <archive>/YYYY/MM/YYYY-MM-DD_HH.MM/screenshots/YYYY-MM-DD_HH.MM.SS.mmm.png
                                                  /YYYY-MM-DD_HH.MM.SS.mmm.json

so that a saved image sits beside the transcript of the same session and can be
aligned with its timestamps. Capture itself lives in the Swift helper, which owns
the screen recording permission.
"""

import base64
import json
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import config
from .meeting_archive import local_time_text, session_archive_dir

log = logging.getLogger(__name__)

image_name_format = "%Y-%m-%d_%H.%M.%S"


@dataclass(frozen=True)
class Screenshot:
    """One archived image, its sidecar metadata file, and that metadata."""
    image_path: Path
    metadata_path: Path
    captured_epoch: float
    metadata: dict


def capture_screenshot(session_started_epoch, now=None):
    """Save one screenshot and its metadata; raise RuntimeError if nothing was saved."""
    captured_epoch = time.time() if now is None else now
    image_path = screenshot_image_path(session_started_epoch, captured_epoch)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    helper_metadata = _run_capture_helper(image_path)
    _require_image_was_written(image_path)
    metadata = _build_metadata(image_path, captured_epoch, helper_metadata)
    metadata_path = _write_metadata(image_path, metadata)
    log.info("OK  screenshot %s (%s)", image_path.name, _describe_source(metadata))
    return Screenshot(image_path=image_path, metadata_path=metadata_path,
                      captured_epoch=captured_epoch, metadata=metadata)


def screenshot_image_path(session_started_epoch, captured_epoch):
    """Timestamped image path, unique to the millisecond so bursts never collide."""
    return (session_archive_dir(session_started_epoch) / "screenshots"
            / (_timestamp_with_milliseconds(captured_epoch) + ".png"))


def read_image_as_data_uri(image_path):
    """Inline an archived image for the vision model, which takes no file paths."""
    encoded = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
    return "data:image/png;base64," + encoded


def _timestamp_with_milliseconds(epoch):
    milliseconds = int(round((epoch - int(epoch)) * 1000))
    return "%s.%03d" % (time.strftime(image_name_format, time.localtime(epoch)), milliseconds)


def _run_capture_helper(image_path):
    """Run the Swift helper and return the metadata it printed as JSON."""
    if not Path(config.screen_capture_helper_path).exists():
        raise RuntimeError("missing screen capture helper %s; run helper/build.sh"
                           % config.screen_capture_helper_path)
    command = [str(config.screen_capture_helper_path), "--output", str(image_path),
               "--target", config.screenshot_target]
    finished = subprocess.run(command, capture_output=True,
                              timeout=config.screenshot_timeout_seconds)
    if finished.returncode != 0:
        raise RuntimeError("screen capture failed: %s"
                           % _first_diagnostic_line(finished.stderr))
    return _parse_helper_metadata(finished.stdout)


def _parse_helper_metadata(helper_stdout):
    text = helper_stdout.decode("utf-8", "replace").strip()
    try:
        parsed = json.loads(text)
    except ValueError as error:
        raise RuntimeError("the screen capture helper printed %r, not metadata (%s)"
                           % (text[:200], error))
    if not isinstance(parsed, dict):
        raise RuntimeError("the screen capture helper printed %r, not a metadata object" % text[:200])
    return parsed


def _first_diagnostic_line(helper_stderr):
    lines = [line.strip() for line in helper_stderr.decode("utf-8", "replace").splitlines()]
    reported = [line for line in lines if line]
    return reported[-1] if reported else "the helper said nothing"


def _require_image_was_written(image_path):
    if not image_path.exists() or image_path.stat().st_size == 0:
        raise RuntimeError("the screen capture helper wrote no image to %s" % image_path)


def _build_metadata(image_path, captured_epoch, helper_metadata):
    """Helper facts plus the timestamps that align the image with the transcript."""
    metadata = dict(helper_metadata)
    metadata["image"] = image_path.name
    metadata["captured_epoch"] = round(captured_epoch, 3)
    metadata["captured_local"] = local_time_text(captured_epoch)
    metadata["bytes"] = image_path.stat().st_size
    return metadata


def _write_metadata(image_path, metadata):
    metadata_path = image_path.with_suffix(".json")
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
    return metadata_path


def _describe_source(metadata):
    return "%s: %s" % (metadata.get("application") or "unknown application",
                       metadata.get("window_title") or metadata.get("target") or "unknown window")
