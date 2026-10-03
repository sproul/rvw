-- Sending one command to the assistant and reading its reply.
--
-- hs.execute is synchronous, which matters for the screenshot commands: the
-- capture is finished before anything can be drawn, so an alert can never appear
-- in the saved image.

local state = require("rvw_state")

local client = {}

local module_dir = debug.getinfo(1, "S").source:sub(2):match("(.*)/")
client.repo_dir = module_dir:gsub("/hammerspoon$", "")
client.rvwctl = client.repo_dir .. "/bin/rvwctl"

--- Send one command and return its single line reply.
-- Every command is marked -hotkey, the menu's included: a hotkey or a menu click
-- leaves the window of interest frontmost, so the assistant must not hide it
-- from a screenshot the way it hides a terminal that typed the command.
function client.send(command)
  local output, succeeded = hs.execute(client.rvwctl .. " -hotkey " .. command)
  local reply = (output or ""):gsub("%s+$", "")
  if not succeeded and reply == "" then
    return "FAIL could not run " .. client.rvwctl
  end
  return reply
end

--- Send several commands in order and return the last reply, or the first failure.
-- Choosing one capture source is two commands (stop, then start the one wanted)
-- and a menu item is one press, so the sequence belongs here.
function client.send_all(commands)
  local reply = "FAIL nothing to send"
  for _, command in ipairs(commands) do
    reply = client.send(command)
    if reply:sub(1, 3) ~= "OK " then
      return reply
    end
  end
  return reply
end

--- The session state, or state.unreachable if no assistant is listening.
function client.status()
  return state.parse_status_fields(client.send("STATUS_FIELDS"))
end

--- Which models the endpoint serves and which one answers; asked only when the
-- menu is opened, because it costs a request to the LLM endpoint.
function client.models()
  return state.parse_status_fields(client.send("MODELS"))
end

return client
