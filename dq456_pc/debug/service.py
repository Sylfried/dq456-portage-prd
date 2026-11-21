"""The debug service of the window (`Game.debug`, created only with `--debug`).

Time control: pause (the engine and the sound stop, the picture stays), one engine step at a time, game clock slower or faster (the factor multiplies the one of the
game, `Game._speed_factor`: x0.1 to x16; above what the computer can do, the steps are cut by the usual budget of a displayed frame, so "faster" is as fast as the
machine allows). Overlay: frame, part, map, the shared state word decoded, both fade planes, the menu that has the input, the engine's pace.

Everything is applied on the window's thread, between two engine steps (`gate`, called at the start of each loop frame): the other debug tools (web panel) only queue
commands (`call`), so the engine is never touched while it runs."""
import collections, queue, threading, time

import pyglet
from pyglet import gl

from dq456_pc.core import engine as ENGINE
from dq456_pc.debug import recorder as RC
from dq456_pc.language import N_, tr

SPEEDS = (0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0)          # game clock factors, F5 / F6 go from one to the next, F7 back to x1
STEP_BURST = 10                                               # Shift + F4: this many engine steps at once
KEYS = {'F2': 'overlay', 'F3': 'pause', 'F4': 'step', 'F5': 'slower', 'F6': 'faster', 'F7': 'normal', 'F8': 'report', 'F9': 'panel'}          # debug keys: they take precedence over the Controls in debug mode (not while the Esc menu is open)
PARTS = {ENGINE.PART_INITIAL: 'start menu', ENGINE.PART_TOWN: 'town', ENGINE.PART_BATTLE: 'battle', ENGINE.PART_FIELD: 'world map', ENGINE.PART_CASINO: 'casino',
         ENGINE.PART_BESTIARY: 'bestiary', ENGINE.PART_CURLING: 'curling', ENGINE.PART_SLIME_RACE: 'slime race'}
FLAG_NAMES = ((ENGINE.FADE_FLAG, 'fade'), (0x2, 'effect'), (ENGINE.PLAYER_LOCKED_FLAG, 'lock'), (0x8, 'pad'), (0x20, 'map'), (0x40, 'ui lock'),
              (ENGINE.SYSTEM_BUSY_FLAG, 'busy'), (ENGINE.BGM_PLAY_FLAG, 'bgm'))          # Global.Flag bits (engine.flags); 0 entries (the game has no such bit) are skipped
NOTE_SECONDS = 4.0                                            # how long a debug note (speed changed, state saved...) stays in the overlay
CALL_TIMEOUT = 3.0                                            # seconds another thread waits for a queued command


def speed_label(f):
    """x0.25, x1, x16."""
    return 'x%g' % f


def flag_names(word):
    """Global.Flag -> 'fade lock +0x10' (named bits, then the others in hexadecimal)."""
    names = [n for b, n in FLAG_NAMES if b and word & b]
    rest = word & ~sum(b for b, _ in FLAG_NAMES)
    if rest: names.append('+%#x' % rest)
    return ' '.join(names) or '-'


def fade_text(fd):
    """One fade plane -> 'a 0.50 -> 1.00 (12)': current opacity, target, frames left."""
    if not fd: return '-'
    return 'a %.2f -> %.2f (%d)' % (fd.get('a', 0.0), fd.get('end', 0.0), fd.get('n', 0))


def overlay_lines(v):
    """The overlay's lines from a dictionary of values (see DebugService.values): pure, tested without a window."""
    head = '%s %s%s' % (tr(N_('debug.title')), speed_label(v['speed']), '  ' + tr(N_('debug.paused')) if v['paused'] else '')
    lines = [head,
             tr(N_('debug.frame_part_map')) % (v['frame'], v['loop'], v['part_name'], v['part'], v['map'] or '-'),
             tr(N_('debug.flags')) % (v['flags'], flag_names(v['flags'])),
             tr(N_('debug.fades')) % (fade_text(v['fade']), fade_text(v['background_fade']))]
    if v.get('menu') is not None: lines.append(tr(N_('debug.menu')) % v['menu'])
    if v.get('message'): lines.append(tr(N_('debug.message_shown')))
    lines.append(tr(N_('debug.pace')) % (v['steps_per_s'], v['step_ms'], v['draw_ms']))
    lines += v.get('notes', [])
    lines.append(tr(N_('debug.keys')))
    if v.get('panel'): lines.append(tr(N_('debug.panel_at')) % v['panel'])
    return lines


