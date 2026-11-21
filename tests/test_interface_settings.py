"""Test of the resets of the interface editor (Esc menu > "Display" > "Interface"): "Restore this interface" and "Restore all interfaces".

    python3 tests/test_interface_settings.py

Checks: (1) that every setting key is classified (interface or not): a key added to DEFAULT without classification makes the test fail;
(2) "Restore this interface" resets only the chosen interface (on-screen buttons and D-pad, or a game window / the dialogues);
(3) "Restore all interfaces" resets all the interfaces and the editor options to DEFAULT, without touching the other settings (sound, display, keys...);
(4) the same actions, called by the "Interface" page (Pause), also change the editor's session state (panel position).
"""
import copy, json, os, sys, tempfile, types, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from dq456_pc.persistence import settings as RG

# keys of DEFAULT that do NOT touch the interface editor (display, sound, text, keys, camera, speeds...);
# "infos" = F1 information lines, "damage_size" = fixed value (no longer a setting)
OUTSIDE_INTERFACE = {'settings_page', 'sprite_mode', 'window_mode', 'msaa', 'render_scale', 'sharpness', 'brightness', 'info', 'sound', 'music_volume', 'sfx_volume',
                  'master_volume', 'jingle_volume', 'interface_volume', 'music_enabled', 'jingles_enabled', 'sfx_enabled', 'interface_enabled', 'text_speed',
                  'resolution', 'vignette', 'edge_blur', 'contrast', 'color_filter', 'motion_blur', 'accel_speed', 'battle_speed', 'quick_save_journal', 'exp_rate', 'gold_rate', 'party_damage_rate', 'enemy_damage_rate', 'difficulty', 'casino_rate', 'random_encounters', 'encounter_rate', 'damage_size',
                  'keys_version', 'keys', 'level_up_heal', 'battle_end_heal'}
OUTSIDE_INTERFACE |= {'camera_invert_lr', 'interface_version', 'gamepads'}          # direction of the camera moved with the mouse; version of the default window sizes
OUTSIDE_INTERFACE |= {'history_favorites_only', 'language'}                                          # history's "Favorites only" checkbox: a window state, not an editor interface

# values different from DEFAULT for each interface setting
OTHER = {'buttons': False, 'dpad': True, 'dpad_size': 1.7, 'rotation_bar': True, 'buttons_size': 1.4, 'buttons_pos': {'map': [10, 5], 'menu': [3, 2.5], 'chat': [0, 9], 'dpad': [20, 9]},
          'speed_button': False, 'speed_button_size': 1.3, 'speed_button_pos': [4.0, 6.0],
          'vehicle_button': False, 'vehicle_button_size': 1.6, 'vehicle_button_pos': [2.0, 7.0],
          'editor_grid': False, 'buttons_snap': False,
          'interface_windows': {'RootMenu': [1.3, 0.1, -0.2], 'Status': [0.7, -0.05, 0.3], 'msg': [1.5, 0.0, 0.1], 'YesNo': [0.8, 0.2, 0.0]},
          'interface_hidden': ['RootMenu', 'Status', 'msg', 'MapButton']}


def modified_settings():
    """Test settings: all the interface keys and a few others at non-default values."""
    r = RG.Settings(None)
    for k, x in OTHER.items(): r[k] = copy.deepcopy(x)
    r['vignette'] = 0.8; r['msaa'] = 8; r['music_volume'] = 0.2; r['keys']['map'] = ['K', None]
    return r


DEFAULT_WINDOWS = dict({n: [RG.COMMANDS_WIDTH, 0.0, 0.0] for n in RG.BATTLE_COMMAND_WINDOWS}, **{n: [RG.SMALL_SCREEN_SCALE, 0.0, 0.0] for n in RG.SMALL_SCREENS},
                       MonsterEncyclopedia=[RG.BOOK_SCALE, 0.0, 0.0])


def interface(r):
    return {k: copy.deepcopy(r[k]) for k in RG.INTERFACE_KEYS}


def others(r):
    return {k: copy.deepcopy(r[k]) for k in OUTSIDE_INTERFACE}


