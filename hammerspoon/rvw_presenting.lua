-- Whether this screen is being shown to other people.
--
-- Presenting mode is the answer to the one thing the assistant must never do:
-- draw an LLM answer, or any other alert, into material I am sharing. It is on
-- when I say it is on (alt-cmd-P), and also whenever a share can be recognised,
-- which is only sometimes. Detection can therefore add certainty but never
-- remove it: a manual "yes, I am presenting" is never overridden by a failure to
-- find a sharing window.

local config = require("rvw_config")

local presenting = {}

local forced = false

local function window_titles_of(application_name)
  local application = hs.application.get(application_name)
  if application == nil then
    return {}
  end
  local titles = {}
  for _, window in ipairs(application:allWindows()) do
    table.insert(titles, (window:title() or ""):lower())
  end
  return titles
end

local function titles_include_a_share(titles, share_titles)
  for _, title in ipairs(titles) do
    for _, fragment in ipairs(share_titles) do
      if title:find(fragment, 1, true) ~= nil then
        return true
      end
    end
  end
  return false
end

--- Whether a screen share can be recognised right now. Best effort; see rvw_config.
function presenting.share_is_detected()
  for _, entry in ipairs(config.sharing_windows) do
    if titles_include_a_share(window_titles_of(entry.application), entry.titles) then
      return true
    end
  end
  return false
end

--- Whether nothing at all may be drawn on this screen.
function presenting.is_on()
  return forced or presenting.share_is_detected()
end

--- Whether the current state was asked for rather than detected.
function presenting.is_forced()
  return forced
end

--- Turn presenting mode on or off by hand, and say which it now is.
function presenting.toggle()
  forced = not forced
  if forced then
    return "OK presenting: the assistant will draw nothing on this screen"
  end
  return "OK not presenting: alerts are visible again"
end

return presenting
