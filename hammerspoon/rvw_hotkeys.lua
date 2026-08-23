-- The user interface of the local listening assistant: global hotkeys and a menu
-- bar item.
--
-- Hammerspoon owns them so that the assistant itself needs no accessibility
-- permission, and so the same keys can later drive an assistant running on the
-- companion Mac: everything here goes through rvwctl and knows nothing else
-- about the daemon. What the keys and the menu do is in rvw_config.lua.
--
-- Install by adding these two lines to ~/.hammerspoon/init.lua:
--   package.path = os.getenv("HOME") .. "/dp/git/rvw/hammerspoon/?.lua;" .. package.path
--   require("rvw_hotkeys")

local actions = require("rvw_actions")
local config = require("rvw_config")
local menu = require("rvw_menu")

local bound_hotkeys = {}

local function bind(entry)
  return hs.hotkey.bind(entry.mods, entry.key, function()
    actions.run(entry)
    menu.refresh()
  end)
end

for _, entry in ipairs(config.hotkeys) do
  table.insert(bound_hotkeys, bind(entry))
end

menu.start()

return {hotkeys = bound_hotkeys, menu = menu}