class Reset(unittest.TestCase):
    def test_classification_complete(self):
        classified = set(RG.INTERFACE_KEYS) | OUTSIDE_INTERFACE
        self.assertEqual(set(RG.DEFAULT) - classified, set(), 'unclassified settings key (interface or not): add it to settings.*_KEYS or to NOT_INTERFACE in this test')
        self.assertEqual(classified - set(RG.DEFAULT), set(), 'key classified but missing from DEFAULT')
        self.assertEqual(set(RG.INTERFACE_KEYS) & OUTSIDE_INTERFACE, set())
        self.assertEqual(len(RG.INTERFACE_KEYS), len(set(RG.INTERFACE_KEYS)))
        self.assertEqual(set(RG.SCREEN_KEYS + RG.SPEED_KEYS + RG.VEHICLE_KEYS + RG.WINDOW_KEYS + RG.EDITOR_KEYS), set(RG.INTERFACE_KEYS))

    def test_old_file_hides_the_dpad_and_the_bar(self):
        """interface v5: a file from before has the D-pad and the rotation bar hidden once (and its small screens still at the old default a quarter larger), the rest
        untouched; a file of v5 keeps them as the player set them"""
        path = os.path.join(tempfile.mkdtemp(), 'reglages.json')
        json.dump({'dpad': True, 'rotation_bar': True, 'buttons': False, 'interface_version': 4, 'interface_windows': {'CityMap': [1.5, 0.0, 0.0], 'ShopMap': [1.2, 0.0, 0.0]}}, open(path, 'w'))
        r = RG.Settings(path); self.assertEqual((r['dpad'], r['rotation_bar'], r['buttons']), (False, False, False))
        w = r['interface_windows']; self.assertEqual((w['CityMap'], w['ShopMap'], w['MonsterEncyclopedia']), ([RG.SMALL_SCREEN_SCALE, 0.0, 0.0], [1.2, 0.0, 0.0], [RG.BOOK_SCALE, 0.0, 0.0]))
        json.dump({'dpad': True, 'rotation_bar': True, 'interface_version': 5}, open(path, 'w'))
        r = RG.Settings(path); self.assertEqual((r['dpad'], r['rotation_bar']), (True, True))

    def test_non_default_test_values(self):
        self.assertEqual(set(OTHER), set(RG.INTERFACE_KEYS))
        for k, x in OTHER.items(): self.assertNotEqual(x, RG.DEFAULT[k], k)

    def test_screen_only(self):
        r = modified_settings(); av = interface(r); ap = others(r)
        r.reset_interface(RG.SCREEN_TARGET)
        for k in RG.SCREEN_KEYS: self.assertEqual(r[k], RG.DEFAULT[k], k)               # buttons and D-pad: everything comes back (display, sizes, positions)
        for k in RG.SPEED_KEYS + RG.VEHICLE_KEYS + RG.WINDOW_KEYS + RG.EDITOR_KEYS: self.assertEqual(r[k], av[k], k)           # battle speed button, vehicle button, windows and editor options: intact
        self.assertEqual(others(r), ap)

    def test_speed_button_alone(self):
        r = modified_settings(); av = interface(r); ap = others(r)
        r.reset_interface(RG.SPEED_TARGET)
        for k in RG.SPEED_KEYS: self.assertEqual(r[k], RG.DEFAULT[k], k)                  # display, size and position: everything comes back (the position: stuck under the bottom box)
        for k in RG.SCREEN_KEYS + RG.VEHICLE_KEYS + RG.WINDOW_KEYS + RG.EDITOR_KEYS: self.assertEqual(r[k], av[k], k)       # buttons and D-pad, vehicle button, windows and editor options: intact
        self.assertEqual(others(r), ap)

    def test_vehicle_button_alone(self):
        r = modified_settings(); av = interface(r); ap = others(r)
        r.reset_interface(RG.VEHICLE_TARGET)
        for k in RG.VEHICLE_KEYS: self.assertEqual(r[k], RG.DEFAULT[k], k)                # display, size and position (back above Commands)
        for k in RG.SCREEN_KEYS + RG.SPEED_KEYS + RG.WINDOW_KEYS + RG.EDITOR_KEYS: self.assertEqual(r[k], av[k], k)
        self.assertEqual(others(r), ap)

    def test_window_only(self):
        r = modified_settings(); av = interface(r); ap = others(r)
        r.reset_interface('RootMenu')
        self.assertNotIn('RootMenu', r['interface_windows']); self.assertNotIn('RootMenu', r['interface_hidden'])          # original size, position and display
        self.assertEqual(r['interface_windows'], {k: x for k, x in av['interface_windows'].items() if k != 'RootMenu'})      # the other windows keep their setting
        self.assertEqual(r['interface_hidden'], [n for n in av['interface_hidden'] if n != 'RootMenu'])                   # ... and their hiding
        for k in RG.SCREEN_KEYS + RG.SPEED_KEYS + RG.VEHICLE_KEYS + RG.EDITOR_KEYS: self.assertEqual(r[k], av[k], k)
        self.assertEqual(others(r), ap)

    def test_dialogues_and_yes_no_independent(self):
        r = modified_settings()
        r.reset_interface('msg')
        self.assertNotIn('msg', r['interface_windows']); self.assertNotIn('msg', r['interface_hidden'])
        self.assertIn('YesNo', r['interface_windows'])                                           # Yes / No is a separate interface
        r.reset_interface('YesNo')
        self.assertNotIn('YesNo', r['interface_windows'])

    def test_window_without_setting(self):
        r = modified_settings(); av = interface(r)
        r.reset_interface('Money')                                                              # nothing to reset: nothing changes, no error
        self.assertEqual(interface(r), av)

    def test_hidden_window_only(self):
        r = RG.Settings(None); r['interface_hidden'] = ['Status']; r.reset_interface('Status')
        self.assertEqual(r['interface_hidden'], [])

    def test_everything(self):
        r = modified_settings(); ap = others(r)
        r.reset_interfaces()
        for k in RG.INTERFACE_KEYS: self.assertEqual(r[k], RG.DEFAULT[k], k)                       # interfaces and editor options (grid, snapping): everything is back
        self.assertEqual(others(r), ap)                                                           # the other settings do not change

    def test_no_sharing_with_default(self):
        r = RG.Settings(None); r.reset_interfaces()
        r['interface_windows']['X'] = [2.0, 0.0, 0.0]; r['interface_hidden'].append('X'); r['buttons_pos']['map'] = [1, 1]
        self.assertEqual((RG.DEFAULT['interface_windows'], RG.DEFAULT['interface_hidden'], RG.DEFAULT['buttons_pos']), (DEFAULT_WINDOWS, [], {}))       # DEFAULT is never modified by a reset (only the battle command windows, the small screens and the book have a default size)
        r2 = RG.Settings(None); r2.reset_interface('X'); r2.reset_interface(RG.SCREEN_TARGET)
        self.assertEqual((RG.DEFAULT['interface_windows'], RG.DEFAULT['interface_hidden'], RG.DEFAULT['buttons_pos']), (DEFAULT_WINDOWS, [], {}))

    def test_storage(self):
        f = os.path.join(tempfile.mkdtemp(prefix='rg_test_'), 'settings.json')
        r = RG.Settings(f)
        for k, x in OTHER.items(): r[k] = copy.deepcopy(x)
        r.reset_interface('Status'); r.reset_interface(RG.SCREEN_TARGET); r.store()
        r2 = RG.Settings(f)
        self.assertEqual(r2['buttons'], True); self.assertEqual(r2['buttons_pos'], {}); self.assertNotIn('Status', r2['interface_windows']); self.assertIn('RootMenu', r2['interface_windows'])
        r2.reset_interfaces(); r2.store()
        for k in RG.INTERFACE_KEYS: self.assertEqual(RG.Settings(f)[k], RG.DEFAULT[k], k)

    def test_preview_hidden_elements(self):
        """Editor preview: each hidden element (the whole interface, or the D-pad alone) is marked, the others are not; the D-pad remains selectable (its rectangle is always given)."""
        try:
            from dq456_pc.menus import pause as PA
            from dq456_pc.menus import hud_mobile as HM
        except Exception as ex:
            self.skipTest('pause.py non importable ici (%r)' % (ex,))
        r = RG.Settings(None); r['dpad'] = r['rotation_bar'] = True
        hud = types.SimpleNamespace(rect=lambda n: (10.0 * len(n), 20.0, 60.0, 50.0))
        P = PA.Pause(types.SimpleNamespace(reg=r, hud=hud)); P.target = RG.SCREEN_TARGET
        mask = lambda: {n: (e[4], e[5]) for n, e in zip(HM.editor_names(P.target), P._preview_elements())}
        self.assertEqual(mask(), {'map': (False, True), 'menu': (False, True), 'chat': (False, True), 'dpad': (False, False), 'bar': (False, False)})      # everything displayed: nothing marked (the battle speed button is no longer here: it has its own interface)
        r['dpad'] = False                                                                       # "Show the D-pad" = Non: only the D-pad is marked (feminine), not the buttons
        self.assertEqual(mask(), {'map': (False, True), 'menu': (False, True), 'chat': (False, True), 'dpad': (True, False), 'bar': (False, False)})
        r['dpad'] = True; r['rotation_bar'] = False                                           # "Show the camera rotation bar" = "No": only the bar is marked (feminine)
        self.assertEqual(mask(), {'map': (False, True), 'menu': (False, True), 'chat': (False, True), 'dpad': (False, False), 'bar': (True, False)}); r['rotation_bar'] = True; r['dpad'] = False
        r['buttons'] = False; r['dpad'] = True                                                  # whole interface hidden: buttons (masculine), D-pad and bar (feminine)
        self.assertEqual(mask(), {'map': (True, True), 'menu': (True, True), 'chat': (True, True), 'dpad': (True, False), 'bar': (True, False)})
        self.assertEqual(len(P._shown_rects()), 5)                                              # (the editor panel and the selection do not depend on the display)
        r['buttons'] = True; P.target = RG.SPEED_TARGET                                          # battle speed button: its own display, independent of the buttons
        self.assertEqual(mask(), {'battle': (False, True)})
        r['speed_button'] = False; self.assertEqual(mask(), {'battle': (True, True)}); r['buttons'] = False; r['speed_button'] = True; self.assertEqual(mask(), {'battle': (False, True)})
        r['buttons'] = True; P.target = RG.SCREEN_TARGET
        r['buttons'] = True; P.target = 'RootMenu'; P.rect_preview = (5.0, 6.0, 7.0, 8.0)
        self.assertEqual(P._preview_elements(), [(5.0, 6.0, 7.0, 8.0, False, False)])
        r['interface_hidden'] = ['RootMenu']
        self.assertEqual(P._preview_elements(), [(5.0, 6.0, 7.0, 8.0, True, False)])              # hidden window: marked (feminine); only that one
        P.target = 'Status'; self.assertEqual(P._preview_elements(), [(5.0, 6.0, 7.0, 8.0, False, False)])
        P.target = 'msg'; P.rect_preview = None; self.assertEqual(P._preview_elements(), [])

    def test_page_interface(self):
        """The "Restore" entries of the "Interface" page call these resets (fake game: `reg` and the window list); the panel's position (session) is reset to the origin by "all"."""
        try:
            from dq456_pc.menus import pause as PA
        except Exception as ex:
            self.skipTest('pause.py non importable ici (%r)' % (ex,))
        r = modified_settings(); P = PA.Pause(types.SimpleNamespace(reg=r, window_names=lambda: {'RootMenu': [1], 'Status': [2], 'Money': [3]}))
        P.target = 'RootMenu'; P.pan_pos = (500.0, 400.0)
        ent = {x['l']: x for x in P._entries_interface() if x['t'] == 'act'}
        self.assertEqual(list(ent)[-3:], ['interface.restore_interface', 'interface.restore_all_interfaces', 'interface.finish'])
        ent['interface.restore_interface']['ok']()
        self.assertNotIn('RootMenu', r['interface_windows']); self.assertIn('Status', r['interface_windows']); self.assertEqual(r['dpad_size'], OTHER['dpad_size'])
        self.assertEqual(P.pan_pos, (500.0, 400.0))                                               # the panel's position is a global editor option
        P.target = RG.SCREEN_TARGET
        ent = {x['l']: x for x in P._entries_interface() if x['t'] == 'act'}
        ent['interface.restore_interface']['ok']()
        self.assertEqual([r[k] for k in RG.SCREEN_KEYS], [RG.DEFAULT[k] for k in RG.SCREEN_KEYS]); self.assertIn('Status', r['interface_windows'])
        ent['interface.restore_all_interfaces']['ok']()
        for k in RG.INTERFACE_KEYS: self.assertEqual(r[k], RG.DEFAULT[k], k)
        self.assertIsNone(P.pan_pos)


