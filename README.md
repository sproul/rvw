# rvw - a local listening assistant for macOS

rvw listens to a conversation on this Mac, transcribes it locally and, on a hotkey,
explains or reconstructs what was just said. It can archive what was on screen, keep
a canonical transcript of a conversation when asked to, and search everything it has
kept. Audio, transcript, screenshots and inference all stay on the machine: nothing
is sent anywhere.

It is built for two machines, an M3 with 96 GB and an M4 Air with 32 GB, and picks a
model sized for whichever one it is running on.

This file is the map. The detailed reasoning behind each piece of work lives in
`doc/phase1` to `doc/phase4`; the operating notes and hard-won gotchas live in
`AGENTS.md`; the model measurements live in `doc/model_benchmarks.md`; the test
strategy lives in `doc/testing`.


## Contents

- [What it does today](#what-it-does-today)
- [How it is put together](#how-it-is-put-together)
- [Repository map](#repository-map)
- [Getting started](#getting-started)
- [Using it](#using-it)
- [What it writes, and where](#what-it-writes-and-where)
- [Configuration](#configuration)
- [Testing](#testing)
- [Status, and what is not verified](#status-and-what-is-not-verified)
- [Troubleshooting](#troubleshooting)


## What it does today

Four phases of work are implemented.

**Phase 1, the minimum viable assistant.** Continuous capture of the microphone and
of the Mac's own audio output as two separate streams, local speech recognition into
a rolling in-memory transcript, and one hotkey that sends roughly the last 60 seconds
of text to a local LLM and streams the explanation back to the assistant's terminal.

**Phase 2, making it usable.** `UNGARBLE_SPEECH`, a second, shorter transcript command whose
prompt asks what words were actually said rather than what they meant - the answer to
a strong accent or a bad recognition. `SCREEN_SAVE`, an archival-only capture of the
frontmost window with its metadata, silent on success so that nothing appears in a
shared screen. `SCREEN_VISION`, the identical save followed by a private
interpretation from a local vision model.

**Phase 3, optional transcript retention.** A session is ephemeral by default and
writes nothing at all. `TRANSCRIPT_TOGGLE` switches it to keeping a canonical
`transcript.jsonl`, with `metadata.json` beside it and a derived `transcript.md`.
Retention is not retrospective in either direction.

**Phase 4, searchable meeting memory.** A SQLite FTS5 index over the retained
transcripts, rebuildable from them and never synchronised between Macs. `SEARCH`
ranks matching utterances by BM25 and links each back to its meeting, its time and
the screenshots taken around it; `RECALL` answers a question from a handful of
retrieved passages and cites them; `REINDEX` rebuilds.


## How it is put together

```
  audio_capture (swift, one process per stream)
        |  mono 16 kHz float32 PCM on stdout
        v
  audio_source.CaptureStream  -- supervises the helper, relays its log
        v
  segmenter.SpeechSegmenter   -- cuts on silence into utterances
        v
  recognizer.RecognitionWorker (one thread, one queue)
        v
  asr.WhisperTranscriber      -- mlx-whisper
        v
  transcript.RollingTranscript ---> meeting_archive.MeetingArchive (only if retained)
        |                                    |
        |                                    v
        |                          transcript.jsonl (canonical)
        |                                    |
        |                                    v
        |                          meeting_index (FTS5, disposable)
        v
  assistant.Assistant  <---- commands.CommandDispatcher <---- control.ControlSocketServer
        |                                                            ^
        v                                                            |
  prompts -> llm.LocalLlm -> LM Studio endpoint            bin/rvwctl <- Hammerspoon hotkey
```

The choices that shape all of it:

- **Capture is a separate process per stream, and the streams are never mixed.** The
  microphone is "me", the Mac's playback is "them", and they stay separable. System
  audio uses a Core Audio process tap, which observes playback without sitting in it:
  no virtual device, no change of output device, headphones keep working.
- **Recognition is cut on silence, not on fixed windows.** Utterance-sized chunks are
  independent, so there is no overlap to deduplicate and no half words at a boundary.
  A segment whose loudest sample is quiet is dropped before recognition, because
  Whisper invents filler phrases such as "Thank you." when handed near silence.
- **Every action is a dispatcher command,** reachable over a unix socket by `bin/rvwctl`
  and bound to a key by Hammerspoon. The assistant therefore needs no accessibility
  permission, and the transport under the dispatcher can later point at a second Mac.
- **Every model-facing action shares one answering path:** one lock, one streaming
  thread, one session log record. A new behaviour is a prompt plus a dispatcher entry,
  not new machinery.
- **Canonical data is plain files; indexes are derived and disposable.** The transcript
  JSONL is the record. `transcript.md` is rendered from it, the FTS index is rebuilt
  from it, and both can be deleted at any time without losing anything.
- **The daemon runs inside `bin/rvw.app`,** because macOS attributes microphone, system
  audio and screen recording to the application responsible for the process that asks.
  In the bundle the assistant is responsible for itself and carries its permissions
  whoever starts it.


## Repository map

| Path | What it is |
| --- | --- |
| `src/rvw/assistant.py` | wiring, the command handlers, the single answering path |
| `src/rvw/audio_source.py` | supervises one `audio_capture` helper, turns its PCM into utterances |
| `src/rvw/segmenter.py` | silence-cut segmentation, with the quiet-segment gate |
| `src/rvw/recognizer.py` | one background worker turning queued audio into transcript entries |
| `src/rvw/asr.py` | mlx-whisper behind a one-method interface |
| `src/rvw/transcript.py` | the rolling in-memory transcript, and the one funnel retention hangs off |
| `src/rvw/meeting_archive.py` | the meeting directory, retention, the canonical JSONL and the rendering |
| `src/rvw/meeting_index.py` | the disposable FTS5 index and its search |
| `src/rvw/recall.py` | numbering retrieved passages for the model and for me |
| `src/rvw/llm.py` | streaming OpenAI-compatible client, with both anti-substitution guards |
| `src/rvw/model_loader.py` | loading the LLM into LM Studio on first need |
| `src/rvw/prompts.py` | the EXPLAIN, UNGARBLE, RECALL and INTERPRET prompts |
| `src/rvw/screenshot.py` | archival save plus the sidecar metadata |
| `src/rvw/commands.py` | transport-independent command dispatcher |
| `src/rvw/control.py` | unix socket front end for the dispatcher |
| `src/rvw/session_log.py` | the terminal view and the detailed log file |
| `src/rvw/config.py` | every tuning knob, path and model choice |
| `helper/audio_capture.swift` | Core Audio / AVAudioEngine capture, built into `bin/audio_capture` |
| `helper/screen_capture.swift` | ScreenCaptureKit capture, built into `bin/screen_capture` |
| `helper/rvw_launcher.swift` | the launcher inside `bin/rvw.app`; deliberately frozen |
| `helper/build.sh`, `helper/build_app.sh` | build the helpers and the bundle |
| `bin/rvw`, `bin/rvwctl` | daemon launcher, and the stdlib-only hotkey client |
| `hammerspoon/rvw_hotkeys.lua` | the global hotkeys |
| `util/` | the installers, the model tooling, the index rebuild, the test runner |
| `test/` | 217 unittest tests |
| `doc/` | phase reports, model benchmarks, this project's reasoning |
| `prompts/` | the specification this was built from |
| `var/` | logs, the meeting archive, the index; git ignored |


## Getting started

You need an Apple Silicon Mac, macOS with ScreenCaptureKit (13+), Hammerspoon for the
hotkeys, and enough free memory for a local model - 32 GB is the smallest machine this
has been made to work on.

### 1. Install everything

```bash
cd ~/dp/git/rvw
util/init.sh
```

That runs three installers in order, and each can be run on its own:

- `util/init_local_models.sh` creates the python 3.12 arm64 virtual environment in
  `.venv`, downloads the LLM this machine is sized for, downloads Whisper and the
  diarization model, and brings the LM Studio server up. `-dry` previews it, `-y`
  runs it unattended.
- `util/init_llm_autostart.sh` installs the `ai.rvw.llm_server` LaunchAgent, which
  runs `util/start_llm_server.sh` at login. Without it the endpoint dies at the next
  reboot and nothing looks wrong until a question is asked.
- `util/init_permissions.sh` explains, provokes and reports the four macOS permissions.
  `-open` jumps to the settings pane of anything missing; `-reset` makes macOS ask
  again after a refusal.

The permissions, and who they are granted to:

| Permission | Needed by | Granted to |
| --- | --- | --- |
| Microphone | `audio_capture --source mic` | `bin/rvw.app` |
| Audio Recording | `audio_capture --source system`, the Core Audio tap | `bin/rvw.app` |
| Screen Recording | `screen_capture` | `bin/rvw.app` |
| Accessibility | the global hotkeys | Hammerspoon |

There is no API that grants a permission, and none that reads one: the only way to
raise the system prompt is to perform the protected action, which is what the script
does with the real helpers.

### 2. Build the helpers

```bash
helper/build.sh
```

This builds `bin/audio_capture`, `bin/screen_capture` and `bin/rvw.app`. Rebuilding
the two capture helpers costs nothing. Rebuilding the bundle voids every permission
granted to it, because its ad hoc signature is pinned to the launcher's bytes, so
`build_app.sh` rebuilds only when its own sources actually changed - and
`helper/rvw_launcher.swift` is meant to stay frozen.

### 3. Install the hotkeys

Add these two lines to `~/.hammerspoon/init.lua`, then reload with `hs -c 'hs.reload()'`:

```lua
package.path = os.getenv("HOME") .. "/dp/git/rvw/hammerspoon/?.lua;" .. package.path
require("rvw_hotkeys")
```

### 4. Check the machine is ready

```bash
util/run_tests.sh          # 217 tests, about 20 seconds
util/list_models.sh        # both model stores, and what the endpoint actually serves
bin/screen_capture --output /tmp/shot.png --target frontmost
```

### 5. Run it

```bash
bin/rvw                          # start the daemon inside bin/rvw.app
bin/rvw --source system --listen # start it already capturing, playback only
bin/rvw -here --debug            # run in this terminal, for debugging
```

The assistant prints a banner listing the hotkeys, says where screenshots are going
and whether the transcript is being kept, and reports whether the LLM and the optional
vision model are loaded. It is then listening for commands.

### 6. First conversation

1. Press alt-cmd-R, or start with `--listen`, and let it transcribe for a minute.
   Recognised utterances appear in the terminal, labelled "me" and "them".
2. Press alt-cmd-E. The explanation of the last 60 seconds streams into the
   assistant's terminal.
3. If a passage was hard to make out, press alt-cmd-C instead: the ungarbling
   reconstructs the words and says which ones it repaired.
4. To keep this conversation, press alt-cmd-T. `bin/rvwctl STATUS` says where the
   transcript is going.
5. Afterwards, `bin/rvwctl SEARCH lease timeout` or `bin/rvwctl RECALL what did they
   say about reconnect behavior`.


## Using it

### Hotkeys

| Key | Command | What it does |
| --- | --- | --- |
| alt-cmd-R | `AUDIO_CAPTURE_TOGGLE` | start or stop capturing |
| alt-cmd-E | `EXPLAIN_SPEECH` | explain the last 60 s |
| alt-cmd-C | `UNGARBLE_SPEECH` | reconstruct the words of the last 45 s |
| alt-cmd-S | `SCREEN_SAVE` | archive the frontmost window; silent on success |
| ctrl-alt-cmd-A | `SCREEN_ADD` | the same save, OCRed and queued for the next analysis |
| ctrl-alt-cmd-S | `SCREEN_ANALYZE` | the same queueing, then submits everything pending |
| alt-cmd-T | `TRANSCRIPT_TOGGLE` | start or stop keeping this transcript |

### Commands

Anything the hotkeys do, and more, over the socket:

Every command also answers to a short case-sensitive alias, shown after the `#`:

```bash
bin/rvwctl EXPLAIN_SPEECH [seconds]        # E
bin/rvwctl UNGARBLE_SPEECH [seconds]        # C
bin/rvwctl SCREEN_SAVE                     # s
bin/rvwctl SCREEN_ADD                      # sa
bin/rvwctl SCREEN_ANALYZE                  # sx
bin/rvwctl SCREEN_VISION [seconds]         # V
bin/rvwctl SEARCH <words>                  # f
bin/rvwctl RECALL <question>               # r
bin/rvwctl REINDEX                         # R
bin/rvwctl AUDIO_CAPTURE_START [mic|system] / AUDIO_CAPTURE_STOP / AUDIO_CAPTURE_TOGGLE
                                           # c+ / c- / c
bin/rvwctl TRANSCRIPT_START / TRANSCRIPT_STOP / TRANSCRIPT_TOGGLE
                                           # t+ / t- / t
bin/rvwctl TRANSCRIPT_SHOW [seconds]       # T
bin/rvwctl ANSWER                          # A
bin/rvwctl MODELS / SET_MODEL <identifier> # ml / m
bin/rvwctl SET_LANGUAGE <language>         # l
bin/rvwctl STATUS / STATUS_FIELDS          # S / F
bin/rvwctl QUIT                            # q
```

`SEARCH` and `RECALL` take free text, which is why they are commands rather than
hotkeys. Every reply is a single line beginning `OK ` or `FAIL `.

### The console

```bash
bin/rvw --source none --console
```

runs the assistant in this terminal (`--console` implies `-here`, which on its
own remains the way to run in-terminal without a console for debugging) with an
`rvw> ` prompt: `s` takes a
screenshot and queues its OCR text, `S` queues and asks the model, `c TEXT` /
`C TEXT` do the same for a typed comment, `send` resubmits after a busy reply,
`pl` lists the selectable prompts in full and `p X` selects one by letter
(`c`, `e`, `r`, `u`, `v`; the choice is kept in `var/selected_prompt` and shared
with `rvwctl pl` / `rvwctl p X`), `:STATUS` and friends reach every socket
command, `q` quits. Software capture reads the screen, so the terminal running
it needs Screen Recording permission.

The same ordered pending context is reachable without a console: `SCREEN_ADD`
(the menu's "Screenshot and queue") archives a shot, OCRs it immediately and
queues the text, and `SCREEN_ANALYZE` does the same and then submits everything
pending under the selected prompt - clearing it only once the request was
accepted, so a busy model loses nothing. Socket commands answer as soon as the
request is queued rather than waiting out the streamed answer, and neither
hides a window nor waits out a capture delay. `SCREEN_VISION` stays the
independent vision-model flow and shares nothing with it.

For `s`/`S` the console hides itself by default: Hammerspoon (`hs -c`, so
Hammerspoon must be running with Accessibility permission) minimizes only the
console's own window, the helper captures the whole main display (including
whatever other windows are visible) while excluding the hidden one by id, and
the window is put back and focused afterwards, even if the capture failed.
Missing Hammerspoon or a window that will not minimize is a FAIL and no capture
happens at all. `--no-auto-hide-console` opts back into the old behaviour: the
window stays, and the screenshot instead pauses `console_capture_delay_seconds`
(2 s) to leave time for a Cmd-Tab back to the window of interest, which is the
right choice when Hammerspoon is not running or minimizing the terminal would
be disruptive.

### The other scripts

```bash
util/list_models.sh        # what is downloaded, what is loaded, what is served
util/start_llm_server.sh   # bring the endpoint up; does nothing if it is up
util/rebuild_index.sh      # REINDEX without a running daemon, for cron or after a copy
util/bench_model.sh <id>   # measure a loaded model; the numbers doc/model_benchmarks.md wants
util/run_tests.sh          # the test suite
```


## What it writes, and where

```
var/meetings/YYYY/MM/YYYY-MM-DD_HH.MM/
    transcript.jsonl        canonical; one JSON object per utterance, append only
    metadata.json           session start and end, streams, host, models used
    transcript.md           derived from the JSONL, rewritten from it, deletable
    screenshots/YYYY-MM-DD_HH.MM.SS.mmm.png
               /YYYY-MM-DD_HH.MM.SS.mmm.json    application, window, size, timestamps
var/index/meetings.db       the FTS5 index; derived, disposable, never synchronised
var/log/rvw_<timestamp>.log the session log, with the full text of every exchange
var/run/rvw.sock            the control socket
```

The session directory is named for the time the assistant started, so one run collects
everything in one place, and an image is associated with the speech around it by
sitting in the same directory with a comparable timestamp. No index makes that
association, so no index can lose it.

An ephemeral session creates none of it. That is the point: there is nothing to find,
nothing to forget to delete, and nothing to explain to whoever was in the conversation.

A transcript line that cannot be read back is fatal rather than skipped, both when
rendering the Markdown and when rebuilding the index. A rendering that was silently
one line short would be believed.


## Configuration

Every knob is a module-level name in `src/rvw/config.py`, and the ones worth changing
from outside are environment variables:

| Variable | Default | What it changes |
| --- | --- | --- |
| `RVW_LLM_URL` | `http://127.0.0.1:1234/v1` | the endpoint |
| `RVW_LLM_MODEL` | `meeting-assistant` | the identifier the model is served under |
| `RVW_LLM_SOURCE_MODEL` | per host, see below | the model loaded under that identifier |
| `RVW_VLM_MODEL` | `meeting-vision` | the vision model identifier |
| `RVW_WHISPER_MODEL` | `mlx-community/whisper-large-v3-turbo` | the recognition model |
| `RVW_WHISPER_LANGUAGE` | `en` | the recognition language |
| `RVW_ARCHIVE_DIR` | `var/meetings` | where meetings are kept |
| `RVW_INDEX_DB` | `var/index/meetings.db` | where the index lives |
| `RVW_RETENTION` | `ephemeral` | `retained` starts a session already keeping |
| `RVW_SCREENSHOT_TARGET` | `frontmost` | `display` captures the whole main display |

Three things about this deserve more than a table row.

**Which model a machine runs is decided in `config.py`, not in the environment.**
`llm_source_model_by_host` gives m3 the 4-bit Qwen3.6-35B-A3B and m4 the 3-bit quant
of the same MoE, matching on the hostname's first component. It is a table in the
source because the daemon runs inside `bin/rvw.app` under LaunchServices and never
sees a shell's exported variables: a machine configured by an export would load the
right model when the installer was run by hand and the wrong one an hour later, when
the idle timeout expired and the daemon reloaded it. `RVW_LLM_SOURCE_MODEL` still
overrides, for trying a candidate; to change a machine permanently, add its row - but
only once it has rows in `doc/model_benchmarks.md`.

**The reasoning model is silenced by a prefill, not by a request parameter.** Qwen3.6
thinks for hundreds of tokens before a one-sentence answer, and this LM Studio build
ignores every request-level reasoning control - measured identical to the token across
`reasoning_effort`, `chat_template_kwargs`, `enable_thinking` and the rest, on both
API versions. Left alone it spent 1023 of 1024 tokens thinking and returned an empty
answer, which is what made it look unusable. `llm.py` therefore prefills a closed,
empty `<think></think>` block as the start of the assistant turn, which the model's
own chat template reads as thinking already done: zero reasoning tokens, a full answer
in about five seconds. Because the thinking channel is closed, a prompt that invites
deliberation makes the model deliberate in the visible answer, so UNGARBLE's prompt is
a decisive, fixed three-part format. Keep that discipline in any new prompt.

**The endpoint cannot be trusted to serve the model it was asked for.** Asked for an
identifier it does not serve, this LM Studio build neither refuses nor loads anything:
it answers with whatever model is loaded and names that substitute in the response. A
screenshot sent to an absent vision model came back described in convincing detail by
the text model. `llm.py` therefore checks that the identifier is served before asking,
and checks the answering model named in every streamed chunk. Neither guard is
redundant: the listing can be right and the routing wrong, and a substitution can be
noticed only once the answer arrives.


## Testing

`util/run_tests.sh` runs 217 unittest tests in about twenty seconds and needs no
model, no permission, no daemon and no network. The strategy behind them - what is
tested in isolation, what has to be stubbed and how, what can only be verified by
hand, and the manual procedures for those - is in **`doc/testing`**.


## Status, and what is not verified

Phases 0 to 4 are implemented. Phase 2 is verified on the M4 against the real helpers
and the real model, Phase 3 on the M3, Phase 4 against a synthetic archive.

Outstanding, in rough order of how much it matters:

- `SCREEN_VISION` against a real vision model. None is downloaded on either
  machine, so the command archives the image and says plainly that nothing
  interpreted it.
- The microphone as a transcript stream on the M4. The permission is granted and the
  helper starts, but only the system stream has produced transcript text.
- The hotkeys themselves: every command in every verification so far was sent with
  `bin/rvwctl`, which reaches the same dispatcher entry but is not the same as
  pressing the key.
- `RECALL` against the real model, end to end, rather than the recorded stub.
- A long meeting, and a large archive. The longest retained session is minutes, and
  the first-use index rebuild is the only cost that grows with the archive.

Known rough edges: only one answer runs at a time, so a second hotkey during a
streaming answer is refused rather than queued; continuous analysis is a plain timer
re-running EXPLAIN_SPEECH; the interpretation is printed in the assistant's terminal, which is
private only as long as that terminal is not the window being shared; `--target
display` captures the main display only; Whisper checks Hugging Face for the model
revision at start-up unless `HF_HUB_OFFLINE=1`.


## Troubleshooting

**"no local LLM at http://127.0.0.1:1234/v1".** The LM Studio server is down, which is
what happens after a reboot if the LaunchAgent is not installed. Run
`util/start_llm_server.sh`, then `util/init_llm_autostart.sh` so it does not recur.

**The first question after a quiet hour takes about 45 seconds.** That is the model
being loaded again. LM Studio unloads it after `llm_idle_ttl_seconds` on purpose, and
an unloaded model is logged INFO rather than FAIL, because it is the ordinary state
between questions.

**"the mic capture helper exited immediately".** The permission was denied, and macOS
no longer prompts. Run `util/init_permissions.sh -open`, or `-reset` to make macOS ask
again.

**A permission that was working is suddenly missing.** `bin/rvw.app` was re-signed.
An ad hoc signature is pinned to the launcher's cdhash, so every grant to the bundle
is void; the permissions have to be granted again.

**"not interpreted: no model is loaded as 'meeting-vision'".** Correct behaviour, not
a fault: the screenshot was archived and no model was allowed to pretend it had seen
it. Load a vision model under that identifier, or set `RVW_VLM_MODEL`.

**A search finds nothing that was just said.** The index is a rebuild behind a live
retained session. Run `bin/rvwctl REINDEX` or `util/rebuild_index.sh`.

**Log prefixes.** `OK ` an action succeeded, `INFO ` routine news worth no one's
effort, `FAIL ` a problem needing attention. FAIL is never spent on something working
as designed, which is what makes it worth reading.
