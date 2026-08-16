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

local function send_command(command)
  local output, succeeded = hs.execute(rvwctl .. " " .. command)
  local reply = (output or ""):gsub("%s+$", "")
  if not succeeded and reply == "" then
    reply = "FAIL could not run " .. rvwctl
  end
  hs.alert.show(reply, 2)
end

local function command_sender(command)
  return function() send_command(command) end
end

hs.hotkey.bind(mods, "r", command_sender("TOGGLE_CAPTURE"))
hs.hotkey.bind(mods_with_ctrl, "r", command_sender("TOGGLE_CONTINUOUS"))
hs.hotkey.bind(mods, "e", command_sender("EXPLAIN"))

hs.alert.show("OK rvw hotkeys: alt-cmd-R capture, ctrl-alt-cmd-R analyse, alt-cmd-E explain")
