"""Dragon Quest V's Slime Touch (SlimeTouchPort, SlimeTouchBehaviour, CgController: core/slime_touch.py), without a window or an engine (DQ_APK_DQ5 for the prefabs of the real data):

1. the port's messages: touch boxes on / off, a CG drawn this frame, a CG or a board played (its picture, its clip "Take <track + 1>");
2. each frame: a CG only advances while drawn; [1, set] when the set of CGs playing changes, [2, set] when some have ended; a loop never ends; bit 31 of a board's play is never sent;
3. the boxes: a touch inside one (only while they are on), the first one hit; a direction (diagonals included) points at a box; window pixels -> orthographic units;
   the game's screen (256 x 384 units) across the window's whole height (the phone's black margins above and below left out: it was small in the middle), its whole
   width in a window narrower than it;
4. the boards are shifted by (cg - 4) x 42 in x, the other CGs keep their prefab's place;
5. the interface: the port of category 5 is a SlimeTouchPort in DQ V; its messages go to it; closing it ends it; DQ V / VI's cmd_mesh shows its mesh (MeshChara.SetVisible(true));
6. (real data) the prefabs `SlimeTouch/...` are read with their clips, `gameover_p` with Unity's built-in Quad.

    python3 tests/test_slime_touch.py
"""
import os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
from dq456_pc.core import slime_touch as ST

LENGTHS = {1: {'start': 3.667}, 2: {'game_over': 2.0}, 3: {'ase': 0.667}}
for _cg in range(4, 8): LENGTHS[_cg] = {'Take %03d' % (k + 1): (0.0, 0.6, 0.017, 0.533, 0.017, 0.467)[k] for k in range(6)}


def new():
    return ST.new(12, lambda cg: LENGTHS[cg])


def frames(st, n, draw=()):
    out = []
    for _ in range(n):
        for cg in draw: ST.notice(st, [1, cg])
        out += ST.update(st)
    return out


class Messages(unittest.TestCase):
    def test_touch_state(self):
        st = new(); self.assertFalse(st['touch'])
        ST.notice(st, [0, 1]); self.assertTrue(st['touch'])
        ST.notice(st, [0, 0]); self.assertFalse(st['touch'])

    def test_play_then_end(self):
        st = new()
        ST.notice(st, [2, 14, 1, 0])                                         # (the engine plays "start": id 14, cg 1)
        self.assertEqual(ST.update(st), [[1, 2]])                          # the set changed: [1, set]
        self.assertEqual(frames(st, 30), [])                               # not drawn: frozen, never ends
        out = frames(st, 115, draw=(1,))                                    # 3.667 s drawn = 111 frames; the end is heard at the next frame
        self.assertEqual(out, [[2, 0]])
        self.assertFalse(st['cg'][1]['playing']); self.assertEqual(st['cg'][1]['t'], 0.0)

    def test_loop_never_ends(self):
        st = new(); ST.notice(st, [2, 16, 3, 0])
        self.assertEqual(ST.update(st), [[1, 8]])
        self.assertEqual(frames(st, 200, draw=(3,)), [])
        self.assertTrue(st['cg'][3]['playing'])

    def test_board(self):
        st = new(); ST.notice(st, [2, 8, 5, 1])                            # board 4 (id 8), picture 5 (board/bes), "Take 002"
        self.assertEqual(st['picture'][4], 'board/bes'); self.assertEqual(st['cg'][4]['clip'], 'Take 002')
        self.assertEqual(ST.update(st), [[1, 16]])                         # (bit 31 is dropped)
        out = frames(st, 20, draw=(4,))
        self.assertEqual(out, [[2, 0]])
        ST.notice(st, [2, 8, 4, 1])                                         # the same board played again: a [1, set] even though its set is the same
        self.assertEqual(ST.update(st), [[1, 16]])
        ST.notice(st, [2, 8, 7, 0]); ST.notice(st, [2, 8, 4, 6])           # (picture or clip out of range: ignored)
        self.assertEqual(st['picture'][4], 'board/slime')

    def test_shown_and_shift(self):
        st = new(); ST.notice(st, [2, 14, 1, 0]); ST.notice(st, [2, 9, 4, 1])
        for cg in (1, 5): ST.notice(st, [1, cg])
        ST.update(st)
        sh = {cg: (name, shift, pic) for cg, name, clip, t, pic, shift in ST.shown(st)}
        self.assertEqual(sh[1], ('SlimeTouch/start_p', 0.0, None))         # the countdown keeps its place
        self.assertEqual(sh[5], ('SlimeTouch/board_p', 42.0, 'board/slime'))
        self.assertEqual(ST.update(st), []); self.assertEqual(ST.shown(st), [])          # nothing drawn this frame


