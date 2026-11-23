"""Run from the repository root: python3 -m unittest discover tests."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import key_hints as kh

KEYS = {
    "prefix": "ctrl+b",
    "new_tab": "cmd+t",
    "close_tab": "cmd+w",
    "goto": "cmd+k",
    "focus_pane_left": "ctrl+h",
    "focus_pane_down": "ctrl+j",
    "focus_pane_up": "ctrl+k",
    "focus_pane_right": "ctrl+l",
    "zoom": ["prefix+z", "cmd+shift+z"],
    "command": [
        {"key": "cmd+shift+d", "type": "popup", "command": "hunk", "description": "live diff"},
        {"key": "cmd+u", "type": "plugin_action", "command": "martinro.next-agent.jump"},
    ],
}
DEFAULTS = {"new_tab": "prefix+c", "detach": "prefix+q", "rename_tab": "prefix+shift+t", "prefix": "ctrl+b"}
GHOSTTY = [("super+c", "copy"), ("super+shift+p", "Ghostty palette")]


def keys() -> list[kh.Key]:
    return kh.collect(KEYS, DEFAULTS, GHOSTTY)


class Keycaps(unittest.TestCase):
    def test_modifiers_read_in_one_order(self):
        self.assertEqual(kh.keycap("cmd+shift+k"), ("⌘⇧", "⌘⇧K"))
        self.assertEqual(kh.keycap("shift+cmd+k"), ("⌘⇧", "⌘⇧K"))

    def test_named_keys_and_the_plus_key(self):
        self.assertEqual(kh.keycap("minus")[1], "-")
        self.assertEqual(kh.keycap("cmd++")[1], "⌘+")
        self.assertEqual(kh.keycap("shift+tab")[1], "⇧⇥")


class Collect(unittest.TestCase):
    def test_config_overrides_the_default_and_keeps_the_rest(self):
        found = {(k.section, k.cap): k.label for k in keys()}
        self.assertEqual(found[("⌘", "⌘T")], "new tab")
        self.assertNotIn(("prefix", "C"), found)
        self.assertEqual(found[("prefix", "Q")], "detach")
        self.assertEqual(found[("prefix", "⇧T")], "rename tab")

    def test_a_list_of_chords_binds_each(self):
        found = {(k.section, k.cap) for k in keys()}
        self.assertIn(("prefix", "Z"), found)
        self.assertIn(("⌘⇧", "⌘⇧Z"), found)

    def test_commands_use_their_description_or_their_command(self):
        found = {k.cap: k.label for k in keys()}
        self.assertEqual(found["⌘⇧D"], "live diff")
        self.assertEqual(found["⌘U"], "martinro.next-agent.jump")

    def test_directions_merge_into_one_row(self):
        ctrl = [k for k in keys() if k.section == "⌃"]
        self.assertEqual(ctrl, [kh.Key("⌃", "⌃", "⌃HJKL", "focus pane ←↓↑→")])

    def test_labels_override_action_names(self):
        found = {k.cap: k.label for k in kh.collect(KEYS, DEFAULTS, [], {"goto": "jump anywhere"})}
        self.assertEqual(found["⌘K"], "jump anywhere")

    def test_sections_come_in_card_order(self):
        order = list(dict.fromkeys(k.section for k in keys()))
        self.assertEqual(order, ["⌘", "⌘⇧", "⌃", "prefix", "Ghostty"])


class Sections(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(kh, "prefix_cap", return_value=("⌃", "⌃B"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_sheet_titles_only_the_prefix_and_ghostty(self):
        titles = [title for title, _ in kh.sections(keys())]
        self.assertEqual(titles, [None, None, None, "⌃B then", "Ghostty"])

    def test_a_cmd_hold_shows_cmd_chords_and_ghosttys(self):
        groups = kh.sections(keys(), "cmd")
        caps = [[k.cap for k in group] for _, group in groups]
        self.assertEqual(caps, [["⌘K", "⌘T", "⌘U", "⌘W"], ["⌘C"]])

    def test_a_ctrl_hold_brings_the_prefix_bindings(self):
        titles = [title for title, _ in kh.sections(keys(), "ctrl")]
        self.assertEqual(titles, [None, "⌃B then"])


class Layout(unittest.TestCase):
    def setUp(self):
        self.groups = [(None, [kh.Key("⌘", "⌘", f"⌘{c}", f"action {c}") for c in "ABCDEFGHIJKL"])]

    def test_sections_read_down_then_across(self):
        card = kh.layout(self.groups, 2)
        self.assertEqual(card.height, 6)
        self.assertEqual(kh.plain(card.lines[0]), "⌘A  action A    ⌘G  action G")
        self.assertEqual(kh.plain(card.lines[5]), "⌘F  action F    ⌘L  action L")

    def test_keys_are_right_aligned_against_their_labels(self):
        groups = [(None, [kh.Key("⌘", "⌘", "⌘B", "short"), kh.Key("⌃", "⌃", "⌃HJKL", "focus")])]
        card = kh.layout(groups, 1)
        self.assertEqual([kh.plain(line) for line in card.lines], ["   ⌘B  short", "⌃HJKL  focus"])

    def test_fit_keeps_six_rows_when_there_is_room(self):
        self.assertEqual(kh.fit(self.groups, 200, 50).columns, 2)

    def test_fit_widens_a_card_too_tall_for_the_terminal(self):
        self.assertEqual(kh.fit(self.groups, 200, 3).columns, 4)

    def test_fit_narrows_a_card_too_wide_for_the_terminal(self):
        card = kh.fit(self.groups, 20, 50)
        self.assertEqual(card.columns, 1)
        self.assertLessEqual(card.width, 20)

    def test_the_footer_sits_right_aligned_under_a_blank_line(self):
        card = kh.layout(self.groups, 2, "any key closes")
        self.assertEqual(kh.plain(card.lines[-2]), "")
        self.assertEqual(kh.plain(card.lines[-1]).strip(), "any key closes")
        self.assertEqual(len(kh.plain(card.lines[-1])), card.width)

    def test_every_line_fits_the_card_width(self):
        card = kh.layout(kh.sections(keys()), 3, "any key closes")
        for line in card.lines:
            self.assertLessEqual(kh.cells(kh.plain(line)), card.width)


if __name__ == "__main__":
    unittest.main()
