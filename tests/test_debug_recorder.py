"""Bug reports of the debug mode (dq456_pc/debug/recorder.py): what each engine step received is recorded (keys, menu messages sent between two steps, the interface ->
engine messages of the step), savestates are kept every SNAPSHOT_STEPS steps (the KEEP last ones), F8 writes a zip, and the replay gives the engine exactly the recorded
inputs (the window's own menu messages are dropped meanwhile). Stand-in window and engine: the CI has neither.

    python3 tests/test_debug_recorder.py
"""
import json, os, sys, tempfile, unittest, zipfile

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)
os.environ['DQ_DEBUG_SNAPSHOT_STEPS'] = '5'
import pyglet
pyglet.options['shadow_window'] = False
from dq456_pc.core import engine as ENGINE
from dq456_pc.debug import recorder as RC
from dq456_pc.debug import service as DS


class State:
    part = ENGINE.PART_TOWN; game_map = 'mbk1f1'; fade = None; background_fade = None; load_wait = None; loading = None


class Engine:
    def __init__(self):
        self.frames = 0; self.state = State(); self.ui = type('U', (), {'f': {}})(); self.msg_visible = False; self.log = type('L', (), {'lines': ['a', 'b']})()
        self.received = []                                   # what the engine got: (frame, held, decl, menus, messages)
        self._menus = []; self.window_data = b''
    def send_menu(self, port, *args): self._menus.append([port] + list(args))
    def _send_messages(self, data): self._got = data
    def step(self, held=0, decl=None):
        self._got = None
        self._send_messages(self.window_data)                 # (Engine.step sends the interface -> engine messages of the step: camera, map objects...)
        self.received.append((self.frames, held, decl, self._menus, self._got)); self._menus = []
        self.frames += 1; return []
    def flags(self): return 0
    def active_menu(self): return None


class Game:
    def __init__(self, replay=None):
        self.engine = Engine(); self.map_name = 'mbk1f1'; self.n_loop = 0; self.t_loop = None; self.acc = 0.0; self.alpha = 1.0; self.height = 720
        self.sound = type('S', (), {'set_paused': lambda s, on: None, 'stop_loops': lambda s: setattr(s, 'quieted', getattr(s, 'quieted', 0) + 1)})(); self.args = type('A', (), {'settings': None, 'debug_replay': replay})()
    def _complete_savestate(self): return {'frames': self.engine.frames}
    def _step(self): self.engine.step(0, 0)
    def _apply_savestate(self, d): self.engine.frames = d['frames']


def play(g, d, n, script):
    """n loop frames of one engine step each; script(frame) -> (held, decl, menu or None, window messages)."""
    for _ in range(n):
        if d.gate(): continue                                  # (paused: the loop does no step, as Game._loop)
        held, decl, menu, data = script(g.engine.frames)
        if menu: g.engine.send_menu(*menu)
        g.engine.window_data = data
        g.engine.step(held, decl)


def script(f):
    return (f % 3, f % 2, [7, 1, f % 4, 0x10] if f % 6 == 0 else None, b'cam%d' % f)


class Recorder(unittest.TestCase):
    def test_record_report_replay(self):
        g = Game(); d = DS.DebugService(g)
        play(g, d, 40, script)
        r = d.recorder
        self.assertEqual(len(r.snapshots), RC.KEEP)
        self.assertEqual([s[0] for s in r.snapshots], [20, 25, 30, 35])          # (one every 5 steps; the oldest ones dropped)
        self.assertEqual(r.steps[0]['frame'], 20)                                # (the steps before the oldest savestate dropped)
        st = r.steps[10 - 0]
        self.assertEqual((st['frame'], st['held'], st['decl'], st['menus'], bytes.fromhex(st['data'])), (30, 0, 0, [[7, 1, 2, 0x10]], b'cam30'))
        with tempfile.TemporaryDirectory() as tmp:
            path = r.report(b'png', tmp)
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist()); info = json.loads(z.read('info.json')); steps = json.loads(z.read('steps.json'))
            self.assertTrue({'start.dqs', 'now.dqs', 'steps.json', 'info.json', 'screenshot.png', 'engine_log.txt'} <= names)
            self.assertEqual((info['frame_start'], info['frame_end'], info['steps'], len(steps)), (20, 40, 20, 20))
            original = [x for x in g.engine.received if x[0] >= 20]
            g2 = Game(replay=path); d2 = DS.DebugService(g2)
            play(g2, d2, 25, lambda f: (1, 1, [9, 1, 3, 0x20], b'other'))           # the window would send other keys and messages: the recording's are used
            got = [x for x in g2.engine.received]
            self.assertEqual(got[:20], original)
            self.assertTrue(d2.paused)                                            # at the end of the recording: paused
            self.assertIsNone(d2.recorder.replay)
            self.assertEqual(len(got), 20)                                         # (nothing after the end of the recording)


if __name__ == '__main__':
    unittest.main()
