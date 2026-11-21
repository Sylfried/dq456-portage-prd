"""Test (rule): sound effect 300 of the menu buttons follows the original game's rule, with a finger as with the keyboard.

    python3 tests/test_keyboard_sfx.py

Original (MenuPortItem.Update, disassembly): on press, `Sound.PlaySE(300)` only if the button's gamepad key is A (0x10) or action (0x800). B (0x20), back (0x400), arrows (1, 2, 4, 8) and zone 0x80 are silent;
the field buttons (Carte, Commandes, Discussion: TouchPad.internalUpdate) play the sound effect. The keyboard plays the sound effect "in the same situations as with a finger".

1. The rule (`button_sfx`): a table of gamepad keys.
2. `Buttons.allowed`: the field buttons allowed by the engine = those that `visible_ones` shows (without the display setting).
3. Real `Game` methods on a fake game: click on a button (A / action: sound; B, arrows, back, map mode: silent), menu with the keyboard (A: sound; B, page arrows, ▲ ▼, column: silent),
   Commandes / Discussion / Carte keys on the field (sound if the engine allows the button), map open (all silent), letters typed in name entry (one per letter).
(Parts 2 and 3 require pyglet: skipped without it, as in CI.)
"""
import inspect, os, sys, types, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
from dq456_pc.window.constants import BTN_MAP, BTN_CHAT, BTN_MENU, SFX_BUTTON, button_sfx
from dq456_pc.core import engine as ENGINE

try:
    import pyglet
    pyglet.options['shadow_window'] = False
    from dq456_pc.menus import hud_mobile as HM
    from dq456_pc.window import game as WND
    from dq456_pc.menus import name_entry as SN
    ERROR = None
except Exception as ex:                       # no pyglet (CI) or window not importable: only the rule is tested
    pyglet = HM = WND = SN = None; ERROR = ex


class Rule(unittest.TestCase):
    def test_a_and_action_play_sfx(self):
        for pad in (0x10, 0x800, 0x810, 0x10 | 0x4):
            self.assertTrue(button_sfx(pad), hex(pad))

    def test_other_keys_are_silent(self):
        for pad in (0, 1, 2, 4, 8, 0x20, 0x80, 0x400, 0x20 | 0x4, 0x1000):
            self.assertFalse(button_sfx(pad), hex(pad))

    def test_sfx_value(self):
        self.assertEqual(SFX_BUTTON, 300)


class SoundGame:
    """Fake game: a log of the requested sound effects and of the messages sent to the menus."""
    pass


def fake_sound():
    s = types.SimpleNamespace(sfx=[]); s.play_sfx = lambda n: s.sfx.append(n); return s


@unittest.skipIf(pyglet is None or WND is None, 'pyglet / fenetre non importables ici : %r' % (ERROR,))
class Allowed(unittest.TestCase):
    """`Buttons.allowed` follows `Buttons.visible_ones` (the "boutons" setting aside)."""

    def game(self, flags=0x28, part=None, effect=None, pause=False, game_map=False, buttons=True):
        J = types.SimpleNamespace(width=1280, height=720, reg={'buttons': buttons, 'dpad': False, 'rotation_bar': False, 'speed_button': False}, map_open=game_map, pause=types.SimpleNamespace(opened=pause))
        J.engine = types.SimpleNamespace(state=types.SimpleNamespace(part=part if part is not None else ENGINE.PART_TOWN, effect=effect), flags=lambda: flags)
        J.hud = HM.Buttons(J); return J

    def test_free_field(self):
        h = self.game(0x28).hud
        for n in HM.NAMES: self.assertTrue(h.allowed(n), n)

    def test_map_requests_bit_0x20(self):
        h = self.game(0x08).hud
        self.assertTrue(h.allowed('menu')); self.assertTrue(h.allowed('chat')); self.assertFalse(h.allowed('map'))

    def test_locked_interface_or_event(self):
        for flags in (0x00, 0x01, 0x02, 0x04, 0x40, 0x10000, 0x20000, 0x28 | 0x40, 0x28 | 0x04):
            h = self.game(flags).hud
            for n in HM.NAMES: self.assertFalse(h.allowed(n), (hex(flags), n))

    def test_pause_effect_battle_and_map_opened(self):
        for kw in (dict(pause=True), dict(effect=object()), dict(part=ENGINE.PART_BATTLE), dict(game_map=True)):
            h = self.game(0x28, **kw).hud
            for n in HM.NAMES: self.assertFalse(h.allowed(n), (list(kw), n))

    def test_independent_of_display_setting(self):
        h = self.game(0x28, buttons=False).hud
        self.assertEqual(h.visible_ones(), ()); self.assertTrue(h.allowed('menu'))

    def test_same_set_as_visible(self):
        for flags in (0x00, 0x08, 0x28, 0x48, 0x09, 0x30008, 0x8 | 0x20):
            for kw in ({}, dict(pause=True), dict(effect=object()), dict(part=ENGINE.PART_BATTLE), dict(game_map=True)):
                h = self.game(flags, **kw).hud
                if kw.get('part') == ENGINE.PART_BATTLE: continue                    # (in battle: only the speed button, specific to the port)
                self.assertEqual({n for n in h.visible_ones() if n in HM.NAMES}, {n for n in HM.NAMES if h.allowed(n)}, (hex(flags), list(kw)))


