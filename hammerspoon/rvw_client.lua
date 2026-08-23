-- Sending one command to the assistant and reading its reply.
--
-- hs.execute is synchronous, which matters for the screenshot commands: the
-- capture is finished before anything can be drawn, so an alert can never appear
-- in the saved image.

local state = require("rvw_state")

local client = {}

local module_dir = debug.getinfo(1, "S").source:sub(2):match("(.*)/")
client.rvwctl = module_dir:gsub("/hammerspoon$", "") .. "/bin/rvwctl"

--- Send one command and return its single line reply.
function client.send(command)
  local output, succeeded = hs.execute(client.rvwctl .. " " .. command)
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

return client
