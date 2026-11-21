"""Player settings (Esc menu): display, sound, keys. Saved in data\\settings\\settings.json (see paths.py)."""
import json, os, sys
import pyglet
from dq456_pc import language as LG
from dq456_pc.persistence import pad_inputs as PI
from dq456_pc.language import N_

# Default keys: action -> [primary key, secondary key] (pyglet.window.key names)
DEFAULT_KEYS = {
    'left': ['A', None], 'right': ['D', None], 'up': ['W', None], 'down': ['S', None],      # (WASD; on an AZERTY keyboard: ZQSD, see layout_keys below)
    'cam_left': ['Q', 'LEFT'], 'cam_right': ['E', 'RIGHT'],      # Q / E (A / E on an AZERTY keyboard, see layout_keys) and the left / right arrows: on the field they rotate the town camera (both together recenter it), everywhere else (menus, messages, battles) they are left / right (the up / down arrows are no longer used by default)
    'menu': ['RETURN', 'TAB'], 'chat': ['LALT', 'RALT'], 'map': ['M', None],
    'A': ['SPACE', None], 'B': ['BACKSPACE', None], 'accel': ['LSHIFT', 'RSHIFT'],
    'L': ['R', None], 'R': ['T', None], 'info': ['F1', None], 'modifier': ['LCTRL', 'RCTRL'],          # (previous / next page: R and T, beside the camera keys)
    'extra': ['F', None],              # DQ VI's vehicle command (the extra field button of the mobile game, key 0x2000: Gourd Island, submarine, Pegasus; see core/pad_modes.py)
    'battle_speed': ['V', None],        # cycles through the battle speed (Manual, Normal, Fast, Very fast): in battle only; V for velocity (a free key: neither the movement keys, nor arrows, nor F1, nor the digits of the savestates)
}


# Movement and camera: the keys that the keyboard LAYOUT puts at the physical places of W, A, S, D, Q and E on a QWERTY. AZERTY: Z Q S D, A and E; QWERTY, QWERTZ: W A S D, Q and E.
# Detection on Windows only (scan code -> virtual key of the active layout); elsewhere, or if the layout is unusual (Dvorak...): W A S D and Q.
# Only the DEFAULT values change: the keys already saved in reglages.json are kept as they are.
PHYSICAL_KEYS = {'up': (0x11, 'W'), 'left': (0x1E, 'A'), 'down': (0x1F, 'S'), 'right': (0x20, 'D'), 'cam_left': (0x10, 'Q'), 'cam_right': (0x12, 'E')}      # action: (scan code of the QWERTY position, QWERTY letter)
OLD_L_SCANCODE = 0x10                 # "Previous page" sat at the place of Q before v9 (keys_version): A on an AZERTY keyboard


def windows_letter(scancode):
    """Letter (uppercase) that the active keyboard layout of Windows assigns to the scan code `scancode`; None outside Windows, on error, or if the key is not a letter."""
    if sys.platform != 'win32': return None
    try:
        import ctypes
        u = ctypes.windll.user32; u.GetKeyboardLayout.restype = ctypes.c_void_p; u.MapVirtualKeyExW.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
        vk = u.MapVirtualKeyExW(scancode, 1, u.GetKeyboardLayout(0))                          # 1 = MAPVK_VSC_TO_VK
    except Exception: return None
    return chr(vk) if 0x41 <= vk <= 0x5A else None


def layout_keys(letter_of=windows_letter):
    """{action: key name} of the PHYSICAL_KEYS keys for the keyboard layout; W A S D, Q and E (QWERTY) if a place is not a letter, if two places give the same letter or if a letter is already taken by another action."""
    others = {n for a, ts in DEFAULT_KEYS.items() if a not in PHYSICAL_KEYS for n in ts if n}
    found = {a: letter_of(sc) for a, (sc, _) in PHYSICAL_KEYS.items()}
    if None in found.values() or len(set(found.values())) < len(found) or others & set(found.values()): return {a: q for a, (_, q) in PHYSICAL_KEYS.items()}
    return found


DEFAULT_KEYS.update({a: [n, DEFAULT_KEYS[a][1]] for a, n in layout_keys().items()})          # (the second keys stay: the camera's arrows)
OLD_MENU = ['RETURN', None]         # default of the "Menu" action before v5 (keys_version): Enter alone; a file that still contains it also receives Tab (see Settings.__init__)
# action labels ("Controls" menu: the page groups the lines by sections, see pause.py; L / R: pages of the game's menus, fixed keys absent from the menu)
ACTIONS = [('up', N_('controls.up')), ('down', N_('controls.down')), ('left', N_('controls.left')), ('right', N_('controls.right')),
           ('cam_left', N_('controls.camera_left')), ('cam_right', N_('controls.camera_right')),
           ('accel', N_('controls.accelerator_key')), ('battle_speed', N_('controls.change_battle_speed')), ('modifier', N_('controls.special_key')),
           ('A', N_('controls.action')), ('menu', N_('controls.menu')), ('B', N_('controls.cancel_back')), ('map', N_('controls.map')), ('chat', N_('controls.party_chat')), ('extra', N_('controls.vehicle_command')),
           ('info', N_('controls.screen_info')), ('L', N_('controls.previous_page')), ('R', N_('controls.next_page'))]          # (labels in French, translated at display time by the Pause menu: `tr(label_text)`)
RESERVED = ('ESCAPE', 'F11')          # Esc opens the menu, F11 toggles window / full screen (like Alt + Enter): not reassignable
NAMES = {'LEFT': '←', 'RIGHT': '→', 'UP': '↑', 'DOWN': '↓', 'LSHIFT': N_('keys.left_shift'), 'RSHIFT': N_('keys.right_shift'), 'LCTRL': N_('keys.left_ctrl'), 'RCTRL': N_('keys.right_ctrl'),
        'LALT': N_('keys.left_alt'), 'RALT': 'Alt Gr', 'SPACE': N_('keys.space'), 'RETURN': N_('keys.enter'), 'ENTER': N_('keys.numpad_enter'), 'BACKSPACE': N_('keys.backspace'), 'TAB': 'Tab',
        'DELETE': N_('keys.delete'), 'INSERT': N_('keys.insert'), 'HOME': N_('keys.home'), 'END': N_('keys.end'), 'PAGEUP': N_('keys.page_up'), 'PAGEDOWN': N_('keys.page_down'), 'CAPSLOCK': N_('keys.caps_lock'),
        'NUM_ADD': N_('keys.numpad_plus'), 'NUM_SUBTRACT': N_('keys.numpad_minus'), 'COMMA': ',', 'PERIOD': '.', 'SEMICOLON': ';', 'COLON': ':', 'EXCLAMATION': '!', 'MINUS': '-', 'PLUS': '+', 'EQUAL': '=', 'SLASH': '/', 'BACKSLASH': '\\'}
# (`key_name` always returns the FRENCH name (data, like the action labels); the menu translates it when drawing it, `tr(key_name(...))`, including
# "Numpad 0" to "Numpad 9" of the other numeric keypad keys)