class DebugService:
    """Owned by the window; `gate` and `draw` are called by it, `key` gets the keys first."""
    SPEEDS = SPEEDS

    def __init__(self, game):
        self.g = game
        self.overlay = True
        self.paused = False
        self.pending_steps = 0
        self.speed = 1.0
        self.commands = queue.Queue()
        self.notes = collections.deque(maxlen=4)                 # (text, time shown until)
        self._labels = None; self._frame_box = None
        self.main_thread = threading.get_ident()
        self.step_ms = 0.0                                        # engine step duration (moving average)
        self.after_step = []                                      # callables (packets) run after each engine step (the other debug tools)
        self.report_requested = False
        self._wrap_engine_step()
        self.recorder = RC.Recorder(self)                         # recording for the bug reports (F8), replay of a report (--debug-replay)
        self.replay_path = getattr(getattr(game, 'args', None), 'debug_replay', None)
        self.ticks = []                                           # callables run at each loop frame, between two engine steps (the scene catalog)
        self.panel_commands = {}; self.panel_pages = {}           # what the scene catalog adds to the web panel: {command: fn(dict)}, {path: fn(query)}
        self.panel = None
        try:
            from dq456_pc.debug import catalog as CT
            self.catalog = CT.Catalog(self)                       # scene catalog: maps, story steps, switches, fights (the panel's "Scenes" tab)
        except Exception as ex: self.catalog = None; print('debug scene catalog not available:', ex, flush=True)
        try:
            from dq456_pc.debug import party as PY
            self.party = PY.Party(self, self.catalog)                    # members, money, items (the panel's "Party" tab)
        except Exception as ex: self.party = None; print('debug party tools not available:', ex, flush=True)
        try:
            from dq456_pc.debug import extras as EX
            self.extras = EX.Extras(self, self.party)                    # side activities, DQ VI's vocations (the panel's "Party" tab)
        except Exception as ex: self.extras = None; print('debug side systems not available:', ex, flush=True)
        try:
            from dq456_pc.debug import book as BK
            self.book = BK.Book(self)                                    # the monster book (the panel's "Book" tab)
        except Exception as ex: self.book = None; print('debug monster book not available:', ex, flush=True)
        try:
            from dq456_pc.debug import texts as TX
            self.texts = TX.Texts(self, self.catalog) if self.catalog is not None else None          # any message in the game's window (the panel's "Texts" tab)
        except Exception as ex: self.texts = None; print('debug text viewer not available:', ex, flush=True)
        port = getattr(getattr(game, 'args', None), 'debug_port', None)
        if port != 0:                                             # (--debug-port 0: no web panel)
            try:
                from dq456_pc.debug import panel as PN
                self.panel = PN.Panel(self, port)
                if self.panel.url(): print('debug panel:', self.panel.url(), flush=True)
            except Exception as ex: print('debug panel not started:', ex, flush=True)

    def _wrap_engine_step(self):
        """Engine.step timed (and followed by the `after_step` callables): the overlay's pace, the panel's packets."""
        eng = self.g.engine; orig = eng.step
        def step(*a, **k):
            rec = getattr(self, 'recorder', None)
            if rec is not None: a = rec.step_begin(a, k); k = {}
            t = time.perf_counter()
            try: r = orig(*a, **k)
            finally:
                if rec is not None: rec.step_end()
            self.step_ms += (1000.0 * (time.perf_counter() - t) - self.step_ms) * 0.1
            for f in self.after_step: f(r)
            return r
        eng.step = step

    # --- keys and time -----------------------------------------------------------------------------------------------------------------------------------------
    def key(self, symbol, modifiers):
        """A debug key -> True (handled). F2 overlay, F3 pause / resume, F4 one step (Shift: STEP_BURST), F5 / F6 slower / faster, F7 x1."""
        action = KEYS.get(pyglet.window.key.symbol_string(symbol))
        if action is None: return False
        if action == 'overlay': self.overlay = not self.overlay
        elif action == 'pause': self.set_paused(not self.paused)
        elif action == 'step': self.step(STEP_BURST if modifiers & pyglet.window.key.MOD_SHIFT else 1)
        elif action in ('slower', 'faster', 'normal'): self.set_speed(self._next_speed(action))
        elif action == 'report': self.report_requested = True          # written at the end of this frame's drawing (its picture is the screenshot)
        elif action == 'panel':
            u = self.panel.open() if self.panel is not None else None
            self.note(tr(N_('debug.panel_at')) % u if u else tr(N_('debug.panel_off')))
        return True

    def _next_speed(self, action):
        if action == 'normal': return 1.0
        i = min(range(len(SPEEDS)), key=lambda k: abs(SPEEDS[k] - self.speed))
        return SPEEDS[max(0, i - 1)] if action == 'slower' else SPEEDS[min(len(SPEEDS) - 1, i + 1)]

    def set_speed(self, f):
        self.speed = float(min(max(f, SPEEDS[0]), SPEEDS[-1])); self.note(tr(N_('debug.speed_now')) % speed_label(self.speed))

    def set_paused(self, on):
        """Pause: no engine step, sound paused; the picture and the windows stay as they are."""
        if on == self.paused: return
        self.paused = on; self.pending_steps = 0
        try: self.g.sound.set_paused(on)
        except Exception: pass
        self.g.t_loop = None; self.g.acc = 0.0

    def step(self, n=1):
        """n engine steps, then paused (pauses first if needed)."""
        if not self.paused: self.set_paused(True)
        self.pending_steps += n

    def factor(self, f):
        """The game's clock factor for this displayed frame, times the debug speed."""
        return f * self.speed

    # --- commands from other threads -------------------------------------------------------------------------------------------------------------------------------
    def call(self, fn, wait=True):
        """Runs fn() on the window's thread at the next loop frame (immediately when already on it) -> its result (wait=True) or None. Raises its exception, or TimeoutError."""
        if threading.get_ident() == self.main_thread: return fn()
        box = {'done': threading.Event()}
        self.commands.put((fn, box))
        if not wait: return None
        if not box['done'].wait(CALL_TIMEOUT): raise TimeoutError('the window did not answer')
        if 'error' in box: raise box['error']
        return box.get('result')

    def _run_commands(self):
        while True:
            try: fn, box = self.commands.get_nowait()
            except queue.Empty: return
            try: box['result'] = fn()
            except Exception as ex: box['error'] = ex
            box['done'].set()

    def gate(self):
        """Start of a loop frame: queued commands, then the pause. -> True when the frame must not advance the engine (paused; the requested steps are done here)."""
        self._run_commands()
        if self.replay_path:
            path, self.replay_path = self.replay_path, None
            try: self.recorder.start_replay(path)
            except Exception as ex: self.note(tr(N_('debug.replay_failed')) % ex)
        self.recorder.between_steps()
        for t in self.ticks:
            try: t()
            except Exception as ex: self.note('tick: %s' % ex)
        if not self.paused: return False
        g = self.g
        if self.pending_steps > 0:
            self.pending_steps -= 1
            g._step()
        g.t_loop = None; g.acc = 0.0; g.alpha = 1.0
        return True

    def note(self, text):
        """A short line shown in the overlay for a few seconds."""
        self.notes.append((text, time.time() + NOTE_SECONDS))

    # --- overlay -------------------------------------------------------------------------------------------------------------------------------------------------
    def values(self):
        """What the overlay shows (also the state the web panel reads)."""
        g = self.g; eng = g.engine; e = eng.state
        menu = None
        try:
            port = eng.active_menu()
            if port is not None:
                f = eng.ui.f.get(port, {}); name = None
                try: name = g._type_name(f.get('type'))
                except Exception: pass
                menu = 'port %s, type %s %s, cursor %s' % (port, f.get('type'), name or '', f.get('cur'))
        except Exception as ex: menu = '? (%s)' % ex                # (a window being rebuilt: its state is read again at the next frame)
        times = list(getattr(g, 'step_times', ()))[-31:]
        sps = (len(times) - 1) / max(1e-6, times[-1] - times[0]) if len(times) > 1 else 0.0
        now = time.time()
        return {'speed': self.speed, 'paused': self.paused, 'frame': eng.frames, 'loop': g.n_loop, 'part': e.part, 'part_name': PARTS.get(e.part, '?'),
                'map': e.game_map, 'flags': eng.flags(), 'fade': e.fade, 'background_fade': e.background_fade, 'menu': menu, 'message': bool(eng.msg_visible),
                'steps_per_s': sps, 'step_ms': self.step_ms, 'draw_ms': 1000.0 * getattr(g, 'dt_drawing', 0.0),
                'notes': [t for t, until in self.notes if until > now], 'panel': self.panel.url() if self.panel is not None else None}

    def write_report(self):
        """F8: the bug report (recorder.Recorder.report), with the picture just drawn."""
        self.report_requested = False
        try:
            from dq456_pc import paths as CH
            path = self.recorder.report(RC.capture_png(), CH.FOLDERS.logfiles)
            self.note(tr(N_('debug.report_written')) % path); print('bug report:', path, flush=True)
        except Exception as ex: self.note(tr(N_('debug.report_failed')) % ex)

    def draw(self):
        """The overlay, top left, over everything (called last in the frame's drawing); a requested bug report first (its screenshot has no overlay)."""
        if self.report_requested: self.write_report()
        if not self.overlay: return
        g = self.g
        try: lines = overlay_lines(self.values())
        except Exception as ex: lines = ['debug: %s' % ex]
        if self._labels is None:
            self._labels = pyglet.text.Label('', font_name=('Consolas', 'DejaVu Sans Mono', 'Courier New'), font_size=10, color=(180, 255, 180, 255), multiline=True, width=1400, anchor_y='top')
            self._frame_box = pyglet.shapes.Rectangle(0, 0, 10, 10, color=(0, 0, 0)); self._frame_box.opacity = 170
        lb = self._labels; text = '\n'.join(lines)
        if lb.text != text: lb.text = text
        lb.x, lb.y = 10, g.height - 8
        gl.glEnable(gl.GL_BLEND); gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        b = self._frame_box; b.x, b.y = 4, g.height - 12 - lb.content_height; b.width, b.height = lb.content_width + 14, lb.content_height + 8
        b.draw(); lb.draw()
