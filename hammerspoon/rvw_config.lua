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
  {mods = {"alt", "cmd"}, key = "r", command = "TOGGLE_CAPTURE",
   description = "capture and transcribe"},
  {mods = {"ctrl", "alt", "cmd"}, key = "r", command = "TOGGLE_CONTINUOUS",
   description = "capture, transcribe and analyse continuously"},
  {mods = {"alt", "cmd"}, key = "e", command = "EXPLAIN",
   description = "explain the last minute"},
  {mods = {"alt", "cmd"}, key = "c", command = "CLARIFY",
   description = "clarify what was just said"},
  {mods = {"alt", "cmd"}, key = "s", command = "SCREENSHOT", silent = true,
   description = "screenshot, save only"},
  {mods = {"ctrl", "alt", "cmd"}, key = "s", command = "INTERPRET_SCREEN", silent = true,
   description = "screenshot, save and interpret"},
  -- Retention is the one command worth an alert even mid-meeting: whether this
  -- conversation is being kept is not something to be unsure about.
  {mods = {"alt", "cmd"}, key = "t", command = "TOGGLE_RETENTION",
   description = "keep or stop keeping the transcript"},
  {mods = {"alt", "cmd"}, key = "p", local_action = "toggle_presenting",
   description = "presenting: draw nothing on this screen"},
}

-- The menu items below the capture and retention state. Anything reached by a
-- hotkey is here too, because the menu is also the reminder of what exists.
config.menu_actions = {
  {title = "Explain the last minute", command = "EXPLAIN"},
  {title = "Clarify what was just said", command = "CLARIFY"},
  {title = "Screenshot", command = "SCREENSHOT", silent = true},
  {title = "Screenshot and interpret", command = "INTERPRET_SCREEN", silent = true},
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
