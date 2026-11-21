"""16/9 PC window: runs the game engine and draws what it asks for (scenery, sprites, camera).
The `Game` class is the core (startup, loop, scene drawing); the rest is arranged by theme in the neighboring modules (see docs/code_plan.md)."""


import collections, math, os, sys, threading, time, traceback
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
import numpy as np
import pyglet
from pyglet import gl
from pyglet.graphics.shader import Shader
from pyglet.graphics.shader import ShaderProgram
from dq456_pc.render import fast_vertices as FV
FV.install()          # (vertex data copied into pyglet's buffers in one block: before the first vertex list)
from dq456_pc import paths as CH
from dq456_pc.persistence import savestates as SS
from dq456_pc import language as LG
from dq456_pc import sound as SOUND
from dq456_pc.language import N_, tr
from dq456_pc.battle import battle_speed as VC
from dq456_pc.window import inputs as EN
from dq456_pc.menus import pre_game as AP
from dq456_pc.menus import hud_mobile as HM
from dq456_pc.menus import pause as PA
from dq456_pc.menus import name_entry as SN
from dq456_pc.persistence import game_rules as GR
from dq456_pc.randomizer import items as RI
from dq456_pc.randomizer import encounters as RN
from dq456_pc.menus import ui_menu as UI
from dq456_pc.menus import sheets as SH
from dq456_pc.world import data as DO
from dq456_pc.world import hd as HD
from dq456_pc.world import map_tint as TC
from dq456_pc.core import history as HI
from dq456_pc.core import engine as ENGINE
from dq456_pc.core import lying_pose as PC
from dq456_pc import games as GM
from dq456_pc.core import load_tracking as SV
from dq456_pc.debug import service as DBG
from dq456_pc.persistence import settings as RG
from dq456_pc.render import postfx as PF
from dq456_pc.window.constants import (EDGE_ACTIONS, EXTENDED_SKIES, FAST_STEP_BUDGET, CAM_JUMP_ROT, CAM_JUMP_TOWN, TITLE_MAP, CB_MIRROR_X, CH_PX, MODE_PP, ENGINE_STEP,
    COMMON_SFX, SPRITE_SCALE, SPRITE_H, SPRITE_L, BTN_TALK)
from dq456_pc.window.shaders import FRAG, FRAG_EFFECT, GEOMETRY, VERTEX, VERTEX_EFFECT
from dq456_pc.window.helpers import adjust_size, camera_free, background_gradient, field_depth_rank, sheet_id, shadow_offset, transitions_dwm, whole_pixels
from dq456_pc.window.menu_display import MenusMixin
from dq456_pc.window.camera import CameraMixin
from dq456_pc.window.field import FieldMixin
from dq456_pc.window.field_followers import FieldFollowers
from dq456_pc.window.keyboard_mouse import KeyboardMouseMixin
from dq456_pc.window.capture_effects import CaptureEffectsMixin
from dq456_pc.window.backdrop import BackdropMixin
from dq456_pc.window.motion_blur import MotionBlurMixin
from dq456_pc.window.snapshots import SnapshotsMixin
from dq456_pc.window.options import OptionsMixin
from dq456_pc.window.full_screen import FullScreenMixin
from dq456_pc.window.gamepad import Gamepad
from dq456_pc.window import battle_replay as RP
from dq456_pc.window.battle_replay import ReplayMixin
from dq456_pc.window.arena_abort import ARENA_ABORT, ArenaAbortMixin
from dq456_pc.window.battle_scene import BattleMixin
from dq456_pc.window.title_screen import TitleMixin
from dq456_pc.window.staffroll import StaffrollMixin
from dq456_pc.window.slime_touch import SlimeTouchMixin
from dq456_pc.window.slime_curling import SlimeCurlingMixin
from dq456_pc.window.sugoroku import SugorokuMixin
from dq456_pc.window.spotlight import SpotlightMixin
from dq456_pc.window.loading_bar import ENGINE_SHARE, LoadingBarMixin, creep
from dq456_pc.window.constants import *      # noqa: these names stay readable as `game.NAME` (tests, tools)
from dq456_pc.window.shaders import *       # noqa
from dq456_pc.window.helpers import *         # noqa


def note_error(text):
    """Appends `text` (lines ending with a newline) to the window's error log (window_errors.log, logs folder); never raises: -> True if written."""
    try:
        path = CH.FOLDERS.errors_logfile(); os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f: f.write(text)
        return True
    except Exception as ex:
        print('Error not written to the log (%r): %s' % (ex, text.strip()), flush=True); return False


def phone_clip(frame, width, height, viewport):
    """(x, y, w, h) in viewport pixels of the phone's screen (450 x 800 interface units centered on `frame` = (cx, cy, s), window pixels), cut to the viewport."""
    cx, cy, s = frame; kx, ky = viewport[0] / max(1.0, float(width)), viewport[1] / max(1.0, float(height))
    x0, x1 = max(0, int(round((cx - 225.0 * s) * kx))), min(int(viewport[0]), int(round((cx + 225.0 * s) * kx)))
    y0, y1 = max(0, int(round((cy - 400.0 * s) * ky))), min(int(viewport[1]), int(round((cy + 400.0 * s) * ky)))
    return x0, y0, max(0, x1 - x0), max(0, y1 - y0)


