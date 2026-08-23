-- The transcript and answer windows, which are a separate program on purpose.
--
-- A window that must never appear in shared material has to be excluded by the
-- window server, and only NSWindow.sharingType = .none does that. A Hammerspoon
-- canvas cannot set it, so the windows are `bin/rvw_view`, one process per
-- window, started and stopped from here. They are children of Hammerspoon, so
-- reloading Hammerspoon closes them, and the viewer exits by itself when the
-- assistant goes away: no window outlives the session it is showing.

local client = require("rvw_client")

local windows = {}

local viewer = client.repo_dir .. "/bin/rvw_view"
local running = {}

local function forget(kind)
  return function() running[kind] = nil end
end

function windows.is_showing(kind)
  local task = running[kind]
  return task ~= nil and task:isRunning()
end

local function open(kind)
  local task = hs.task.new(viewer, forget(kind), {"--window", kind})
  if task == nil or not task:start() then
    return "FAIL could not start " .. viewer
  end
  running[kind] = task
  return "OK the " .. kind .. " window is open, and is invisible to screen capture"
end

local function close(kind)
  running[kind]:terminate()
  running[kind] = nil
  return "OK the " .. kind .. " window is closed"
end

--- Show or hide one window, and say which it now is.
function windows.toggle(kind)
  if windows.is_showing(kind) then
    return close(kind)
  end
  return open(kind)
end

return windows
