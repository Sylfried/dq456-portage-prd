"""Every button of the game windows can be reached with the keyboard alone (the three games).

    python3 tests/test_keyboard_reach.py

1. Real `Game` methods on a fake game (pyglet needed; skipped without it, as in CI):
   * a cancel button that has a number ("Rien" of the shops: number 0 like "Acheter", gamepad 0x20) is a stop of the cursor, lit alone, and Enter touches it as the finger does;
   * the rows of a list of amounts that only carry their ◀ ▶ (DQ VI's slime race bets) are stops of the cursor; left / right press the row's arrows, they are not page arrows;
   * a list that scrolls (the party on the left of the field menu): up on its first row, down on its last, press its ▲ / ▼ when they are shown; a lone up / down button (map mode) is not one;
   * at the end of a row or a column, the cursor goes on to the other end (before, it went back one button: the first rows of a list could not be reached going down).
2. Real layouts (data\\cache\\<game>\\<language>\\ui_layout.json, skipped without them): every button drawn in every window of the three games is either a stop of the keyboard
   cursor (`MenusMixin._cursor_targets`) or a button another key presses (back B / Esc, page arrows, a quantity row's arrows, the ▲ ▼ of an amount, the map's buttons, the
   bestiary's action, a list's ▲ ▼, the arena's back button, the scope's back button).
"""
import glob, inspect, os, sys, types, unittest

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
from dq456_pc.core import engine as ENGINE

try:
    import pyglet
    pyglet.options['shadow_window'] = False
    from dq456_pc.window import game as WND
    from dq456_pc.window import menu_display as MD
    from dq456_pc.menus import ui_menu as UI
    import test_keyboard_sfx as TK
    ERROR = None
except Exception as ex:
    pyglet = WND = MD = UI = TK = None; ERROR = ex

PORT = 3


def window(pads, typ=99):
    """Fake game window: `pads` = [(number, gamepad, x0, z0, x1, z1, element)] in interface units (shown buttons)."""
    bs = [(b, pad, x0, z0, x1, z1, 0.0, nid) for b, pad, x0, z0, x1, z1, nid in pads]
    N = {nid: dict(id=nid, buttonId=b, pad=pad, vtx=[[0.0, 0.0]]) for b, pad, x0, z0, x1, z1, nid in pads}
    return types.SimpleNamespace(typ=typ, N=N, buttons=lambda ident=False: [t if ident else t[:7] for t in bs])


SHOP_ROOT = [(0, 0x10, -167, -318, -25, -262, 10), (1, 0x10, 25, -318, 167, -262, 11), (0, 0x20, 86, -394, 218, -338, 12)]          # Acheter, Vendre, Rien (Make_ShopRoot)
BETS = [(0, 0x10, 40, -390, 156, -342, 20), (-1, 0x20, 160, -390, 216, -342, 21), (0, 4, -190, -320, -162, -290, 22), (0, 8, 162, -320, 190, -290, 23)] + \
       [(r, pad, x, 30 - 38 * r, x + 30, 54 - 38 * r, 30 + 2 * r + (pad == 8)) for r in (1, 2, 3) for pad, x in ((4, 20), (8, 156))]          # DQ VI's Make_SlimeRace_BetB


