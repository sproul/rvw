"""Tests for the Phase 6 menu bar and its hotkey configuration.

The menu bar itself is Hammerspoon's, and most of it can only be looked at. Two
things can be tested, and they are the two that would hurt:

1. Every command the menus and the hotkeys name is a command the assistant
   actually understands. A menu item quietly naming a command that was renamed
   would fail only when it was pressed, mid-conversation.
2. The pure decisions in `hammerspoon/rvw_state.lua`: what the menu bar title
   says about the session, and above all that nothing at all is drawn while I am
   presenting, because an alert on a shared screen is the one failure the
   audience sees before I do.
"""

import re
import unittest
from pathlib import Path

import lua_testing
from test_assistant import expected_commands

repo_dir = Path(__file__).resolve().parents[1]
hammerspoon_dir = repo_dir / "hammerspoon"

command_source_pattern = re.compile(r"(?:commands?\s*=|send(?:_all)?\()\s*(\{[^}]*\}|\"[^\"]*\")")
quoted_string_pattern = re.compile(r"\"([^\"]*)\"")
hotkey_table_pattern = re.compile(r"config\.hotkeys\s*=\s*\{(.*?)\n\}", re.DOTALL)

idle_status = "OK capture=none continuous=off language=en languages=de,en,fr " \
              "meeting=2026-08-22_21.30 model=meeting-assistant retention=ephemeral " \
              "segments=0 streams=mic,system vision_model=meeting-vision"
busy_status = "OK capture=mic,system continuous=on language=de languages=de,en,fr " \
              "meeting=2026-08-22_21.30 model=meeting-assistant retention=retained " \
              "segments=12 streams=mic,system vision_model=meeting-vision"


def lua_files():
    return sorted(hammerspoon_dir.glob("*.lua"))


def hotkey_entries():
    """The text of each entry in the hotkey table, one per binding."""
    text = (hammerspoon_dir / "rvw_config.lua").read_text(encoding="utf-8")
    table = hotkey_table_pattern.search(text)
    assert table is not None, "FAIL rvw_config.lua no longer declares config.hotkeys"
    return [entry for entry in table.group(1).split("{") if "key =" in entry]


def commands_named_in(text):
    """Every assistant command named in one lua file, argument words removed."""
    named = []
    for assignment in command_source_pattern.findall(text):
        for quoted in quoted_string_pattern.findall(assignment):
            named.append(quoted.split()[0])
    return named


class CommandsNamedByTheUserInterfaceTest(unittest.TestCase):
    """The menus and hotkeys are only useful if the daemon knows their commands."""

    def test_every_command_named_in_the_lua_is_registered_by_the_assistant(self):
        for path in lua_files():
            for command in commands_named_in(path.read_text(encoding="utf-8")):
                self.assertIn(command, expected_commands, "%s names %s" % (path.name, command))

    def test_the_menu_bar_reads_the_machine_readable_status(self):
        text = (hammerspoon_dir / "rvw_client.lua").read_text(encoding="utf-8")
        self.assertIn("STATUS_FIELDS", commands_named_in(text))

    def test_every_hotkey_names_a_command_or_a_local_action(self):
        entries = hotkey_entries()
        self.assertTrue(entries, "no hotkeys are declared")
        for entry in entries:
            self.assertTrue("command =" in entry or "local_action =" in entry, entry)

    def test_every_hotkey_describes_itself_for_the_menu(self):
        for entry in hotkey_entries():
            self.assertIn("description =", entry)

    def test_the_lua_is_plain_ascii(self):
        """A unicode glyph in a menu bar title is exactly the kind of decoration
        that renders differently everywhere and reads as line noise in a diff."""
        for path in lua_files():
            path.read_text(encoding="ascii")


class MenuBarTitleTest(lua_testing.PureLuaTestCase):
    """The title is the recording indicator: it has to be right at a glance."""

    def title_for(self, status_reply, presenting="false"):
        return self.evaluate('module.menu_title(module.parse_status_fields(%s), %s)'
                             % (self.quoted(status_reply), presenting))

    @staticmethod
    def quoted(status_reply):
        return "nil" if status_reply is None else '"%s"' % status_reply

    def test_an_idle_session_shows_no_flags(self):
        self.assertEqual("rvw -", self.title_for(idle_status))

    def test_listening_retaining_and_analysing_each_add_a_letter(self):
        self.assertEqual("rvw LRA", self.title_for(busy_status))

    def test_presenting_is_shown_because_it_silences_everything_else(self):
        self.assertEqual("rvw LRAP", self.title_for(busy_status, presenting="true"))

    def test_an_assistant_that_is_not_running_says_so_rather_than_looking_idle(self):
        self.assertEqual("rvw x", self.title_for(None))

    def test_a_failed_reply_is_not_read_as_a_session(self):
        self.assertEqual("rvw x", self.title_for("FAIL the assistant is not listening"))