class AlwaysCenteredCamera(unittest.TestCase):
    """There is no "Center the camera on the player" setting: the camera is always centered on the hero, except when the game takes it over. A settings.json that still contains `camera_centree` or `suivi_camera_v`
    loads without error, these keys are ignored and are not rewritten on save."""
    def load(self, d):
        c = os.path.join(tempfile.mkdtemp(prefix='rg_cam_'), 'settings.json')
        with open(c, 'w', encoding='utf-8') as f: json.dump(d, f)
        return RG.Settings(c), c

    def test_no_more_key(self):
        for k in ('camera_centree', 'suivi_camera_v'):
            self.assertNotIn(k, RG.DEFAULT); self.assertIn(k, RG.OBSOLETE_KEYS); self.assertNotIn(k, RG.Settings(None).v)

    def test_old_file_loads_without_error(self):
        for d in ({'camera_centree': False}, {'camera_centree': True, 'suivi_camera_v': 1}, {'camera_centree': False, 'suivi_camera_v': 1, 'msaa': 8}):
            r, c = self.load(d)
            for k in ('camera_centree', 'suivi_camera_v'): self.assertNotIn(k, r.v)
            self.assertEqual(r['msaa'], d.get('msaa', RG.DEFAULT['msaa']))        # (the file's other settings are kept)
            r.store()
            with open(c, encoding='utf-8') as f: raw_value = json.load(f)
            for k in ('camera_centree', 'suivi_camera_v'): self.assertNotIn(k, raw_value, 'the key disappears from the file at the next save')


