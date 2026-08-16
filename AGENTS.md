# rvw - local listening assistant

## Layout
- `src/rvw/` python assistant (capture supervision, segmentation, ASR, LLM, commands,
  screenshot archiving)
- `helper/audio_capture.swift` Core Audio capture helper, built into `bin/audio_capture`
- `helper/screen_capture.swift` ScreenCaptureKit helper, built into `bin/screen_capture`
- `bin/rvw` daemon launcher, `bin/rvwctl` hotkey client (system python, stdlib only)
- `hammerspoon/rvw_hotkeys.lua` global hotkeys, required from `~/.hammerspoon/init.lua`
- `doc/` phase reports and model reasoning, `prompts/` the specification
- `var/meetings/YYYY/MM/YYYY-MM-DD_HH.MM/screenshots/` archived images and their
  sidecar metadata; move the root with `RVW_ARCHIVE_DIR`

## Commands
- Set up everything: `util/init.sh` (models, then permissions)
- Set up models and the python environment: `util/init_local_models.sh` (`-dry` to preview, `-y` unattended)
- Check and request the macOS permissions: `util/init_permissions.sh` (`-open` jumps to the
  settings pane of anything missing, `-reset` makes macOS ask again after a refusal)
- Build both capture helpers: `helper/build.sh`
- Run the tests: `util/run_tests.sh` (unittest, no pytest in the venv)
- Run the assistant: `bin/rvw [--source mic|system|both] [--listen] [--debug]`
- Send a command: `bin/rvwctl EXPLAIN|CLARIFY|SCREENSHOT|INTERPRET_SCREEN|TOGGLE_CAPTURE|TOGGLE_CONTINUOUS|STATUS|QUIT`
- Take one screenshot by hand: `bin/screen_capture --output /tmp/shot.png --target frontmost`

## Notes
- The venv is `.venv` at the repo root, python 3.12 arm64; MLX needs arm64 throughout.
- The LLM is reached at `http://127.0.0.1:1234/v1` under the identifier
  `meeting-assistant`; override with `RVW_LLM_MODEL` and `RVW_LLM_URL`.
- The model must be loaded first: `lms load qwen3.6-35b-a3b --identifier meeting-assistant`
  (`lms` lives in `~/.lmstudio/bin`).
- `INTERPRET_SCREEN` needs a vision model loaded as `meeting-vision`; override with
  `RVW_VLM_MODEL`. Everything else works without it.
- Microphone, system audio and screen recording are granted to the application that
  launches the helpers, and Accessibility to Hammerspoon; `util/init_permissions.sh`
  explains and probes all four.
- After editing `hammerspoon/rvw_hotkeys.lua`, reload with `hs -c 'hs.reload()'`.
- Tuning knobs (silence threshold, window lengths, models) are all in `src/rvw/config.py`.
