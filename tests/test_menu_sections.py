"""Test: in the game's menus the movement keys go from option to option and the camera keys change the section / category, looping.

    python3 tests/test_menu_sections.py

1. `_section_target`: while a character's window waits (spells, equipment, status), the next / previous character of the list on the left (type 4) is touched, the last leading to
   the first; hidden and grayed characters are skipped; with no chosen character, or a single one, the page arrows (◀ ▶ of the menu or of another window) instead; nothing: None.
   A party longer than the list (▲ ▼, gamepad 1 / 2): past the last one shown ▼, before the first ▲ (then the last one of that page is touched, `_section_last`), and the
   first / last one shown when the chosen one is on another page. (Real-engine check, DQ V save 2 with 7 members: Items, Abel -> ... -> Sancho -> ▼ Nera -> ... and back.)
   `_keyboard_port`: page arrows active (no button): the most recent list, even with a single button ("Sac" alone on the last page of the transfer targets).
2. `_entries`: a camera key press sends the target once (sound effect for a character, silent page), the held key repeats nothing; the keyboard stays in the character's window
   while the game gives the input to the list (`_keyboard_port`); a menu with no interface keeps the camera keys as directions; the movement keys never change the interface.
(Real-engine check, save 1: Carac. Alina -> Kiryl -> Borya -> Party -> Alina, Magic, Items and Misc > Equipment.)
"""
import inspect, os, sys, types, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..'))
from dq456_pc.core import engine as ENGINE

try:
    import pyglet
    pyglet.options['shadow_window'] = False
    from dq456_pc.window import game as WND
    from dq456_pc.menus import ui_menu as UI
    import test_keyboard_sfx as TK
    ERROR = None
except Exception as ex:                       # no pyglet (CI)
    pyglet = WND = UI = TK = None; ERROR = ex

CHARS, LIST, PAGES = 5, 8, 7


def window(typ, buttons, chars=None):
    """Fake game window: `buttons` = [(number, gamepad)] (active), `chars` = [(number, state, visible)] (character nodes of a type 4 list)."""
    N = {}
    for i, (b, st, vis) in enumerate(chars or []): N[i] = dict(id=i, buttonId=b, pad=0x10, state=st, vis=vis)
    bs = [(b, pad, 0.0, -10.0 * i, 10.0, -10.0 * i + 8.0, 0.0) for i, (b, pad) in enumerate(buttons)]
    bs += [(n['buttonId'], 0x10, 0.0, -10.0 * n['id'], 10.0, -10.0 * n['id'] + 8.0, 0.0) for n in N.values() if n['vis'] and UI.is_state_active(n['state'])]
    return types.SimpleNamespace(typ=typ, N=N, visible=lambda nid: N[nid]['vis'] if nid in N else True, buttons=lambda ident=False: list(bs))


