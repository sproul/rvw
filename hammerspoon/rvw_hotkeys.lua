-- Global hotkeys for the local listening assistant.
--
-- Hammerspoon owns the hotkeys so that the assistant itself needs no
-- accessibility permission, and so the same key can later drive an assistant
-- running on the companion Mac. Each hotkey sends one command through rvwctl.
--
-- Install by adding these two lines to ~/.hammerspoon/init.lua:
--   package.path = os.getenv("HOME") .. "/dp/git/rvw/hammerspoon/?.lua;" .. package.path
--   require("rvw_hotkeys")

local module_dir = debug.getinfo(1, "S").source:sub(2):match("(.*)/")
local rvwctl = module_dir:gsub("/hammerspoon$", "") .. "/bin/rvwctl"

local mods = {"alt", "cmd"}
local mods_with_ctrl = {"ctrl", "alt", "cmd"}

-- hs.execute is synchronous, so the capture is already finished before any alert
-- is drawn and the alert can never appear in the saved image.
local function send_command(command, silent_on_success)
  local output, succeeded = hs.execute(rvwctl .. " " .. command)
  local reply = (output or ""):gsub("%s+$", "")
  if not succeeded and reply == "" then
    reply = "FAIL could not run " .. rvwctl
  end
  if silent_on_success and reply:sub(1, 3) == "OK " then
    return
  end
  hs.alert.show(reply, 2)
end

local function command_sender(command)
  return function() send_command(command, false) end
end

-- The screenshot actions say nothing when they succeed: an alert would be
-- visible to everyone I am sharing my screen with.
local function silent_command_sender(command)
  return function() send_command(command, true) end
end

hs.hotkey.bind(mods, "r", command_sender("TOGGLE_CAPTURE"))
hs.hotkey.bind(mods_with_ctrl, "r", command_sender("TOGGLE_CONTINUOUS"))
hs.hotkey.bind(mods, "e", command_sender("EXPLAIN"))
hs.hotkey.bind(mods, "c", command_sender("CLARIFY"))
hs.hotkey.bind(mods, "s", silent_command_sender("SCREENSHOT"))
hs.hotkey.bind(mods_with_ctrl, "s", silent_command_sender("INTERPRET_SCREEN"))

hs.alert.show("OK rvw hotkeys: alt-cmd-R capture, ctrl-alt-cmd-R analyse, alt-cmd-E explain, "
  .. "alt-cmd-C clarify, alt-cmd-S screenshot, ctrl-alt-cmd-S screenshot and interpret")