# Battle command windows (the bottom frame: Combattre / Fuir, Attaquer / Magie / Objets / Defense, targets, items and spells, info): frame of 459 interface units, whereas
# the battle action messages (types 2 and 3 of the message window) make it 510. By default these windows are enlarged to the width of the dialogue boxes (510 / 459): the
# speed button, stuck to the bottom-left corner of the bottom box, no longer shifts when an action message replaces the menu at the start of a turn. Same mechanism as
# any window adjusted in the editor: [relative size, x offset, y offset].
BATTLE_COMMAND_WINDOWS = ('BattleRoot', 'BattleAction', 'BattleTarget', 'BattleEnemyTarget', 'BattlePartyTarget', 'BattleGo', 'BattleIM', 'BattleInfo', 'BattleChangeInfo')
WINDOW_COMPANIONS = {'BattleChangeButton': 'BattleIM'}          # a window drawn inside another's frame (the party change's Examine / Choose bar, in the bottom of the member list) takes that window's size and place
COMMANDS_WIDTH = round(510.0 / 459.0, 4)
# The small screens laid over the game (map overviews, the town's shop list, their buttons; the shop pictures, window/menu_display.py): one and a half times the size of the game's,
# then a quarter more (interface v5; the three games), at most what the screen holds. The maps and their buttons are editor windows (their default size below); the pictures are not in
# the editor (fixed size). The bestiary (its list, its sheet, its parchment and its monster) a quarter larger than the game's, its parchment kept on the screen.
SMALL_SCREEN_SCALE = 1.875
OLD_SMALL_SCREEN_SCALE = 1.5                  # (before interface v5: a small screen still at this size takes the new one)
SMALL_SCREENS = ('CityMap', 'WorldLargeMap', 'ShopMap', 'MapButton')
BOOK_SCALE = 1.25

DEFAULT = {
    'language': 'system',            # language of the port's texts and of the game version (APK folder): 'system' (the computer's language) or a language code ('fr-fr'...; language.py)
    'sprite_mode': 0,            # "Shader": 0 originals ("None", the default; 4 before: an untouched 4 is migrated, keys_version 7), 1 EPX x8, 4 pixel perfect (see hd.py); 2 (smoothed regions) and 3 (bicubic), removed because they only blurred, are read as 1
    'window_mode': 0,            # window: 0 windowed, 1 full screen (borderless window at the desktop resolution, the monitor's display mode does not change); F11 / Alt + Enter switch from one to the other (formerly the boolean "full_screen"; the old exclusive full screen, mode 2, became 1)
    'msaa': 4,                    # MSAA antialiasing (0, 2, 4, 8): the scene is drawn off-screen (postfx.py), the change is immediate; limited by the graphics card
    'render_scale': 1.0,         # supersampling: the scene is drawn at this scale (1 to 2) then downscaled (1 = normal; 2 = 4 times more pixels computed, flicker and jagged edges greatly reduced)
    'sharpness': 0.0,               # 0 to 1: adaptive sharpening of the scene's edges (0 = none)
    'brightness': 1.0,            # 0.5 to 1.5: gamma curve of the scene (1 = like the game; higher = brighter)
    'contrast': 0.0,              # "Contrast", 0 to 1: contrast of the scene around mid-gray, x1 (0 = like the game) to x2
    'color_filter': 'none',       # "Filter": colours of the scene (COLOR_FILTERS: none, sepia, gray, vivid, soft, warm, cool)
    'motion_blur': 0.0,           # "Motion blur", 0 to 1: the previous frame over the new one as much as the camera moved; it has no line in the menu: always 0 at load
    'info': False,               # F1: information lines
    'sound': True, 'music_volume': 1.0, 'sfx_volume': 1.0,
    'master_volume': 1.0, 'jingle_volume': 1.0, 'interface_volume': 1.0,      # sound: overall volume, jingles and fanfares volume, interface sounds volume (categories: see sound.py; "bruitages" = the game's sound effects)
    'music_enabled': True, 'jingles_enabled': True, 'sfx_enabled': True, 'interface_enabled': True,       # per-category switches
    'text_speed': 4.0,         # dialogues: letters displayed per engine frame (30 per second); 4 = like the game (see TEXT_SPEEDS)
    'resolution': [1920, 1080],   # window: window size [width, height] (brought back to the screen if it is too large); full screen: no effect (the window takes the desktop resolution)
    'camera_invert_lr': False,  # camera rotated with the mouse (wheel click held + movement): reverses the left / right direction (no = like the arrow keys: dragging to the right = right arrow); does not change the keyboard arrow keys
    'vignette': 0.0,              # (0, like mobile; 0.3 before: an untouched 0.3 is migrated, keys_version 6) darkening of the edges of the game image (0 = none, 1 = black corners): gives volume to the scenery and 2D sprites, hides the plain background outside the map
    'edge_blur': 0.0,             # "Edge blur": blur of the edges of the game image, same shape and same places as the vignette (0 = none, like mobile; 1 = the corners fully blurred)
    'accel_speed': 1.5,         # "Accelerator multiplier" (1 to 4; 8 before): walking multiplier (running) as long as the "Accelerator key" is held (formerly "vitesse_course"); NEVER applies to battles (see battle_speed)
    'quick_save_journal': None,  # adventure log (0 to 2) of the last quick save: its "Resume" makes it the church's log in cyan again, as after loading a log (Engine._journal_hooks)
    'battle_speed': 0.0,        # (default Manual: the original game's battle messages, advanced by hand) "Battle speed": 0 Manual (normal speed, each message is advanced by hand), 1 Normal, 2 Fast, 3 Very fast (BATTLE_SPEEDS): the engine runs 1, 2 or 3 times as many steps per second during a battle (monster and hit animations, movements, delays, message scrolling, fades); the music and sound effects keep their speed (see battle_speed.py)
    'history_favorites_only': False,   # "History" window: "Favorites only" checkbox checked? (state saved at each change, restored from one session to the next; no line in the Esc menu: it is changed in the window itself, see ui_history.py)
    'level_up_heal': False,         # "HP and MP restored on level up": when a character gains a level, it recovers all its HP and all its MP (the original game does not do this); disabled = like the game
    'exp_rate': 1.0,                # "Experience gained": the experience of a won battle is multiplied before the game shares it out (RATES: Much less x0.5, Less x0.75, Normal x1 = the game, More x1.25, Much more x1.5; see Engine._reward_hooks)
    'gold_rate': 1.0,               # "Gold gained": the same for the gold of a battle
    'casino_rate': 1.0,             # "Casino tokens won": the tokens won at the slot machines, at poker (and its double-up games) and at the monster arena are multiplied (RATES; see Engine._casino_hooks)
    'random_encounters': True,      # "Random encounters": No = no random battle while walking (Engine._encounter_hooks); the battles of the story (bosses, events) stay
    'encounter_rate': 1.0,          # "Encounter rate" (grayed while "Random encounters" is No): how fast the game's encounter countdown runs at each step (RATES)
    'party_damage_rate': 1.0,       # "Damage taken by the party": the damage of every hit, spell or breath on a member of the party is multiplied (RATES; see Engine._damage_hooks)
    'enemy_damage_rate': 1.0,       # "Damage taken by enemies": the same on a monster
    'difficulty': 2,                # "Difficulty" (above the notched settings): 0 Dragon egg (the easiest) ... 2 Dragon (= the game) ... 4 Dragon god, 5 Custom; a level sets the 5 notched settings (difficulty_rates) and grays them, Custom lets them be set by hand
    'battle_end_heal': False,       # "HP and MP restored at the end of a battle": after every battle (won, fled or lost), the members still standing recover all their HP and all their MP (the original game does not do this); disabled = like the game
    'damage_size': 1.6,         # size of the damage numbers on the monsters (1 = size of the original layout, very small on a large screen)
    'editor_grid': True,       # interface editor: placement grid displayed (32 x 18 cells)
    'settings_page': 'display',     # the Esc menu's page open on the right when it was last closed (SETTINGS_PAGES); the menu opens on it again (Display the first time)
    'buttons': True,              # touch buttons of the mobile version ("Map", "Controls", "Talk") displayed on screen, outside battle
    'dpad': False, 'dpad_size': 1.0,      # D-pad (touch gamepad) on screen, usable with the mouse: displayed or not (not by default since interface v5); relative size (in addition to the game's "D-pad size" option)
    'rotation_bar': False,       # (not by default since interface v5) camera rotation bar (at the very bottom of the screen, across its full width) shown and usable with the mouse on the field of towns, dungeons and towers
    'buttons_size': 1.0, 'buttons_snap': True,       # relative size; positions placed on a grid (snapping)
    'interface_windows': dict({n: [COMMANDS_WIDTH, 0.0, 0.0] for n in BATTLE_COMMAND_WINDOWS}, **{n: [SMALL_SCREEN_SCALE, 0.0, 0.0] for n in SMALL_SCREENS}, MonsterEncyclopedia=[BOOK_SCALE, 0.0, 0.0]),     # game windows: name (Make_* of MenuPortItemTable, or 'msg' for the dialogues) -> [relative size, x offset, y offset as a fraction of the screen]; absent: like the game (see pause.py, interface editor)
    'buttons_pos': {},            # button or 'croix' -> [column, row] of its top-left corner on the screen grid (empty: original positions, see hud_mobile.py); the battle speed button has its own (speed_button_pos)
    'vehicle_button': True, 'vehicle_button_size': 1.0, 'vehicle_button_pos': [],      # "Vehicle button" interface (DQ VI's extra field button, core/pad_modes.py): shown or not; relative size; [column, row] of its top-left corner (empty: above Commands, as on mobile)
    'speed_button': True, 'speed_button_size': 1.0, 'speed_button_pos': [],      # "Battle: speed button" interface (split out of "On-screen buttons and D-pad"): shown in battle or not; relative size; [column, row] of its top-left corner (empty: stuck under the bottom box, see hud_mobile.Buttons.anchor)
    'interface_version': 5,             # version of the default sizes of the game windows (interface_windows): a file saved before (without this key) receives the new default sizes once, except for a window the player has adjusted; v5: the small screens a quarter larger, the bestiary too, the D-pad and the rotation bar hidden; v4: the two target lists at the width of the other battle command windows; v2: battle command windows at the width of the dialogues; v3: the small screens (maps, shop list, their buttons) one and a half times larger
    'interface_hidden': [],     # interface editor, "Show this interface on screen" = No: names of the game windows (Make_*, or 'msg' for the dialogues) that are not drawn; for the on-screen buttons and D-pad, it is the 'boutons' setting
    'keys_version': 9,               # settings version (v8 -> v9: camera Q / E (A / E in AZERTY) with the arrows as second keys, previous / next page R / T, vehicle command F, for the exact old defaults; v7 -> v8: battle speed 1 (Normal), the old default, -> 0 (Manual); v6 -> v7: shader 4, the old default, -> 0 "None"; v5 -> v6: vignette 0.3, the old default, -> 0): a key list older than v3 is discarded (v3: ZQSD to walk, arrow keys = camera, Space = action); v3 -> v4: "Party chat" = Alt / Alt Gr (if those keys are free), accelerator x1.5 (if running was at the original x2); v4 -> v5: "Menu" = Enter and Tab (if "Menu" still had the old default, Enter alone, and Tab is free); an action added since takes its default key
    'keys': {k: list(v) for k, v in DEFAULT_KEYS.items()},
    'gamepads': {f: dict(PI.DEFAULTS[f]) for f in PI.FAMILIES},       # third column of "Controls", one binding per controller family (Xbox, PlayStation, Nintendo Switch): action -> gamepad input (PI.PAD_INPUTS name), list of inputs, or None; read as extra keys, see window/gamepad.py. `Settings.v['gamepad']` (not saved) is the binding of the family in use
}