class SpeedButtonMigration(unittest.TestCase):
    """An old reglages.json (the battle speed button was part of "On-screen buttons and D-pad") keeps the button's state: display, position, size; once only."""
    def load(self, d):
        f = os.path.join(tempfile.mkdtemp(prefix='rg_mig_'), 'settings.json')
        with open(f, 'w', encoding='utf-8') as h: json.dump(d, h)
        return RG.Settings(f), f

    def test_hidden_buttons_still_hide_the_button(self):
        r, _ = self.load({'buttons': False, 'buttons_size': 1.5}); self.assertEqual((r['buttons'], r['speed_button']), (False, False))
        self.assertEqual((r['speed_button_size'], r['speed_button_pos']), (1.0, []))              # (never placed: its plate followed "Flee" and ignored the buttons' size)

    def test_buttons_shown(self):
        r, _ = self.load({'buttons': True}); self.assertEqual((r['speed_button'], r['speed_button_size'], r['speed_button_pos']), (True, 1.0, []))

    def test_position_and_size_kept(self):
        r, _ = self.load({'buttons': True, 'buttons_size': 1.4, 'buttons_pos': {'combat': [7, 3.5], 'map': [2, 2], 'dpad': [20, 9]}})
        self.assertEqual((r['speed_button_pos'], r['speed_button_size']), ([7.0, 3.5], 1.4))
        self.assertEqual(r['buttons_pos'], {'map': [2, 2], 'dpad': [20, 9]}); self.assertEqual(r['buttons_size'], 1.4)           # the other buttons keep theirs

    def test_only_once(self):
        """The file saved by the new version contains the three keys: the player's choice is authoritative, even if "Boutons" changes afterward."""
        r, f = self.load({'buttons': False}); r['speed_button'] = True; r['buttons'] = False; r.store()
        r2 = RG.Settings(f); self.assertEqual((r2['buttons'], r2['speed_button']), (False, True))

    def test_new_file_or_without_buttons(self):
        r = RG.Settings(None); self.assertEqual((r['speed_button'], r['speed_button_size'], r['speed_button_pos']), (True, 1.0, []))
        r, _ = self.load({'vignette': 0.2}); self.assertEqual((r['speed_button'], r['speed_button_pos']), (True, []))

    def test_invalid_values(self):
        r, _ = self.load({'buttons': True, 'buttons_pos': {'combat': 'x'}}); self.assertEqual(r['speed_button_pos'], []); self.assertNotIn('combat', r['buttons_pos'])
        r, _ = self.load({'speed_button': 'window.yes', 'speed_button_size': 'grand', 'speed_button_pos': [1]}); self.assertEqual((r['speed_button'], r['speed_button_size'], r['speed_button_pos']), (True, 1.0, []))
        r, _ = self.load({'speed_button': True, 'speed_button_size': 9, 'speed_button_pos': [1, 2]}); self.assertEqual((r['speed_button_size'], r['speed_button_pos']), (2.0, [1.0, 2.0]))
        r, _ = self.load({'buttons': True, 'buttons_pos': {'combat': [True, 1]}}); self.assertEqual(r['speed_button_pos'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
