# rvw - local listening assistant

## Layout
- `src/rvw/` python assistant (capture supervision, segmentation, ASR, LLM, commands,
  screenshot archiving, optional transcript retention)
- `helper/audio_capture.swift` Core Audio capture helper, built into `bin/audio_capture`
- `helper/screen_capture.swift` ScreenCaptureKit helper, built into `bin/screen_capture`
- `bin/rvw` daemon launcher, `bin/rvwctl` hotkey client (system python, stdlib only)
- `helper/rvw_launcher.swift` + `helper/rvw_app.plist` built by `helper/build_app.sh` into
  `bin/rvw.app`, the bundle that owns the macOS permissions
- `hammerspoon/rvw_hotkeys.lua` global hotkeys, required from `~/.hammerspoon/init.lua`
- `doc/` phase reports and model reasoning, `prompts/` the specification
- `var/meetings/YYYY/MM/YYYY-MM-DD_HH.MM/` everything one session keeps:
  `transcript.jsonl` and `metadata.json`, `transcript.md` rendered from the JSONL, and
  `screenshots/` with the archived images and their sidecar metadata; move the root with
  `RVW_ARCHIVE_DIR`
- `var/index/meetings.db` the SQLite FTS5 index over the transcripts, derived data rebuilt
  from them and never synchronised between Macs; move it with `RVW_INDEX_DB`

## Commands
- Set up everything: `util/init.sh` (models, the login agent, then permissions)
- Set up models and the python environment: `util/init_local_models.sh` (`-dry` to preview, `-y` unattended)
- Install the LaunchAgent that starts the LLM endpoint at login: `util/init_llm_autostart.sh` (`-dry` to preview)
- Start the LLM endpoint now, if it is not already up: `util/start_llm_server.sh`
- List every model on the machine, in both stores: `util/list_models.sh`
- Check and request the macOS permissions: `util/init_permissions.sh` (`-open` jumps to the
  settings pane of anything missing, `-reset` makes macOS ask again after a refusal)