# Game windows absent from the interface editor (pause.py): game launch (start menu, name, title, license, network: handled separately), without a mesh (nothing to adjust),
# irrelevant on PC or that follow the monsters on screen. Their settings saved by an old version are discarded on load (they shifted a lone text or a launch window).
REMOVED_WINDOWS = ('StartMenuFirst', 'StartMenuSecond', 'StartMenuSelect', 'StartNameEntry', 'SaveOverwrite', 'TitleTouch', 'PortalButton', 'NetworkRoot', 'NetworkPLST', 'NetworkCODE', 'NetworkITEM',
                     'ViewVolumeDecision', 'License', 'MagicInfo', 'Config', 'NENE_ItemView', 'MonsterName', 'BattleHoppingNumber', 'FightStadiumAbort')

# Interface editor (pause.py: Affichage > Interface): classification of the settings and resets. Every DEFAULT key that touches the interface must appear in one of these
# three groups (tests/test_interface_settings.py checks it: a key added without classification makes the test fail).
SCREEN_TARGET = 'buttons'                                    # "On-screen buttons and D-pad": one of the editor's interfaces (the others: 'msg' and the game windows, by their Make_* name)
SCREEN_KEYS = ('buttons', 'dpad', 'dpad_size', 'rotation_bar', 'buttons_size', 'buttons_pos')      # settings of SCREEN_TARGET: display of the buttons and the D-pad, sizes, positions
SPEED_TARGET = 'speed_button'                           # "Battle: speed button": its own editor interface ("Battle" group), separate from SCREEN_TARGET
SPEED_KEYS = ('speed_button', 'speed_button_size', 'speed_button_pos')      # settings of SPEED_TARGET: display, size, position
VEHICLE_TARGET = 'vehicle_button'                       # "Vehicle button" (DQ VI only): its own editor interface ("Touch screen" group), separate from SCREEN_TARGET
VEHICLE_KEYS = ('vehicle_button', 'vehicle_button_size', 'vehicle_button_pos')      # settings of VEHICLE_TARGET: display, size, position
WINDOW_KEYS = ('interface_windows', 'interface_hidden')                       # settings per game window (or 'msg'): size and position, display
SETTINGS_PAGES = ('display', 'sound', 'controls', 'states', 'history', 'achievements', 'difficulty', 'cheats', 'randomizer')          # pages of the Esc menu it can open on (not the "Quit" question)
EDITOR_KEYS = ('editor_grid', 'buttons_snap')                                # options of the editor itself, common to all interfaces: grid displayed, snapping
INTERFACE_KEYS = SCREEN_KEYS + SPEED_KEYS + VEHICLE_KEYS + WINDOW_KEYS + EDITOR_KEYS
DEFAULT_WINDOW = (1.0, 0.0, 0.0)                           # game window with no saved setting: relative size, x offset, y offset (fractions of the screen)