class Game(FullScreenMixin, CameraMixin, FieldMixin, BattleMixin, TitleMixin, StaffrollMixin, SlimeTouchMixin, SlimeCurlingMixin, SugorokuMixin, SpotlightMixin, MenusMixin, KeyboardMouseMixin, SnapshotsMixin, OptionsMixin, CaptureEffectsMixin, BackdropMixin, MotionBlurMixin, ReplayMixin, ArenaAbortMixin, LoadingBarMixin, pyglet.window.Window):
    def __init__(self, args, reg=None):
        SV.note('startup', width_px=args.width_px, height_px=args.height_px, full_screen=getattr(args, 'full_screen', 0), shot=bool(args.shot))
        self._logo_logged = False
        self.swallowed = set(); self.alt_cand = False; self.alt_tap = False; self.desktop = None; self._toggling = False       # F11 / Alt + Enter: swallowed keys (neither action nor confirmation), whether Alt can still count as "party chat" (press alone), Alt release alone to handle; desktop size; window / full screen switch in progress
        self._ready = False                      # events (mouse...) received while the window is being created are ignored
        self._logo_touched = False; self._closed = False      # entry logos: a press skips them; window closed while they are displayed
        self.ext_map = []; self.ext_sky = None; self._M_town = None; self._town_aspect = 16 / 9; self.name_entry = SN.NameEntry(self); self.entry_port = 15; self.license_panel = None; self.port_screen = None; self.initial_status = None; self.last_device = 'keyboard'; self.title_screen = None; self.title_n = 0; self.pre_game_files = {}      # title screen: state of "Touchez l'ecran" (TapToStart) as long as port 12 holds the type 73 window
        self.reg = reg or RG.Settings(None)
        self.pause = PA.Pause(self); self.history = HI.History(getattr(args, 'history', None)); self.history.snapshotter = self._complete_savestate          # history: latest dialogues and battles and the favorites, in two files next to the viewer (see history.py)
        if not args.shot: args.width_px, args.height_px = adjust_size(args.width_px, args.height_px)       # (test captures keep the requested size)
        cfg = gl.Config(depth_size=24, double_buffer=True, major_version=3, minor_version=3)       # (MSAA antialiasing is no longer requested from the window: the scene is drawn off-screen, see postfx.py; adjustable on the fly)
        self.win_mode = self._create_window(args, cfg, RG.valid_window_mode(getattr(args, 'full_screen', 0)))        # stored mode: the window is created directly in this mode, logos included: no window that appears then grows, no later switch
        self.window_size = (args.width_px, args.height_px)         # window size to restore when leaving a full screen
        self.post = PF.PostFX(self, log=lambda *a: print(*a, flush=True))
        self.args = args
        self.game_rules = GR.GameRules(getattr(args, 'saves_write', None) or '.', self.reg)          # rules and settings of each game (persistence/game_rules.py)
        self.game_language = getattr(args, 'game_language', None) or LG.current_language()          # language of the loaded APKs: read at startup, it does not change until the next one
        self._log_window('creation')
        self._wait('Loading the engine...', 0.02)
        self.mode = RG.valid_sprites_mode(args.hd); self.smoothing = not os.environ.get('DQ_NO_SMOOTHING'); self.aniso = self._anisotropy()          # texture smoothing: always on (no setting); DQ_NO_SMOOTHING=1: (debugging) nearest neighbor
        self.pool = ThreadPoolExecutor(max_workers=1); self.hd_jobs = {}; self.scenery_hd = {}; self.scenery_arr = {}; self.map_src = None; self.sprite_vl = {}; self.icon_vl = {}; self.icon_tex = {}; self._menu_tex = None; self._linked_domain = None; self.plans = {}
        self.field_key = None; self.field_tex = None; self.field_tex_icon = None; self.field_nearest = None; self.field_vl = None; self.field_base = None; self.field_icon = None
        self.pattern_n = 0; self.acc_pattern = np.zeros(5, np.int64); self.field_g = None; self.field_pattern = None; self.field_acc_seen = None       # animated water of the world map
        self._field_view = None       # world map: visible area (cx, cz, half-width, half-height in tiles)
        self.cam_direction = (0, False); self.cam_recenter = False; self.cam_two = False; self.cam_request = 0.0      # camera: game camera buttons sent at the last step (direction, with the mouse?), return to north requested, the two arrows held, rotation requested with the mouse not yet done (degrees), rest of the speed (see camera.py)
        self.mouse = (0, 0); self.mouse_freeze = None
        self.mouse_guard = EN.MouseGuard(); self.wheel = EN.WheelGesture(); self.bar_gesture = EN.WheelGesture(); self.bar_center = False; self.bar_end = 0          # inputs: who has control (keyboard or mouse, see inputs.py; replaces mouse_freeze); middle button gesture (click = recenter, drag = rotate the camera)
        self.field_followers = FieldFollowers()                                 # world map: the characters walking with the camera at a steady distance from it (field_followers.py)
        self.anchor_effect = {}; self._shadow_steps = {}; self._view_border = None; self._sprites_border = None          # town mesh effects: slot -> (character, depth ratio, angular offset); (position, right, up, forward) of the viewer's camera
        self.n_battle = 0; self.cb_tex = {}; self.cb_vl = {}
        self.ui_tf = {}; self.ui_k = 1.0; self.cur_local = {}; self.cursor_step = {}; self.press_state = None; self.press_node = None; self.hovered = None; self.hover_node = None; self.click_a = self.click_b = False; self.ui_dz = 0.0; self.ui_dzp = {}; self.ui_followers = set()       # mouse: button pressed (port, number, gamepad), clicks pending
        self.cb_mx = CB_MIRROR_X; self.cb_order = int(os.environ.get('DQ_ORDER', '0'))                 # battle: monster textures (m<id>) and meshes
        self.undo_state = None; self.toasts = []; self.quick_save = None; self.combo = [False, False]
        self.helptext = bool(self.reg['info']); self.helptext_width = 0.0; self.helptext_key = None; self.helptext_border = pyglet.shapes.Rectangle(0, 0, 1, 1, color=(0, 0, 0, 150))      # F1: information lines at the top right + translucent black frame
        self.t_startup = time.time()
        self.stats = True; self.spacings = collections.deque(maxlen=120); self.t_prev = None; self.dt_drawing = 0.0
        self.prog = ShaderProgram(Shader(VERTEX, 'vertex'), Shader(GEOMETRY, 'geometry'), Shader(FRAG, 'fragment'))
        self.program_effect = ShaderProgram(Shader(VERTEX_EFFECT, 'vertex'), Shader(FRAG_EFFECT, 'fragment'))
        self.program_gradient = None; self.gradient_vl = None       # gradient background of towns: program and full-screen quad, created on first need
        self.effect_tex = None; self.sim_effect = None; self.effect_vl = None; self.ref_effect = None; self.effect_tex_ready = False      # battle start effect: screen capture, simulation, vertices
        self.data = DO.Data(None, args.cache, log=self.loading_log(0.65))          # (the data pack, read once: the bar moves during the startup)
        self.backgrounds = TC.Backgrounds(self.data, log=lambda *a: print(*a, flush=True)); self.tz = TC.TzKeys(self.data, log=lambda *a: print(*a, flush=True)); self._gl_tint = None        # scenery tint (packet 33): "tz_" textures excluded; last value of the `tint` uniform
        self._start_engine(args)
        self._wait('Loading the save...', 0.55)
        self.data.apk = self.engine.apk_data
        if not args.slot and self.mode not in (0, MODE_PP): self._prepare_title_scenery()          # (the title comes after the opening credits: its enlarged scenery is ready before it)
        self.prep_stop = threading.Event(); self.prep_proc = None
        self.hud = HM.Buttons(self); self.hud_decl = 0; self.direct_keys = 0; self.map_view = 1; self.map_open = False; self.map_two_views = False; self.map_toggle = False; self.damage_count = []; self._tex_count = None
        if not args.no_preparation:           # extracts maps, sprites and monsters not yet in the cache in the background: no more reading of the big data packet in the middle of a game
            self._launch_preparation(args.cache)
        self.sound = SOUND.Sound(self.engine.apk_data, args.cache, log=self.loading_log(0.7), music_volume=args.music_volume, sfx_volume=args.sfx_volume, active=bool(self.reg['sound']) and not args.no_sound,
                           logfile=lambda t: note_error('--- %s: sound: %s ---\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), t)))
        self.sound.preload(COMMON_SFX); self.sound.preload_music()
        self._sound_opt = None; self._t_preview = 0.0; self.apply_mix(initial=True); self.apply_text(); self.apply_game(); self.engine.scroll_callback = self._adopt_scroll; self.apply_scroll()
        self.key_list = pyglet.window.key.KeyStateHandler(); self.push_handlers(self.key_list)
        self.gamepad = Gamepad(self)          # the controllers become pseudo keys
        self.map_name = None; self.scenery_background = None; self.map_vl = []; self.tex_gl = {}; self._tex_outlines = []; self._outline_jobs = {}; self.sprite_tex = {}; self.palettes = None; self.pam_gl = {}
        self.map_pos = {}; self.spans = {}; self.names = {}; self.objects = None; self.bounds_x = None
        self.fbx_cache = {}; self.fbx_player = {}           # animated 3D models (fbx.py: the title dragon): number -> {'status', 'm', ...} ; slot -> animation player
        self.tag_label = pyglet.text.Label('', x=10, y=self.height - 10, anchor_y='top', font_size=11, color=(255, 255, 255, 255), multiline=True, width=600)
        self.lbl_stats = pyglet.text.Label('', x=10, y=self.height - 50, anchor_y='top', font_size=10, color=(255, 230, 150, 255), multiline=True, width=600)
        self.lbl_hd = pyglet.text.Label('', x=10, y=self.height - 30, anchor_y='top', font_size=10, color=(200, 220, 255, 255), multiline=True, width=600)
        if args.slot:
            ok = self.engine.load_save(args.slot)
            print('save %d loaded: yes' % args.slot if ok else 'save %d loaded: NO (file data%d.dat not found or invalid in %s)' % (args.slot, args.slot, args.saves_read), flush=True)
            if ok:      # the engine is left to start the saved map before the first display
                for _ in range(6): self.engine.step(0, 0)
                self._wait('Loading the map...', 0.75)
                e = self.engine.state
                if e.game_map: self._load_map(e.game_map)      # heavy loading happens here, not during the game
                for sp in list(e.sprites.values()): self._sheet(sheet_id(sp, e.part == ENGINE.PART_FIELD), self.mode)
                e.sounds = [x for x in e.sounds if x[0] == 37][-1:]      # the loading sound effects are lost, the map music stays
        self.n = 0; self._reported_hooks = 0; self._heap_level = 0; self.dt_engine = 0.0; self.pad_prev = 0; self.rep = {}; self.prev_action = {}
        self.msg_key = None; self.msg_img = None; self.msg_h = 0
        self.lbl_menu = {}; self.menu_to_release = None; self.section_focus = None; self.section_last = None; self.scenery_ticks = None; self._scenery_acc = 0.0; self._replay_skipping = False; self._closing_game_menu = 0
        self.ui_render = None; self.ui_g = None; self.ui_msg = None; self.canvas_key = None
        self._init_ui()          # (simple menus only as a fallback, when the original layout is missing or unusable:; the test option --ui-simple was removed)
        self._preload_first_scene()
        self.show_loading(1.0); self.loading = False
        self._play_logos()                                                        # (the entry logos, once everything is loaded)
        # fixed-step loop: the engine advances exactly at 30 Hz (like FixedUpdate in the original) whatever the display rate, and the displayed frame is computed between two steps
        self.acc = 0.0; self.t_loop = None; self.alpha = 1.0; self.prev = None; self.step_times = collections.deque(maxlen=60)
        self.n_loop = 0; self.base_c = {}
        self.fixed_test = bool(args.shot) and not os.environ.get('DQ_REAL_TIME')         # (test) one step per displayed frame, no clock or interpolation; DQ_REAL_TIME=1: real clock (speed, running)
        pyglet.clock.schedule(self._guarded_loop)      # runs before the windows are drawn (pyglet.app.run(interval=0)); the error barrier is in _guarded_loop
        self._ready = True
        SV.note('constructor finished')

    # ---- window mode: 0 window, 1 full screen (borderless, at the desktop resolution: the monitor's display never changes)
    WINDOW_TITLE = N_('window.pc_port')          # (displayed by full_screen.py with tr, % the game's name)

    @staticmethod
    def _simulated_failure(mode):
        """(test) DQ_SIM_FAILURE=1 (or 0, or 1,0): creating / switching to this mode (1 full screen, 0 window) fails, to try the fallbacks."""
        if str(mode) in os.environ.get('DQ_SIM_FAILURE', '').split(','): raise RuntimeError('simulated failure (DQ_SIM_FAILURE)')

    def _init_ui(self):
        """Original menu windows (layout read from the game's Unity code); without the file cache/ui_layout.json the simple menus are kept. The game's sheets and fonts are
        extracted in any case: the field buttons, the D-pad, the bubbles and the Esc menu use them too."""
        a = self.args; lay = os.path.join(a.cache, 'ui_layout.json')
        resources = UI.prepare_resources(a.apk, a.cache, log=self.loading_log(0.95))
        if not os.path.exists(lay):
            print('Menus: original layout missing (%s) -> simple menus. The launcher builds it at the first start of each game (tools\\generate_ui.bat does it by hand).' % lay, flush=True); return
        if not resources: return
        try:
            from PIL import Image
            ml = UI.PageLayout(lay, os.path.join(a.cache, 'ui_frameData.bin'))
            sheets = {0: SH.rgba(a.cache, 'ui_MenuTexture.png'), 1: SH.rgba(a.cache, 'ui_Button.png')}          # (decoded once, shared with the field buttons, the D-pad, the bubbles, the damage digits and the Esc menu)
            if 'hyodai1' in self.pre_game_files: sheets['hyodai1'] = np.array(Image.open(self.pre_game_files['hyodai1']).convert('RGBA'))      # chapter title banner (type 12)
            self.ui_render = UI.Render(ml, sheets, os.path.join(a.cache, 'ui_font.otf')); self.ui_g = UI.Manager(ml); self.engine.ui.obs = self.ui_g; self.ui_g.proj = self._ui_monster_x; self.ui_render.picture_source = self.data.texture; self.ui_render.face_source = UI.face_loader(a.cache); self.ui_g.replay(self.engine.ui)
            if ml.message: self.ui_msg = UI.MessageWindow(ml)
            print(('Menus: original layout loaded (%d types, message window).' if self.ui_msg else 'Menus: original layout loaded (%d types).') % len(ml.types), flush=True)
        except Exception as e:
            print('Menus: original layout unusable (%r) -> simple menus.' % (e,), flush=True); self.ui_render = None; self.ui_g = None

    def _start_engine(self, args):
        """The engine loads in a thread, the title images are read meanwhile (once), behind the loading bar. The entry logos (Boot.Logo: SQUARE ENIX, ArtePiazza) are chosen here and
        played once everything is loaded (`_play_logos`): no black loading screen after them. No logos for a direct launch on a save (--slot N), for test captures (except --logos)
        or with --no-logos."""
        res = {}
        def load():
            try: res['m'] = ENGINE.Engine(args.apk, args.saves_read, args.saves_write)
            except BaseException as ex: res['e'] = ex
        thread = threading.Thread(target=load, daemon=True, name='engine'); thread.start()
        self.pre_game_files = AP.paths(args.cache)
        if len(self.pre_game_files) < len(AP.resources()) and not args.no_preparation: AP.prepare(args.apk, args.cache, log=self.loading_log(ENGINE_SHARE[1])); self.pre_game_files = AP.paths(args.cache)      # first time: the title images are extracted before the logos (the engine loads in the meantime)
        logos = AP.Logos(self.pre_game_files) if self.pre_game_files and not args.no_logos and (args.logos or (not args.shot and not args.slot)) else None
        self.logos = logos if logos is not None and logos.ok else None
        t_start = time.perf_counter()
        while thread.is_alive():                                                  # (black screen and the loading bar while the engine loads)
            if self._closed: sys.exit(0)
            self.show_loading(creep(time.perf_counter() - t_start)); time.sleep(0.02)
        thread.join()
        SV.note('engine loaded', seconds=round(time.perf_counter() - t_start, 2))
        if 'e' in res: raise res['e']
        self.engine = res['m']; self.engine.ignored_types = (ARENA_ABORT,); self.engine.force_monster = args.force_monster; self.engine.anim_duration = self._anim_duration; self.engine.map_obj_pos = self._map_obj_pos; self.engine.bgm_alive = lambda: self.sound.bgm_alive(); self.engine.fbx_clips = self._fbx_clips; self.engine.text_asset = self.data.text_asset; self.engine.prefab_clips = self._slime_touch_clips
        self.engine.items_plan = lambda uid: RI.plan_for_code(self.game_rules.seed_of(uid), self.engine.core.fs)          # randomizer, items: the plan of the game being played (its seed, rules file)
        self.engine.encounters_plan = lambda uid: RN.plan_for_code(self.game_rules.seed_of(uid), self.engine.core)        # randomizer, encounters: same
        self.engine.quick_journal = self.reg['quick_save_journal']; self.engine.on_quick_save = lambda j: self.reg.__setitem__('quick_save_journal', j)        # (the quick save's adventure log, given back by its "Resume")
        try:                                                                                                  # savestates from before the savestates of each game: into their game's folder (once)
            n = SS.sort_by_game(CH.FOLDERS.savestates_folder(args.saves_write), self.engine.core, log=lambda *a: print(*a, flush=True))
            if n: print('%d savestate(s) sorted by game' % n, flush=True)
        except Exception as ex: print('savestates not sorted by game:', ex, flush=True)
        self.follow_journals()                                                                                # (what belongs to games no adventure log holds any more: deleted, see follow_journals)
        self.debug = DBG.DebugService(self) if getattr(args, 'debug', False) else None                        # debug mode (--debug): time control, overlay (dq456_pc/debug)

    def _preload_first_scene(self):
        """What the first frames loaded behind a black screen, loaded now behind the loading bar (before the logos): the title's map (read from the APK files on a first launch; the
        engine's title then finds it in place), the shader programs made on first use and the Esc menu's labels."""
        if not self.args.slot and self.map_name is None and TITLE_MAP:
            try: self._load_map(TITLE_MAP)
            except Exception as ex: print('title map not loaded in advance (%r)' % (ex,), flush=True)
            self.show_loading(0.97)
        self.pause.warm_up(); self._warm_up_gl(); self.warmed_up = True

    def _play_logos(self):
        """The entry logos (Boot.Logo: SQUARE ENIX, ArtePiazza), everything being loaded: a press (key, click) skips the current logo."""
        logos, self.logos = getattr(self, 'logos', None), None
        if logos is None: return
        t0 = time.perf_counter(); count = 0; worst = 0.0; self._logo_touched = False
        while not logos.finished:
            if self._closed: sys.exit(0)
            self.switch_to(); self.dispatch_events()
            t = time.perf_counter(); worst = max(worst, t - t0); dt = min(t - t0, 0.1); t0 = t; count += 1
            gl.glClearColor(0.0, 0.0, 0.0, 1.0); self.clear(); gl.glViewport(0, 0, *self.get_framebuffer_size())
            if self._logo_touched: logos.touch()
            logos.do_update(dt); logos.draw_all(self.width, self.height)
            self._logo_touched = False
            self.flip()
            if not self._logo_logged: self._logo_logged = True; self._log_window('1st frame shown (logos)')
        SV.note('logos finished', frames=count, worst_gap_ms=round(1000 * worst))

    def _wait(self, text, fraction=None):
        """Black screen with the loading bar at `fraction` (the step's text goes to the console): the window stays alive during the startup loads."""
        print(text, flush=True)
        self.show_loading(fraction)
        SV.note('wait (black frame)', text=text)

    def _launch_preparation(self, cache):
        """Cache preparation in a separate process, at low priority (data.py on the command line): in a thread, its extractions slowed the game down. Fallback: thread."""
        import subprocess
        try:
            kw = {'creationflags': 0x00004000} if os.name == 'nt' else {'preexec_fn': lambda: os.nice(10)}              # BELOW_NORMAL_PRIORITY_CLASS / nice
            kw['env'] = dict(os.environ, DQ_LANGUAGE=LG.current_language())              # (the preparation process speaks the tool's language, not the computer's)
            self.prep_proc = subprocess.Popen([sys.executable, '-m', 'dq456_pc.world.data', self.engine.apk_data, cache], stdin=subprocess.PIPE, cwd=CH.ROOT, **kw)
        except Exception as ex:
            print('Cache preparation: separate process failed (%r), running inside the game.' % (ex,), flush=True)
            threading.Thread(target=self.data.prepare, args=(self.prep_stop,), daemon=True, name='preparation-cache').start()

    def on_close(self):
        if not self._ready: self._closed = True; return           # closing during the logos: the constructor stops
        if not self._dying: self._save_on_exit()                  # (a fatal error has already saved everything: see _fatal)
        try: self.gamepad.release()                              # (the controllers are freed: a game started again in another language opens them again)
        except Exception as ex: print('Gamepads not released (%r)' % (ex,), flush=True)
        super().on_close()

    def _save_on_exit(self):
        """Saves what must be saved before closing: settings, history, sound shutdown, end of the cache preparation subprocess.

        Each step is guarded separately: if one fails (which happens right after a fatal error), the following ones are still done.
        -> list of the failed steps, for the log."""
        failed = []
        for name_str, do in (('settings', lambda: self.reg.store()), ('history', lambda: self.history.store(True)), ('sound', lambda: self.sound.shutdown())):
            try: do()
            except Exception as ex: failed.append('%s (%r)' % (name_str, ex))
        p = getattr(self, 'prep_proc', None)
        if p is not None:
            try: p.stdin.close()
            except Exception: pass
        return failed

    debug = None                                                  # the debug service (--debug, dq456_pc/debug/service.py); None in normal play
    _dying = False                                                # a fatal error is being handled: no more frames, no more steps (see _fatal)
    dead = False                                                  # the game stopped on an error: `main` returns a non-zero exit code, the launcher keeps the console open

    def _fatal_context(self):
        """What is known about the game session at the time of the error, for the log: frame, map, game, engine heap. Never raises."""
        lines = []
        try: lines.append('frame %s, loop %s' % (getattr(self, 'n', '?'), getattr(self, 'n_loop', '?')))
        except Exception: pass
        try:
            e = self.engine.state
            lines.append('map %r, part %s' % (e.game_map, e.part))
        except Exception: pass
        try:
            c = self.engine.core
            lines.append('engine heap %d MB of %d MB' % (c.heap_used() >> 20, c.HEAP_SIZE >> 20))
        except Exception: pass
        return ''.join('  %s\n' % x for x in lines)

    def _fatal(self, where):
        """The game cannot continue: what can be saved is saved, the full trace goes to the log, then the window closes cleanly.

        Before, an error coming up from the engine (heap exhausted, hook error, timeout) made the **window vanish without saving anything**:
        `on_close` was not run, so settings, history and sound were lost, and the log `window_errors.log` stayed empty
        even though the launcher sends the player there. The trace only went to the console, which nobody sees when the game is started from the `.bat`.
        """
        if self._dying: return
        self._dying = True; type(self).dead = True
        trace = traceback.format_exc()
        failed = self._save_on_exit()
        kept = 'settings, history and sound saved' if not failed else 'NOT saved: ' + ', '.join(failed)
        text = '--- %s: the game stopped on an error in %s ---\n%s  %s\n%s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), where, self._fatal_context(), kept, trace)
        note_error(text)
        print(text, flush=True)
        print('The game stopped on an error. Details in %s' % CH.FOLDERS.errors_logfile(), flush=True)
        try: self.close()
        except Exception: pass

    if sys.platform == 'win32':
        try:
            @pyglet.window._PlatformEventHandler(0x0106)          # WM_SYSCHAR: the Alt + Enter character. Without this handler, Windows (DefWindowProc) plays its "error tone" at every Alt + Enter. The other Alt + key combinations are left to Windows.
            def _event_syschar(self, msg, wParam, lParam): return 0 if wParam == 13 else None
        except Exception: pass

    def pause_game(self, yes):
        """"Settings" menu (Esc) open (yes) or closed. The game keeps running behind it, with no input (see `tick`), on all pages; a page of Pause.FROZEN_PAGES (empty) would stop it (engine and sound: `_sync_freeze`, at every frame).
        On closing, keys already held do not count as a new press."""
        self.acc = 0.0; self.t_loop = None
        if not yes:
            if self._game_frozen: self._game_frozen = False; self.sound.set_paused(False)
            self.pad_prev = self.pad(); self.prev_action = {a: self._held(a) for a in EDGE_ACTIONS}; self.rep = {}; self.hovered = None; self.press_state = None; self.hover_node = None
            self.mouse_guard.keyboard(self.mouse)             # the pointer selects nothing in the game's menus until it has moved away from its previous position (keyboard selection must not jump; see inputs.MouseGuard)

    _focus_lost = False                                               # the window no longer has focus: SOUND is muted, the game keeps running without input (used to stop it, poll); see on_deactivate / on_activate and _loop

    def on_deactivate(self):
        """The window loses focus (Alt + Tab, click on another window or another screen, minimizing): the sound is muted (Sound.lose_focus), the game keeps running and the held mouse buttons are released (the release outside the window never arrives: wheel camera, on-screen D-pad, pressed button)."""
        self._focus_lost = True
        if not self._ready: return                # (while the window is being created, nothing exists yet: `_loop` mutes the sound as soon as it runs)
        try: self.sound.lose_focus(True)
        except Exception as ex: print('Sound: muting on focus loss failed (%r)' % (ex,), flush=True)
        self.wheel.cancel(); self.bar_gesture.cancel(); self.press_state = None; self.hovered = None; self.hud.dpad_drop(); self.hud.press_state = None; self._camera_forget()          # (rotation requested with the mouse, return to north pending: not applied afterwards)

    def on_activate(self):
        """The window regains focus: the sound comes back and keys that are already held do not count as a new press (the game was not stopped: the clock is not reset)."""
        self._focus_lost = False
        if not self._ready: return
        try: self.sound.lose_focus(False)
        except Exception as ex: print('Sound: unmuting on focus return failed (%r)' % (ex,), flush=True)
        if self._ready:
            self.pad_prev = self.pad(); self.prev_action = {a: self._held(a) for a in EDGE_ACTIONS}; self.rep = {}
            self.mouse_guard.keyboard(self.mouse)

    def ask_quit(self):
        """Esc menu > "Quit the game": the Esc menu is already closed; the engine opens the game's box with its own question (MSG_QUIT_APP, "Voulez-vous vraiment quitter l'application ?" or its
        English text), Yes / No as for the return to the title; "Yes" quits (`quit_requested`, read after each step)."""
        self.engine.request_return_to_title(quit_app=True)

    def quit_game(self):
        """Esc menu > Quitter > Oui (and "Quit" of the start menu): the same closing as the window's close button (settings, history, sound; no game save: like the original game, the automatic one is only written on arrival on a map, see docs/engine-patches.md)."""
        self.on_close(); pyglet.app.exit()

    # ---- game language: the game's APK files follow the "Language" setting; they are read when the game starts, so changing the language means starting the game again (in the same process: `main` loops)
    reload_request = None                                                  # set by reload_game: the loop stops, `main` starts a new Game with the language of the setting (and loads the state `reload_request['state']`)

    def language_target(self):
        """Language of the game version that the "Language" setting asks for (game_version_of the language displayed)."""
        return self.game_version_of(LG.current_language())

    def game_version_of(self, language):
        """Language of the game version that `language` runs: its own if its APK folder has the files, otherwise the one it falls back on, English then French (Folders.choose_apk); the
        language already loaded when the APK files were forced (--apk: tests)."""
        if getattr(self.args, 'apk_forced', False): return self.game_language
        return CH.FOLDERS.choose_apk(language)[1]

    def language_pending(self):
        """The setting asks for another game language than the loaded one: the game restarts on the title screen."""
        return self.language_target() != self.game_language

    def on_title(self):
        """The title screen is displayed (the map of the title, in town part; not while the game's return to title is under way)."""
        e = self.engine.state
        return e.part == ENGINE.PART_TOWN and e.game_map == TITLE_MAP and self.map_name == TITLE_MAP and self.engine.back_to_title_state is None

    def reload_game(self, language=None, state=None):
        """Closes the window like Quitter does (settings, history, sound: nothing else is waiting to be written, the game's own saves are written when they happen) and asks `main` to start the game again: the engine reloaded with the APK files of the
        language of the setting, the title screen in that language, no entry logos. `language` (a language code): sets the "Language" setting first (loading a state of another game version); `state`: number of the savestate to load once the game is back."""
        if language is not None: self.reg['language'] = language; LG.select_language(language)
        full = self.is_fullscreen; where = None
        try: where = None if full else tuple(self.get_location())
        except Exception: pass
        self.reload_request = {'state': state, 'size': tuple(self.window_size) if full else (self.width, self.height), 'location': where}          # (the new window opens where and as big as this one; start_menu: it goes on to the start menu)
        self.quit_game()

    def _reload_step(self):
        """Every loop: the language of the setting differs from the loaded one and the title screen is displayed with the menu closed -> reload. -> True if the game is closing."""
        if self.reload_request is not None: return True
        if self.pause.opened or getattr(self, 'port_screen', None) is not None or not self.on_title() or self.language_target() == self.game_language: return False          # (the APK folders are looked at only on the title screen; the language screen restarts when it closes)
        self.reload_game(); return True

    def _load_after_reload(self):
        """First loop of a game started again to load a state of another game version (the confirmation was given before the reload)."""
        n, self.args.reload_state = self.args.reload_state, None
        self._after_reload = True                                  # (the engine has no game yet: the state is the game last played's, SnapshotsMixin.state_path)
        try: self.load_state(n); self.toast(tr('save_states.state_loaded') % n)
        except Exception as ex: self._note_state_failure(tr('save_states.loading_state') % n, 'Loading savestate %d' % n, ex)
        finally: self._after_reload = False

    def back_to_title_possible(self):
        """The "Return to title screen" line of the Pause menu is active only in towns and on the world map (not on the title screen): there, the original box (Engine.request_return_to_title) receives the keys and clicks."""
        e = self.engine.state                  # (a game loaded straight onto the world map still has the title's town map loaded: only a TOWN on that map is the title)
        return e.part in (ENGINE.PART_TOWN, ENGINE.PART_FIELD) and not (e.part == ENGINE.PART_TOWN and getattr(self, 'map_name', None) == TITLE_MAP) and self.engine.back_to_title_state is None

    def back_to_title(self):
        """Pause menu > "Return to title screen": the Pause menu is already closed; the engine opens the game's original box (its text, Yes / No); "Yes" starts its return to the title, with its fade
        (Engine.request_return_to_title). The game's only way back to the title."""
        self.engine.request_return_to_title()

    _sfx_this_frame = None                                                  # sound effects already started during the current displayed frame when the battle is accelerated (None: no filter; see _loop and battle_speed.sounds_to_play)

    _game_frozen = False                                                  # are the engine and the sound stopped behind the menu? (see _sync_freeze)

    def _sync_freeze(self):
        """Stops the sound and the engine clock when the menu moves to a page that stops the game (Pause.FROZEN_PAGES), resumes when it leaves it; on return, no catch-up of the elapsed time."""
        stop = self.pause.freezes_game()
        if stop != self._game_frozen:
            self._game_frozen = stop; self.sound.set_paused(stop); self.acc = 0.0; self.t_loop = None

    def _guarded_loop(self, dt):
        """What pyglet calls on every frame: `_loop` under the error barrier. Nothing propagates up to pyglet, which would make the window vanish without saving anything."""
        if self._dying: return
        try: self._loop(dt)
        except Exception: self._fatal('the game loop')

    def _loop(self, dt):
        """Advances the engine in 1/30 s steps (real clock, at most 3 catch-up steps per displayed frame; a longer stall is forgotten) then sets alpha, the fraction of the current step."""
        self.n_loop += 1
        if getattr(self, '_replay', None) is not None and self.replay_fading(): self.t_loop = None; return          # (end of a replay: the engine waits for the fade to black)
        if getattr(self.args, 'reload_state', None): self._load_after_reload()
        elif self._reload_step(): return
        self.gamepad.poll(not self._focus_lost)
        if self.args.keys_test or self.args.shots_loop: self._test_keys()
        if self.args.options_test: self._options_test()
        if self._focus_lost and not self.sound.out_of_focus: self.sound.lose_focus(True)          # (the event arrived before the window was created); otherwise the game runs as usual: only the sound is muted
        if os.environ.get('DQ_WAIT_HD') and self.hd_jobs: wait_futures(list(self.hd_jobs.values()))      # (test) reproducible captures: waits for the HD computations to finish in a background task
        if self.pause.opened:
            self.pause.do_update(); self._sync_freeze()
            if self.pause.freezes_game(): self.acc = 0.0; self.t_loop = None; self.alpha = 1.0; return        # page of FROZEN_PAGES (none): the engine is stopped; otherwise it runs with no input (tick)
        if self.debug is not None and self.debug.gate(): return          # debug mode: queued commands, pause, step by step
        t = time.perf_counter()
        dt = 0.0 if self.t_loop is None else t - self.t_loop
        self.t_loop = t
        if self.fixed_test:
            self._step(); self.alpha = 1.0; return
        f0 = self._speed_factor(); f = self.debug.factor(f0) if self.debug is not None else f0; in_battle = self._battle_clock() and f > 1.0          # (debug mode: its speed multiplies the game's)
        self._replay_skipping = f0 == RP.REPLAY_SKIP and getattr(self, '_replay', None) is not None
        if not self._replay_skipping and getattr(self, '_replay_skip', None) is not None: self.replay_skip_step(False)          # a turn played at the game's clock ("Manual", "Normal": no battle step) ends the skip too: the next one holds the monsters
                                                  # from ITS start (with the first skip kept, every later one put each monster at the frame of a clock started turns ago: the animations jumped between two turns)
        if self._replay_skipping:          # battle replay between two turns: the engine runs ahead, the scenery's animations keep the real clock, and so do the monsters' (see _do_battle_step)
            self._scenery_acc += dt; self.scenery_ticks = int(self._scenery_acc / ENGINE_STEP); self._scenery_acc -= self.scenery_ticks * ENGINE_STEP
        else: self.scenery_ticks = None; self._scenery_acc = 0.0
        self.engine.clock_speed = f          # play time of the save follows real time, walking and battle (the engine counts only a share of the steps when f > 1, see Engine._game_time_hooks)
        self._sfx_this_frame = set() if in_battle else None            # accelerated battle: the same sound effect is started only once per displayed frame (see VC.sounds_to_play); its playback speed does not change
        self.acc, n, cut = VC.advance_time(self.acc, dt, f, self._do_battle_step if in_battle else self._terrain_step, time.perf_counter, ENGINE_STEP,
                                           RP.REPLAY_BUDGET if self._replay_skipping else FAST_STEP_BUDGET)      # (15 % tolerance, catch-up and computation budget: see battle_speed.advance_time)
        self.alpha = VC.display_fraction(self.acc, ENGINE_STEP)          # (shifted by the 15 % tolerance instead of clamped at 0)

    def _speed_factor(self):
        """Speed factor of the game clock while the hero walks (setting: accelerator while its key is held). Only on the field (town, dungeon, world map),
        with a direction held, with no message, menu, battle or screen effect, and no game event (player lock, Global.Flag 4): when the game has control, its own pace takes precedence over the setting. A fade alone (flag 1, player free) no longer prevents acceleration: the arrival fade of a map lasts 30 frames while the hero is already walking
        from the 12th (measured on the real engine); before, the accelerator did not work during that second. Same thing on return from a battle: the return fade lasts 30 frames, the player is locked until frame 11 then free
        (the hero walks from frame 13, measured on the real engine); before, `State.battle_transition` cut the acceleration during the 30 frames, i.e. 0.6 s of normal walking despite the run key being held. During the exit fade, the player is locked (flag 4): nothing changes there. The engine then does more steps per second (see _loop): the hero, the NPCs and the animations go faster together,
        the companions keep their distance (the position history advances at the same rate) and the encounter rate follows the distance walked (it counts steps)."""
        R = self.reg; eng = self.engine; e = eng.state
        if getattr(self, '_replay', None) is not None and self.replay_between_turns(): return RP.REPLAY_SKIP          # battle replay, between two turns: the command phase goes by at once
        if e.effect is not None or getattr(e, 'screen_effect', None) is not None: return 1.0          # screen effect of the Capture class (travel, lamp, Riremito: the engine waits for it to end); battle entry cascade: game clock (its duration follows "Transition speed", in engine frames: Engine._start_battle_effect); neither battle speed, nor walking speed, nor accelerator
        if self._battle_clock() and self._battle_won(): return 1.0          # a won battle's results (experience, gold, items, level ups) and its exit: the game's clock at every "Battle speed", as in "Manual": at x3 the texts went by too fast, even by hand, and the map came back with the end of a jingle
        if self._battle_clock(): return VC.factor(R['battle_speed'], True)          # battle, from the end of the cascade (black screen, arrival fade, "X apparait" message) through the end fade inclusive: "Battle speed" alone ("Manual" and "Normal": 1, "Fast": 2, "Very fast": 3); neither the walking speed nor the accelerator multiplier applies there
        if self.pause.opened: return 1.0          # "Settings" menu: no accelerated walking (no direction is sent to the game)
        if e.transition_guard > 0: return 1.0          # between two fades of a battle (the engine changes scene 1 to 3 frames later): game clock. The fade BACK to the map is no longer cut off outright: only the player lock (further down) counts, as for the arrival fade on a map
        if R['accel_speed'] == 1.0: return 1.0
        if e.part == ENGINE.PART_BATTLE or e.part in ENGINE.MINIGAME_PARTS or eng.msg_visible or eng.active_menu() is not None or e.effect is not None or getattr(e, 'screen_effect', None) is not None or e.load_wait is not None: return 1.0          # casino, bestiary: never accelerated walking (the held arrows serve the menus there)
        if not (self.pad() & (ENGINE.UP | ENGINE.DOWN | ENGINE.LEFT | ENGINE.RIGHT)): return 1.0
        if eng.flags() & ENGINE.PLAYER_LOCKED_FLAG: return 1.0       # the game in control (scene, event without message, exit fade): its pace is its own, the held direction changes nothing there; a fade alone (arrival on a map, player free) no longer counts there
        f = 1.0
        if self._held('accel') or self.hud.dpad_run(): f = float(R['accel_speed'])          # running: accelerator key held, or on-screen cross held at the edge of its ring (hud_mobile.TRAVEL_THRESHOLD)
        return max(0.25, min(4.0, f))          # (x4 at most)

    def player_control(self):
        """Does the player have control of their character? Yes only on the field (town, dungeon, tower, world map), when the game has locked neither the player (event, scene, talking with an NPC, exit fade:
        Global.Flag 4, the same condition as accelerated walking, see _speed_factor), with no message, no game menu (commands, shop, Divers...), no screen effect or loading, and outside battle ("combat"
        part, entry cascade, black screen waiting for the battle scene, between two fades of a battle). A fade alone (arrival on a map, return from battle, player free) does not count."""
        eng = self.engine; e = eng.state
        if e.part not in (ENGINE.PART_TOWN, ENGINE.PART_FIELD) or self._battle_clock() or e.transition_guard > 0: return False
        if eng.msg_visible or eng.active_menu() is not None: return False
        return not (eng.flags() & ENGINE.NOT_FREE_FLAGS)

    def history_possible(self):
        """The history ("History" line of the Esc menu: no more key of its own) and the Esc key's Esc menu are only accessible when the player has control of their character: not during a battle, nor when the game
        takes control (dialogue, scene, event, exit fade, game menu), nor on the title screen."""
        return self.title_screen is None and getattr(self, '_replay', None) is None and self.player_control()

    def _terrain_step(self):
        """One engine step outside battle, accelerated (walking, accelerator). Returns False when a battle transition has just started during this step (start screen effect triggered by an encounter): VC.advance_time stops there
        and the rest of the frame resumes at the game clock; without this, the remaining catch-up steps of an accelerated walk would shorten the start of the cascade."""
        e = self.engine.state; before = e.effect is not None
        self._step()
        return before or e.effect is None

    def _test_keys(self):
        """(test) keys pressed at loop frame n, or "cN+k" = k loop frames after the battle has reached N frames: 'n:NAME' (NAME = pyglet key name;
        +NAME / -NAME = held / released; @x@y = mouse click (@x@y@2: hover only); %x%y &x&y *x*y = press / drag / release of the middle button); captures '<shot>_<n>.png'; the last capture quits."""
        a = self.args
        def instant(n):                                         # loop frame number at which event n occurs (None if not yet known)
            if isinstance(n, int): return n
            if n[0] not in self.base_c and self.n_battle == n[0] and self.n_battle > 0: self.base_c[n[0]] = self.n_loop
            return self.base_c[n[0]] + n[1] if n[0] in self.base_c else None
        for ev in list(a.keys_test):
            n, name_str = ev
            if instant(n) != self.n_loop: continue
            a.keys_test.remove(ev); n = self.n_loop
            if name_str[0] == '@':                                  # @x@y: left click at (x, y) pixels from the top left; @x@y@1: right click; @x@y@2: hover only
                v = [int(t) for t in name_str[1:].split('@')] + [0]; x, y = v[0], self.height - v[1]
                self.on_mouse_motion(x, y, 0, 0)
                if v[2] != 2: self.on_mouse_press(x, y, pyglet.window.mouse.RIGHT if v[2] else pyglet.window.mouse.LEFT, 0); a.keys_test.append((n + 3, '~@%d@%d' % (v[0], v[1])))
            elif name_str[0] == '!': x, y = [int(t) for t in name_str[1:].split('!')]; self.on_mouse_motion(x, self.height - y, 0, 0); self.on_mouse_press(x, self.height - y, pyglet.window.mouse.LEFT, 0)      # !x!y: press without automatic release (release: ~@x@y)
            elif name_str[0] == '~': x, y = [int(t) for t in name_str[2:].split('@')]; self.on_mouse_release(x, self.height - y, pyglet.window.mouse.LEFT, 0)
            elif name_str[0] == '^': x, y = [int(t) for t in name_str[1:].split('^')]; self.on_mouse_drag(x, self.height - y, 0, 0, pyglet.window.mouse.LEFT, 0)      # ^x^y: mouse moved with a button pressed
            elif name_str[0] == '%': x, y = [int(t) for t in name_str[1:].split('%')]; self.on_mouse_motion(x, self.height - y, 0, 0); self.on_mouse_press(x, self.height - y, pyglet.window.mouse.MIDDLE, 0)      # %x%y: middle button (the wheel) pressed; &x&y: pointer moved with the middle button pressed; *x*y: released
            elif name_str[0] == '&': x, y = [int(t) for t in name_str[1:].split('&')]; self.on_mouse_drag(x, self.height - y, 0, 0, pyglet.window.mouse.MIDDLE, 0)
            elif name_str[0] == '*': x, y = [int(t) for t in name_str[1:].split('*')]; self.on_mouse_release(x, self.height - y, pyglet.window.mouse.MIDDLE, 0)
            elif name_str[0] == '$': self.dispatch_event('on_text', name_str[1:])                                                                                   # $text: typed characters
            elif name_str[0] in '+-': self.dispatch_event('on_key_press' if name_str[0] == '+' else 'on_key_release', getattr(pyglet.window.key, name_str[1:]), self._mod_alt_test())
            else: self.dispatch_event('on_key_press', getattr(pyglet.window.key, name_str), self._mod_alt_test()); a.keys_test.append((n + 3, '-' + name_str))       # short press: released 3 frames later
        for n in list(a.shots_loop):
            if instant(n) == self.n_loop:
                a.shots_loop.remove(n); self.dispatch_event('on_draw'); self.flip()
                name_n = ('b%d' % n) if isinstance(n, int) else ('c%d+%d' % n)
                pyglet.image.get_buffer_manager().get_color_buffer().save(a.shot.replace('.png', '_%s.png' % name_n))
                if os.environ.get('DQ_REAL_TIME'): print('STEP engine=%d loop frames=%d t=%.1f' % (self.n, self.n_loop, time.time() - self.t_startup), flush=True)
                if not a.shots_loop: self.on_close(); pyglet.app.exit()

    def _mod_alt_test(self):
        """(test) Alt modifier of a simulated key event: the real keyboard supplies it (Alt held: MOD_ALT); +LALT then RETURN = Alt + Enter."""
        K = pyglet.window.key
        return K.MOD_ALT if self.key_list[K.LALT] or self.key_list[K.RALT] else 0

    def _options_test(self):
        """(test) --options-test "frame:Label=value;frame:Label=value": applies options of the Display page at the given loop frame, through the same functions as the Esc menu (value read as JSON: 3, true, 0.5, [1280, 720])."""
        import json
        for ev in list(self.args.options_test):
            n, lib, val = ev
            if n != self.n_loop: continue
            self.args.options_test.remove(ev)
            for entry in self.pause._entries_display():
                if entry.get('l') != lib: continue
                v = json.loads(val)
                if isinstance(v, list): v = tuple(v)
                (entry['set'](v) if entry['t'] == 'choice' else entry['set_value'](float(v))); print('OPTION %s = %s (frame %d)' % (lib, val, n), flush=True)

    def _step(self, mute=False):
        self.prev = self._snapshot()
        self.step_times.append(time.perf_counter())
        t0 = time.perf_counter()
        self.tick(ENGINE_STEP, mute)
        if getattr(self.engine, 'quit_requested', False): self.engine.quit_requested = False; self.quit_game()          # (the game's box: "Yes" to quitting the application)
        for slot, sp in self.engine.state.sprites.items():                     # the offset of a shadow the engine places (the balloon's), step by step (see shadow_offset)
            oc, ps = sp.get('shadow_field'), sp.get('pos')
            if oc is not None and ps is not None and len(oc) >= 2: self._shadow_steps.setdefault(slot, collections.deque(maxlen=3)).append((oc[0] - ps[0], oc[1] - ps[1]))
            else: self._shadow_steps.pop(slot, None)
        self._anchor_effects(self.engine.state)
        e = self.engine.state; field = e.part == ENGINE.PART_FIELD and e.field and e.field.get('cam')
        self.field_followers.step(e.field['cam'] if field else None, {k: s.get('pos') for k, s in e.sprites.items()} if field else {})
        self._log_timing('step', t0)

    def _log_timing(self, kind, t0):
        """(test) DQ_FRAME_TIMES=<file>: log "kind loop-frame engine-frame duration-in-ms" of each engine step and each draw (spots stalls on a first display:
        texture or model loaded on the fly)."""
        f = os.environ.get('DQ_FRAME_TIMES')
        if f:
            with open(f, 'a') as fh: fh.write('%s %d %d %.1f\n' % (kind, self.n_loop, self.engine.frames, 1000.0 * (time.perf_counter() - t0)))

    def _snapshot(self):
        """State at the end of the step that has just finished: positions of the characters and the camera, for interpolation."""
        e = self.engine.state
        tup = lambda v: None if v is None else tuple(float(x) for x in v)
        return dict(key=(e.part, e.game_map), ret=getattr(e, 'loading', None) is not None, sp={k: tup(s['pos']) for k, s in e.sprites.items() if s['pos'] is not None},
                    cam_pos=tup(e.cam_pos), cam_target=tup(e.cam_target), field=tup(e.field['cam']) if e.field and e.field['cam'] else None,
                    fbx={k: (tup(m.get('pos')), tup(m.get('rot'))) for k, m in getattr(e, 'fbx', {}).items()},
                    icons={k: (ic.get('id'), tup(ic.get('pos')), ic.get('alpha')) for k, ic in getattr(e, 'icons', {}).items()},
                    meshes={k: (getattr(e, 'models', {}).get(k), tup(mm.get('pos'))) for k, mm in getattr(e, 'meshes', {}).items()},
                    shadow={k: (tup(s.get('shadow_field')), tup(s.get('shadow_pos'))) for k, s in e.sprites.items() if s.get('shadow_field') is not None or s.get('shadow_pos') is not None})

    def _interpolate(self):
        """Replaces, for the duration of a draw, the positions of the characters and the camera (world map, towns) with those of the instant between the previous step and the current step;
        a jump (map change, forced move) is not interpolated. Returns what is needed to put everything back."""
        pr, a, e = self.prev, self.alpha, self.engine.state
        saved = []
        if pr is None or a >= 1.0 or e.part == ENGINE.PART_BATTLE or pr['key'] != (e.part, e.game_map) or pr.get('ret', False) != (getattr(e, 'loading', None) is not None): return Game._follower_offsets(self, saved, 1.0)      # (the frame that lifts the loading hold is not interpolated from the camera of the hold)
        threshold = 24.0 if e.part == ENGINE.PART_FIELD else CAM_JUMP_TOWN       # map pixels / town units per step; beyond that, it is a jump
        whole = e.part == ENGINE.PART_FIELD and getattr(self, 'mode', None) == MODE_PP          # pixel perfect world map: the drawing puts the camera and the sprites on whole map pixels (round of each coordinate)
        flat = e.part == ENGINE.PART_FIELD                                     # world map: (x, y, height) in map pixels; the height (the balloon: 20 over the sea, 60 over land) is a depth for the stacking, never a movement
        def between(q, c):
            if q is None or c is None or len(q) != len(c): return None
            n = 2 if flat and len(c) > 2 else len(c)                          # (the balloon's height changing between two steps no longer made it skip its interpolation: it trembled at the coast)
            d = max(abs(x - y) for x, y in zip(q[:n], c[:n]))
            if d == 0 or d > threshold: return None
            if whole: return tuple(x + whole_pixels((y - x) * a) for x, y in zip(q[:n], c[:n])) + tuple(c[n:])          # (the way covered since the step, not each coordinate, goes to whole pixels: a diagonal moves on both axes at the same frame)
            return tuple(x + (y - x) * a for x, y in zip(q[:n], c[:n])) + tuple(c[n:])
        for k, sp in e.sprites.items():
            v = between(pr['sp'].get(k), sp['pos'])
            if v is not None: saved.append((sp, 'pos', sp['pos'], False)); sp['pos'] = v
            q = pr.get('shadow', {}).get(k)                                   # a shadow the engine places itself (the balloon's on the world map, a separate one in town): with its character
            if q is not None:
                for j, name_str in enumerate(('shadow_field', 'shadow_pos')):
                    v = between(q[j], sp.get(name_str))
                    if v is not None: saved.append((sp, name_str, sp[name_str], False)); sp[name_str] = v
        for k, m in getattr(e, 'fbx', {}).items():                            # 3D models of the scene (packet 32), like the characters
            q = pr.get('fbx', {}).get(k)
            if q is None: continue
            v = between(q[0], m.get('pos'))
            if v is not None: saved.append((m, 'pos', m['pos'], False)); m['pos'] = v
            r0, r1 = q[1], m.get('rot')
            if r0 is not None and r1 is not None and len(r0) == len(r1):
                da = [(y - x + 180.0) % 360.0 - 180.0 for x, y in zip(r0, r1)]          # (degrees, by the shortest way; more than 90 in one step is a cut)
                if any(da) and max(abs(d) for d in da) <= 90.0: saved.append((m, 'rot', m['rot'], False)); m['rot'] = tuple(y - d * (1.0 - a) for y, d in zip(r1, da))
        for k, ic in getattr(e, 'icons', {}).items():                          # icon bubbles (packets 19/20, Riseup): the item that rises out of a chest, a jar, a wardrobe moves 0.25 a step
            q = pr.get('icons', {}).get(k)                                    # and fades out; drawn at the step's position only, it went up by jerks of 30 Hz next to the smooth characters
            if q is None or q[0] != ic.get('id'): continue                     # (a new icon, or another one in the same slot: from its own first position)
            v = between(q[1], ic.get('pos'))
            if v is not None: saved.append((ic, 'pos', ic['pos'], False)); ic['pos'] = v
            if isinstance(q[2], (int, float)) and isinstance(ic.get('alpha'), (int, float)) and q[2] != ic['alpha']:
                saved.append((ic, 'alpha', ic['alpha'], False)); ic['alpha'] = q[2] + (ic['alpha'] - q[2]) * a
        for k, mm in getattr(e, 'meshes', {}).items():                         # monster meshes of a town scene (DQ IV's last boss rising at the end, the camera following him; DQ V / VI's monsters in town):
            q = pr.get('meshes', {}).get(k)                                   # drawn at the step's position next to the interpolated camera, they shook by a step's movement
            if q is None or q[0] != getattr(e, 'models', {}).get(k): continue   # (another model in the slot: from its own first position)
            v = between(q[1], mm.get('pos'))
            if v is not None: saved.append((mm, 'pos', mm['pos'], False)); mm['pos'] = v
        cam = self._interpolate_camera(pr, a) if e.part != ENGINE.PART_FIELD else None          # (town: the target, and the camera around it when it rotates; world map: map camera, further down)
        if cam is not None:
            for name_str, v in zip(('cam_pos', 'cam_target'), cam): saved.append((e, name_str, getattr(e, name_str), True)); setattr(e, name_str, v)
        if e.field and e.field['cam']:
            v = between(pr['field'], e.field['cam'])
            if v is not None: saved.append((e.field, 'cam', e.field['cam'], False)); e.field['cam'] = v
        return Game._follower_offsets(self, saved, a)

    def _follower_offsets(self, saved, a):
        """World map: the characters walking with the camera drawn at their smoothed distance from it (field_followers.py; `a`: fraction of the step)."""
        e = self.engine.state; f = getattr(self, 'field_followers', None)
        if f is None or e.part != ENGINE.PART_FIELD: return saved
        for k, sp in e.sprites.items():
            d = f.offset(k, a); p = sp.get('pos')
            if d is None or p is None or len(p) < 2: continue
            saved.append((sp, 'pos', p, False)); sp['pos'] = (p[0] + d[0], p[1] + d[1]) + tuple(p[2:])
        return saved

    def _interpolate_camera(self, pr, a):
        """(position, target) of the town camera between the previous step `pr` and the current step, at fraction `a`, or None (nothing moved, or a cut: map change, scene shot, angle set in one go). The target follows the movement; the camera rotates
        around it (game camera buttons, scenes): its position RELATIVE to the target is interpolated, in heading (by the shortest path) and in radius, so that the rotation stays smooth between two engine steps (at 30 Hz, one rotation notch moves the camera by
        1.7 units: more than the jump threshold on a single coordinate); a relative movement of more than CAM_JUMP_ROT times the distance in one step is a cut."""
        e = self.engine.state
        p0, c0, p1, c1 = pr['cam_pos'], pr['cam_target'], e.cam_pos, e.cam_target
        if any(v is None for v in (p0, c0, p1, c1)) or not (len(p0) == len(c0) == len(p1) == len(c1) == 3): return None
        p0, c0, p1, c1 = (np.array(v, float) for v in (p0, c0, p1, c1))
        if not ((p1 != p0).any() or (c1 != c0).any()): return None
        if np.abs(c1 - c0).max() > CAM_JUMP_TOWN: return None
        r0, r1 = p0 - c0, p1 - c1
        h0, h1 = math.hypot(r0[0], r0[2]), math.hypot(r1[0], r1[2])
        if np.abs(r1 - r0).max() > max(CAM_JUMP_TOWN, CAM_JUMP_ROT * max(h0, h1)): return None
        c = c0 + (c1 - c0) * a
        if min(h0, h1) < 1e-6: r = r0 + (r1 - r0) * a
        else:
            a0, a1 = math.atan2(r0[0], r0[2]), math.atan2(r1[0], r1[2]); da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
            h = h0 + (h1 - h0) * a; ang = a0 + da * a
            r = np.array([h * math.sin(ang), r0[1] + (r1[1] - r0[1]) * a, h * math.cos(ang)])
        return tuple(float(x) for x in c + r), tuple(float(x) for x in c)

    def on_draw(self):
        """The drawing, under the error barrier."""
        if self._dying: return
        try: self._draw_frame()
        except Exception: self._fatal('drawing a frame')

    def _clear_color(self, e):
        """Clear color of the frame (and of the off-screen rendering): that of packet 8, and in battle that of the battle backdrop by default.

        NEVER returns None: a battle on a map without scenery (`cmd_fld_load` with the name "0", for example the prologue battle) has neither packet 8 nor scenery, and `glClearColor(None[0], ...)`
        made the window crash. Black is the default background of the game, as on the waiting screens.
        """
        background = e.background
        if e.part == ENGINE.PART_BATTLE and not any(background) and not os.environ.get('DQ_ESTIMATED_BACKGROUND'): background = self.backgrounds.color(self.map_src) or self.scenery_background      # battle background = bg_color of the loaded scenery (FETC, BackColor.SetColor of cmd_fld_load); estimate while waiting for the list / DQ_ESTIMATED_BACKGROUND=1: (test) the old estimate
        elif e.part == ENGINE.PART_BATTLE and self.scenery_background and not any(background): background = self.scenery_background
        elif getattr(e, 'map_background', False) and getattr(self, 'map_background', None) and getattr(self, 'map_name', None) == e.game_map: background = self.map_background          # DQ V / VI: cmd_fld_load paints the map's FETC color (BackColor), until a packet 8
        if e.part == ENGINE.PART_BESTIARY: return (0.0, 0.0, 0.0)          # the bestiary: the town's color left over is not the book's (its parchment and the blurred scenery, see backdrop.py)
        return background or (0.0, 0.0, 0.0)

    def _draw_frame(self):
        self._drawing_count = getattr(self, '_drawing_count', 0) + 1
        if self._drawing_count == 1:
            self._log_window('1st game frame')
            if not getattr(self, 'warmed_up', False):                    # (done during the loading, before the logos: _preload_first_scene)
                self.pause.warm_up()                                     # (the Esc menu's text labels: its first opening no longer stalls)
                self._warm_up_gl()                                       # (the shader programs made on first use)
            if getattr(self, 'dwm_cut', False): pyglet.clock.schedule_once(lambda dt: transitions_dwm(self, True), 2.0)
        if self._drawing_count <= 90 or self._drawing_count % 120 == 0: self._check_real_size()
        saved = self._interpolate(); t0 = time.perf_counter(); self._drawing_kind = '?'
        try: self._draw()
        finally:
            for obj, name_str, old, attr in reversed(saved):              # (last changed, first put back: a position changed twice gets its own back)
                if attr: setattr(obj, name_str, old)
                else: obj[name_str] = old
            self._log_timing('draw', t0)
            self._absorb_blocking(t0)
            if SV.ACTIVE: self._track_drawing(t0)

    def _absorb_blocking(self, t0):
        """A very long frame (more than 4 engine steps: 133 ms) among the 3 that follow a map load (scenery and character textures, shaders, off-screen rendering: first frame of a scene) must not
        be caught up: otherwise the engine redoes 3 steps at once (VC.advance_time) and the entry fade skips by as much (on screen: the picture already appears half faded). The engine clock restarts from zero; the time
        of the stall is lost (loading screen). Limits: a slow machine (frames of 100 ms and more) does not freeze the game, only a real stall is absorbed, at most 3 times per load."""
        e = self.engine.state; lookup_key = e.loaded_maps
        if lookup_key != getattr(self, '_absorb_key', None): self._absorb_key = lookup_key; self._absorb_n = 0
        self._absorb_n += 1
        if self._absorb_n <= 3 and time.perf_counter() - t0 > 4 * ENGINE_STEP: self.t_loop = None

    def _track_drawing(self, t0):
        """(diagnostic, DQ_TRACKING) one line per drawn frame during the first 12 frames and during the 16 frames that follow a map load or a game part change: what was drawn (`_drawing_kind`:
        scene, loading black...), the scene state (camera, characters placed), the opacity of the two fade planes, the draw hold (`State.loading`)."""
        e = self.engine.state; lookup_key = (e.loaded_maps, e.part)
        if lookup_key != getattr(self, '_tracking_key', None): self._tracking_key = lookup_key; self._tracking_n = 0
        n = getattr(self, '_tracking_n', 99)
        if self._drawing_count <= 12 or n < 16:
            places = sum(1 for sp in e.sprites.values() if sp['pos'] is not None)
            SV.note('draw', n=self._drawing_count, img=self.engine.frames, kind=self._drawing_kind, part=hex(e.part), game_map=e.game_map, loaded=self.map_name, camera=SV.rounded(e.cam_pos, 1), characters='%d/%d' % (places, len(e.sprites)),
                     fade='%.2f/%d' % (e.fade['a'], e.fade['n']), background='%.2f/%d' % (e.background_fade['a'], e.background_fade['n']), retained=getattr(e, 'loading', None) is not None, ms=round(1000 * (time.perf_counter() - t0), 1))
        self._tracking_n = n + 1

    def _note_state_failure(self, what, console_what, ex):
        """A savestate (digit shortcut, Ctrl + digit) could not be saved or loaded: one line in the console and in the error log, and the small on-screen message."""
        text = '--- %s: %s failed: %r ---\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), console_what, ex)
        print(text, end='', flush=True); note_error(text)
        self.toast(tr('window.failed_r') % (what, ex))

    def _report_write_failures(self, core):
        """A save that the engine could not write (GameCore.h_fclose, after two tries): the old file is intact and the game continues; small on-screen message and a line in the error log."""
        lines, core.write_failures = core.write_failures, None
        text = ''.join('--- %s: save failed: %s ---\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), l) for l in lines)
        print(text, end='', flush=True); note_error(text)
        self.toast(tr('window.save_failed_game_continues_details'), duration=6.0)

    def _watch_heap(self, core):
        """Engine heap ("Core.malloc" always advances, "h_free" frees nothing, 256 MB in all; about 480 battles or 7,000 map loads, i.e. hours of play): the engine would stop all at once
        at 256 MB. A line in the error log notes it at 200 MB (78 %), then at 240 MB (94 %) (no on-screen message), once per threshold (again if the heap goes back down, for example after loading
        a savestate). Closing then relaunching the game resets the memory to zero; the game writes its automatic save (data5) on every arrival on a map, nothing is written here: a savestate would keep the heap full."""
        u = core.heap_used(); level = 2 if u >= core.HEAP_CRITICAL else (1 if u >= core.HEAP_ALERT else 0)
        if level > self._heap_level:
            pc = round(100.0 * u / core.HEAP_SIZE)
            note_error('--- %s: engine memory at %d %% (%d MB of %d MB) ---\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), pc, u >> 20, core.HEAP_SIZE >> 20))
        self._heap_level = level

    def _report_missing_hooks(self, eng):
        """Engine settings that the apk in use did not let install (Engine._record_missing: another engine version than the one studied): the game runs with the original behavior for those points. One line per point in the log
        of errors (no on-screen message), only once (points found later, after loading a savestate, are announced in turn)."""
        new_ones = eng.missing_hooks[self._reported_hooks:]; self._reported_hooks = len(eng.missing_hooks)
        text = ''.join('--- %s: engine setting not applied: %s ---\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), l) for l in new_ones)
        note_error(text)

    def _paint_ground_meshes(self, e, cam3d):
        """DQ V / VI meshes with the Custom/DssaNormal shader (the T'n'T board's squares): with the scenery (queue Geometry), depth test and depth write on, no face culling."""
        meshes = self._battle_meshes(e, np.column_stack(cam3d), True, ground=True)
        if not meshes: return
        self.prog['cutoff'] = 0.05; gl.glDisable(gl.GL_CULL_FACE); self._linked_domain = None
        for tex, tw, th, vl, shader, hd in meshes:
            self.prog['crisp'] = 0.0 if hd else (1.0 if self.smoothing else 2.0); self.prog['tsize'] = (float(tw), float(th)); self._paint(tex, vl)
        self.prog['crisp'] = 0.0; self.prog['tsize'] = (1.0, 1.0); self.prog['cutoff'] = 0.5

    def tick(self, dt, mute=False):
        """One engine step (30 per second of game clock). `mute`: extra step of an accelerated battle while the engine waits for the player (menu, message waiting for a key): the engine advances (monster animations,
        windows, timers) with no key or click, those still being read at the normal rate (see _do_battle_step)."""
        r = getattr(self, '_replay', None)
        if r is not None:
            if r.get('fade') is None and self.replay_over(): self.stop_battle_replay()          # (the recording is over; fade to black, then the game is put back)
            if r.get('fade') is not None: return                                                   # (stopping: no more engine steps during the fade)
        p = self.args.script.get(self.n, self.pad()) if self.args.script is not None else self.pad()
        if self.args.slot and self.args.auto_message and self.n < 112:
            # (test) closes the welcome message automatically
            p = ENGINE.A if (70 <= self.n <= 72 or 88 <= self.n <= 90) else 0
        if self.args.battle_script is not None and self.n_battle > 0: p = self.args.battle_script.get(self.n_battle, 0)
        if self.args.force_field and self.n == self.args.force_field: self.engine.start_field()
        if os.environ.get('DQ_SPR') and self.n == 100: print('SPRITES', {k: (sp['id'], tuple(round(float(x), 2) for x in sp['pos']) if sp['pos'] is not None else None, sp.get('pose'), sp.get('dir'), sp.get('visible', True)) for k, sp in self.engine.state.sprites.items()}, 'icons', self.engine.state.icons, flush=True)
        if os.environ.get('DQ_TOWN') and self.n == 30: self.engine.start_town(os.environ['DQ_TOWN'])        # (test) loads this town or dungeon
        if os.environ.get('DQ_THE_END') and self.n == int(os.environ['DQ_THE_END']): self.engine.start_the_end()        # (test) the end of the game (credits, then "The End", then the title) at this loop frame
        if self.args.clicks:                                                # (test) simulated clicks: frame:x:y[:d] (d = right click), x and y in pixels from the top left; press at the frame, release 3 frames later
            for c in self.args.clicks:
                if c[3] == 2:                                              # d = 2: hover without click
                    if self.n == c[0]: self.on_mouse_motion(c[1], self.height - c[2], 0, 0)
                    continue
                if self.n == c[0]: (self.on_mouse_press(c[1], self.height - c[2], pyglet.window.mouse.RIGHT if c[3] else pyglet.window.mouse.LEFT, 0))
                if self.n == c[0] + 3: self.on_mouse_release(c[1], self.height - c[2], pyglet.window.mouse.LEFT, 0)
        if self._closing_game_menu and not mute: self._close_game_menu_step()          # ("Settings" opened from the game's Divers menu: that menu is closed behind it)
        p, decl = (0, 0) if (mute or self.pause.opened) else self._entries(p)          # "Settings" menu open: the game runs but receives no key or click (the menu's keys do not make the hero walk); battle replay: the player turns the messages, the recording answers the menus (see _entries)
        if r is not None and decl & ENGINE.A and self.replay_on_last_page(): self.stop_battle_replay(); return          # (replay of a won battle: the key that turns "X est vaincu." ends it, the rewards are not replayed)
        eng = self.engine
        if os.environ.get('DQ_CAMV') and self.n % int(os.environ['DQ_CAMV']) == 0: self._camv(eng)
        if os.environ.get('DQ_DEBUG') and (p or decl): print('n=%d p=%#x decl=%#x msg=%s active=%s' % (self.n, p, decl, eng.msg_visible, eng.active_menu()), flush=True)
        if not mute: self.curling_inputs()          # DQ VI's slime curling: the touch panel and the engine's point placed on the screen (slime_curling.py)
        if not mute: p = self.sugoroku_held(p)          # DQ V's T'n'T: the direction of a clicked arrow (sugoroku.py)
        t0 = time.time(); eng.step(p, decl); self.dt_engine = time.time() - t0
        self._camera_after_step(eng)          # camera rotation requested at this step: extra notches according to the set speed or the drag (camera.py)
        if len(eng.missing_hooks) > self._reported_hooks: self._report_missing_hooks(eng)
        self._watch_heap(eng.core)          # engine heap almost full
        if eng.core.write_failures: self._report_write_failures(eng.core)          # a save could not be written: the game continues, the player is warned
        if eng.core.journals_changed: eng.core.journals_changed = False; self.follow_journals()          # an adventure log saved, copied or deleted: what belongs to a game follows the logs (savestates, rules, history)
        self.history.do_update(eng)          # dialogues and battles of this step -> history (see history.py)
        self.game_rules.update(eng, self.reg, self.apply_all)          # new game (its rules are written), game entered (adventure log, savestate: its settings are put in place), settings changed during it (written to its file)
        if eng.request_pause: eng.request_pause = False; self.pause.open_menu(side=self.pause.last_page()); self._closing_game_menu = CLOSE_MENU_STEPS          # "Settings" button of the Miscellaneous menu: the Pause menu opens instead of the game's original settings (Engine._divert_params)
        self._update_title()
        for code, *_ in eng.ui.system:
            if code == 999: self.pause.open_menu('quit')            # MenuPortSendQuit: return to the start menu = quit the application (ApplicationQuit: Yes / No question)
        eng.ui.system = []
        if self.ui_g: self.ui_g.tick()                     # animations of the original windows, once per game frame
        if eng.ui.damage:                                   # damage numbers on monsters (HoppingNumber): one per hit
            for ch, h, dm in eng.ui.damage: self.damage_count.append(dict(ch=ch, h=h, dm=dm, t0=eng.frames))
            eng.ui.damage = []
        if os.environ.get('DQ_FLAG') and self.n % int(os.environ['DQ_FLAG']) == 0: print('FLAG n=%d %#x part=%s msg=%s menu=%s cam=%s>%s' % (self.n, eng.flags(), eng.state.part, eng.msg_visible, eng.active_menu(), None if eng.state.cam_pos is None else tuple(round(float(x), 2) for x in eng.state.cam_pos), None if eng.state.cam_target is None else tuple(round(float(x), 2) for x in eng.state.cam_target)), flush=True)
        if eng.state.part == ENGINE.PART_FIELD and eng.state.field is not None: self._advance_patterns()
        self.sound.image = self.n
        evs = VC.sounds_to_play(eng.state.sounds, self._sfx_this_frame) if eng.state.sounds else []          # (jingles and messages as in the game: nothing waits for a jingle)
        if evs: self.sound.events(evs, engine_frame=eng.frames)
        eng.state.sounds = []
        self.sound.tick()
        game = eng.game_options()[:2]
        if game != self._sound_opt: self._sound_opt = game; self.sound.set_mix(game={'music': SOUND.game_option_volume(game[0]), 'effects': SOUND.game_option_volume(game[1])})        # "volume" options of the game's Parametres window
        if os.environ.get('DQ_STATE') and self.n % 25 == 0: print('STATE n=%d part=%#x map=%s field=%s p=%#x' % (self.n, eng.state.part, eng.state.game_map, None if eng.state.field is None else eng.state.field['cam'], p), flush=True)
        e = self.engine.state
        self._map_objects()
        self.n += 1
        self.n_battle = self.n_battle + 1 if self.engine.state.part == ENGINE.PART_BATTLE else 0
        if self.args.shots_effect and e.effect is not None and e.effect['n'] in self.args.shots_effect and self.args.shot:       # (test) captures during the battle start effect
            self.dispatch_event('on_draw'); self.flip()
            pyglet.image.get_buffer_manager().get_color_buffer().save(self.args.shot.replace('.png', '_e%d.png' % e.effect['n']))
        if self.args.shots_battle and self.n_battle in self.args.shots_battle and self.args.shot:       # (test) intermediate captures during a battle
            self.dispatch_event('on_draw'); self.flip()
            pyglet.image.get_buffer_manager().get_color_buffer().save(self.args.shot.replace('.png', '_%d.png' % self.n_battle))
            print('battle', self.n_battle, 'engine frame', self.engine.frames, 'msg type', getattr(self.ui_msg, 'type', None), [(k, len(m['v']), m['pos']) for k, m in sorted(self.engine.state.meshes.items())], flush=True)
        if self.args.shot and (self.n >= self.args.frames or (self.args.battle_frames and self.n_battle >= self.args.battle_frames)):
            self.dispatch_event('on_draw'); self.flip()
            pyglet.image.get_buffer_manager().get_color_buffer().save(self.args.shot)
            print('screenshot:', self.args.shot, [(q, 'cur', f['cur'], list(f['cells'].items())[:3]) for q, f in self.engine.ui.visible_ones()], self.engine.active_menu(), self.engine.msg_visible, flush=True); pyglet.app.exit()

    def _close_game_menu_step(self):
        """"Settings" opened from the game's Misc menu closes the game's menu completely: the game's menu is closed behind it, one press of its back button (gamepad 0x20, as the B key sends it) every other step, the release in between,
        until no game window is visible any more (Misc list -> root menu -> closed). Between two windows the engine has no menu with the input for a frame or two: that is waited for, it is not
        the end (until then the closing stopped there, on the root menu). At most CLOSE_MENU_STEPS steps."""
        eng = self.engine
        if self.menu_to_release is not None: eng.send_menu(self.menu_to_release, 1, -1, 0); self.menu_to_release = None; return
        self._closing_game_menu -= 1
        if eng.msg_visible or eng.state.part not in (ENGINE.PART_TOWN, ENGINE.PART_FIELD): self._closing_game_menu = 0; return
        port = eng.active_menu()
        if port is None:
            if not any(q > 1 and f.get('visible') for q, f in eng.ui.f.items()): self._closing_game_menu = 0          # (closed)
            return                                                                                                      # (a window is changing: next step)
        q = self._port_for_gamepad(port, 0x20); eng.send_menu(q, 1, -1, 0x20); self.menu_to_release = q

    def _draw(self):
        self._draw_scene()
        if self.quick_save is not None and not self.pause.opened:       # digit shortcut: the savestate and the thumbnail are taken from the frame that has just been drawn
            n, self.quick_save = self.quick_save, None
            try:
                t = self._clone_thumbnail(); png = self._png_thumbnail(t); t.delete(); self.save_state(n, png); self.toast(tr('save_states.state_saved') % n)
            except Exception as ex: self._note_state_failure(tr('save_states.saving_state') % n, 'Saving savestate %d' % n, ex)
        if not self.pause.opened: self.hud.draw_all(self.hud.visible_ones() if getattr(self, '_replay', None) is None else [n for n in self.hud.visible_ones() if n == HM.BATTLE])
        if getattr(self, '_replay', None) is not None: self.draw_replay_overlay()
        elif self.arena_back_shown(): self.draw_arena_back()          # monster arena: the back button replaces the "Quit" window
        if self.pause.opened:
            if self.pause.wants_thumbnail: self.pause.mini_tex = self._clone_thumbnail(); self.pause.wants_thumbnail = False       # the game without the menu, for the savestate thumbnail (copy on the graphics card: no blocking readback)
            if self.pause.blurs_game(): self._blur_window()                 # the game blurred behind the menu (not the Interface and Affichage pages, where it must be seen)
            self.pause.draw_all()
        if self.toasts and time.time() < self.toasts[0][1]:                # brief message at the top of the screen (saves only)
            k = max(0.75, self.height / 900.0); gl.glDisable(gl.GL_DEPTH_TEST); gl.glEnable(gl.GL_BLEND); gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
            label = self._lbl(self.toasts[0][0], 16 * k, (255, 255, 255, 255)); w = label.content_width + 28 * k; h = 36 * k
            r = pyglet.shapes.Rectangle((self.width - w) / 2, self.height - 24 * k - h, w, h, color=(0, 0, 0)); r.opacity = 170; r.draw()
            label.x = (self.width - label.content_width) / 2; label.y = self.height - 24 * k - h + 11 * k; label.draw()
        self.draw_replay_veil()                                          # (end of a battle replay: fade to black, then the history from the black)
        self._draw_helptext()                                            # information lines (F1) over everything: menus, history, screen buttons, toasts, veils; not in the savestate thumbnails
        if self.debug is not None: self.debug.draw()                     # debug overlay (--debug), over the information lines

    def _phone_clip(self):
        """(x, y, w, h) in pixels of the drawing area of the phone's portrait screen in the bestiary's frame (book_frame: 450 x 800 interface units around its
        center), for glScissor; None without a frame."""
        frame = getattr(self, 'book_frame', None)
        if frame is None: return None
        return phone_clip(frame(), self.width, self.height, getattr(self, '_scene_viewport', None) or (self.width, self.height))

    def _draw_scene(self):
        t_start = time.perf_counter()
        if self.t_prev is not None: self.spacings.append(t_start - self.t_prev)
        self.t_prev = t_start
        e = self.engine.state
        if self._outline_jobs: self._take_outlines()                     # (battle sceneries softened in the background)
        if os.environ.get('DQ_MAP_TEST'):                                  # (test) DQ_MAP_TEST="name;x,y,z;cx,cy,cz": shows a town/cave map with a fixed camera (scenery animations)
            trial_name, pc, cc = os.environ['DQ_MAP_TEST'].split(';')
            e.part = 0; e.game_map = trial_name; e.field = None; e.background = (0.0, 0.0, 0.0); e.top_background = e.bottom_background = None; e.cam_free = False
            if os.environ.get('DQ_MAP_NO_SPRITES'): e.sprites.clear()                          # (test) without the engine's characters (the hero would overlap the objects under study)
            e.cam_pos = tuple(float(v) for v in pc.split(',')); e.cam_target = tuple(float(v) for v in cc.split(','))
            if os.environ.get('DQ_MAP_MOVE'):                              # (test) DQ_MAP_MOVE="dx,dy,dz": the camera moves by this vector at every displayed frame (flicker in motion)
                dv = [float(v) * self.n_loop for v in os.environ['DQ_MAP_MOVE'].split(',')]
                e.cam_pos = tuple(a + b for a, b in zip(e.cam_pos, dv)); e.cam_target = tuple(a + b for a, b in zip(e.cam_target, dv))
        if e.effect is not None and self.ref_effect is not e.effect: self.ref_effect = e.effect; self.effect_tex_ready = False      # new battle start effect: the first frame is drawn normally then copied
        if e.effect is not None and self.effect_tex_ready: self._drawing_kind = 'effet'; self._draw_effect(e.effect); return
        ef_c = self._capture_state(e)                                            # screen effect of the Capture class (travel, lamp, Riremito): same principle, but the interface stays drawn on top (capture_effects.py)
        if ef_c is not None and self.capture_ready:
            sim = self._capture_advance(ef_c)
            if (sim.render_active and not getattr(sim, 'live_capture', False)) or e.black_screen is not None: self._drawing_kind = 'screen effect'; self._draw_capture_effect(sim); return          # (for the first two frames the game is still displayed normally; on arrival of a travel it is the departure's black)
        if e.effect is None and e.load_wait is not None:                # end of the effect, battle scene not yet arrived: black (otherwise the previous scene would show)
            self._drawing_kind = 'black (waiting for the battle)'; gl.glClearColor(0.0, 0.0, 0.0, 1.0); self.clear(); return
        field = e.part == ENGINE.PART_FIELD and e.field is not None        # world map (tiles) or town (3D scenery)
        cam3d = None                                                        # town: (right, up, forward) of the camera, for the character panels
        retained = getattr(e, 'loading', None) is not None                 # map loading under a covered screen, scene not yet in place: only the black and the fades are drawn (State.update_loading)
        if not field and e.game_map and e.game_map != self.map_name:
            self._load_map(e.game_map); self.t_loop = None              # synchronous loading (several tenths of a second): the time spent is not caught up by the engine (the entry fade keeps its duration, instead of skipping 3 steps in one frame)
        background = self._clear_color(e)
        gl.glClearColor(background[0], background[1], background[2], 1.0)
        self.clear()
        is_battle = e.part == ENGINE.PART_BATTLE and e.cam_pos is not None and e.cam_rot is not None
        staff = self._staffroll_up(e, field, is_battle)                       # end credits: they cover the whole window (black) and hide the scene, the end screen's meshes stay above
        if self._endroll_up():                                                  # DQ V / VI end credits: the scene camera is off, the orthographic layer on black (staffroll.py)
            self._drawing_kind = 'end credits'; self._draw_endroll(e)
            self._update_message(); self._draw_message(); self._draw_menus(); self._draw_fade(); return
        if self._slime_touch_up():                                              # DQ V's Slime Touch: the scene camera is off, the orthographic layer on black (slime_touch.py)
            self._drawing_kind = 'slime touch'; self._draw_slime_touch(e)
            self._update_message(); self._draw_message(); self._draw_menus(); self._draw_fade(); return
        if retained or (e.black_screen is not None and ef_c is None) or (e.part == ENGINE.PART_FIELD and e.field is None) or (not field and not is_battle and e.part != ENGINE.PART_BATTLE and (e.game_map or '').startswith('btl_')):
            # world map not yet sent, or battle over and next scene not yet arrived: the previous scene is not shown with another one's camera (battle backdrop image on return
            # from a flee); or map loading not yet complete (held): the engine has sent only the scenery, the camera and the characters arrive on the next frame
            self._drawing_kind = 'black (loading held)' if retained else 'black (scene not there yet)'; gl.glClearColor(0.0, 0.0, 0.0, 1.0); self.clear()
            if retained: self.post.begin((0.0, 0.0, 0.0)); self.post.end()                   # (prepares the off-screen rendering under the black: creating it does not delay the 1st visible frame)
            gl.glViewport(0, 0, *self.get_framebuffer_size()); self._draw_fade(True)
            if not (e.game_map or '').startswith('btl_'):               # (end of battle: the battle windows, like the party status at the top left, stay "visible" in the engine for a moment: on the black screen only that box could be seen)
                self._update_message(); self._draw_message(); self._draw_menus()
            self._draw_fade(); return
        if not field and not is_battle and (e.cam_pos is None or (e.cam_target is None and not camera_free(e))) and self._event_view(e) is None:          # (DQ V / VI: a map's camera movement replaces the engine's camera,
            self._drawing_kind = 'nothing (no camera)'; return                                                                                        #  even before the engine has sent one: DQ VI's title flight)
        self._drawing_kind = 'scene'
        gl.glEnable(gl.GL_DEPTH_TEST); gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        self.post.game_sepia = bool(getattr(e, 'screen_sepia', False))          # DQ V / VI: Sepia image effect of the scene camera (the interface is drawn after, untouched)
        self._scene_viewport = self.post.begin(background); gl.glViewport(0, 0, *self._scene_viewport)          # scene drawn off-screen if a display setting requires it (MSAA antialiasing, sharpness, brightness: postfx.py); otherwise directly into the window
        if not field and not is_battle and e.part != ENGINE.PART_BESTIARY and background_gradient(e.top_background, e.bottom_background): self._paint_background_gradient(e.top_background, e.bottom_background)       # town: BackColor gradient background (not the bestiary's: the town's sky left over, see backdrop.py)
        if not field and not is_battle: self._draw_backdrop(e, background, background_gradient(e.top_background, e.bottom_background))          # casino, bestiary on a black background: the blurred scenery of the place just left
        aspect = self.width / max(1, self.height)
        if field: M, dh, w, h = self._field_camera(e, aspect); self._update_field(e)
        elif is_battle:
            M, M_scenery = self._battle_camera(e, aspect); self._M_battle = M; dh = np.array([1.0, 0, 0]); w, h = SPRITE_L * SPRITE_SCALE, SPRITE_H * SPRITE_SCALE
        else:
            M, cam3d = self._town_camera(e, aspect)
            if self.objects is not None and self.map_name == e.game_map: self._orient_scenery(cam3d); self._smooth_objects(self.alpha)         # trees, candles...: facing the camera, whatever its rotation; moving objects between two steps
            # sprites: vertical panels turned toward the camera (persistent vertex lists, one per slot)
            fh = np.array([e.cam_target[0] - e.cam_pos[0], 0, e.cam_target[2] - e.cam_pos[2]]) if e.cam_pos is not None and e.cam_target is not None else np.array([cam3d[2][0], 0.0, cam3d[2][2]]); fh /= np.linalg.norm(fh) or 1
            dh = np.cross([0, 1, 0], fh); dh /= np.linalg.norm(dh) or 1
            w, h = SPRITE_L * SPRITE_SCALE, SPRITE_H * SPRITE_SCALE
        up = np.array([0, 0, -h]) if field else np.array([0, h, 0])       # world map: the top of the sprite is north (-Z)
        to_draw = []; shadows = []; pp = self.mode == MODE_PP
        corr = self._edge_correction(); C_view = self._view_border[0] if self._view_border is not None else None          # edge correction: intensity (0 to 1) and position of the viewer's camera
        tilt = self._sprite_tilt()                                         # trial: share of the tilt of the world's verticals taken by the sprites
        rank = {}
        if field: rank = field_depth_rank(e.sprites)      # world map: flat sprites, all at the same height, stacked according to field_depth_rank
        for slot, s in sorted(e.sprites.items()):
            if s['pos'] is None or s['col'][3] < 0.02 or not s.get('visible', True): continue      # packet 14: Chara.SetVisible
            f = self._sheet(sheet_id(s, field), self.mode)
            if not f: continue
            nl = f['nl']
            col, row = s['pose'] % f.get('cols', 3), s['dir'] % nl          # (boat: 12 columns of 40 x 40, see hd.SPECIAL_SHEETS)
            pw, ph = f.get('pw', 1.0), f.get('ph', 1.0)                       # boat panel: 0.8 x 0.8 versus 0.48 x 0.64 for a character
            sleeping = bool(s.get('sleeping')) and not field and not is_battle and cam3d is not None          # lying character (packet 16): drawn flat by lying_pose, see further down
            u0, u1, v0, v1 = HD.uv_cell(f, col, row)                                 # cell (col, row) of the atlas (original or HD), gutters excluded
            p = np.array([(whole_pixels(s['pos'][0]) if pp else s['pos'][0]) / CH_PX, 0.04 + rank.get(slot, 0) * 0.0005, (whole_pixels(s['pos'][1]) if pp else s['pos'][1]) / CH_PX]) if field else np.array(s['pos'], float)       # world map: flat sprite (top view), the hero (slot 0) above the others
            if cam3d is not None:
                # Chara.normalBillboard: the panel (0.48 x 0.64) takes the camera's rotation, so it faces the screen (not squashed by the tilt); its vertices
                # are offset in depth (bottom 0.185, top 0.65 toward the camera, Chara.SystemInit): once on screen it behaves like a standing character for the z-buffer
                k = 1.0 if os.environ.get('DQ_NO_SCALE') else min(max(float(s.get('scale', 1.0)), 0.0), 8.0)          # packet 13, bit 11: Chara.SetScale (Transform.localScale of the panel, feet fixed); 1.0 almost everywhere, 0.738 for the hero and the partner in mlb1a, 0.952 in ev07 (the rest: start values of a frame)
                wk, hk = w * k * pw, h * k * ph
                r_, u_, f_ = self._sprite_reference(cam3d, corr); zb, zh = 0.185 / 0.48 * wk, (0.65 - 0.185) / 0.48 * wk       # depth offsets at the panel's scale (the top tilts 36 degrees toward the camera: the panel is almost vertical in the world)
                if ENGINE.ENGINE_V2: zb, zh = 0.2 / 0.48 * w * k, (0.44677734 - 0.2) / 0.48 * w * k          # DQ V / VI (Chara.SetSharedMesh): bottom 0.2, top 0.4468 toward the camera, whatever the mesh's size
                # Original panel by default ("exactly as in the game": see _edge_correction, _sprite_reference and _town_panel): the top of the panel, being closer, moves
                # away from the center of the image and the sprite "leans" outward near the left / right edges (up to 15 to 20 degrees in 16/9), as on mobile but more visible with the
                # field of view of 16/9; the edge correction (vertical edges, constant proportions, unchanged size and depth) remains available (DQ_EDGE_CORRECTION=100), as
                # does the shear (DQ_SPRITE_TILT, see _sprite_tilt)
                if sleeping and C_view is not None:
                    # Lying pose (packets 16 / 17, Chara.SetSleepPose, shader Custom/CharaSleep; see lying_pose.py): the panel is NOT standing, the edge correction (which straightens a
                    # standing panel) is never applied to it; seen from above, at full size, rotated according to the sleeping direction, with the depth of the flat body (z-buffer,
                    # overlaps by a bed, a wall...)
                    ang, qd = s.get('sleeping_rot', (0.0, 0))
                    qv = PC.vertices(p, ang, qd, float(getattr(e, 'sleeping_height', 0.0)), wk / (PC.HALF_V2 * 2 if ENGINE.ENGINE_V2 else 0.48), cam3d, C_view, GM.profile().key)          # (DQ V / VI: a square quad, see lying_pose)
                    if qv is None: continue
                    a, b, c, d = qv
                else:
                    pb = p
                    if s.get('billboard2') and C_view is not None: pb = p - cam3d[2] * ENGINE.billboard2_pull(float(np.linalg.norm(np.array(C_view, float) - p)))          # DQ VI, Chara.normalBillboard2 (the long flight of e01din2): the panel comes toward the camera, its shadow stays
                    a, b, c, d = self._town_panel(pb, r_, u_, f_, wk, hk, zb, zb + zh, corr, C_view, tilt, True)          # (the top no longer slides outward at the left and right edges, always)
                q = np.array([a, b, c, a, c, d], np.float32).ravel().tolist()
            else:
                kf = float(getattr(e, 'field_scale', 1.0)) if field and slot in getattr(e, 'floats', ()) else 1.0          # packet 30: the balloon's panel (Field.SetFloatingObject) follows the field's scale, feet fixed: it keeps its on-screen size
                a, b = p - dh * w * kf * pw / 2, p + dh * w * kf * pw / 2; upk = up * ph * kf
                q = np.array([a, b, b + upk, a, b + upk, a + upk], np.float32).ravel().tolist()
            uv = [u0, v0, u1, v0, u1, v1, u0, v0, u1, v1, u0, v1]
            cl = list(map(float, s['col'])) * 6
            vl = self.sprite_vl.get(slot)
            if vl is None: vl = self.sprite_vl[slot] = self.prog.vertex_list(6, gl.GL_TRIANGLES, position=('f', q), tex_coords=('f', uv), colors=('f', cl))
            else: vl.position[:] = q; vl.tex_coords[:] = uv; vl.colors[:] = cl
            to_draw.append((slot, f, vl))
            if s.get('shadow', True) and not (sleeping and not s.get('shadow_separate')):       # packet 15: the engine can hide a character's shadow (world-map boats...); lying, the shadow goes 64 below the ground (Chara.lieBillboard) unless it is separate
                al = float(s['col'][3]) * float(s.get('shadow_alpha', 1.0))
                if cam3d is not None: shadows.append((slot, np.array(s['shadow_pos'], float) if s.get('shadow_separate') and s.get('shadow_pos') else p, al))
                elif field:
                    oc = s.get('shadow_field')                      # packet 15, bit 14: the engine gives the shadow's position on the world map (the balloon: further south than its feet); otherwise the shadow is under the feet
                    if oc is not None and len(oc) >= 2:
                        dx, dy = shadow_offset(self._shadow_steps.get(slot), (oc[0] - s['pos'][0], oc[1] - s['pos'][1]))          # the shadow at the balloon's own place plus the engine's offset (see shadow_offset)
                        po = np.array([p[0] + (whole_pixels(dx) if pp else dx) / CH_PX, p[1], p[2] + (whole_pixels(dy) if pp else dy) / CH_PX])
                    else: po = p
                    shadows.append((slot, po, al))
        icons = self._icons(e, field, is_battle, dh, cam3d)
        if staff: self._draw_staffroll(); to_draw = []; icons = []; shadows = []
        if os.environ.get('DQ_PROJ') and self.n_loop % int(os.environ['DQ_PROJ']) == 0: self._dbg_proj(M, e, cam3d, to_draw, icons)
        self._linked_domain = None
        self.prog.use()
        self.prog['mvp'] = tuple(float(x) for x in M.T.ravel()); self.prog['cutoff'] = 0.5; self.prog['crisp'] = 0.0; self.prog['tsize'] = (1.0, 1.0); self.prog['amul'] = 1.0
        self._gl_tint = None; self._set_tint((1.0, 1.0, 1.0))        # (the uniform is 0 until it has been written: all black)
        gl.glActiveTexture(gl.GL_TEXTURE0); late = False; chara_queue = 0; between = False
        if field: self._paint_field(e)
        elif is_battle:
            self.prog['mvp'] = tuple(float(x) for x in M_scenery.T.ravel())
            self._paint_scenery()
            self.prog['mvp'] = tuple(float(x) for x in M.T.ravel()); self.prog['cutoff'] = 0.05; self.prog['crisp'] = 1.0
            gl.glDisable(gl.GL_CULL_FACE); gl.glDepthMask(gl.GL_FALSE)         # DssaMonster* shaders: ZWrite Off, Cull Off, ZTest LEqual
            monster_meshes = self._battle_meshes(e)             # rewrites the vertices of the monsters/effects ("dirty" buffers: not yet sent to the GPU)
            self._linked_domain = None                         # forces the rebind + commit() on the 1st draw: otherwise the monsters come out with the previous frame's data
            fbx_pending = bool(e.fbx)                           # 3D models with the Effect0 shader (Transparent+300 = 3300): after the monsters (2100 to 2200), before DssaCharaTransparent (3850)
            for tex, tw, th, vl, shader, hd in monster_meshes:
                if fbx_pending and shader == 3: self._paint_fbx(e, True); fbx_pending = False
                self.prog['crisp'] = 0.0 if hd else (2.0 if (pp or not self.smoothing) else 1.0)           # enlarged texture: soft mipmap filtering; original: sharp pixels (nearest neighbor if smoothing is off)
                if shader == 3: gl.glDisable(gl.GL_DEPTH_TEST)                 # DssaCharaTransparent: ZTest Always
                self.prog['tsize'] = (float(tw), float(th)); self._paint(tex, vl)
                if shader == 3: gl.glEnable(gl.GL_DEPTH_TEST)
            if fbx_pending: self._paint_fbx(e, True)
            gl.glDepthMask(gl.GL_TRUE)
            self.prog['crisp'] = 0.0; self.prog['tsize'] = (1.0, 1.0)
        else:
            self._M_town = M; self._town_aspect = aspect
            late = e.part != ENGINE.PART_BESTIARY and not staff and not os.environ.get('DQ_TR_EARLY')          # the transparent scenery of a higher render queue than the characters' goes after them (see _paint_scenery); DQ_TR_EARLY: (test) all of it before
            chara_queue = int(getattr(e, 'chara_queue', 0))
            between = late and chara_queue > 0 and C_view is not None and self.objects is not None and self.map_name == e.game_map and self.map_name != TITLE_MAP          # DQ V: the transparent scenery of the characters' own queue is sorted with them, by distance (field._same_queue_objects)
            if e.part != ENGINE.PART_BESTIARY and not staff: self._paint_scenery(True, ('before' if between else 'early') if late else 'all', chara_queue)          # bestiary: the town map stays loaded (no packet 0) but the book's camera, at 83 units, sees only a slice of it (the church, at the edge of the far plane) behind the list and the sheet: the book shows only the monster (packets 21 / 22)
            if e.fbx and not staff: self._paint_fbx(e)               # animated 3D models (the title dragon): same queue as the opaque scenery
            if e.meshes and cam3d is not None and ENGINE.ENGINE_V2 and not staff: self._paint_ground_meshes(e, cam3d)          # DQ V's T'n'T board: its squares (Custom/DssaNormal, queue Geometry)
            if cam3d is not None and ENGINE.ENGINE_V2 and not staff: self._paint_sugoroku_arrows(e)          # DQ V's T'n'T board: the direction arrows (window Make_Sugoroku_Arrow, in the scene)
        self.prog['cutoff'] = 0.05
        if shadows and not os.environ.get('DQ_NO_SHADOW'):
            if cam3d is not None: self._draw_character_shadows(shadows, cam3d)
            elif field: self._draw_field_shadows(shadows, w * CH_PX / 24.0)
        sequence = [(None, x) for x in to_draw]
        same = self._same_queue_objects(chara_queue, C_view) if between else ()
        if same:                                                           # farthest first; at equal distance the characters keep their order
            eye = np.asarray(C_view, float); far = []
            for n, (slot, f, vl) in enumerate(to_draw):
                q = np.asarray(vl.position[:], float).reshape(6, 3); far.append((-float(np.linalg.norm((q[0] + q[1] + q[2] + q[5]) / 4.0 - eye)), 0, n, (None, (slot, f, vl))))
            far += [(-d, 1, n, ((key, first, count), None)) for n, (d, key, first, count) in enumerate(same)]
            sequence = [x[3] for x in sorted(far, key=lambda x: x[:3])]
        run = []                                                           # (consecutive objects of the scenery between two characters: one pass)
        for scenery, sprite in sequence + [(None, None)]:
            if scenery is not None: run.append(scenery); continue
            if run: self._paint_scenery(True, 'same', chara_queue, run); self.prog['cutoff'] = 0.05; run = []
            if sprite is None: break
            slot, f, vl = sprite
            self.prog['crisp'] = 0.0 if f['hd'] else (2.0 if (pp or not self.smoothing) else 1.0); self.prog['tsize'] = (float(f['tw']), float(f['th']))
            hue = e.sprites[slot].get('hue') if slot in e.sprites else None          # DQ VI: Custom/CharaHueIce / CharaHueGray (frozen, petrified characters)
            if hue: self.prog['hue'] = (1.0 if hue[0] else 2.0, float(hue[1]))
            self._paint(f['t'], vl)       # like Unity (Chara shader, normal ZTest): a character hidden by scenery is not visible at all
            if hue: self.prog['hue'] = (0.0, 0.0)
        if late: self.prog['cutoff'] = 0.05; self._paint_scenery(True, 'late', chara_queue); self.prog['cutoff'] = 0.05; self.prog['crisp'] = 0.0; self.prog['tsize'] = (1.0, 1.0)
        if not field and not is_battle and e.meshes and e.part in (ENGINE.PART_TOWN,) + ENGINE.MINIGAME_PARTS and cam3d is not None:
            # Mesh effects of the town (packets 21, 22: Morph spell, splashes, the prologue's sword strikes, credits and title logo), turned toward the camera. They go AFTER the
            # characters (Custom/Chara: Transparent queue, 3000): DssaChara Transparent+100, DssaMonster0 +150, DssaMonster1 +200, DssaCharaTransparent Overlay-150 (3850, ZTest
            # Always: in front of everything, like the Riseup bubbles, from the same queue). Before, they were drawn before the characters: a character hidden by the effect
            # covering it (the cloud of the Morph spell went "behind" Elisa).
            self.prog['cutoff'] = 0.05; self.prog['crisp'] = 1.0; self.prog['tsize'] = (1.0, 1.0)
            gl.glDisable(gl.GL_CULL_FACE); gl.glDepthMask(gl.GL_FALSE)
            self._linked_domain = None
            clip = self._phone_clip() if e.part == ENGINE.PART_BESTIARY else None          # the bestiary's monster: cut at the phone's screen edges, as the game shows it (a large one goes past the parchment)
            if clip: gl.glEnable(gl.GL_SCISSOR_TEST); gl.glScissor(*clip)
            for tex, tw, th, vl, shader, hd in self._battle_meshes(e, np.column_stack(cam3d), True):
                self.prog['crisp'] = 0.0 if hd else (1.0 if self.smoothing else 2.0)
                if shader == 3: gl.glDisable(gl.GL_DEPTH_TEST)
                self.prog['tsize'] = (float(tw), float(th)); self._paint(tex, vl)
                if shader == 3: gl.glEnable(gl.GL_DEPTH_TEST)
            if clip: gl.glDisable(gl.GL_SCISSOR_TEST)
            gl.glDepthMask(gl.GL_TRUE); self.prog['crisp'] = 0.0; self.prog['tsize'] = (1.0, 1.0)
        if (field or (not is_battle and cam3d is not None)) and getattr(e, 'particles', None) is not None: self._paint_particles(e, field)          # DQ V / VI particles (snow, petals, leaves): Transparent+400, after the characters and mesh effects
        if icons:                                                            # icon bubbles (the item that rises out of a chest, a jar, a wardrobe; the "!" above a character): Custom/Riseup is
            self.prog['cutoff'] = 0.05; self.prog['crisp'] = 2.0 if (pp or not self.smoothing) else 1.0; self.prog['tsize'] = (32.0, 32.0)          # queue Overlay-150 with ZTest and ZWrite off (read in the
            gl.glDisable(gl.GL_DEPTH_TEST); gl.glDepthMask(gl.GL_FALSE)          # game's shader): after the mesh effects and the particles, over the whole scene (scenery in front included); only the
            for tex, vl in icons: self._paint(tex, vl)                       # orthographic layer and the windows go over it
            gl.glEnable(gl.GL_DEPTH_TEST); gl.glDepthMask(gl.GL_TRUE); self.prog['crisp'] = 0.0; self.prog['tsize'] = (1.0, 1.0)
        if not field and e.meshes and any(st.get('ortho') for st in e.mesh_state.values()):
            # DQ V / VI: meshes of the orthographic layer (packet MESH_ORTHO: logos, title texts), drawn over the scene by SceneCamera.s_DSSAOrthoCamera (depth 0.5, clears only the depth)
            self.prog['mvp'] = tuple(float(x) for x in self._ortho_matrix(aspect, e).T.ravel()); self.prog['cutoff'] = 0.05; self.prog['tsize'] = (1.0, 1.0)
            gl.glDisable(gl.GL_DEPTH_TEST); gl.glDisable(gl.GL_CULL_FACE)
            self._linked_domain = None
            for tex, tw, th, vl, shader, hd in self._battle_meshes(e, None, True, ortho=True):
                self.prog['crisp'] = 0.0 if hd else (1.0 if self.smoothing else 2.0); self.prog['tsize'] = (float(tw), float(th)); self._paint(tex, vl)
            gl.glEnable(gl.GL_DEPTH_TEST); self.prog['crisp'] = 0.0; self.prog['tsize'] = (1.0, 1.0)
        self.prog.stop()
        gl.glDisable(gl.GL_DEPTH_TEST)
        if not field and not is_battle and not staff: self._draw_spotlight(e, M, aspect)          # DQ V / VI Spotlight (packet 34, effect 7): over the scene, under the vignette and the interface
        if not staff: self._draw_screen_mask(e, aspect)                     # DQ VI mask (packet 34, effect 11)
        if e.part == ENGINE.PART_TOWN and not field and not staff and e.game_map != TITLE_MAP and e.game_map not in EXTENDED_SKIES: self._draw_wide_sides(e)          # (window wider than 16:9: towns and dungeons; not the title nor the maps whose sky is extended)
        if e.part != ENGINE.PART_BATTLE and not field and not staff: self._draw_vignette()          # (not on the world map, which is entirely 2D)
        self._draw_fade(True)
        if e.fbx and not field: self._draw_screen_models(e)          # (the dream filter, over the scene and the background fade, behind the windows)
        self.post.end()
        self._motion_blur(M)                                                      # ("Motion blur": the previous frame over this one, as much as the camera moved)
        if e.part != ENGINE.PART_BATTLE and not field and not staff: self._blur_edges()          # ("Edge blur": same places as the vignette)
        self._blur_sides(e)                                                       # (DQ VI's slime curling: the sides of the phone's column blurred, backdrop.py)
        self._backdrop_keep(e)                                                    # (town, world map: reduced copy of the scene, the blurred background of the mini-games)
        if ef_c is not None and not self.capture_ready:                         # Capture effect starting: the scene (without interface) is copied, then the normal frames continue (the black of a travel in progress covers everything)
            self._capture_grab(ef_c)
            if e.black_screen is not None: gl.glClearColor(0.0, 0.0, 0.0, 1.0); self.clear()
        elif ef_c is not None and self.capture_sim is not None and getattr(self.capture_sim, 'live_capture', False) and self.capture_sim.render_active:
            self._drawing_kind = 'screen effect (live)'; self._capture_live(self.capture_sim)          # DQ VI Pegasus: the scene just drawn is captured again, the effect replaces it
        self._draw_damage(e)
        self._update_message(); self._draw_name_entry(); self._draw_message(); self._draw_menus(); self._draw_title(); self._draw_fade(); self._draw_license(); self._draw_saves()          # ("Touchez l'ecran" and the copyright under the front fade, like the game's windows)
        self.dt_drawing += (time.perf_counter() - t_start - self.dt_drawing) * 0.1
        if e.effect is not None and not self.effect_tex_ready: self._clone_screen(); self.effect_tex_ready = True       # capture of the game screen, without the help lines

    def _info(self):
        """Information lines (F1): location and window, display settings, sound, performance. Nothing can be changed here: the settings are in the Esc menu."""
        e = self.engine.state; field = e.part == ENGINE.PART_FIELD and e.field is not None; is_battle = e.part == ENGINE.PART_BATTLE
        yes, no = tr(N_('window.yes')), tr(N_('window.no'))
        location = tr(N_('save_states.world_map')) if field else (tr(N_('window.battle')) % e.game_map if is_battle else tr(N_('window.map')) % (e.game_map or '?'))
        mode_name = tr(HD.MODES[self.mode][0]) if self.mode < MODE_PP else 'Pixel perfect'
        scenery = tr(N_('window.smoothed_original_rendering')) if self.mode in (0, MODE_PP) else mode_name
        self.tag_label.text = tr(N_('window.window_x_engine_frame_part')) % (location, self.width, self.height, tr(N_('window.fullscreen')) if self.is_fullscreen else '', self.engine.frames, e.part)
        self.lbl_hd.text = tr(N_('window.sprites_scenery_texture_smoothing_antialiasing')) % (
            mode_name, scenery, yes if self.smoothing else no, 'x%d' % self.post.msaa if self.post.msaa else no, round(self._engine_yaw()),
            (yes if self.sound.active else no) if self.sound.ok else tr(N_('window.unavailable')), round(self.sound.vol_music * 100), round(self.sound.vol_sfx * 100))