- Build both capture helpers and `bin/rvw.app`: `helper/build.sh`
- Rebuild only the bundle: `helper/build_app.sh` (does nothing unless its own sources changed)
- Rebuild the searchable meeting index from the transcripts: `util/rebuild_index.sh` (the same
  rebuild as the assistant's `REINDEX`, without a running daemon)
- Run the tests: `util/run_tests.sh` (unittest, no pytest in the venv)
- Run the assistant: `bin/rvw [--source mic|system|both] [--listen] [--debug]`, which starts it
  inside `bin/rvw.app`; `bin/rvw -here ...` runs it in this terminal instead
- Send a command: `bin/rvwctl EXPLAIN|CLARIFY|SCREENSHOT|INTERPRET_SCREEN|SEARCH|RECALL|REINDEX|TOGGLE_CAPTURE|TOGGLE_CONTINUOUS|START_RETAINING|STOP_RETAINING|TOGGLE_RETENTION|STATUS|QUIT`
  (`SEARCH <words>` and `RECALL <question>` take free text, e.g. `bin/rvwctl RECALL what did they say about reconnect behavior`)
- Take one screenshot by hand: `bin/screen_capture --output /tmp/shot.png --target frontmost`

## Notes
- The venv is `.venv` at the repo root, python 3.12 arm64; MLX needs arm64 throughout.
- The LLM is reached at `http://127.0.0.1:1234/v1` under the identifier
  `meeting-assistant`; override with `RVW_LLM_MODEL` and `RVW_LLM_URL`.
- The LM Studio server does not survive a reboot: `lms server` has no boot option and the
  installer starts it once. Nothing looks wrong afterwards, because the assistant still
  listens and transcribes and only a question finds the endpoint gone, so the LaunchAgent
  `ai.rvw.llm_server` runs `util/start_llm_server.sh` at login instead. The installer runs
  that same script, so there is one account of what "the LLM is up" means.
- The LLM loads on demand: LM Studio unloads it after `llm_idle_ttl_seconds` idle, and
  `src/rvw/model_loader.py` loads it again on the first question, which costs that one
  question about 45s. An unloaded model is ordinary and is logged INFO, not FAIL.
- A session is ephemeral unless asked otherwise and then writes nothing at all: the rolling
  transcript lives in memory and ages out. `TOGGLE_RETENTION` (alt-cmd-T), `START_RETAINING`
  and `STOP_RETAINING` switch it, `STATUS` reports it, and `RVW_RETENTION=retained` makes a
  session start out keeping its transcript.
- Retention is not retrospective: switching it on keeps the speech from that moment, not what
  is still sitting in the rolling window, which was said while the session was ephemeral.
  Switching it off keeps what is already written, because that is a decision about what
  happens next and not a way to unsay anything.
- `transcript.jsonl` is canonical: one JSON object per utterance, appended and never
  rewritten, each line carrying its own local time and speaker label so it can be read
  without this repository. `transcript.md` is derived from it and rewritten from it, so it can
  be deleted at any time; nothing reads it back.
- The Phase 4 search index (`var/index/meetings.db`) is derived the same way: SQLite FTS5 over
  the transcripts, rebuilt from them by `REINDEX` or `util/rebuild_index.sh` and never
  synchronised between Macs (principle 7). `SEARCH <words>` ranks matching utterances by BM25
  and links each back to its meeting, time and any screenshots taken around it; `RECALL
  <question>` retrieves a few passages, answers from those alone and cites them. Only retained
  meetings are indexed, and one can opt out with `"index": false` in its `metadata.json`.
  Semantic/vector retrieval is deliberately not built yet; see `doc/phase4`.
- Log prefixes: `OK` an action succeeded, `INFO` routine news worth no one's effort,
  `FAIL` a problem needing attention. Never spend FAIL on something working as designed.
- `util/init_local_models.sh` reads the model, identifier, context length and idle timeout
  from `src/rvw/config.py` and resolves the `lms` model key through `rvw.model_loader`, so
  the installer and the daemon cannot disagree about what to load.
- Qwen3.6 thinks ~489 tokens before a one sentence answer and this LM Studio build ignores
  every request level reasoning control; see the measurements in `src/rvw/config.py`. Left to
  think it spends its whole token budget reasoning and returns an empty answer (measured
  1023/1024 tokens, empty), which is what made it look unusable. `llm.py` therefore prefills a
  closed, empty `<think></think>` block as the start of the assistant turn (`config.reasoning_prefill`),
  which the model's chat template reads as thinking already done: measured 0 reasoning tokens and
  a full EXPLAIN answer in ~5s, streaming included. The vision model is built with
  `suppress_reasoning=False`. One rough edge: CLARIFY, which asks the model to deliberate over
  word choices, moves that deliberation into the visible answer now that the thinking channel is
  closed, and rambles; its prompt still needs tightening.
- `INTERPRET_SCREEN` needs a vision model loaded as `meeting-vision`; override with
  `RVW_VLM_MODEL`. Everything else works without it. Without one it archives the image
  and replies `not interpreted: no model is loaded as 'meeting-vision'`.
- This LM Studio build answers a request for an identifier it does not serve with
  whatever model is loaded, and names that substitute in the response, so it never
  refuses. `llm.py` therefore checks that the identifier is served before asking, and
  checks the answering model in every streamed chunk. Do not remove either check: without
  them a screenshot sent to the vision model comes back written by the text model and
  looks like a success.
- Microphone, system audio and screen recording are granted to `bin/rvw.app`, and
  Accessibility to Hammerspoon; `util/init_permissions.sh` explains and probes all four.
  Both audio probes read one launcher log, so each probe waits for the launcher to record
  the helper's exit before returning; without that wait the next probe reads the previous
  one's verdict and calls a granted permission missing.
  The bundle only becomes the responsible application when LaunchServices starts it, so
  everything that needs those permissions goes through `open -n -a bin/rvw.app`.
- Re-signing `bin/rvw.app` voids every permission granted to it: an ad hoc signature is
  pinned to the launcher's cdhash. `helper/rvw_launcher.swift` is therefore meant to stay
  frozen, and `helper/build_app.sh` rebuilds only when its sources actually change.
  Rebuilding the daemon or either capture helper costs nothing.
- After editing `hammerspoon/rvw_hotkeys.lua`, reload with `hs -c 'hs.reload()'`.
- Tuning knobs (silence threshold, window lengths, models) are all in `src/rvw/config.py`.
