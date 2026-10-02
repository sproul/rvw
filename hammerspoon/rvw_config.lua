-- What the hotkeys and the menu bar do: the one file to edit.
--
-- Every entry names either a command the assistant understands (test/test_menu_bar.py
-- checks that it does) or one of the local actions implemented in rvw_actions.lua.
-- silent = true means say nothing when the command succeeds, which is what keeps
-- an action invisible in the screen it has just captured.

local config = {}

-- How often the menu bar title is refreshed. Each refresh is one rvwctl run, so
-- this is a few tens of milliseconds of system python every few seconds.
config.status_poll_seconds = 5

config.hotkeys = {
  {mods = {"alt", "cmd"}, key = "r", command = "AUDIO_CAPTURE_TOGGLE",
   description = "capture and transcribe"},
  {mods = {"alt", "cmd"}, key = "e", command = "EXPLAIN_SPEECH",
   description = "explain the last minute"},
  {mods = {"alt", "cmd"}, key = "c", command = "UNGARBLE_SPEECH",
   description = "ungarble what was just said"},
  {mods = {"alt", "cmd"}, key = "s", command = "SCREENSHOT", silent = true,
   description = "screenshot, save only"},
  {mods = {"ctrl", "alt", "cmd"}, key = "s", command = "SCREEN_VISION", silent = true,
   description = "screenshot, save and interpret"},
  -- Retention is the one command worth an alert even mid-meeting: whether this
  -- conversation is being kept is not something to be unsure about.
  {mods = {"alt", "cmd"}, key = "t", command = "TRANSCRIPT_TOGGLE",
   description = "keep or stop keeping the transcript"},
  {mods = {"alt", "cmd"}, key = "p", local_action = "toggle_presenting",
   description = "presenting: draw nothing on this screen"},
  -- Both windows are invisible to screen capture (see rvw_windows.lua), so these
  -- two are safe to press mid-meeting; nothing else here draws anything at all.
  {mods = {"alt", "cmd"}, key = "w", local_action = "toggle_transcript_window",
   description = "show or hide the rolling transcript window"},
  {mods = {"ctrl", "alt", "cmd"}, key = "w", local_action = "toggle_answer_window",
   description = "show or hide the answer window"},
}

-- The menu items below the capture and retention state. Anything reached by a
-- hotkey is here too, because the menu is also the reminder of what exists.
config.menu_actions = {
  {title = "Explain the last minute", command = "EXPLAIN_SPEECH"},
  {title = "Ungarble what was just said", command = "UNGARBLE_SPEECH"},
  {title = "Screenshot", command = "SCREENSHOT", silent = true},
  {title = "Screenshot and interpret", command = "SCREEN_VISION", silent = true},
  {title = "Rebuild the search index", command = "REINDEX"},
}

-- How the capture streams are named in the menu; the assistant's own labels for
-- them are "me" and "them".
config.stream_titles = {mic = "me (microphone)", system = "them (system audio)"}

-- Recognising a screen share is best effort and always will be: a Meet or Teams
-- share inside a browser tab is indistinguishable from the tab. Zoom does open a
-- window of its own for it, and that is worth catching, but the manual
-- presenting toggle (alt-cmd-P) is the mechanism to rely on. Each entry is an
-- application name and the lower case window title fragments that mean it is
-- sharing.
config.sharing_windows = {
  {application = "zoom.us", titles = {"zoom share statusbar window", "as_toolbar"}},
}

return config
