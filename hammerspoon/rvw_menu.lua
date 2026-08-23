-- The menu bar item: what the assistant is doing, and everything it can be told.
--
-- The title is the indicator (see rvw_state.menu_title) and is refreshed on a
-- timer; the menu itself is built when it is opened, from a status read at that
-- moment, so nothing in it can be stale. Every choice goes through rvw_actions,
-- which is also what the hotkeys use.

local actions = require("rvw_actions")
local client = require("rvw_client")
local config = require("rvw_config")
local presenting = require("rvw_presenting")
local state = require("rvw_state")

local menu = {}

local bar = nil
local poll_timer = nil

local function run_and_refresh(entry)
  return function()
    actions.run(entry)
    menu.refresh()
  end
end

--- Read the state and put it in the menu bar title.
function menu.refresh()
  if bar == nil then
    return
  end
  bar:setTitle(state.menu_title(client.status(), presenting.is_on()))
end

local function capture_includes(status, stream_name)
  for name in (status.capture or ""):gmatch("[^,]+") do
    if name == stream_name then
      return true
    end
  end
  return false
end

local function single_source_item(status, stream_name)
  return {title = "Listen to " .. (config.stream_titles[stream_name] or stream_name),
          checked = capture_includes(status, stream_name),
          fn = run_and_refresh({commands = {"STOP_CAPTURE", "START_CAPTURE " .. stream_name}})}
end

local function every_stream_is_capturing(status)
  for _, name in ipairs(state.stream_names(status)) do
    if not capture_includes(status, name) then
      return false
    end
  end
  return state.is_listening(status)
end

--- One item per capture stream this run of the assistant offers, then both and none.
local function source_items(status)
  local names = state.stream_names(status)
  local items = {}
  if #names > 1 then
    table.insert(items, {title = "Listen to everything",
                         checked = every_stream_is_capturing(status),
                         fn = run_and_refresh({commands = {"START_CAPTURE"}})})
  end
  for _, name in ipairs(names) do
    table.insert(items, single_source_item(status, name))
  end
  table.insert(items, {title = "Stop listening", disabled = not state.is_listening(status),
                       fn = run_and_refresh({command = "STOP_CAPTURE"})})
  return items
end

local function toggle_items(status)
  return {
    {title = "Analyse continuously", checked = state.is_analysing(status),
     fn = run_and_refresh({command = "TOGGLE_CONTINUOUS"})},
    {title = "Keep the transcript of this conversation",
     checked = state.is_retaining(status),
     fn = run_and_refresh({command = "TOGGLE_RETENTION"})},
    {title = "Presenting: draw nothing on this screen", checked = presenting.is_on(),
     disabled = presenting.share_is_detected() and not presenting.is_forced(),
     fn = run_and_refresh({local_action = "toggle_presenting"})},
  }
end

local function action_items()
  local items = {}
  for _, entry in ipairs(config.menu_actions) do
    table.insert(items, {title = entry.title, fn = run_and_refresh(entry)})
  end
  return items
end

local function hotkey_description(entry)
  return table.concat(entry.mods, "-") .. "-" .. entry.key .. ": " .. entry.description
end

local function hotkey_items()
  local items = {}
  for _, entry in ipairs(config.hotkeys) do
    table.insert(items, {title = hotkey_description(entry), disabled = true})
  end
  return items
end

--- What cannot be changed from here but is worth knowing: the models and the keys.
local function information_items(status)
  return {
    {title = "Model: " .. (status.model or "unknown"), disabled = true},
    {title = "Vision model: " .. (status.vision_model or "unknown"), disabled = true},
    {title = "Hotkeys", menu = hotkey_items()},
  }
end

local function append_all(items, more)
  for _, item in ipairs(more) do
    table.insert(items, item)
  end
end

local separator = {title = "-"}

--- Everything in the menu, for the state given, or for the state right now.
-- Taking the status as an argument is what lets a test build the whole menu,
-- including the menu of a session that is not running at all.
function menu.items(status)
  status = status or client.status()
  local items = {{title = state.status_summary(status), disabled = true}, separator}
  append_all(items, source_items(status))
  table.insert(items, separator)
  append_all(items, toggle_items(status))
  table.insert(items, separator)
  append_all(items, action_items())
  table.insert(items, separator)
  append_all(items, information_items(status))
  table.insert(items, separator)
  append_all(items, {{title = "Quit the assistant",
                      fn = run_and_refresh({command = "QUIT"})}})
  return items
end

--- Put the item in the menu bar and keep its title current.
function menu.start()
  if bar ~= nil then
    return
  end
  bar = hs.menubar.new()
  bar:setMenu(function() return menu.items() end)
  menu.refresh()
  poll_timer = hs.timer.doEvery(config.status_poll_seconds, menu.refresh)
end

function menu.stop()
  if poll_timer ~= nil then
    poll_timer:stop()
    poll_timer = nil
  end
  if bar ~= nil then
    bar:delete()
    bar = nil
  end
end

return menu