def default_window(name_str):
    """Relative size, x offset, y offset of a game window with no player setting: the one from DEFAULT (battle command windows) or, otherwise, DEFAULT_WINDOW (like the game)."""
    return tuple(DEFAULT['interface_windows'].get(name_str, DEFAULT_WINDOW))


# Settings files written before the keys were in English: old key -> current key. Read once, when the file is loaded (`migrate_keys`); the file is
# rewritten with the new keys at the next save. The old names exist only here.
LEGACY_KEYS = {
    'langue': 'language', 'mode_sprites': 'sprite_mode', 'mode_fenetre': 'window_mode', 'echelle_rendu': 'render_scale', 'nettete': 'sharpness', 'luminosite': 'brightness',
    'infos': 'info', 'son': 'sound', 'volume_musique': 'music_volume', 'volume_bruitages': 'sfx_volume', 'volume_general': 'master_volume', 'volume_jingles': 'jingle_volume',
    'volume_interface': 'interface_volume', 'musique_active': 'music_enabled', 'jingles_actifs': 'jingles_enabled', 'bruitages_actifs': 'sfx_enabled',
    'interface_active': 'interface_enabled', 'vitesse_texte': 'text_speed', 'camera_inverser_gd': 'camera_invert_lr', 'vitesse_accel': 'accel_speed',
    'vitesse_combat': 'battle_speed', 'historique_favoris_seuls': 'history_favorites_only', 'soin_niveau': 'level_up_heal', 'taille_degats': 'damage_size',
    'grille_edition': 'editor_grid', 'boutons': 'buttons', 'croix': 'dpad', 'croix_taille': 'dpad_size', 'barre_rotation': 'rotation_bar', 'boutons_taille': 'buttons_size',
    'boutons_aimant': 'buttons_snap', 'interface_fenetres': 'interface_windows', 'boutons_pos': 'buttons_pos', 'bouton_vitesse': 'speed_button',
    'bouton_vitesse_taille': 'speed_button_size', 'bouton_vitesse_pos': 'speed_button_pos', 'interface_v': 'interface_version', 'interface_masquees': 'interface_hidden',
    'touches_v': 'keys_version', 'touches': 'keys', 'manette': 'gamepad',
}
LEGACY_ACTIONS = {'haut': 'up', 'bas': 'down', 'gauche': 'left', 'droite': 'right', 'cam_gauche': 'cam_left', 'cam_droite': 'cam_right',
                  'discussion': 'chat', 'carte': 'map', 'historique': 'history', 'infos': 'info', 'modif': 'modifier', 'vit_combat': 'battle_speed'}      # ids of the actions (keys and gamepad)
LEGACY_ELEMENTS = {'carte': 'map', 'discussion': 'chat', 'croix': 'dpad', 'boutons': 'buttons', 'bouton_vitesse': 'speed_button'}      # on-screen buttons and interfaces of the editor ('combat' is handled by migrate_speed_button)


def migrate_keys(d):
    """Old settings file (keys in French) -> keys of today, in place: settings, action ids of "keys" and "gamepad", buttons of "buttons_pos", entries of "interface_hidden", value 'systeme'.
    A key already in the new form wins over its old form."""
    for old, new in LEGACY_KEYS.items():
        if old in d:
            x = d.pop(old)
            if new not in d: d[new] = x
    for k in ('keys', 'gamepad'):
        if isinstance(d.get(k), dict):
            for old, new in LEGACY_ACTIONS.items():
                if old in d[k]:
                    x = d[k].pop(old)
                    if new not in d[k]: d[k][new] = x
    if isinstance(d.get('buttons_pos'), dict):
        for old, new in LEGACY_ELEMENTS.items():
            if old in d['buttons_pos']:
                x = d['buttons_pos'].pop(old)
                if new not in d['buttons_pos']: d['buttons_pos'][new] = x
    if isinstance(d.get('interface_hidden'), list): d['interface_hidden'] = [LEGACY_ELEMENTS.get(n, n) if isinstance(n, str) else n for n in d['interface_hidden']]
    if d.get('language') == 'systeme': d['language'] = 'system'


# Keys of old settings files that no longer exist: never read (the loader only takes the DEFAULT keys), hence absent from the file at the next save.
# 'full_screen' (boolean) is converted to 'window_mode'; 'mode_fenetre_retour' (last non-exclusive mode), 'edge_correction' (removed option:
# sprite edge correction is always applied) and 'lissage' (removed option: texture smoothing is always applied) are discarded; 'camera_centree' and 'suivi_camera_v'
# too (the camera is always centered on the hero, except when the game takes it). 'ecran_plein' (choice of the full-screen monitor) and 'sortie_audio'
# (choice of the audio device) were removed: full screen is done on the window's screen, and the sound stays on the
# Windows default output.
OBSOLETE_KEYS = ('plein_ecran', 'mode_fenetre_retour', 'correction_bords', 'lissage', 'defilement_force', 'defilement_combat', 'camera_centree', 'suivi_camera_v', 'ecran_plein', 'sortie_audio', 'distance_camera', 'angle_camera', 'camera_inverser_hb', 'zoom', 'vitesse_camera', 'sprite_edges')
COLOR_FILTERS = ('none', 'sepia', 'gray', 'vivid', 'soft', 'warm', 'cool')          # values of 'color_filter', in the order of the "Filter" list; matrices in render/postfx.py
MODES_SPRITES = (0, 1, 4)      # possible values of 'sprite_mode' (4 keeps its number: old settings files and the --hd options remain valid)
MSAA_VALUES = (0, 2, 4, 8)


def valid_sprites_mode(v):
    """Value read (old settings file, --hd option) -> existing mode: 2 (smoothed regions) and 3 (bicubic) became 1 (EPX x8, the closest: same x8 enlargement of the outlines, without the blur); everything else -> pixel perfect."""
    try: v = int(v)
    except (TypeError, ValueError): return DEFAULT['sprite_mode']
    if v in (2, 3): return 1
    return v if v in MODES_SPRITES else DEFAULT['sprite_mode']


def valid_window_mode(v):
    """Value read (reglages.json, --full-screen option) -> 0 (window) or 1 (full screen). A boolean is the old "full_screen" setting (true = 1); 2, the old exclusive full screen, becomes 1: the full screen that remains;
    anything invalid -> window."""
    if isinstance(v, bool): return 1 if v else 0
    try: n = int(v)
    except (TypeError, ValueError, OverflowError): return DEFAULT['window_mode']
    if n != v: return DEFAULT['window_mode']
    return {0: 0, 1: 1, 2: 1}.get(n, DEFAULT['window_mode'])


