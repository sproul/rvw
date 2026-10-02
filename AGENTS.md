# rvw - local listening assistant

## Layout
- `src/rvw/` python assistant (capture supervision, segmentation, ASR, LLM, commands,
  screenshot archiving, optional transcript retention)
- `helper/audio_capture.swift` Core Audio capture helper, built into `bin/audio_capture`
- `helper/screen_capture.swift` ScreenCaptureKit helper, built into `bin/screen_capture`
- `helper/rvw_view.swift` the transcript and answer windows, built into `bin/rvw_view`;
  needs no permission, and its windows are excluded from screen capture
- `bin/rvw` daemon launcher, `bin/rvwctl` hotkey client (system python, stdlib only)
- `helper/rvw_launcher.swift` + `helper/rvw_app.plist` built by `helper/build_app.sh` into
  `bin/rvw.app`, the bundle that owns the macOS permissions
- `hammerspoon/` the user interface: `rvw_hotkeys.lua` is the entry point required from
  `~/.hammerspoon/init.lua` and binds the keys; `rvw_menu.lua` is the menu bar item;
  `rvw_config.lua` is what the keys and the menu do and the only file to edit;
  `rvw_state.lua` is pure (no hs API) and holds the title and alert decisions;
  `rvw_client.lua` talks to the daemon through `bin/rvwctl`; `rvw_presenting.lua` knows
  whether this screen is being shared; `rvw_windows.lua` starts and stops `bin/rvw_view`;
  `rvw_actions.lua` runs one entry for both
- `README.md` the overview: architecture, getting started, usage, configuration
- `doc/` phase reports and model reasoning, `doc/testing` the test strategy and the
  manual verification procedures, `prompts/` the specification
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
- Send a command: `bin/rvwctl EXPLAIN_SPEECH|UNGARBLE_SPEECH|SCREENSHOT|SCREEN_VISION|SEARCH|RECALL|REINDEX|AUDIO_CAPTURE_TOGGLE|AUDIO_CAPTURE_START|AUDIO_CAPTURE_STOP|TRANSCRIPT_START|TRANSCRIPT_STOP|TRANSCRIPT_TOGGLE|TRANSCRIPT_SHOW|ANSWER|MODELS|SET_MODEL|SET_LANGUAGE|PROMPT_LIST|PROMPT_SET|STATUS|STATUS_FIELDS|QUIT`,
  or each command's case-sensitive shortcut (E, C, s, V, f, r, R, c, c+, c-, t+, t-, t, T, A,
  ml, m, l, pl, p, S, F, q)
  (`SEARCH <words>` and `RECALL <question>` take free text, e.g. `bin/rvwctl RECALL what did they say about reconnect behavior`)
- Show one window by hand: `bin/rvw_view --window transcript|answer [--seconds 300]`; the
  menu bar and alt-cmd-W / ctrl-alt-cmd-W do the same thing
- Take one screenshot by hand: `bin/screen_capture --output /tmp/shot.png --target frontmost`

## Notes
- The venv is `.venv` at the repo root, python 3.12 arm64; MLX needs arm64 throughout.
- The LLM is reached at `http://127.0.0.1:1234/v1` under the identifier
  `meeting-assistant`; override with `RVW_LLM_MODEL` and `RVW_LLM_URL`. Those name the
  identifier and the endpoint, not the model: the model loaded under that identifier is
  `config.llm_source_model` / `RVW_LLM_SOURCE_MODEL`.
- Which model that is differs per machine, because the machine's memory decides it:
  `config.llm_source_model_by_host` gives m3 (96 GB) the 4-bit Qwen3.6-35B-A3B and m4
  (32 GB) the 3-bit quant of it, matching on the hostname's first component, with
  `llm_source_model_default` for a machine not in the table. Measurements and the
  reasoning are in `doc/model_benchmarks.md`; add a machine's row only once it has
  rows there. Do not settle this per machine with an exported variable: the daemon runs
  inside `bin/rvw.app` under LaunchServices and never sees a shell's environment, so an
  export would configure the installer and not the reload after the idle timeout.
- The LM Studio server does not survive a reboot: `lms server` has no boot option and the
  installer starts it once. Nothing looks wrong afterwards, because the assistant still
  listens and transcribes and only a question finds the endpoint gone, so the LaunchAgent
  `ai.rvw.llm_server` runs `util/start_llm_server.sh` at login instead. The installer runs
  that same script, so there is one account of what "the LLM is up" means.
