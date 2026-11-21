"""Keyboard, mouse and gamepad: window events and key reading."""
import os, time
import pyglet
from dq456_pc import language as LG
from dq456_pc.language import tr
from dq456_pc.window import inputs as EN
from dq456_pc.menus import hud_mobile as HM
from dq456_pc.core import engine as ENGINE
from dq456_pc import games as GM
from dq456_pc.persistence import pad_inputs as PI
from dq456_pc.persistence import savestates as SS
from dq456_pc.window.constants import CURSOR_CANCEL, CURSOR_ROW, EDGE_ACTIONS, BTN_MAP, BTN_CHAT, BTN_MENU, BTN_TALK, BTN_SAVES, BTN_LANGUAGE, SFX_BUTTON, button_sfx


def physical_key(reg, symbol):
    """Keyboard key that stands for a gamepad input in the name entry (it reads physical keys: the letters are typed there): directions = arrows, A = Enter, B = Backspace.
    Any other key (and any other input) is returned as it is."""
    if not PI.is_pad(symbol): return symbol
    K = pyglet.window.key
    return {'up': K.UP, 'down': K.DOWN, 'left': K.LEFT, 'right': K.RIGHT,          # (the camera's left / right inputs are the shoulders: not a direction)
            'A': K.RETURN, 'B': K.BACKSPACE}.get(reg.action_of(symbol), symbol)


SECTION_LAST_STEPS = 30          # engine steps the party list has to show its previous page after ▲ (camera key), before its last member is touched
DIRECT_BUTTONS = (('Make_MonsterEncyclopedia', 0x800), ('Make_ScopeBack', 0x200))          # buttons whose MenuParts + 0x4d is set (MenuPortItemTable): "Action" of the bestiary sheet (the three games), whose key is read
                                                                  # from the game's keys (book::BookMonsterDraw::execute: g_Pad trigger 0x800 -> startAnimation: the monster's attack and its sound effect), never from the
                                                                  # window; the back button of the scope views (DQ V / VI, gamepad 0x200). (The back buttons 0x400 have their own handling.)
MAP_MODE_V2 = 1                    # DQ V / VI: gamepad key of the "map mode" button (type 22) and of the plan (type 25): decide, as on release of the finger (DQ IV: 2, up)