class StatusParsingTest(lua_testing.PureLuaTestCase):

    def field(self, status_reply, name):
        return self.evaluate('tostring(module.parse_status_fields("%s").%s)'
                             % (status_reply, name))

    def test_the_fields_are_read_by_name(self):
        self.assertEqual("mic,system", self.field(busy_status, "capture"))
        self.assertEqual("retained", self.field(busy_status, "retention"))
        self.assertEqual("meeting-assistant", self.field(busy_status, "model"))

    def test_a_reachable_session_is_marked_as_reachable(self):
        self.assertEqual("true", self.field(idle_status, "reachable"))

    def test_the_available_streams_are_a_list_the_menu_can_offer(self):
        self.assertEqual("mic system", self.evaluate(
            'table.concat(module.stream_names(module.parse_status_fields("%s")), " ")'
            % busy_status))

    def test_the_summary_line_names_what_is_happening_and_where_it_goes(self):
        summary = self.evaluate('module.status_summary(module.parse_status_fields("%s"))'
                                % busy_status)
        self.assertIn("retained", summary)
        self.assertIn("2026-08-22_21.30", summary)


class MenuContentsTest(lua_testing.PureLuaTestCase):
    """The menu is built when it is opened, so a mistake in it shows up in front of
    me mid-meeting. Building it here for a live session, an idle one and no session
    at all is the cheapest way to know that it cannot fail then."""

    module_name = "rvw_menu"

    def titles(self, status_reply):
        """Every menu title, checked ones marked, for one status reply."""
        listed = self.evaluate_body("""
            local state = require("rvw_state")
            local titles = {}
            for _, item in ipairs(module.items(state.parse_status_fields(%s))) do
              table.insert(titles, item.title .. (item.checked and " [checked]" or ""))
            end
            return table.concat(titles, "\\n")
            """ % ("nil" if status_reply is None else '"%s"' % status_reply))
        return listed.splitlines()

    def test_a_source_is_offered_for_every_stream_the_assistant_runs(self):
        titles = self.titles(idle_status)
        self.assertIn("Listen to me (microphone)", titles)
        self.assertIn("Listen to them (system audio)", titles)
        self.assertIn("Listen to everything", titles)

    def test_the_running_sources_and_the_kept_transcript_are_ticked(self):
        titles = self.titles(busy_status)
        self.assertIn("Listen to everything [checked]", titles)
        self.assertIn("Keep the transcript of this conversation [checked]", titles)

    def test_nothing_is_ticked_in_an_idle_session(self):
        self.assertEqual([], [title for title in self.titles(idle_status)
                              if "[checked]" in title and "Presenting" not in title])

    def test_the_menu_of_an_assistant_that_is_not_running_still_builds(self):
        titles = self.titles(None)
        self.assertIn("the assistant is not running", titles)
        self.assertIn("Model: unknown", titles)

    def test_every_action_and_the_model_are_named(self):
        titles = self.titles(busy_status)
        self.assertIn("Explain the last minute", titles)
        self.assertIn("Screenshot and interpret", titles)
        self.assertIn("Model: meeting-assistant", titles)
        self.assertIn("Quit the assistant", titles)

    def test_both_windows_can_be_shown_from_the_menu(self):
        titles = self.titles(idle_status)
        self.assertIn("Transcript window", titles)
        self.assertIn("Answer window", titles)

    def test_the_recognition_language_is_shown_and_offered(self):
        titles = self.titles(busy_status)
        self.assertIn("Recognising: de", titles)
        languages = self.submenu_titles(busy_status, "Recognising: de")
        self.assertEqual(["de [checked]", "en", "fr"], languages)

    def submenu_titles(self, status_reply, item_title):
        """The titles inside one submenu, checked ones marked."""
        listed = self.evaluate_body("""
            local state = require("rvw_state")
            local titles = {}
            for _, item in ipairs(module.items(state.parse_status_fields("%s"))) do
              if item.title == "%s" then
                for _, entry in ipairs(item.menu or {}) do
                  table.insert(titles, entry.title .. (entry.checked and " [checked]" or ""))
                end
              end
            end
            return table.concat(titles, "\\n")
            """ % (status_reply, item_title))
        return listed.splitlines()


class AlertPolicyTest(lua_testing.PureLuaTestCase):
    """What may be drawn on the screen, which may be a screen everybody can see."""

    def alert_is_wanted(self, reply, silent_on_success="false", presenting="false"):
        return self.evaluate('tostring(module.alert_is_wanted("%s", %s, %s))'
                             % (reply, silent_on_success, presenting))

    def test_nothing_is_drawn_while_presenting(self):
        self.assertEqual("false", self.alert_is_wanted("OK explaining", presenting="true"))

    def test_not_even_a_failure_is_drawn_while_presenting(self):
        self.assertEqual("false", self.alert_is_wanted("FAIL no model", presenting="true"))

    def test_a_silent_action_says_nothing_when_it_succeeds(self):
        self.assertEqual("false", self.alert_is_wanted("OK screenshot saved",
                                                       silent_on_success="true"))

    def test_a_silent_action_still_reports_a_failure(self):
        self.assertEqual("true", self.alert_is_wanted("FAIL capture helper missing",
                                                      silent_on_success="true"))

    def test_an_ordinary_action_reports_its_reply(self):
        self.assertEqual("true", self.alert_is_wanted("OK capture running (mic)"))


if __name__ == "__main__":
    unittest.main()
