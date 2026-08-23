-- Running one configured entry: a hotkey press or a menu choice.
--
-- Hotkeys and menu items do the same thing and must keep doing the same thing,
-- so both go through here. An entry either names commands for the assistant or a
-- local action that never leaves Hammerspoon.

local client = require("rvw_client")
local presenting = require("rvw_presenting")
local state = require("rvw_state")
local windows = require("rvw_windows")

local actions = {}

local local_actions = {
  toggle_answer_window = function() return windows.toggle("answer") end,
  toggle_presenting = presenting.toggle,
  toggle_transcript_window = function() return windows.toggle("transcript") end,
}

local function commands_of(entry)
  if entry.commands ~= nil then
    return entry.commands
  end
  return {entry.command}
end

local function reply_to(entry)
  if entry.local_action ~= nil then
    local action = local_actions[entry.local_action]
    if action == nil then
      return "FAIL unknown local action " .. entry.local_action
    end
    return action()
  end
  return client.send_all(commands_of(entry))
end

--- Report a reply, unless this screen is being shared or the action is a quiet one.
function actions.report(reply, silent_on_success)
  if state.alert_is_wanted(reply, silent_on_success, presenting.is_on()) then
    hs.alert.show(reply, 3)
  end
end

--- Run one entry from rvw_config and report what happened.
function actions.run(entry)
  actions.report(reply_to(entry), entry.silent)
end

--- A function suitable for a hotkey binding or a menu callback.
function actions.runner(entry)
  return function() actions.run(entry) end
end

return actions