BOUNDS = {'accel_speed': (1.0, 4.0), 'vignette': (0.0, 1.0), 'edge_blur': (0.0, 1.0), 'contrast': (0.0, 1.0), 'motion_blur': (0.0, 1.0), 'dpad_size': (0.5, 2.0)}      # limits of the Esc menu sliders
BOUNDS.update({'render_scale': (1.0, 2.0), 'sharpness': (0.0, 1.0), 'brightness': (0.5, 1.5)})
BOUNDS.update({'music_volume': (0.0, 1.0), 'sfx_volume': (0.0, 1.0), 'master_volume': (0.0, 1.0), 'jingle_volume': (0.0, 1.0), 'interface_volume': (0.0, 1.0), 'text_speed': (0.25, 4000.0)})
SOUND_TEXT_BOOLEANS = ('music_enabled', 'jingles_enabled', 'sfx_enabled', 'interface_enabled', 'level_up_heal', 'battle_end_heal', 'random_encounters', 'camera_invert_lr', 'history_favorites_only')      # (Yes / No settings: any value that is not a boolean reverts to the default)
TEXT_SPEEDS = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 4000.0)     # text speed notches (letters per frame); 4 = the game, 4000 = all at once
BATTLE_SPEEDS = (0.0, 1.0, 2.0, 3.0)                       # notches of "Battle speed": Manual (normal speed + messages advanced by hand), Normal (= the game), Fast, Very fast
RATES = (0.5, 0.75, 1.0, 1.25, 1.5)
RATE_NAMES = {0.5: N_('difficulty.rate_much_less'), 0.75: N_('difficulty.rate_less'), 1.0: N_('common.normal'), 1.25: N_('difficulty.rate_more'), 1.5: N_('difficulty.rate_much_more')}      # the 5 notches of "Experience gained", "Gold gained", "Damage taken by..." (names only: never the factor)
RATE_KEYS = ('exp_rate', 'gold_rate', 'casino_rate', 'party_damage_rate', 'enemy_damage_rate', 'encounter_rate')
DIFFICULTIES = (0, 1, 2, 3, 4, 5); DIFFICULTY_CUSTOM = 5
DIFFICULTY_NAMES = {0: N_('difficulty.dragon_egg'), 1: N_('difficulty.dragonling'), 2: N_('difficulty.dragon'), 3: N_('difficulty.dragon_chief'), 4: N_('difficulty.dragon_god'), 5: N_('difficulty.custom')}
EASIER_WHEN_HIGHER = ('exp_rate', 'gold_rate', 'casino_rate', 'enemy_damage_rate')


def difficulty_rates(level):
    """The 6 notched settings of a difficulty level (0 Dragon egg, the easiest, to 4 Dragon god; 2 Dragon = Normal everywhere, the game): the easier, the more experience, gold,
    casino tokens and damage on the monsters, the less damage on the party and the fewer random encounters."""
    return {k: RATES[4 - level] if k in EASIER_WHEN_HIGHER else RATES[level] for k in RATE_KEYS}


BATTLE_SPEED_NAMES = {0.0: N_('common.manual'), 1.0: N_('common.normal'), 2.0: N_('common.fast'), 3.0: N_('common.very_fast')}      # labels shown to the player ("Texte" page, battle screen button, key message): never "x1", "x2", "x3"
# Battle message scrolling: OptionStatus.battleSpeed (g_Option + 5, original value 2: OptionStatus::initialize); BattleAutoFeed::setMessageSpeed:
# 0 / 1 / 2 / 3 = wait of 12 / 22 / 32 / 42 frames (0.4 / 0.73 / 1.07 / 1.4 s) before the next message, 4 = no automatic advance (key press).
# "Battle speed" imposes 4 in Manuel and 0 (the shortest, 12 frames) otherwise (with 2, the original value, 32 frames, the damage number only appeared
# 0.77 s after the start of the hit animation and an attack turn lasted 5.7 s; with 0: 0.43 s and 3.0 s; the hit animation keeps its duration): the Rapide and
# Very fast speeds speed up the engine clock, so this wait too.
BATTLE_MANUAL, BATTLE_NORMAL = 0.0, 1.0
CODE_SCROLL_MANUAL, CODE_SCROLL_AUTO = 4, 0


def scroll_code(speed):
    """Value of OptionStatus.battleSpeed to impose on the engine for this notch of "Battle speed": 4 (by hand) in Manuel, 0 (wait of 12 frames between two messages: the animations chain together with no dead time) otherwise."""
    return CODE_SCROLL_MANUAL if battle_notch(speed) == BATTLE_MANUAL else CODE_SCROLL_AUTO


def speed_after_game_code(code, speed):
    """The game itself changes battleSpeed (loading a save, original "Parametres" window): 4 -> Manuel; any other value -> leaves Manuel (Normal), the other notches are kept."""
    v = battle_notch(speed)
    try: code = int(code)
    except (TypeError, ValueError): return v
    if code == CODE_SCROLL_MANUAL: return BATTLE_MANUAL
    return BATTLE_NORMAL if v == BATTLE_MANUAL else v