- The LLM loads on demand: LM Studio unloads it after `llm_idle_ttl_seconds` idle, and
  `src/rvw/model_loader.py` loads it again on the first question, which costs that one
  question about 45s. An unloaded model is ordinary and is logged INFO, not FAIL.
- A session is ephemeral unless asked otherwise and then writes nothing at all: the rolling
  transcript lives in memory and ages out. `TRANSCRIPT_TOGGLE` (alt-cmd-T), `TRANSCRIPT_START`
  and `TRANSCRIPT_STOP` switch it, `STATUS` reports it, and `RVW_RETENTION=retained` makes a
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
  a full EXPLAIN_SPEECH answer in ~5s, streaming included. The vision model is built with
  `suppress_reasoning=False`. Because the thinking channel is closed, a prompt that invites
  deliberation makes the model deliberate in the visible answer: UNGARBLE_SPEECH did this and rambled,
  so its prompt was tightened to a decisive, fixed three-part format ("do not think out loud, do
  not weigh options, do not reconsider"), verified terse over five runs on m3 (0.7-1.6s each).
  Keep that discipline in any new prompt built for this model.
- `SCREEN_VISION` needs a vision model loaded as `meeting-vision`; override with
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
- After editing anything in `hammerspoon/`, reload with `hs -c 'hs.reload()'`.
- The menu bar title is the recording indicator: `rvw -` idle, `rvw L` listening, `R`
  keeping the transcript, `A` analysing continuously, `P` presenting, `rvw x` no assistant
  running at all. It is refreshed every `config.status_poll_seconds` by one `STATUS_FIELDS`
  call. `STATUS` is prose for a person and `STATUS_FIELDS` is the same state as key=value
  pairs; both are rendered from `Assistant._status_fields`, so keep them that way rather
  than parsing the prose anywhere.
- Presenting mode (alt-cmd-P, or the menu) suppresses every alert, failures included:
  while the screen is shared the audience reads them before I do. Turning it on is
  therefore silent; turning it off is not. Automatic share detection is best effort and
  only recognises Zoom's own share window (`config.sharing_windows`); it can add certainty
  but never remove a manual "I am presenting". Nothing else the assistant draws depends on
  that detection: the two windows are excluded from capture by the window server itself, as
  the next note but two explains, so they are safe whether a share was noticed or not.
- The lua is tested two ways (`test/test_menu_bar.py`): statically, that every command it
  names is one the dispatcher registers, and behaviourally, by evaluating the pure modules
  in the running Hammerspoon with `hs -c` (`test/lua_testing.py`, skipped if it is not
  running). There is no standalone lua on these machines. `lua_testing` forgets every
  loaded `rvw_*` module first: Hammerspoon holds its own copy from whenever its
  configuration was loaded, and a test measuring that copy would pass on a broken checkout.
- The transcript and answer windows are `bin/rvw_view`, one process per window, polling
  `TRANSCRIPT_SHOW` and `ANSWER`. They exist as a separate Swift program because only
  `NSWindow.sharingType = .none` makes a window invisible to ScreenCaptureKit, Zoom, Meet,
  Teams and this assistant's own screenshot helper; Hammerspoon cannot set it, and it could
  not go into `bin/rvw.app` without voiding that bundle's permissions. If the window server
  refuses the setting the viewer dies rather than showing a capturable window, and after
  eight unanswered polls it exits, so no window outlives its session. Verified on the M3 on
  2026-08-22: on screen according to the Accessibility API, absent from a full display
  capture taken at the same moment.
- `ANSWER` reports the answer being written or the last one, from `src/rvw/answer_buffer.py`,
  which the same `_write_token` fills as the terminal output. A failed answer says FAIL
  there, so a window never implies that more is coming.
- `bin/rvwctl` reads until the assistant closes the connection, because a transcript reply
  is far larger than one read and half a transcript looks like a transcript.
  `RVW_CONTROL_SOCKET` moves the socket for the daemon and the client together.
- `MODELS` and `SET_MODEL` choose between the models the endpoint already serves; loading a
  different one is Phase 8. An identifier the endpoint does not serve is refused, because
  this build would answer for it with whatever is loaded. `SET_LANGUAGE` changes what the
  recogniser expects from the next utterance onwards, restricted to
  `config.recognition_languages`; `STATUS_FIELDS` reports `language` and `languages`, so the
  menu keeps no list of its own.
- Tuning knobs (silence threshold, window lengths, models) are all in `src/rvw/config.py`.