@unittest.skipIf(TK is None, 'pyglet / window not importable here: %r' % (ERROR,))
class Keys(unittest.TestCase):
    def game(self, pads):
        j = TK.OnTheGame.game(self, [], port=PORT)
        j.ui_g.f[PORT] = window(pads); j._direct_button = lambda port, pad: 0
        j._cursor = types.MethodType(inspect.getattr_static(WND.Game, '_cursor'), j)
        return j

    def test_book_action_is_a_direct_button(self):
        """the bestiary's Action: its key alone goes to the game, whether the window's name has "Make_" or not (DQ IV's names have none)"""
        from dq456_pc.window import keyboard_mouse as KM
        for name in ('Make_MonsterEncyclopedia', 'MonsterEncyclopedia', 'ScopeBack'):
            j = types.SimpleNamespace(engine=types.SimpleNamespace(ui=types.SimpleNamespace(f={PORT: {'type': 28}})), _type_name=lambda t, n=name: n)
            want = 0x200 if name == 'ScopeBack' else 0x800
            self.assertEqual(KM.KeyboardMouseMixin._direct_button(j, PORT, want | 0x10), want)
            self.assertEqual(KM.KeyboardMouseMixin._direct_button(j, PORT, 0x10), 0)

    def test_cancel_button_with_a_number(self):
        j = self.game(SHOP_ROOT)
        self.assertEqual(set(j._cursor_targets(j.ui_g.f[PORT])), {0, 1, (MD.CANCEL, 12)})
        j._menu_input(PORT, ENGINE.DOWN); self.assertEqual(j._cursor(PORT), (MD.CANCEL, 12)); self.assertEqual(j._cursor_nodes(PORT, j.ui_g.f[PORT]), (12,))
        self.assertEqual(j.sent, [])                                                                         # (moving: nothing sent)
        j._menu_input(PORT, ENGINE.A); self.assertEqual(j.sent, [(PORT, 1, 0, 0x20)]); self.assertEqual(j.sound.sfx, [])          # (touched like the finger does: silent)
        j._menu_input(PORT, ENGINE.LEFT); self.assertEqual(j._cursor(PORT), 1)                                                     # (Vendre, the nearest on the left)
        j._menu_input(PORT, ENGINE.A); self.assertEqual(j.sent[-1], (PORT, 1, 1, 0x10)); self.assertEqual(j.sound.sfx, [300])
        self.assertEqual(j._cursor_nodes(PORT, j.ui_g.f[PORT]), ())                                                                # (a confirm button is lit by its number)

    def test_rows_of_amounts(self):
        j = self.game(BETS); wnd = j.ui_g.f[PORT]
        self.assertEqual(set(j._cursor_targets(wnd)), {0, (MD.ROW, 1), (MD.ROW, 2), (MD.ROW, 3)})
        j.cur_local[(PORT, 1)] = (MD.ROW, 2)
        j._menu_input(PORT, ENGINE.RIGHT); j._menu_input(PORT, ENGINE.LEFT)
        self.assertEqual(j.sent, [(PORT, 1, 2, 8), (PORT, 1, 2, 4)])                                         # (the row's own arrows, as touched)
        self.assertEqual(sorted(j._cursor_nodes(PORT, wnd)), [34, 35])
        j.sent.clear(); j._menu_input(PORT, ENGINE.A); self.assertEqual(j.sent, [])                          # (a row has nothing to confirm)
        self.assertEqual(j._page_arrow(PORT, 4), (PORT, 0, 8)); self.assertEqual(j._page_arrow(PORT, 8), (PORT, 0, 4))          # (the page arrows: number 0, at the sides; not a row's)
        j._menu_input(PORT, ENGINE.DOWN); self.assertEqual(j._cursor(PORT), (MD.ROW, 3)); j._menu_input(PORT, ENGINE.DOWN); self.assertEqual(j._cursor(PORT), 0)

    def test_edges_loop_to_the_other_end(self):
        rows = [(r, 0x10, 0, -30 - 60 * r, 100, -10 - 60 * r, 60 + r) for r in range(4)]
        j = self.game(rows); j.cur_local[(PORT, 1)] = 3; j._menu_input(PORT, ENGINE.DOWN); self.assertEqual(j._cursor(PORT), 0)
        j._menu_input(PORT, ENGINE.UP); self.assertEqual(j._cursor(PORT), 3)
        j = self.game([(c, 0x10, 120 * c, 0, 120 * c + 100, 40, 70 + c) for c in range(3)]); j.cur_local[(PORT, 1)] = 2
        j._menu_input(PORT, ENGINE.RIGHT); self.assertEqual(j._cursor(PORT), 0); j._menu_input(PORT, ENGINE.LEFT); self.assertEqual(j._cursor(PORT), 2)

    def test_one_row_of_arrows_is_a_page(self):
        j = self.game([(-1, 0x20, 160, -390, 216, -342, 1), (0, 4, -190, -320, -162, -290, 2), (0, 8, 162, -320, 190, -290, 3)])          # (Make_MuseumInfo)
        self.assertEqual(j._cursor_targets(j.ui_g.f[PORT]), {}); self.assertEqual(j._page_arrow(PORT, 4), (PORT, 0, 8))

    def test_list_that_scrolls(self):
        up, down = ENGINE.MENU_DIRS[ENGINE.UP], ENGINE.MENU_DIRS[ENGINE.DOWN]
        rows = [(r, 0x10, -200, -30 - 90 * r, -90, 10 - 90 * r, 40 + r) for r in range(4)]
        j = self.game(rows + [(-1, up, -170, 50, -120, 70, 50), (-1, down, -170, -390, -120, -370, 51)])
        j.cur_local[(PORT, 1)] = 1; j._menu_input(PORT, ENGINE.UP); self.assertEqual((j.sent, j._cursor(PORT)), ([], 0))
        j._menu_input(PORT, ENGINE.UP); self.assertEqual(j.sent, [(PORT, 1, -1, up)]); self.assertEqual(j._cursor(PORT), 0)          # (first row: ▲; the cursor stays)
        j.cur_local[(PORT, 1)] = 3; j._menu_input(PORT, ENGINE.DOWN); self.assertEqual(j.sent[-1], (PORT, 1, -1, down))
        j = self.game(rows + [(-1, down, -170, -390, -120, -370, 51)]); j.ui_g.f[PORT].N[50] = dict(id=50, buttonId=-1, pad=up, vtx=[[0.0, 0.0]])
        j.cur_local[(PORT, 1)] = 0; j._menu_input(PORT, ENGINE.UP); self.assertEqual((j.sent, j._cursor(PORT)), ([], 3))          # (▲ not shown: at the top of the list, the cursor loops)
        j = self.game(rows + [(-1, up, -170, 50, -120, 70, 50)])                                                                 # (a lone up / down button, the map's "map mode": not a list's)
        j.cur_local[(PORT, 1)] = 0; j._menu_input(PORT, ENGINE.UP); self.assertEqual((j.sent, j._cursor(PORT)), ([], 3))