def battle_notch(v, default=None):
    """Value read (settings.json, typed input) -> the nearest notch of BATTLE_SPEEDS (on a tie the faster); anything that is not a number (text, boolean, None, nan) -> `default` (None: the setting's default, Manual)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v: return DEFAULT['battle_speed'] if default is None else default
    v = min(BATTLE_SPEEDS[-1], max(BATTLE_SPEEDS[0], v))                                  # (limits: never slower than Manual, nor faster than Very fast)
    return min(BATTLE_SPEEDS, key=lambda c: (abs(c - v), -c))


def next_battle_speed(v):
    """"Change battle speed" key: Manual -> Normal -> Fast -> Very fast -> Manual (an invalid value is read as Normal, an out-of-range value as the nearest notch)."""
    v = battle_notch(v)
    return BATTLE_SPEEDS[(BATTLE_SPEEDS.index(v) + 1) % len(BATTLE_SPEEDS)]


def key_name(name_str):
    """Readable name of a key (pyglet name) for the menu."""
    if not name_str: return '—'
    if name_str in PI.LABELS: return PI.LABELS[name_str]
    return NAMES.get(name_str, name_str.replace('NUM_', LG.tr('keys.numpad_prefix')) if name_str.startswith('NUM_') else (name_str[1:] if name_str.startswith('_') and name_str[1:].isdigit() else name_str))


def key_symbol(name_str):
    """pyglet name -> key code (None if unknown)."""
    return PI.SYMBOL.get(name_str) or getattr(pyglet.window.key, name_str, None) if name_str else None


def name_of(symbol):
    """Key code -> pyglet name."""
    return PI.NAME.get(symbol) or pyglet.window.key.symbol_string(symbol)


class Settings:
    def __init__(self, path=None, data=None):
        """The settings of the file `path`, or of `data` (the same dict as the file: the settings of a game, game_rules), checked and brought up to date; missing or unreadable: the defaults."""
        self.path = path; self.v = json.loads(json.dumps(DEFAULT)); self.family = 'XB'
        if data is not None or (path and os.path.exists(path)):
            try:
                if data is not None: d = json.loads(json.dumps(data))
                else:
                    with open(path, encoding='utf-8') as f: d = json.load(f)
                migrate_keys(d)
                if 'window_mode' not in d and 'plein_ecran' in d: d['window_mode'] = d['plein_ecran']      # old "Fullscreen" setting (boolean) -> 1 (borderless full screen, as before) or 0; the boolean is no longer saved (see OBSOLETE_KEYS)
                if 'accel_speed' not in d and isinstance(d.get('vitesse_course'), (int, float)) and not isinstance(d.get('vitesse_course'), bool): d['accel_speed'] = d['vitesse_course']      # old "Run multiplier" -> accelerator speed (the old camera multiplier, accel_camera, is dropped: a single factor for both)
                if isinstance(d.get('keys'), dict) and 'accel' not in d['keys']:       # old run and camera acceleration keys -> a single "Accelerator key": "Courir (maintenir)", failing that the other three ("appuyer" no longer exists)
                    for old in ('courir', 'courir_bascule', 'cam_accel', 'cam_accel_bascule'):
                        l = d['keys'].get(old)
                        if isinstance(l, list) and any(l): d['keys']['accel'] = l; break
                    else:
                        if isinstance(d['keys'].get('courir'), list): d['keys']['accel'] = [None, None]        # the player had cleared these keys: the accelerator has no key
                if 'speed_button' not in d and 'speed_button_size' not in d and 'speed_button_pos' not in d: self.migrate_speed_button(d)      # (only once: afterwards the three keys are saved)
                old = d.get('defilement_combat'); old = 4 if old is None and d.get('defilement_auto') is False else old                # (any old "Automatic scrolling: no" = manual messages)
                if d.get('defilement_force') is True and 'battle_speed' not in d and old != 4: d['battle_speed'] = BATTLE_NORMAL      # old forced automatic messages -> Normal (Manual became the default)
                if d.get('defilement_force') is True and old == 4: d['battle_speed'] = BATTLE_MANUAL      # old forced "Battle messages: Manual" -> "Battle speed: Manual" (the other notches no longer have an equivalent: the wait follows the battle speed; nothing forced: the saved speed stays)
                saved_v = d.get('keys_version')
                for k, x in d.items():
                    if k == 'keys' and saved_v not in (3, 4, 5, 6, 7, 8, DEFAULT['keys_version']): continue
                    if k == 'keys' and isinstance(x, dict):
                        for a, ts in x.items():
                            if a in DEFAULT_KEYS and isinstance(ts, list): self.v['keys'][a] = (list(ts) + [None, None])[:2]
                    elif k in DEFAULT and k != 'keys' and k != 'damage_size': self.v[k] = x          # (the size of the damage numbers is no longer a setting: fixed value)
                if d.get('interface_version') != DEFAULT['interface_version'] and isinstance(self.v['interface_windows'], dict):      # old default window sizes -> new ones, once only (afterwards the key is saved and the player's choice prevails): window with no setting or at the old default size (1, 0, 0)
                    fw = dict(self.v['interface_windows'])
                    for n, nv in DEFAULT['interface_windows'].items():
                        if n not in fw or fw[n] == list(DEFAULT_WINDOW) or (n in SMALL_SCREENS and fw[n] == [OLD_SMALL_SCREEN_SCALE, 0.0, 0.0]): fw[n] = list(nv)
                    self.v['interface_windows'] = fw
                    self.v['dpad'] = self.v['rotation_bar'] = False          # (v5: the D-pad and the rotation bar hidden, once; shown again from the Esc menu)
                if saved_v == 3:                                      # v3 -> v4: new default values, without touching what the player set themselves
                    ts = self.v['keys']
                    if ts['chat'] == [None, None] and not any(n in (a for l in ts.values() for a in l) for n in ('LALT', 'RALT')): ts['chat'] = list(DEFAULT_KEYS['chat'])
                    for a in ('modifier',):
                        for i, n in enumerate(ts[a]):
                            if n and any(n in l for b, l in ts.items() if b != a): ts[a][i] = None        # a key already taken by another action stays with that action
                    if self.v['accel_speed'] == 2.0: self.v['accel_speed'] = DEFAULT['accel_speed']
                if saved_v in (3, 4):                                 # v4 -> v5: new default of "Menu" (Enter and Tab). Only the exact old default (Enter alone) changes: a player's choice is kept, and Tab stays with the action that took it
                    ts = self.v['keys']
                    if ts['menu'] == OLD_MENU and not any('TAB' in l for a, l in ts.items() if a != 'menu'): ts['menu'] = list(DEFAULT_KEYS['menu'])
                if saved_v in (3, 4, 5) and self.v['vignette'] == 0.3: self.v['vignette'] = DEFAULT['vignette']          # v5 -> v6: the old default becomes the new one; a value the player chose stays
                if saved_v in (3, 4, 5, 6) and self.v['sprite_mode'] == 4: self.v['sprite_mode'] = DEFAULT['sprite_mode']          # v6 -> v7: the old default shader (pixel perfect) becomes the new one, "None"
                if saved_v in (3, 4, 5, 6, 7) and d.get('battle_speed') == BATTLE_NORMAL: self.v['battle_speed'] = DEFAULT['battle_speed']          # v7 -> v8: the old default battle speed (Normal) becomes the new one, "Manual"
                if saved_v in (3, 4, 5, 6, 7, 8): self.migrate_camera_keys(self.v['keys'])          # v8 -> v9: the camera on Q / E (A / E in AZERTY)
                saved = d.get('keys') if isinstance(d.get('keys'), dict) else {}
                for a in DEFAULT_KEYS:                                  # action added since: its original key is dropped if another action has taken it
                    if a in saved: continue
                    ts = self.v['keys']
                    for i, n in enumerate(ts[a]):
                        if n and any(n in l for b, l in ts.items() if b != a): ts[a][i] = None
                self.v['gamepads'] = self.read_gamepads(d)
                self.v.pop('pad_mouse', None)          # (the option no longer exists)
                self.v['keys_version'] = DEFAULT['keys_version']
                self.v['interface_version'] = DEFAULT['interface_version']
                for ts in self.v['keys'].values():                   # a reserved key (F11: toggles window / full screen) can no longer be an action key: removed from an old file
                    for i in range(2):
                        if ts[i] in RESERVED: ts[i] = None
                for k in RATE_KEYS:
                    if isinstance(self.v[k], bool) or self.v[k] not in RATES: self.v[k] = DEFAULT[k]          # (one of the 5 notches, nothing else)
                dv = self.v['difficulty']
                if 'difficulty' not in d: dv = next((n for n in DIFFICULTIES[:-1] if all(self.v[k] == r for k, r in difficulty_rates(n).items())), DIFFICULTY_CUSTOM)          # (file from before the setting: the level of its notches, otherwise Custom)
                self.v['difficulty'] = int(dv) if isinstance(dv, (int, float)) and not isinstance(dv, bool) and dv in DIFFICULTIES else DEFAULT['difficulty']
                if self.v['difficulty'] != DIFFICULTY_CUSTOM: self.v.update(difficulty_rates(self.v['difficulty']))          # (a level decides the notches)
                if self.v['quick_save_journal'] not in (None, 0, 1, 2) or isinstance(self.v['quick_save_journal'], bool): self.v['quick_save_journal'] = None
                if self.v['settings_page'] not in SETTINGS_PAGES: self.v['settings_page'] = DEFAULT['settings_page']
                for k, (mi, ma) in BOUNDS.items(): self.v[k] = min(ma, max(mi, self.v[k])) if isinstance(self.v[k], (int, float)) and not isinstance(self.v[k], bool) else DEFAULT[k]       # values from old versions brought back to the sliders
                for k in SOUND_TEXT_BOOLEANS:
                    if not isinstance(self.v[k], bool): self.v[k] = DEFAULT[k]
                self.v['battle_speed'] = battle_notch(self.v['battle_speed'], DEFAULT['battle_speed'])
                self.v['text_speed'] = min(TEXT_SPEEDS, key=lambda x: max(x, self.v['text_speed']) / min(x, self.v['text_speed']))        # one of the menu's notches
                fw = self.v['interface_windows']; self.v['interface_windows'] = {n: x for n, x in fw.items() if n not in REMOVED_WINDOWS and isinstance(x, list) and len(x) == 3} if isinstance(fw, dict) else {}      # interface editor: windows removed from the editor
                ms = self.v['interface_hidden']; self.v['interface_hidden'] = [n for n in ms if isinstance(n, str) and n not in REMOVED_WINDOWS] if isinstance(ms, list) else []
                bs = self.v['speed_button_size']; self.v['speed_button_size'] = min(2.0, max(0.5, float(bs))) if isinstance(bs, (int, float)) and not isinstance(bs, bool) and bs == bs else DEFAULT['speed_button_size']
                if not isinstance(self.v['speed_button'], bool): self.v['speed_button'] = DEFAULT['speed_button']
                bp = self.v['speed_button_pos']; self.v['speed_button_pos'] = [float(x) for x in bp] if isinstance(bp, list) and len(bp) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in bp) else []
                bs = self.v['vehicle_button_size']; self.v['vehicle_button_size'] = min(2.0, max(0.5, float(bs))) if isinstance(bs, (int, float)) and not isinstance(bs, bool) and bs == bs else DEFAULT['vehicle_button_size']
                if not isinstance(self.v['vehicle_button'], bool): self.v['vehicle_button'] = DEFAULT['vehicle_button']
                bp = self.v['vehicle_button_pos']; self.v['vehicle_button_pos'] = [float(x) for x in bp] if isinstance(bp, list) and len(bp) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in bp) else []
                if isinstance(self.v['buttons_pos'], dict): self.v['buttons_pos'] = {n: x for n, x in self.v['buttons_pos'].items() if n not in ('combat', 'extra')}      # (the speed button's position is no longer here)
                self.v['sprite_mode'] = valid_sprites_mode(self.v['sprite_mode'])
                self.v['window_mode'] = valid_window_mode(self.v['window_mode'])          # (2 = old exclusive full screen -> 1)
                self.v['language'] = LG.normalized(self.v['language'])          # (an earlier version's code: 'fr' -> 'fr-fr'; unknown: the default)
                if self.v['msaa'] not in MSAA_VALUES: self.v['msaa'] = DEFAULT['msaa']
                if self.v['color_filter'] not in COLOR_FILTERS: self.v['color_filter'] = DEFAULT['color_filter']
                self.v['motion_blur'] = 0.0          # (no line any more; a value set while it existed would stay out of reach)
            except Exception as ex:
                print('Unreadable settings (%s): default values (%r)' % (path, ex), flush=True)
        self.codes_cache = {}

    @staticmethod
    def migrate_speed_button(d):
        """Old file: the battle speed button was part of "On-screen buttons and D-pad" (display = 'boutons', size = 'buttons_size', position = buttons_pos['combat']); it now has its own settings,
        carried over as they behaved: shown if the buttons were; its position kept if the player had placed it (otherwise it stays stuck to the bottom box) and then the size of the buttons (without a position, the plate
        followed that of "Flee" and ignored the size). `buttons_pos` loses the key 'combat'. Modifies `d` in place."""
        bp = d.get('buttons_pos'); pos = bp.pop('combat', None) if isinstance(bp, dict) else None
        if isinstance(d.get('buttons'), bool): d['speed_button'] = d['buttons']
        if isinstance(pos, list) and len(pos) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in pos):
            d['speed_button_pos'] = [pos[0], pos[1]]
            t = d.get('buttons_size')
            if isinstance(t, (int, float)) and not isinstance(t, bool): d['speed_button_size'] = t

    def __getitem__(self, k): return self.pad if k == 'gamepad' else self.v[k]          # ('gamepad': the binding of the family in use)

    def __setitem__(self, k, x):
        self.v[k] = x; self.codes_cache = {}

    # ---- interface editor: resets (always from DEFAULT, never from copied values)
    @staticmethod
    def default(lookup_key):
        """A modifiable copy of a setting's default value."""
        return json.loads(json.dumps(DEFAULT[lookup_key]))

    def reset_interface(self, target):
        """"Restore this interface": the editor's `target` interface (SCREEN_TARGET, 'msg' or the Make_* name of a game window) returns entirely to its original state, and only that one:
        position, size, display ("Show this interface on screen") and its other options. On-screen buttons and D-pad: all of SCREEN_KEYS (display, D-pad and button size, positions); battle speed button: all of SPEED_KEYS.
        Game window: its WINDOW_KEYS entry (absent from DEFAULT = size, position and display of the game). The editor options (EDITOR_KEYS) do not change."""
        if target == SCREEN_TARGET:
            for k in SCREEN_KEYS: self[k] = self.default(k)
            return
        if target == SPEED_TARGET:                                    # battle speed button: display, size and position (back under the bottom box)
            for k in SPEED_KEYS: self[k] = self.default(k)
            return
        if target == VEHICLE_TARGET:                                  # DQ VI's vehicle button: display, size and position (back above Commands)
            for k in VEHICLE_KEYS: self[k] = self.default(k)
            return
        win = dict(self['interface_windows']); win.pop(target, None)
        if target in DEFAULT['interface_windows']: win[target] = self.default('interface_windows')[target]
        self['interface_windows'] = win
        masked = [n for n in self['interface_hidden'] if n != target]
        if target in DEFAULT['interface_hidden']: masked.append(target)
        self['interface_hidden'] = masked

    def reset_interfaces(self):
        """"Restore all interfaces": all the interfaces (SCREEN_KEYS and WINDOW_KEYS) and the editor options (EDITOR_KEYS: grid, snapping) return to DEFAULT."""
        for k in INTERFACE_KEYS: self[k] = self.default(k)

    @staticmethod
    def read_gamepads(d):
        """The three gamepad bindings of a settings file. A file from before (a single `gamepad` binding): still the default of the time -> the new defaults of each family; changed by the
        player -> his binding for the three families (completed with each family's defaults)."""
        saved = d.get('gamepads')
        if isinstance(saved, dict): return {f: PI.clean(PI.with_stick(saved.get(f)), PI.DEFAULTS[f]) for f in PI.FAMILIES}          # (with_stick: a file from before the left stick's own inputs)
        old = d.get('gamepad')
        if not isinstance(old, dict) or PI.clean(old, PI.OLD_DEFAULT_PAD) == PI.OLD_DEFAULT_PAD: return {f: dict(PI.DEFAULTS[f]) for f in PI.FAMILIES}
        return {f: PI.clean(PI.with_stick(old), PI.DEFAULTS[f]) for f in PI.FAMILIES}

    @property
    def pad(self):
        """Gamepad binding of the controller family in use (`family`, set by the window from the connected controller)."""
        return self.v['gamepads'][self.family]

    def set_pad_family(self, family):
        """The controller in use is of this family ('XB', 'PS', 'NS'): its binding becomes the one read (keys, menu). Unknown family: Xbox."""
        family = family if family in PI.FAMILIES else 'XB'
        if family != self.family: self.family = family; self.codes_cache = {}

    def store(self):
        if not self.path: return
        try:
            tmp = self.path + '.tmp'
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)          # (a new installation: data\settings does not exist yet)
            with open(tmp, 'w', encoding='utf-8') as f: json.dump({k: x for k, x in self.v.items() if k != 'gamepad'}, f, indent=1, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as ex:
            print('Settings not saved:', ex, flush=True)

    # ---- keys
    def codes(self, action):
        """pyglet key codes of an action."""
        c = self.codes_cache.get(action)
        if c is None:
            c = self.codes_cache[action] = tuple(s for s in (key_symbol(n) for n in [*self.v['keys'][action], *PI.inputs(self.pad.get(action))]) if s is not None)      # (the two keys, then the gamepad inputs)
        return c

    def action_of(self, symbol):
        """Action bound to a key (or None): a key serves only one action."""
        name_str = name_of(symbol)
        if PI.is_pad(symbol): return next((a for a, n in self.pad.items() if name_str in PI.inputs(n)), None)
        for a, ts in self.v['keys'].items():
            if name_str in ts: return a
        return None

    def bind(self, action, column, symbol):
        """Binds a key to (action, column 0/1); the key is removed from any other action. -> name of the action that had it, or None."""
        name_str = name_of(symbol); previous = None
        for a, ts in self.v['keys'].items():
            for i in range(2):
                if ts[i] == name_str and (a, i) != (action, column): ts[i] = None; previous = a
        self.v['keys'][action][column] = name_str; self.codes_cache = {}
        return previous

    def unbind(self, action, column):
        self.v['keys'][action][column] = None; self.codes_cache = {}

    def reset_to_default(self, action):
        """Resets the keys of an action to their original values (an original key since taken by another action is removed from that action). -> names of the actions that lost it."""
        lost = []; ts = self.v['keys']; ts[action] = [None, None]
        for col, name_str in enumerate(DEFAULT_KEYS[action]):
            if not name_str: continue
            for a, others in ts.items():
                for i in range(2):
                    if others[i] == name_str and (a, i) != (action, col): others[i] = None; lost.append(a)
            ts[action][col] = name_str
        self.codes_cache = {}
        return lost

    @staticmethod
    def migrate_camera_keys(ts):
        """v8 -> v9: the keys still at their old default take the new one: camera left / right (the arrows) -> Q / E for the keyboard's layout (A / E in AZERTY), the arrows staying as second
        keys; "Previous page" (the place of Q: A in AZERTY) -> R, "Next page" (R) -> T, vehicle command (E) -> F. A key the player chose stays; a new key another action already has stays with it."""
        moved = []
        if ts['L'][1] is None and ts['L'][0] in ('Q', windows_letter(OLD_L_SCANCODE)): ts['L'] = list(DEFAULT_KEYS['L']); moved.append('L')
        for a, old in (('R', ['R', None]), ('extra', ['E', None]), ('cam_left', ['LEFT', None]), ('cam_right', ['RIGHT', None])):
            if list(ts[a]) == old: ts[a] = list(DEFAULT_KEYS[a]); moved.append(a)
        for a in moved:
            for i, n in enumerate(ts[a]):
                if n and any(n in l for b, l in ts.items() if b != a): ts[a][i] = None

    def is_default_binding(self, action):
        return list(self.v['keys'][action]) == list(DEFAULT_KEYS[action])

    def bind_pad(self, action, name_str, column=None):
        """Binds a gamepad input to an action; it is removed from any other action. `column` None: it replaces the action's inputs; 0 or 1 (the two "Bouton" cells of "Controls"): it takes that
        place, the other input stays (an input already in the other place moves). -> name of the action that had it, or None."""
        pad = self.pad; previous = next((a for a, n in pad.items() if name_str in PI.inputs(n) and a != action), None)
        if previous: pad[previous] = PI._simple([n for n in PI.inputs(pad[previous]) if n != name_str])
        if column is None: pad[action] = name_str
        else:
            slots = [None if n == name_str else n for n in PI.inputs(pad.get(action))]; slots += [None] * (2 - len(slots))
            slots[column] = name_str; pad[action] = PI._simple([n for n in slots if n])
        self.codes_cache = {}
        return previous

    def pad_slots(self, action):
        """The two gamepad inputs shown in the "Bouton 1" / "Bouton 2" cells of an action (None: empty cell)."""
        names = list(PI.inputs(self.pad.get(action)))[:2]
        return names + [None] * (2 - len(names))

    def unbind_pad(self, action, column=None):
        """Removes the action's gamepad inputs (`column` None), or the one of cell 0 / 1."""
        if column is None: self.pad[action] = None
        else:
            names = list(PI.inputs(self.pad.get(action)))
            if column < len(names): del names[column]
            self.pad[action] = PI._simple(names)
        self.codes_cache = {}

    def reset_pad(self, action):
        """Gives an action its original gamepad inputs back, for the family in use (taken from the actions that have them now). -> name of the last action that lost one, or None."""
        pad = self.pad; pad[action] = None; lost = None
        for n in PI.inputs(PI.DEFAULTS[self.family][action]):
            other = next((a for a, x in pad.items() if n in PI.inputs(x) and a != action), None)
            if other: pad[other] = PI._simple([x for x in PI.inputs(pad[other]) if x != n]); lost = other
        pad[action] = PI.DEFAULTS[self.family][action]; pad[action] = list(pad[action]) if isinstance(pad[action], (list, tuple)) else pad[action]; self.codes_cache = {}
        return lost

    def is_default_pad(self, action):
        return PI.inputs(self.pad[action]) == PI.inputs(PI.DEFAULTS[self.family][action])

    def default_keys(self):
        self.v['keys'] = {k: list(x) for k, x in DEFAULT_KEYS.items()}; self.v['gamepads'] = json.loads(json.dumps(DEFAULT['gamepads'])); self.codes_cache = {}
