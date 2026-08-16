# rvw - local listening assistant

## Layout
- `src/rvw/` python assistant (capture supervision, segmentation, ASR, LLM, commands)
- `helper/audio_capture.swift` Core Audio capture helper, built into `bin/audio_capture`
- `bin/rvw` daemon launcher, `bin/rvwctl` hotkey client (system python, stdlib only)
- `hammerspoon/rvw_hotkeys.lua` global hotkeys, required from `~/.hammerspoon/init.lua`
- `doc/` phase reports and model reasoning, `prompts/` the specification

## Commands
- Set up models and the python environment: `util/init_local_models.sh` (`-dry` to preview, `-y` unattended)
- Build the capture helper: `helper/build.sh`
- Run the tests: `util/run_tests.sh` (unittest, no pytest in the venv)
- Run the assistant: `bin/rvw [--source mic|system|both] [--listen] [--debug]`
- Send a command: `bin/rvwctl EXPLAIN|TOGGLE_CAPTURE|TOGGLE_CONTINUOUS|STATUS|QUIT`

## Notes
- The venv is `.venv` at the repo root, python 3.12 arm64; MLX needs arm64 throughout.
- The LLM is reached at `http://127.0.0.1:1234/v1` under the identifier
  `meeting-assistant`; override with `RVW_LLM_MODEL` and `RVW_LLM_URL`.
- The model must be loaded first: `lms load qwen3.6-35b-a3b --identifier meeting-assistant`
  (`lms` lives in `~/.lmstudio/bin`).
- After editing `hammerspoon/rvw_hotkeys.lua`, reload with `hs -c 'hs.reload()'`.
- Tuning knobs (silence threshold, window lengths, models) are all in `src/rvw/config.py`.