class Boxes(unittest.TestCase):
    def test_touch(self):
        st = new()
        self.assertIsNone(ST.touch(st, -68, -75))                          # boxes off
        ST.notice(st, [0, 1])
        self.assertEqual(ST.touch(st, -68, -75), [0, 0])
        self.assertEqual(ST.touch(st, 68 + 19, -155 - 14), [0, 7])
        self.assertIsNone(ST.touch(st, 0, 0))
        st['live'] = False; self.assertIsNone(ST.touch(st, -68, -75))

    def test_directions(self):
        self.assertEqual(ST.box_of_direction(ST.UP | ST.LEFT), 0)
        self.assertEqual(ST.box_of_direction(ST.RIGHT), 4)
        self.assertEqual(ST.box_of_direction(ST.DOWN | 0x10), 6)          # (the A bit does not count)
        self.assertIsNone(ST.box_of_direction(0))
        self.assertIsNone(ST.box_of_direction(ST.UP | ST.DOWN))
        for b in range(8):                                                  # each box has its direction, on its side of the board's middle
            d = [k for k, v in ST.DIRECTIONS.items() if v == b][0]
            cx, cy = ST.BOXES[b]
            if d & ST.UP: self.assertEqual(cy, -75.0)
            if d & ST.DOWN: self.assertEqual(cy, -155.0)
            if not d & (ST.UP | ST.DOWN): self.assertEqual(cy, -114.0)

    def test_window_to_layer(self):
        self.assertEqual(ST.window_to_layer(640, 360, 1280, 720, 228.5), (0.0, 0.0))
        x, y = ST.window_to_layer(640, 0, 1280, 720, 228.5); self.assertAlmostEqual(y, -228.5)

    def test_screen_across_the_window_height(self):
        try:
            import pyglet
            pyglet.options['shadow_window'] = False
            from dq456_pc.window import slime_touch as WST
        except Exception as ex: self.skipTest('window not importable here: %r' % (ex,))
        self.assertEqual(WST.half_height(16 / 9), 192.0); self.assertEqual(WST.half_height(), 192.0)
        x, y = ST.window_to_layer(640, 720, 1280, 720, WST.half_height(16 / 9)); self.assertAlmostEqual(y, 192.0)          # (the top of the window: the top of the board)
        self.assertAlmostEqual(WST.half_height(0.5), 256.0)                                                               # (narrower than 256 x 384: its whole width, 128 / 0.5)


class Interface(unittest.TestCase):
    def setUp(self):
        from dq456_pc import games as GM
        from dq456_pc.core import engine as ENGINE
        self.GM, self.ENGINE = GM, ENGINE
        self.old = (ENGINE.ENGINE_V2, GM.profile)
        ENGINE.ENGINE_V2 = True; GM.profile = lambda: GM.GAMES['dq5']

    def tearDown(self):
        self.ENGINE.ENGINE_V2, self.GM.profile = self.old

    def test_port(self):
        ui = self.ENGINE.Interface()
        ui.apply([(0, [12, 1, 5, 0]), (12, [0, 1]), (12, [2, 14, 1, 0])])
        self.assertTrue(ui.slime_touch['live']); self.assertEqual(ui.slime_touch['pending'], [[0, 1], [2, 14, 1, 0]])
        ui.apply([(0, [12, 2])]); self.assertFalse(ui.slime_touch['live'])

    def test_mesh_shown_by_its_packet(self):
        st = self.ENGINE.State()
        st.apply([(21, [24, 10900], []), (23, [24, 0], [])], lambda: '')
        self.assertFalse(st.mesh_state[24]['visible'])
        st.apply([(22, [24, 4, -1, 0, 1, 2], [0.0, 0.0, 0.0] + [0.0] * 20)], lambda: '')
        self.assertTrue(st.mesh_state[24]['visible'])


APK5 = os.environ.get('DQ_APK_DQ5', '')


@unittest.skipUnless(os.path.isfile(os.path.join(APK5, 'split_UnityDataAssetPack.apk')), 'DQ_APK_DQ5 not set (a folder with the 3 APK files of Dragon Quest V)')
class RealPrefabs(unittest.TestCase):
    def test_prefabs(self):
        import tempfile
        from dq456_pc.world import data as D
        d = D.Data(os.path.join(APK5, 'split_UnityDataAssetPack.apk'), tempfile.mkdtemp())
        lengths = {cg: {k: c['dur'] for k, c in d.prefab_model(ST.prefab(cg))['clips'].items()} for cg in (1, 2, 3, 4)}
        self.assertAlmostEqual(lengths[1]['start'], 3.667, places=2)
        self.assertEqual(sorted(lengths[4]), ['Take %03d' % k for k in range(1, 7)])
        go = d.prefab_model('SlimeTouch/gameover_p')
        self.assertEqual(len(go['v']), 4)                                   # Unity's built-in Quad
        self.assertEqual(go['groups'][0]['st'][:2], (0.6666600108146667, 0.5))
        for name in ST.BOARD_PICTURES: self.assertIsNotNone(d.texture(name.split('/')[-1]))


if __name__ == '__main__':
    unittest.main()