def layouts():
    out = {}
    for game in ('dq4', 'dq5', 'dq6'):
        found = sorted(glob.glob(os.path.join(ROOT, 'data', 'cache', game, '*', 'ui_layout.json')))
        if found: out[game] = found[0]
    return out


OTHER_KEYS = {0x20: 'back (B, Esc)', 0x4: 'page arrow', 0x8: 'page arrow', 0x400: "map's back / arena's back", 0x1000: "arena's back", 0x200: "scope's back", 0x800: "bestiary's action"}


@unittest.skipIf(TK is None or not layouts(), 'no ui_layout.json in data\\cache\\<game> (or pyglet missing): %r' % (ERROR,))
class RealWindows(unittest.TestCase):
    def test_every_button_has_a_key(self):
        missing = []
        for game, path in layouts().items():
            ml = UI.PageLayout(path)
            for typ, t in ml.types.items():
                if not t.get('nodes'): continue
                w = UI.Window(ml, int(typ)); w.visible = lambda nid: True
                for n in w.N.values(): n['state'] = 0
                targets = MD.MenusMixin._cursor_targets(w)
                steps = {b for b, pad, *_ in w.buttons() if b >= 0 and pad & 0x3}
                for n in w.N.values():
                    b, pad = n['buttonId'], n['pad']
                    if not pad or b is None or not n['vtx'] or not any(m.get('part') in (7, 10) for m in n['mats']): continue
                    if (b in targets and pad & 0x10) or (MD.CANCEL, n['id']) in targets or ((MD.ROW, b) in targets and pad & 0xc): continue          # the cursor
                    if any(pad & k for k in OTHER_KEYS): continue
                    if pad & 0x3 and (b in steps or b < 0): continue                                        # ▲ ▼ of an amount (_step_entry), a list's ▲ ▼ (_list_arrow) or the map's mode (map key)
                    missing.append('%s type %s %s: number %s, gamepad %#x' % (game, typ, t.get('name'), b, pad))
        self.assertEqual(missing, [])


if __name__ == '__main__':
    unittest.main(verbosity=1)