def two_menus(pads, port=3):
    """A fake menu: list of (number, gamepad, x0, z0, x1, z1) in interface units, rendered as `wnd` (buttons) and pixel zones."""
    bs = [(b, pad, x0, z0, x1, z1, 0.0, 100 + i) for i, (b, pad, x0, z0, x1, z1) in enumerate(pads)]
    wnd = types.SimpleNamespace(typ=99, buttons=lambda ident=False: [t if ident else t[:7] for t in bs], N={})
    return wnd, bs


@unittest.skipIf(pyglet is None or WND is None, 'pyglet / fenetre non importables ici : %r' % (ERROR,))
class OnTheGame(unittest.TestCase):
    """Real `Game` methods (_menu_input, _step_entry, on_mouse_press, _entries, _hud_sfx) on a fake game."""

    def game(self, buttons, flags=0x28, port=3, grid=False):
        wnd, zones = two_menus(buttons)
        j = types.SimpleNamespace()
        j.sound = fake_sound(); j.sent = []
        j.engine = types.SimpleNamespace(
            ui=types.SimpleNamespace(f={port: {'grid': grid, 'order': 1, 'cur': -1, 'type': 99, 'input': True, 'cells': [], 'visible': True}}),
            send_menu=lambda p, *a: j.sent.append((p,) + a), active_menu=lambda: port, input_menus=lambda: [port], msg_visible=False, flags=lambda: flags, shops=lambda: (0, 0),
            state=types.SimpleNamespace(part=ENGINE.PART_TOWN, effect=None))
        j.ui_g = types.SimpleNamespace(f={port: wnd}); j.cur_local = {}; j.cursor_step = {}; j.hover_node = None; j.menu_to_release = None
        j.width, j.height = 1280, 720; j.reg = {'buttons': False, 'dpad': False, 'rotation_bar': False, 'speed_button': False}; j.pause = types.SimpleNamespace(opened=False); j.map_open = False
        j.hud = HM.Buttons(j); j.title_screen = None; j.license_panel = None; j.port_screen = None; j.name_entry = types.SimpleNamespace(active=False); j.hud_decl = 0
        j.click_a = j.click_b = False; j.press_state = None; j.press_node = None; j._ready = True; j.ui_render = True; j.last_device = None; j.mouse = None; j.n = 0
        j.mouse_guard = types.SimpleNamespace(click=lambda: None); j.wheel = types.SimpleNamespace(in_progress=False, cancel=lambda: None)
        j._zones = lambda p: zones if p == port else []; j._map_ports = lambda: {}; j._terrain_free = lambda: False
        j._port_for_gamepad = lambda p, pad: p; j._is_start_menu = lambda p: False; j._keyboard_port = lambda p: p
        j._name_entry_in_progress = lambda: False; j._menu_hidden = lambda t: False
        j._bits_camera = lambda: 0; j._camera_forget = lambda: None            # (the game's camera has nothing to do with sound effects: test_entries.py and test_engine_camera.py)
        j.slime_touch_click = lambda x, y: False; j.slime_touch_keys = lambda p, d: (p, d); j.curling_press = lambda x, y: False; j.curling_release = lambda: False; j.sugoroku_click = lambda x, y: False; j.sugoroku_held = lambda p: p          # (no Slime Touch, curling or T'n'T here: window/slime_touch.py, slime_curling.py, sugoroku.py)
        for name_str in ('_menu_input', '_cursor_targets', '_cursor_nodes', '_touch_node', '_list_arrow', '_step_entry', '_step_columns', '_active_step', '_neighbor', '_page_arrow', '_button_under', 'on_mouse_press', '_hud_sfx', '_entries', '_step_toward_hover', 'arena_back_shown', 'arena_abort_decl', 'arena_back_press', 'arena_back_release'):
            f = inspect.getattr_static(WND.Game, name_str, None)
            if isinstance(f, staticmethod): setattr(j, name_str, f.__func__)
            elif f is not None: setattr(j, name_str, types.MethodType(f, j))
        j.arena_pending = j.arena_pressed = False; j.arena_zone = None
        return j

    # ---- finger / mouse: button under the pointer
    def click(self, pad):
        j = self.game([(0, pad, 0.0, 0.0, 10.0, 10.0)]); j._zones = lambda p: [(0, pad, 100, 100, 200, 200, 0.0, 7)]
        j.on_mouse_press(150, 150, pyglet.window.mouse.LEFT, 0); return j

    def test_click_on_a_or_action_button_plays_sfx(self):
        for pad in (0x10, 0x800):
            self.assertEqual(self.click(pad).sound.sfx, [300], hex(pad))

    def test_click_on_other_buttons_is_mute(self):
        for pad in (0x20, 0x400, 1, 2, 4, 8, 0x80):
            j = self.click(pad); self.assertEqual(j.sound.sfx, [], hex(pad)); self.assertIsNotNone(j.press_state, hex(pad))             # (the button is indeed pressed, only the sound is missing)

    def test_right_click_and_click_on_empty_space(self):
        j = self.click(0x10); j.sound.sfx.clear(); j.on_mouse_press(900, 900, pyglet.window.mouse.LEFT, 0); self.assertEqual(j.sound.sfx, [])

    def test_map_back_button_is_mute(self):
        j = self.game([(-1, 0x400, 0, 0, 1, 1), (-1, 2, 5, 5, 6, 6)]); j._map_ports = lambda: {22: 3}
        j._zones = lambda p: [(-1, 0x400, 100, 100, 200, 200, 0.0, 7), (-1, 2, 300, 300, 400, 400, 0.0, 8)] if p == 3 else []
        j.on_mouse_press(150, 150, pyglet.window.mouse.LEFT, 0); j.on_mouse_press(350, 350, pyglet.window.mouse.LEFT, 0)
        self.assertEqual(j.sound.sfx, []); self.assertEqual(j.press_state, (3, -1, 2))

    def test_down_arrow_of_a_list_is_not_the_map_mode(self):
        """The ▼ of the party list (field menu, a party longer than the list) has no number and gamepad 2, like the map's "mode" button: it was taken for it outside the
        map and went nowhere. Only the map's window toggles the view; elsewhere the button is sent to its window."""
        def release(map_ports):
            j = self.game([(-1, 2, 0, 0, 1, 1)]); j._map_ports = lambda: map_ports; j._zones = lambda p: [(-1, 2, 300, 300, 400, 400, 4.0, 8)] if p == 3 else []
            j.bar_gesture = types.SimpleNamespace(in_progress=False); j.curling_release = lambda: False; j._replay = None; j.terrain_press = None
            j.hud.dpad_on = False; j.hud.press_state = None; j._direct_button = lambda p, pad: 0; j.map_toggle = False
            j.on_mouse_release = types.MethodType(inspect.getattr_static(WND.Game, 'on_mouse_release'), j)
            j.on_mouse_press(350, 350, pyglet.window.mouse.LEFT, 0); j.on_mouse_release(350, 350, pyglet.window.mouse.LEFT, 0); return j
        j = release({}); self.assertEqual(j.sent[-1], (3, 1, -1, 2)); self.assertFalse(j.map_toggle)
        j = release({22: 3}); self.assertTrue(j.map_toggle); self.assertEqual(j.sent, [])

    # ---- keyboard: menu with a local cursor (one-column list of A buttons)
    ENTRIES = [(0, 0x10, 0, 100, 50, 120), (1, 0x10, 0, 70, 50, 90), (2, 0x10, 0, 40, 50, 60)]

    def test_keyboard_a_plays_sfx(self):
        j = self.game(self.ENTRIES); j._menu_input(3, ENGINE.A)
        self.assertEqual(j.sound.sfx, [300]); self.assertEqual(j.sent[-1][1:], (1, 0, 0x10))

    def test_keyboard_b_is_mute(self):
        j = self.game(self.ENTRIES); j._menu_input(3, ENGINE.B)
        self.assertEqual(j.sound.sfx, []); self.assertEqual(j.sent[-1][1:], (1, -1, 0x20))                 # (the cancel message is sent anyway)

    def test_keyboard_moving_cursor_is_mute(self):
        j = self.game(self.ENTRIES)
        for bit in (ENGINE.UP, ENGINE.DOWN): j._menu_input(3, bit)
        self.assertEqual(j.sound.sfx, [])

    def test_keyboard_page_arrows_silent(self):
        j = self.game(self.ENTRIES + [(-1, 8, 0, 0, 10, 10), (-1, 4, 20, 0, 30, 10)])
        j._port_for_gamepad = lambda p, pad: 9 if pad in (4, 8) else p                       # the arrows are in another window
        j._menu_input(3, ENGINE.RIGHT); j._menu_input(3, ENGINE.LEFT)
        self.assertEqual(j.sound.sfx, []); self.assertEqual([e[:1] + e[-1:] for e in j.sent], [(9, 4), (9, 8)])

    def test_page_arrows_by_place(self):
        """DQ V / VI: ◀ sends 4 and ▶ 8 (bestiary sheet, Statut pages, shop list), DQ IV the other way round: left / right press the arrow on that side, with its own key."""
        j = self.game(self.ENTRIES + [(-1, 4, 0, 0, 10, 10), (-1, 8, 20, 0, 30, 10)])
        self.assertEqual(j._page_arrow(3, 8)[2], 4); self.assertEqual(j._page_arrow(3, 4)[2], 8)
        j._port_for_gamepad = lambda p, pad: 9 if pad in (4, 8) else p
        j.ui_g.f[9] = j.ui_g.f[3]
        j._menu_input(3, ENGINE.RIGHT); j._menu_input(3, ENGINE.LEFT)
        self.assertEqual([e[:1] + e[-1:] for e in j.sent], [(9, 8), (9, 4)])

    # ---- keyboard: numeric input (▲ ▼ columns, A to confirm)
    STEP = [(0, 2, 0, 50, 10, 60), (0, 1, 0, 0, 10, 10), (1, 2, 20, 50, 30, 60), (1, 1, 20, 0, 30, 10), (5, 0x10, 40, 20, 80, 40)]

    def test_numeric_entry(self):
        j = self.game(self.STEP)
        j._menu_input(3, ENGINE.LEFT); self.assertEqual(j.cursor_step[(3, 1)], 0)                                    # column change: silent
        j._menu_input(3, ENGINE.RIGHT); self.assertEqual(j.cursor_step[(3, 1)], 1)
        j._menu_input(3, ENGINE.UP); j._menu_input(3, ENGINE.DOWN); self.assertEqual([e[-2:] for e in j.sent], [(1, 2), (1, 1)])            # ▲ ▼: message to the engine
        self.assertEqual(j.sound.sfx, [], 'column, ▲ and ▼ silent')
        j._menu_input(3, ENGINE.A); self.assertEqual(j.sound.sfx, [300], 'A (confirm) plays the sound'); self.assertEqual(j.sent[-1][-1], 0x10)
        j._menu_input(3, ENGINE.B); self.assertEqual(j.sound.sfx, [300], 'B muet'); self.assertEqual(j.sent[-1][-1], 0x20)

    # ---- keyboard: field keys
    def key_list(self, **fr):
        j = self.game(self.ENTRIES, flags=fr.pop('flags', 0x28))
        j.engine.active_menu = lambda: None
        base = {a: False for a in ('menu', 'chat', 'map', 'L', 'R')}; base.update(fr)
        j._edges = lambda: dict(base); j.rep = {}; j.pad_prev = 0; j.reg = dict(j.reg); j.hud_decl = fr.pop('hud_decl', 0)
        return j

    def inputs(self, j): return j._entries(0)

    def test_field_enter_space_and_m_play_the_sfx(self):
        for name_str, button in (('menu', BTN_MENU), ('chat', BTN_CHAT), ('map', BTN_MAP)):
            j = self.key_list(**{name_str: True}); p, decl = self.inputs(j); self.assertEqual(decl, button, name_str); self.assertEqual(j.sound.sfx, [300], name_str)

    def test_field_without_key_silent(self):
        j = self.key_list(); self.inputs(j); self.assertEqual(j.sound.sfx, [])

    def test_field_disallowed_button_silent(self):
        for flags in (0x00, 0x08 | 0x40):                                           # forbidden: nothing happens, no sound
            for name_str in ('menu', 'chat', 'map'):
                j = self.key_list(flags=flags, **{name_str: True}); self.inputs(j); self.assertEqual(j.sound.sfx, [], (hex(flags), name_str))
        j = self.key_list(flags=0x08, game_map=True); self.inputs(j); self.assertEqual(j.sound.sfx, [], 'the map requires bit 0x20')

    def test_field_action_key_silent(self):
        j = self.key_list(); j.click_a = True; p, decl = self.inputs(j); self.assertEqual(j.sound.sfx, [])           # (A / click on the field: talk, no button)

    def test_field_onscreen_button_already_sounds_no_duplicate(self):
        j = self.key_list(menu=True); j.hud_decl = BTN_CHAT; p, decl = self.inputs(j)                              # mouse press on the Discussion button: the sound was already played on press
        self.assertEqual(decl, BTN_CHAT); self.assertEqual(j.sound.sfx, [])

    def test_map_opened_by_keyboard_silent(self):
        for ports in ({22: 3}, {25: 3, 26: 4}):
            j = self.key_list(game_map=True); j._map_ports = lambda ports=ports: ports; j.map_view = 1; j.map_toggle = False; j.map_open = False
            j.engine.ui.f.update({q: {'grid': False, 'cells': [], 'visible': False, 'type': 25} for q in ports.values() if q not in j.engine.ui.f})
            j.menu_to_release = None; self.inputs(j); self.assertEqual(j.sound.sfx, [], ports)


class EntryOfName(unittest.TestCase):
    """A letter typed on the keyboard plays the button's sound effect, like a click on the letter."""

    @unittest.skipIf(SN is None, 'saisie_nom non importable ici')
    def test_typed_letter(self):
        s = SN.NameEntry.__new__(SN.NameEntry); s.J = types.SimpleNamespace(sound=fake_sound()); s.text = ''; s.max = 8; s.cur = 0; s.mouse = 1
        s.text_entered('ab'); self.assertEqual(s.text, 'ab'); self.assertEqual(s.J.sound.sfx, [300, 300])
        s.text_entered('@@'); self.assertEqual(s.J.sound.sfx, [300, 300], 'caractere hors grille : rien')
        s.text = 'x' * 8; s.text_entered('z'); self.assertEqual(s.J.sound.sfx, [300, 300], 'nom plein : rien')


if __name__ == '__main__':
    unittest.main(verbosity=1)