class KeyboardMouseMixin:
    """Keyboard, mouse and gamepad: window events and key reading."""

    def on_text(self, text):
        """Esc menu: entering a value with the keyboard."""
        if self._ready and self.pause.opened and self.pause.entry is not None: self.pause.text(text)
        elif self._ready and self.pause.opened and getattr(self.pause, 'screen_page', None) == 'name' and self.pause.popup is None: self.name_entry.text_entered(text)          # (the name page of a new game's panel)
        elif self._ready and self._name_entry_in_progress(): self.name_entry.text_entered(text)
        elif self._ready and getattr(self, 'port_screen', None) is not None and hasattr(self.port_screen, 'text'): self.port_screen.text(text)          # (the language screen's forms)
        return pyglet.event.EVENT_HANDLED

    def on_key_press(self, symbol, modifiers):
        """The only fixed key: Esc (menu). The others are those of the Controls menu (pad, on-screen info); the open menu gets all the keys."""
        K = pyglet.window.key
        if symbol == PI.SYMBOL['PAD_START']: symbol = K.ESCAPE          # gamepad Start = Esc (opens and closes the Esc menu, back in the game's menus)
        if self._window_shortcut(symbol, modifiers): return pyglet.event.EVENT_HANDLED          # F11 / Alt + Enter: from anywhere (logos, title, game, Esc menu)
        if symbol in (K.LALT, K.RALT): self.alt_cand = True                # Alt alone = party chat, on release (see on_key_release and _edges)
        if not self._ready: self._logo_touched = True; return pyglet.event.EVENT_HANDLED
        self.mouse_guard.keyboard(self.mouse)                             # keyboard in control: hover only takes over at the pointer's first real movement (a click takes control back immediately)
        if self.pause.opened: self.pause.key_press(physical_key(self.reg, symbol) if getattr(self.pause, 'screen_page', None) == 'name' else symbol, modifiers); return pyglet.event.EVENT_HANDLED          # (the name page of a new game's panel reads physical keys, like the name entry)
        if self.debug is not None and self.debug.key(symbol, modifiers): return pyglet.event.EVENT_HANDLED          # debug mode (--debug): F2 to F7 first
        if getattr(self, '_replay', None) is not None: self.replay_key(symbol); return pyglet.event.EVENT_HANDLED          # battle replay: only stop and speed
        self.last_device = 'gamepad' if PI.is_pad(symbol) else 'keyboard'
        if self._name_entry_in_progress(): self.name_entry.key_press(physical_key(self.reg, symbol), modifiers); return pyglet.event.EVENT_HANDLED          # name entry: the keyboard belongs to it
        if self.port_screen is not None: self.port_screen.key_press(symbol, modifiers); return pyglet.event.EVENT_HANDLED                       # Sauvegardes or language screen (they read the keys of "Controls")
        if self.license_panel is not None:                                       # Licence screen: scrolling (the up / down keys of "Controls") and back
            step = {'up': -30.0, 'down': 30.0}.get(self.reg.action_of(symbol), {K.PAGEUP: -360.0, K.PAGEDOWN: 360.0, K.HOME: -1e9, K.END: 1e9}.get(symbol))
            if step is not None: self.license_panel.scroll_by(step)
            elif symbol in (K.ESCAPE, K.BACKSPACE) or self.reg.action_of(symbol) == 'B': self._license_back()
            return pyglet.event.EVENT_HANDLED
        if symbol == K.ESCAPE:                                             # Esc: back in the game's menus and the map (like the Cancel key); the Esc menu only when the player moves freely (not in battle, messages, scenes, title...)
            if self._esc_back(): self.click_b = True
            elif self.history_possible(): self.pause.open_menu(side=self.pause.last_page())          # (the menu opens on the page last open, Display the first time)
            return pyglet.event.EVENT_HANDLED
        if self.reg.action_of(symbol) == 'info': self.helptext = not self.helptext; self.reg['info'] = self.helptext; return pyglet.event.EVENT_HANDLED
        if self.reg.action_of(symbol) == 'battle_speed':          # Manual -> Normal -> Fast -> Very fast, in battle only
            if self.engine.state.part == ENGINE.PART_BATTLE: self.change_battle_speed()
            return pyglet.event.EVENT_HANDLED
        n = self._state_number(symbol)
        if n is not None:                                                  # savestate shortcuts: digit = save, Ctrl + digit = load
            if self._held('modifier'):
                self.combo[1] = True
                if self.state_info(n):
                    try: self.load_state(n); self.toast(tr('save_states.state_loaded') % n)
                    except SS.OtherLanguage as ex:                          # a state of another game version: the Esc menu offers to switch the game to its language
                        self.toast(tr('save_states.state_other_version_game_load') % (n, LG.language_name(ex.language)), duration=5.0)
                    except Exception as ex: self._note_state_failure(tr('save_states.loading_state') % n, 'Loading savestate %d' % n, ex)
                else: self.toast(tr('save_states.state_empty') % n)
            else: self.quick_save = n
            return pyglet.event.EVENT_HANDLED
        if symbol in (K.LSHIFT, K.RSHIFT) or symbol in self.reg.codes('modifier'):      # Shift + modifier key (no digit): undoes the last load, on release
            ctrl = symbol in self.reg.codes('modifier') or self._held('modifier'); do_update = symbol in (K.LSHIFT, K.RSHIFT) or self.key_list[K.LSHIFT] or self.key_list[K.RSHIFT]
            if ctrl and do_update: self.combo = [True, False]
        return pyglet.event.EVENT_HANDLED

    def on_key_release(self, symbol, modifiers):
        K = pyglet.window.key
        self.swallowed.discard(symbol)
        if symbol in (K.LALT, K.RALT):         # Alt released: if nothing else was done meanwhile (not Alt + Enter), it is the press of the party chat key
            cand, self.alt_cand = self.alt_cand, False
            if cand and self._ready and not self.pause.opened and symbol in self.reg.codes('chat') and not (self.key_list[K.LALT] or self.key_list[K.RALT]): self.alt_tap = True
        if (symbol in (K.LSHIFT, K.RSHIFT) or symbol in self.reg.codes('modifier')) and self.combo[0]:
            triggered = not self.combo[1]; self.combo = [False, False]
            if triggered and self._ready and not self.pause.opened: self.cancel_loading()

    def slime_touch_click(self, x, y):
        """(window/slime_touch.py, earlier in the Game's classes: the boxes of DQ V's Slime Touch) A window without it: no box."""
        return False

    def slime_touch_keys(self, p, decl):
        """(window/slime_touch.py) A window without it: the keys are left as they are."""
        return p, decl

    def curling_press(self, x, y):
        """(window/slime_curling.py) A window without it: the click is not the curling's."""
        return False

    def curling_release(self):
        """(window/slime_curling.py)"""
        return False

    def sugoroku_click(self, x, y):
        """(window/sugoroku.py) A window without it: the click is not the board's."""
        return False

    def sugoroku_held(self, p):
        """(window/sugoroku.py)"""
        return p

    def _esc_back(self):
        """Esc serves as the Cancel key when a game window (menu, map) is open on the field: otherwise it opens the Esc menu, only if the player moves freely."""
        eng = self.engine
        if eng.state.part == ENGINE.PART_INITIAL: return self.name_entry.active or self.license_panel is not None or (not eng.msg_visible and eng.active_menu() is not None)           # start menu: Esc = Cancel (back from the root = quit)
        if eng.state.part in ENGINE.MINIGAME_PARTS:          # casino, bestiary: Esc = back button of the open window (gamepad 0x20); reels spinning, message, cards being dealt: no back, the Esc menu opens
            p = eng.active_menu()
            return p is not None and not eng.msg_visible and self._can_go_back(p)
        if eng.state.part not in (ENGINE.PART_TOWN, ENGINE.PART_FIELD) or eng.state.effect is not None or getattr(eng.state, 'screen_effect', None) is not None: return False
        return bool(self.map_open) or (not eng.msg_visible and eng.active_menu() is not None)

    def _can_go_back(self, port):
        """Does a visible game window currently have a Cancel / Back button (gamepad key 0x20)? (casino, bestiary: Esc presses it; otherwise it opens the Esc menu)"""
        if not self.ui_g: return False
        for q in [port] + [q for q, fq in self.engine.ui.f.items() if q > 1 and fq['visible']]:
            wnd = self.ui_g.f.get(q)
            if wnd and any(b[1] & 0x20 for b in wnd.buttons()): return True
        return False

    def _button_under(self, port, x, y):
        """Button of the `port` window under the pointer (frontmost first): (number, gamepad, depth, element identifier) or None."""
        h = [(pr, b, pad, nid) for b, pad, x0, y0, x1, y1, pr, nid in self._zones(port) if x0 <= x <= x1 and y0 <= y <= y1]
        if not h: return None
        pr, b, pad, nid = max(h, key=lambda t: t[0])
        return b, pad, pr, nid

    def on_mouse_press(self, x, y, button, modifiers):
        """Mouse = mobile touch: press on a button of the active menu (MenuPortItem.Update, PUSH_STATE_BEGIN); right click = Cancel."""
        if not self._ready: self._logo_touched = True; return        # (during the logos, the engine is still loading: `self.engine` does not exist)
        M = pyglet.window.mouse; eng = self.engine
        self.last_device = 'mouse'; self.mouse_guard.click(); self.mouse = (x, y)             # a click always acts, immediately: the mouse takes control
        if button == M.MIDDLE: self._wheel_press(x, y); return                                           # middle button: the camera only (never a menu, message or button click)
        if self.pause.opened: self.pause.click(x, y, button); return
        if getattr(self, '_replay', None) is not None: self.replay_press(x, y, button); return
        if self.arena_back_press(x, y, button): return                                                     # monster arena: back button (leaves the fight)
        if self._name_entry_in_progress(): self.name_entry.click(x, y, button); return
        if self.port_screen is not None: self.port_screen.click(x, y, button); return
        if self.license_panel is not None: self._license_click(x, y, button); return
        if self.title_screen is not None and button == M.LEFT: self.click_a = True; return                 # title: a touch anywhere confirms (the gamepad receives A)
        if button == M.RIGHT: self.click_b = True; return
        if button == M.LEFT and self.slime_touch_click(x, y): return                                    # DQ V's Slime Touch: a box of the board (otherwise the click goes to the game's windows)
        if button == M.LEFT and self.curling_press(x, y): return                                        # DQ VI's slime curling: the finger on the touch screen (until the release)
        if button == M.LEFT and self.sugoroku_click(x, y): return                                       # DQ V's T'n'T: an arrow of the board (its direction, as if held)
        if button == M.LEFT:
            vis = self.hud.visible_ones()
            if vis:
                n = self.hud.press(x, y, vis)                                                            # on-screen buttons (Carte, Commandes, Discussion) and cross
                if n == HM.BAR: self._bar_press(x, y); return                                           # camera rotation bar: nothing else happens (no sound effect, no action key)
                if n: n != HM.DPAD and self.sound.play_sfx(SFX_BUTTON); return
        if button == M.LEFT and self._terrain_free(): self.terrain_press = (time.monotonic(), x, y); return          # free field: a left CLICK anywhere = action key, like Space; given on release, and only for a short press
        if button != M.LEFT or not self.ui_render: return
        cp = self._map_ports()
        if 22 in cp:                                                               # map window buttons (no number): back (gamepad key 0x400, closes the map) and mode (gamepad up, changes view), active on release
            h = self._button_under(cp[22], x, y)
            if h and h[0] == -1 and h[1] in (0x400, 2): self.press_state = (cp[22], h[0], h[1]); return                      # (back and mode: gamepad 0x400 and 2, silent as in MenuPortItem.Update)
        key_list = [(h[2], -i, port, h) for i, port in enumerate(eng.input_menus()) for h in [self._button_under(port, x, y)] if h]      # all the menus that wait for input: the frontmost button, then the most recent window
        if key_list:
            _, _, port, h = max(key_list, key=lambda t: t[:2]); self.press_state = (port, h[0], h[1]); self.press_node = h[3]; button_sfx(h[1]) and self.sound.play_sfx(SFX_BUTTON); return
        if eng.msg_visible: self.click_a = True                                      # touching the message = next page

    def _section_last(self, eng):
        """After ▲ of the party list (camera key back from the first member shown): once the list shows the previous page, its last member is touched; given up after
        SECTION_LAST_STEPS engine steps."""
        q, lines, until = self.section_last; f = eng.ui.f.get(q)
        if f is None or eng.frames > until: self.section_last = None; return
        if self._menu_lines(f) == lines: return
        self.section_last = None; last = self._character_list_members(q)
        if last: eng.send_menu(q, 1, last[-1], 0x10); self.menu_to_release = q; self.sound.play_sfx(SFX_BUTTON)

    def _direct_button(self, port, pad):
        """The game key of the button of gamepad `pad` in the window of `port` if it is one of the direct buttons (DIRECT_BUTTONS), otherwise 0. The button's gamepad can carry other bits
        than the direct key (DQ VI's bestiary "Action"): the key alone goes to the game."""
        f = self.engine.ui.f.get(port); name_str = self._type_name(f['type']) if f and f.get('type') is not None else None
        return next((g for n, g in DIRECT_BUTTONS if name_str and name_str.startswith(n) and pad & g == g), 0)

    def _close_map(self, game_map):
        """The map's back button (0x400 added to the game's keys). DQ VI's maps (fld::FieldImageMap::execute, state 7: the world map and the town plan with its shop list) only close
        when the plan's item (type 25) answers 5 to ExecInput2, which the 0x400 key never gives in the port: the plan's port receives gamepad 2 instead (the answer 5, as checked in
        the engine); everywhere else, the key."""
        if GM.profile().key == 'dq6' and 25 in game_map:
            self.engine.send_menu(game_map[25], 1, -1, 2); self.menu_to_release = game_map[25]
        else: self.hud_decl = BTN_CHAT

    def on_mouse_motion(self, x, y, dx, dy):
        """Mouse hover: the button under the pointer is selected as with the arrows. Touch menu (local cursor): immediate; engine-cursor menu:
        the cursor is brought to the button by direction presses, one frame in three (see _step_toward_hover)."""
        if not self._ready: return
        self.mouse = (x, y)
        if self.bar_gesture.in_progress: self.bar_gesture.cancel()                 # (same for the rotation bar)
        if self.wheel.in_progress: self.wheel.cancel()                  # (a movement with no button pressed: the middle button's release went unnoticed, outside the window or during a focus loss; the gesture is abandoned)
        if not self.mouse_guard.has_moved((x, y)): return                # simple tremor after keyboard use: neither the mode nor the selection changes (cumulative distance since the last key, see inputs.MouseGuard)
        self.last_device = 'mouse'
        if self.pause.opened: self.pause.hovered(x, y); return
        if self._name_entry_in_progress(): self.name_entry.hovered(x, y); return
        if self.port_screen is not None: self.port_screen.hover(x, y); return
        if self.license_panel is not None: self.license_panel.hovered = self.license_panel.zone(x, y); return
        if not self.ui_render or self.press_state: return
        eng = self.engine
        key_list = [(h[2], -i, port, h) for i, port in enumerate(eng.input_menus()) for h in [self._button_under(port, x, y)] if h and (h[0] >= 0 or self._is_button(port, h[3]))]
        if not key_list: self.hovered = None; return                  # outside a button: the last hovered element alone stays highlighted (the engine's cursor is there too); cleared by on_mouse_leave or a key
        _, _, port, h = max(key_list, key=lambda t: t[:2]); f = eng.ui.f[port]; self.hover_node = (port, h[3])
        if h[0] < 0: self.hovered = None                              # button with no number (back, page arrows): only the element lights up, the menu's cursor does not move
        elif f.get('grid'): self.hovered = [port, h[0], 0] if f['cur'] != h[0] else None
        else:
            targets = self._cursor_targets(self.ui_g.f.get(port)); k = (CURSOR_CANCEL, h[3]) if (CURSOR_CANCEL, h[3]) in targets else ((CURSOR_ROW, h[0]) if (CURSOR_ROW, h[0]) in targets else h[0])          # ("Rien" shares its number with "Acheter": the cursor goes to the element)
            if self.cur_local.get((port, f['order'])) != k: self.cur_local[(port, f['order'])] = k; self.hovered = None

    def _is_button(self, port, nid):
        """True if the element has a button drawing (plate, part 7) or an arrow drawing (part 10): the window frame that covers the screen (gamepad 128) is not one."""
        node = self.ui_g.f[port].N.get(nid) if self.ui_g and port in self.ui_g.f else None
        return bool(node) and any(m.get('part') in (7, 10) for m in node['mats'])

    def on_mouse_leave(self, x, y):
        self.hover_node = None
        if self.pause.opened: self.pause.output()                       # Esc menu: the pointer has left the window, nothing is hovered any more (the plates light up again)

    def _step_toward_hover(self):
        """A direction press toward the hovered button (menus whose cursor is in the engine), as long as the cursor is not there (at most 24 presses)."""
        sv = self.hovered; eng = self.engine
        if not sv: return
        port, b, n = sv; f = eng.ui.f.get(port)
        if f is None or not f.get('grid') or port != eng.active_menu() or f['cur'] == b or n >= 24: self.hovered = None; return
        if self.n % 3: return
        cols = max(1, f['grid'][0]); cc, cr = f['cur'] % cols, f['cur'] // cols; tc, tr = b % cols, b // cols
        D = ENGINE.MENU_DIRS; pad = (D[ENGINE.DOWN] if tr > cr else D[ENGINE.UP]) if tr != cr else (D[ENGINE.RIGHT] if tc > cc else D[ENGINE.LEFT])       # (the menu's own direction keys: they differ between DQ IV and DQ V / VI)
        eng.send_menu(port, 1, -1, pad); self.menu_to_release = port; sv[2] += 1

    def on_mouse_release(self, x, y, button, modifiers):
        """Release on the same button (PUSH_STATE_FINISH): MenuPort.Send([1, button number, button's gamepad key])."""
        if not self._ready: return
        if button == pyglet.window.mouse.MIDDLE: self._wheel_release(); return
        if button == pyglet.window.mouse.LEFT and self.bar_gesture.in_progress: self._bar_release(); return
        if button == pyglet.window.mouse.LEFT and self.curling_release(): return                               # DQ VI's slime curling: the finger leaves the screen
        if self.pause.opened: self.pause.release(x, y, button); return
        if getattr(self, '_replay', None) is not None: self.replay_release(x, y, button); return
        if self.arena_back_release(x, y, button): return
        if self.license_panel is not None: self.license_panel.dragging = None; return
        tp, self.terrain_press = getattr(self, 'terrain_press', None), None
        if tp is not None and button == pyglet.window.mouse.LEFT:
            if time.monotonic() - tp[0] <= EN.system_click()[0] and not EN.is_drag(x - tp[1], y - tp[2]) and self._terrain_free(): self.click_a = True          # (Windows' click time and drag rectangle)
            return
        if button == pyglet.window.mouse.LEFT and self.hud.dpad_on:
            if self.hud.dpad_release() and self._terrain_free(): self.click_a = True              # touch on the central circle of the cross (without dragging) = action key, as on mobile
            return
        if button == pyglet.window.mouse.LEFT and self.hud.press_state:
            n = self.hud.let_go(x, y, self.hud.visible_ones())
            if n == HM.BATTLE: self.change_battle_speed()                      # battle screen speed button: passes nothing to the engine
            elif n: self.hud_decl = HM.DECL[n]
            return
        if button != pyglet.window.mouse.LEFT or not self.press_state: return
        port, b, pad = self.press_state; self.press_state = None
        h = self._button_under(port, x, y)
        if b == -1 and pad == 0x400:                       # back button: MenuPortItem.Update adds 0x400 to the game's keys (the engine closes the map) instead of sending a message to the port
            if h and h[0] == b: self._close_map(self._map_ports())
            return
        if b == -1 and pad == 2 and port == self._map_ports().get(22):          # "map mode" button of the map's window (type 22, icon 19 / 20 / 2 sent by the engine): gamepad up = changes view (handled with the Carte key);
            if h and h[0] == b and h[1] == pad: self.map_toggle = True            # (only there: the ▼ of the party list on the left of the field menu has the same number and gamepad, it went nowhere)
            return
        if h and h[0] == b and b == BTN_SAVES and self._is_start_menu(port): self._open_saves(); return                # "Saves" button of the start menu (in place of "Sauv. cloud"): specific to the port
        if b == BTN_LANGUAGE and self._is_start_menu(port):                       # language plate of the start menu: specific to the port, never sent to the engine
            if h and h[0] == b: self.open_language_screen()
            return
        direct = self._direct_button(port, pad) if h and h[0] == b and port in self.engine.input_menus() else 0
        if direct:
            self.direct_keys |= direct; return                                    # MenuPortItem.Update: a button marked direct (MenuParts + 0x4d) adds its gamepad key to the game's keys (TouchPad.m_externalToggle) instead of sending a message
        if h and h[0] == b and port in self.engine.input_menus():
            self.engine.send_menu(port, 1, b, pad); self.menu_to_release = port
            f = self.engine.ui.f.get(port)
            if f: f['cur'] = b

    def pad(self):
        """Game gamepad from the configured keys: directions, A (action / confirm), B (cancel). On the field, the camera keys are the camera's (game camera buttons, see _bits_camera) and the directions
        are the screen's: the game rotates them according to its camera, as on mobile; in a menu, a message or a battle, the camera keys are left / right."""
        p = 0; free = self._terrain_free()
        for action, bit in (('up', ENGINE.UP), ('down', ENGINE.DOWN), ('left', ENGINE.LEFT), ('right', ENGINE.RIGHT)):
            if self._held(action): p |= bit
        if not free:
            for action, bit in (('cam_left', ENGINE.LEFT), ('cam_right', ENGINE.RIGHT)):
                if self._held(action): p |= bit
        else:
            p |= self.hud.dpad_bits()                                                                        # on-screen cross held with the mouse: one more direction (relative to the screen, like the keys)
        if self._held('A'): p |= ENGINE.A
        if self._held('B'): p |= ENGINE.B
        return p

    def _hud_sfx(self, name_str):
        """Commandes / Discussion / Carte key on the field: sound effect 300 of the on-screen button, in the same situations as with a finger (is the button allowed by the engine, whether displayed or not)."""
        if self.hud.allowed(name_str): self.sound.play_sfx(SFX_BUTTON)

    def _edges(self):
        """Presses (rising edge) of X, Start, L and R since the last engine frame."""
        r = {}; K = pyglet.window.key; k = self.key_list
        for a in EDGE_ACTIONS:
            if a == 'chat': v = any(k[c] and c not in self.swallowed for c in self.reg.codes(a) if c not in (K.LALT, K.RALT))        # (Alt: on release, see alt_tap: Alt + Enter must not start the party chat)
            else: v = self._held(a)
            r[a] = v and not self.prev_action.get(a, False); self.prev_action[a] = v
        if self.alt_tap: r['chat'] = True; self.alt_tap = False
        if r['chat'] and os.environ.get('DQ_DEBUG'): print('FRONT party chat n=%d' % self.n, flush=True)
        if self.swallowed: self.swallowed = {c for c in self.swallowed if k[c]}          # (a swallowed key released outside the window: the list empties)
        if self.alt_cand and not (k[K.LALT] or k[K.RALT]): self.alt_cand = False
        return r

    def _entries(self, p):
        """Keys and clicks of this engine step -> (p, decl): held buttons and just-pressed buttons, as the engine wants them (menu that has the input: everything goes through `_menu_input`, p = decl = 0).
        Only in "real" steps: an extra step of an accelerated battle (`tick(mute=True)`) reads none, so that the key rate (arrow repeat, first press) does not change."""
        eng = self.engine; terrain = False
        if self.click_a: p |= ENGINE.A; self.click_a = False                  # mouse clicks: next page / cancel
        if self.click_b: p |= ENGINE.B; self.click_b = False
        decl = p & ~self.pad_prev                      # buttons that have just been pressed
        pressed = decl                                 # (before the repeat of held directions: a camera key changes the menu's interface once per press)
        fr = self._edges()
        p, decl = self.slime_touch_keys(p, decl)            # DQ V's Slime Touch: A with a direction held hits a box (that A is not given to the game)
        if self.engine.msg_visible and fr['menu']: p |= ENGINE.A; decl |= ENGINE.A        # Enter and Space also turn the message pages
        for bit in (ENGINE.UP, ENGINE.DOWN, ENGINE.LEFT, ENGINE.RIGHT):      # repeat of held directions (menu cursors)
            if p & bit:
                self.rep[bit] = self.rep.get(bit, 0) + 1
                if self.rep[bit] > 12 and (self.rep[bit] - 12) % 4 == 0: decl |= bit
            else: self.rep[bit] = 0
        self.pad_prev = p
        eng = self.engine
        if self.menu_to_release is not None:                 # end of the OK / Cancel press sent to the menu
            eng.send_menu(self.menu_to_release, 1, -1, 0); self.menu_to_release = None
        elif getattr(self, 'section_last', None) is not None: self._section_last(eng)          # (the test doubles of the window have no such attribute)
        if (pressed & ENGINE.A or fr['menu']) and getattr(eng, 'slot_paying', bool)():          # slot machine paying out its tokens one by one: A (key, click, Enter, Space) is the mobile game's screen tap, which pays the rest at once
            self._camera_forget(); return 0, BTN_TALK                         # (MaterielMenu_SLOT::showEffect reads the tap in g_Pad + 2; the menu window would have taken A)
        replay = getattr(self, '_replay', None) is not None
        if replay: self.replay_feed()                        # battle replay: the choices come from the recording, the player only turns the messages
        game_map = self._map_ports() if not eng.msg_visible else {}
        shown = {t for t in (24, 25, 26) if t in game_map and eng.ui.f.get(game_map[t], {}).get('visible')}          # (the views of the map on screen: town plan / shop list, world large / small map)
        if bool(game_map) != self.map_open: self.map_open = bool(game_map); self.map_view = 1; self.map_first = shown           # map open: first version
        entries = not ENGINE.ENGINE_V2 and bool(game_map) and 24 in game_map and 25 in game_map and eng.ui.f.get(game_map[24], {}).get('cells') and eng.shops()[1] > 0          # town with shops: the map has a 2nd view, the shop list (type 24)
        self.map_two_views = (25 in game_map and 26 in game_map) or bool(entries)
        if game_map:
            # Carte key: 1st time the first version (in town: the plan), 2nd time the other version (in town: the shop list; gamepad up on the plan window: FieldImageMap::execute),
            # 3rd time closes (back button: 0x400, like Unity's MenuPortItem.Update); Cancel or the back button also close. The cursor and the arrows are not used here
            # (except the arrows of the shop list: previous / next shop).
            do_toggle, self.map_toggle = self.map_toggle, False
            if ENGINE.ENGINE_V2:                                     # DQ V / VI (TownImageMap / FieldImageMap::execute): the view changes when the plan (type 25) or the "map mode" button of type 22 is decided
                second = bool(shown) and shown != getattr(self, 'map_first', shown)           # (gamepad 1 on release, as the finger), not by the up key: town = plan <-> shop list, world = DQ V large map <-> small map, DQ VI small <-> large
                two = 24 in game_map or (25 in game_map and 26 in game_map)
                if (fr['map'] and not second or do_toggle) and two and 22 in game_map:
                    eng.send_menu(game_map[22], 1, -1, MAP_MODE_V2); self.menu_to_release = game_map[22]; fr['map'] = False
                elif fr['map'] or (decl & ENGINE.B): self._close_map(game_map)
                elif second and 24 in game_map and decl & (ENGINE.LEFT | ENGINE.RIGHT): eng.send_menu(game_map[24], 1, -1, 8 if decl & ENGINE.RIGHT else 4); self.menu_to_release = game_map[24]          # list arrows: the left one (x -190) sends 4, the right one 8
            elif entries:                                              # town: M = plan, then shop list, then closed; the "map mode" button alternates plan / list
                in_entries = bool(eng.ui.f.get(game_map[24], {}).get('visible'))
                if do_toggle: eng.send_menu(game_map[25], 1, -1, 2); self.menu_to_release = game_map[25]
                elif fr['map'] and not in_entries: eng.send_menu(game_map[25], 1, -1, 2); self.menu_to_release = game_map[25]
                elif fr['map'] or (decl & ENGINE.B): self.hud_decl = BTN_CHAT
                elif in_entries and decl & (ENGINE.LEFT | ENGINE.RIGHT): eng.send_menu(game_map[24], 1, -1, 4 if decl & ENGINE.RIGHT else 8); self.menu_to_release = game_map[24]         # list arrows: next / previous shop
            elif (fr['map'] and self.map_view == 1 or do_toggle) and self.map_two_views:           # (the screen's "map mode" button changes view in both directions)
                eng.send_menu(game_map[25], 1, -1, 2); self.menu_to_release = game_map[25]; self.map_view = 3 - self.map_view; fr['map'] = False
            elif fr['map'] or (decl & ENGINE.B): self.hud_decl = BTN_CHAT
        port = None if game_map or self.port_screen is not None else eng.active_menu()
        port = EN.menu_under_message(eng, port)              # message displayed and the stack menu is not waiting for input: the keys go to the window that is waiting (Yes / No of the quit-or-double), otherwise to the message
        if port is not None: port = self._keyboard_port(port)      # memory criterion only: the message first
        if port is not None and self.menu_to_release is None and not replay: self._step_toward_hover()
        if port is not None and replay: p = decl = 0                  # (replay: the menu is answered by the recording, see replay_feed)
        elif port is not None:                               # a menu waits for the keys: they are sent to it, not to the field
            f_port = eng.ui.f.get(port)
            cam = sum(bit for a, m, bit in (('cam_left', 'left', ENGINE.LEFT), ('cam_right', 'right', ENGINE.RIGHT)) if self._held(a) and not self._held(m))
            if decl & cam:                                   # camera keys: previous / next interface of the menu (character, page), looping; a menu without one keeps them as directions
                target = self._section_target(port, -1 if decl & cam & ENGINE.LEFT else 1)
                if target is not None:
                    decl &= ~cam
                    if pressed & cam and self.menu_to_release is None:
                        eng.send_menu(target[0], 1, target[1], target[2]); self.menu_to_release = target[0]
                        if target[2] & 0x10: self.sound.play_sfx(SFX_BUTTON); self.section_focus = (target[0], port)          # (a character is a button the finger touches: sound effect 300, like on mobile; the page arrows are silent)
                        elif target[1] == -1 and target[2] == 1 and self._is_character_list(target[0]):          # ▲ of the party: the game chooses the first one of the page it shows, the previous one is its last
                            self.section_last = (target[0], self._menu_lines(eng.ui.f[target[0]]), eng.frames + SECTION_LAST_STEPS)
            self._menu_input(port, decl | (ENGINE.A if fr['menu'] else 0) | (ENGINE.B if fr['map'] and f_port and f_port.get('type') == 25 else 0) | (ENGINE.LEFT if fr['L'] else 0) | (ENGINE.RIGHT if fr['R'] else 0), (1 if fr['L'] else 0) | (2 if fr['R'] else 0))        # L / R = left / right (previous / next page of single-column menus)
            p = decl = 0
        elif self.title_screen is not None:                       # title screen: only A confirms (Space, Enter or touch); no walking, no camera
            p = decl = ENGINE.A if (decl & ENGINE.A or fr['menu']) else 0
        elif (self.name_entry.active and not eng.msg_visible) or self.license_panel is not None or self.port_screen is not None: p = decl = 0           # name entry, Licence screen: everything goes through the port's keyboard
        elif not eng.msg_visible:
            # on the field: A = talk / examine, Cancel, X or Start = open the menu (the engine wants these buttons alone)
            hd, self.hud_decl = self.hud_decl, 0                # (the engine wants these buttons alone) A, then on-screen buttons, then Commandes / Discussion / Carte keys
            mode = self.hud.mode_shown()                        # (the touch pad's input mode: the keys of the buttons the mobile game hides in it do nothing, see core/pad_modes.py)
            if decl & ENGINE.A:
                decl = BTN_TALK
            elif hd: decl = hd
            elif fr['menu'] and mode['menu']: decl = BTN_MENU; self._hud_sfx('menu')
            elif fr['chat'] and mode['chat']: decl = BTN_CHAT; self._hud_sfx('chat')
            elif fr['map'] and mode['map']: decl = BTN_MAP; self._hud_sfx('map')
            elif fr.get('extra') and self.hud.allowed(HM.EXTRA): decl = HM.DECL[HM.EXTRA]; self._hud_sfx(HM.EXTRA)          # DQ VI's vehicle command (the extra button's key 0x2000), only in the modes that show that button
            p &= ~(ENGINE.A | ENGINE.B)
            terrain = True; p |= self._bits_camera()                          # (on the field, bits 4 and 5 of the held keys are the game's camera buttons: left, right, both = return to north)
        elif decl & ENGINE.A: p, decl = EN.touch_for_engine(p, decl, ENGINE.A, ENGINE.B, BTN_TALK, eng.awaits_touch())          # open message: A turns the page (0x10); the experience gift menu, in its last state, waits for the screen touch (0x100 alone)
        if not terrain: self._camera_forget()          # (no menu, no message, no title: a rotation requested with the mouse or a pending return to north are not triggered afterwards)
        if pressed & ENGINE.B and self.arena_back_shown(): self.arena_pending = True          # monster arena: B key and right click = the back button
        if getattr(self, 'direct_keys', 0): decl |= self.direct_keys; self.direct_keys = 0          # direct buttons (DIRECT_BUTTONS): their gamepad key goes with the game's keys of this step
        return p, self.arena_abort_decl(decl)
