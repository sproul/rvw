-- What the menu bar knows and what it is allowed to show.
--
-- This module deliberately touches nothing: no hs API, no process, no file. It
-- turns one STATUS_FIELDS reply into a table, decides what the menu bar title
-- says, and decides whether a reply may be drawn on the screen at all. Keeping
-- those decisions here is what makes them testable without Hammerspoon, and the
-- last of them is the one that must never be wrong, because an alert drawn while
-- I am sharing my screen is seen by the audience before it is seen by me.

local state = {}

-- The reply says nothing about a session, because there is no session to ask.
state.unreachable = {reachable = false}

local function trimmed(text)
  return ((text or ""):gsub("^%s+", ""):gsub("%s+$", ""))
end

local function successful_reply_body(reply)
  local text = trimmed(reply)
  if text:sub(1, 3) ~= "OK " then
    return nil
  end
  return text:sub(4)
end

--- One STATUS_FIELDS reply as a table of named values.
-- Anything that is not a successful reply is an assistant we cannot see, which
-- is a state of its own and not an empty session.
function state.parse_status_fields(reply)
  local body = successful_reply_body(reply)
  if body == nil then
    return state.unreachable
  end
  local fields = {reachable = true}
  for name, value in body:gmatch("([%w_]+)=(%S+)") do
    fields[name] = value
  end
  return fields
end

--- The capture streams this run of the assistant offers, in the order given.
function state.stream_names(status)
  local names = {}
  for name in (status.streams or ""):gmatch("[^,]+") do
    table.insert(names, name)
  end
  return names
end

function state.is_listening(status)
  return status.reachable and status.capture ~= nil and status.capture ~= "none"
end

function state.is_retaining(status)
  return status.retention == "retained"
end

function state.is_analysing(status)
  return status.continuous == "on"
end

local function status_flags(status, presenting)
  local flags = ""
  if state.is_listening(status) then flags = flags .. "L" end
  if state.is_retaining(status) then flags = flags .. "R" end
  if state.is_analysing(status) then flags = flags .. "A" end
  if presenting then flags = flags .. "P" end
  return flags
end

--- The menu bar title: the recording indicator, read at a glance.
-- L listening, R keeping the transcript, A analysing continuously, P presenting
-- and therefore silent; "-" for a session doing none of it and "x" for no
-- assistant at all, which must not look like an idle one.
function state.menu_title(status, presenting)
  if not status.reachable then
    return "rvw x"
  end
  local flags = status_flags(status, presenting)
  if flags == "" then
    flags = "-"
  end
  return "rvw " .. flags
end

--- One line of prose at the top of the menu, for when a glance is not enough.
function state.status_summary(status)
  if not status.reachable then
    return "the assistant is not running"
  end
  return string.format("listening to %s, %s, %s utterance(s), %s",
    status.capture or "nothing", status.retention or "unknown retention",
    status.segments or "?", status.meeting or "no meeting directory")
end

--- Whether a reply to a command may be drawn on the screen.
-- Presenting silences everything, failures included: while I am sharing, an
-- explanation of why a screenshot failed is worse than the failure. Silent
-- actions say nothing when they work, so that the screenshot hotkey leaves no
-- trace in the image it just captured.
function state.alert_is_wanted(reply, silent_on_success, presenting)
  if presenting then
    return false
  end
  if silent_on_success and successful_reply_body(reply) ~= nil then
    return false
  end
  return true
end

return state