@unittest.skipIf(TK is None, 'pyglet / window not importable here: %r' % (ERROR,))
class Sections(unittest.TestCase):

    def game(self, chars, pages=True, active=LIST, held=(), scroll=False):
        j = TK.OnTheGame.game(self, [(0, 0x10, 0.0, 0.0, 10.0, 8.0), (1, 0x10, 0.0, -10.0, 10.0, -2.0)], port=active)
        wins = {LIST: window(8, [(-1, 0x20), (0, 0x10), (1, 0x10)])}
        if chars is not None: wins[CHARS] = window(4, [(-1, 0x80)] + ([(-1, 1), (-1, 2)] if scroll else []), chars)
        if pages: wins[PAGES] = window(6, [(-1, 4), (-1, 8)])
        if active not in wins: wins[active] = window(13, [(-1, 0x20)])
        order = {q: i for i, q in enumerate(sorted(wins))}
        j.ui_g = types.SimpleNamespace(f=wins)
        j.engine.ui.f = {q: {'grid': None, 'order': order[q], 'cur': 0, 'type': w.typ, 'input': True, 'cells': {(0, 0): 'x'}, 'visible': True} for q, w in wins.items()}
        j.engine.input_menus = lambda: sorted(wins, key=lambda q: -order[q])
        j.engine.active_menu = lambda: active
        j.slime_touch_click = lambda x, y: False; j.slime_touch_keys = lambda p, d: (p, d); j.curling_press = lambda x, y: False; j.curling_release = lambda: False; j.sugoroku_click = lambda x, y: False; j.sugoroku_held = lambda p: p          # (no Slime Touch, curling or T'n'T here: window/slime_touch.py, slime_curling.py, sugoroku.py)
        for name_str in ('_section_target', '_keyboard_port', '_port_for_gamepad', '_page_arrow', '_is_character_list', '_character_list_members', '_section_last'):
            setattr(j, name_str, types.MethodType(inspect.getattr_static(WND.Game, name_str), j))
        j.section_focus = None; j.section_last = None; j.engine.frames = 100; j.held = set(held); j._held = lambda a: a in j.held; j.hovered = None
        j._edges = lambda: {a: False for a in ('menu', 'chat', 'map', 'L', 'R')}; j.rep = {}; j.pad_prev = 0
        return j

    PARTY = [(0, UI.STATE_NORMAL, False), (1, UI.STATE_INVISIBLE, True), (2, UI.STATE_NORMAL, True), (3, UI.STATE_NORMAL, True)]      # (node 0 hidden; 1 chosen)

    # ---- 1. what to press
    def test_next_and_previous_character_loop(self):
        j = self.game(self.PARTY)
        self.assertEqual(j._section_target(LIST, 1), (CHARS, 2, 0x10))
        self.assertEqual(j._section_target(LIST, -1), (CHARS, 3, 0x10))                                       # previous of the first visible one: the last (the hidden node 0 is skipped)
        last = [(0, UI.STATE_NORMAL, False), (1, UI.STATE_NORMAL, True), (2, UI.STATE_NORMAL, True), (3, UI.STATE_INVISIBLE, True)]
        self.assertEqual(self.game(last)._section_target(LIST, 1), (CHARS, 1, 0x10))                           # next of the last: the first

    def test_grayed_character_skipped(self):
        party = [(1, UI.STATE_INVISIBLE, True), (2, UI.STATE_GRAYED, True), (3, UI.STATE_NORMAL, True)]
        self.assertEqual(self.game(party)._section_target(LIST, 1), (CHARS, 3, 0x10))

    def test_last_page_with_a_single_button_gets_the_keyboard(self):
        """Transfer targets: the game gives the input to the page arrows (no button); on the last page "Sac" is alone: the keyboard goes to it (it used to stay on the arrows: the bag
        could not be chosen with the keys). A single-button window that the game itself makes active, with a fuller list beside it, still gives the keys to the list."""
        j = self.game(None, active=PAGES); j.ui_g.f[LIST] = window(5, [(-1, 0x20), (0, 0x10)])
        self.assertEqual(j._keyboard_port(PAGES), LIST)
        j = self.game(None, active=CHARS); j.ui_g.f[CHARS] = window(9, [(0, 0x10)]); j.engine.ui.f[CHARS] = dict(j.engine.ui.f[LIST], type=9)
        self.assertEqual(j._keyboard_port(CHARS), LIST)

    def test_from_the_character_list_itself(self):
        self.assertEqual(self.game(self.PARTY, active=CHARS)._section_target(CHARS, 1), (CHARS, 2, 0x10))      # (after a switch the game gives the input to the list)

    def test_pages_without_chosen_character(self):
        j = self.game([(1, UI.STATE_NORMAL, True), (2, UI.STATE_NORMAL, True)])
        self.assertEqual(j._section_target(LIST, 1), (PAGES, -1, 4)); self.assertEqual(j._section_target(LIST, -1), (PAGES, -1, 8))
        self.assertEqual(self.game([(1, UI.STATE_INVISIBLE, True)])._section_target(LIST, 1), (PAGES, -1, 4))  # a single character: the pages
        all_chosen = [(1, UI.STATE_INVISIBLE, True), (2, UI.STATE_INVISIBLE, True)]                               # (item submenu: every character disabled)
        self.assertEqual(self.game(all_chosen)._section_target(LIST, 1), (PAGES, -1, 4))

    def test_party_longer_than_the_list(self):
        j = self.game(self.PARTY, scroll=True)
        self.assertEqual(j._section_target(LIST, 1), (CHARS, 2, 0x10))
        self.assertEqual(j._section_target(LIST, -1), (CHARS, -1, 1))                                         # before the first one shown: ▲ (no longer the last one of this page)
        last = [(0, UI.STATE_NORMAL, False), (1, UI.STATE_NORMAL, True), (2, UI.STATE_NORMAL, True), (3, UI.STATE_INVISIBLE, True)]
        self.assertEqual(self.game(last, scroll=True)._section_target(LIST, 1), (CHARS, -1, 2))                # past the last one shown: ▼
        other_page = [(1, UI.STATE_NORMAL, True), (2, UI.STATE_GRAYED, True), (3, UI.STATE_NORMAL, True)]      # (the chosen one is on another page)
        j = self.game(other_page, scroll=True)
        self.assertEqual(j._section_target(LIST, 1), (CHARS, 1, 0x10)); self.assertEqual(j._section_target(LIST, -1), (CHARS, 3, 0x10))

    def test_back_a_page_then_its_last_member(self):
        j = self.game(self.PARTY, scroll=True); lines = ['Nera', 'Kicifrot', 'Sac']; j._menu_lines = lambda f: lines
        self.press(j, 'cam_left')
        self.assertEqual(j.sent, [(CHARS, 1, -1, 1)]); self.assertEqual(j.section_last[:2], (CHARS, lines))      # ▲ sent, silent; then waits for the page to change
        j._section_last(j.engine); self.assertEqual(len(j.sent), 1)                                            # (same page still shown)
        lines = ['Abel', 'Harry', 'Sancho']; j._section_last(j.engine)
        self.assertEqual(j.sent[-1], (CHARS, 1, 3, 0x10)); self.assertIsNone(j.section_last)                   # its last one shown touched
        j.section_last = (CHARS, lines, 90); j._section_last(j.engine); self.assertIsNone(j.section_last); self.assertEqual(len(j.sent), 2)          # (given up: too late)

    def test_nothing_to_change(self):
        self.assertIsNone(self.game(None, pages=False)._section_target(LIST, 1))

    # ---- 2. keys
    def press(self, j, *actions):
        j.held = set(actions); p = sum(bit for a, bit in (('cam_left', ENGINE.LEFT), ('cam_right', ENGINE.RIGHT), ('left', ENGINE.LEFT), ('right', ENGINE.RIGHT), ('down', ENGINE.DOWN)) if a in j.held)
        out = j._entries(p); j.menu_to_release = None; return out

    def test_camera_key_touches_the_next_character(self):
        j = self.game(self.PARTY)
        self.press(j, 'cam_right')
        self.assertEqual(j.sent, [(CHARS, 1, 2, 0x10)]); self.assertEqual(j.sound.sfx, [300])                  # a character: touched, with the sound effect of a touch
        self.assertEqual(j.section_focus, (CHARS, LIST))
        for _ in range(30): self.press(j, 'cam_right')                                                        # held: no repeat
        self.assertEqual(len(j.sent), 1)

    def test_keyboard_stays_in_the_character_window(self):
        j = self.game(self.PARTY); j.section_focus = (CHARS, LIST)
        self.assertEqual(j._keyboard_port(CHARS), LIST)                                                      # the game gave the input to the list: the keys still go to the slots
        j.engine.input_menus = lambda: [CHARS]
        self.assertEqual(j._keyboard_port(CHARS), CHARS); self.assertIsNone(j.section_focus)                 # the character's window closed: back to normal

    def test_page_arrow_is_silent(self):
        j = self.game(None)
        self.press(j, 'cam_left')
        self.assertEqual(j.sent, [(PAGES, 1, -1, 8)]); self.assertEqual(j.sound.sfx, [])

    def test_no_interface_keeps_directions(self):
        j = self.game(None, pages=False); got = []; j._menu_input = lambda port, decl, pages=0: got.append((port, decl))
        self.press(j, 'cam_right')
        self.assertEqual(j.sent, []); self.assertEqual(got, [(LIST, ENGINE.RIGHT)])                              # (the menu receives "right", as)

    def test_movement_keys_never_change_the_interface(self):
        j = self.game(self.PARTY); got = []; j._menu_input = lambda port, decl, pages=0: got.append((port, decl))
        self.press(j, 'right'); self.press(j, 'down')
        self.assertEqual(j.sent, []); self.assertEqual(got, [(LIST, ENGINE.RIGHT), (LIST, ENGINE.DOWN)])          # (the options of the character's window)


if __name__ == '__main__':
    unittest.main()
