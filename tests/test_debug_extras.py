"""Debug mode, the side activities and DQ VI's vocations (dq456_pc/debug/extras.py), in the game's own data. Stand-in engine: the CI has no game.

    python3 tests/test_debug_extras.py

(Real-engine check: DQ VI save 1: contest rank 5 then `StoryStatus::getContestRank` says 5, arena ranks, a curling course opened and a best score, a vocation at
level 8 (`HaveJob::getJobLevel` says 8); DQ V save 2: the Slime Touch ranking; DQ IV: no side activity.)
"""
import os, struct, sys, types, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, '..'))
from dq456_pc.debug import extras as EX

GF, CURL, TOUCH = 0x100, 0x200, 0x300
JOB_NAMES = {1: 'Guerrier', 2: 'Artiste martial'}


class Core:
    def __init__(self):
        self.ram = bytearray(0x2000); self.flags = set(); self.calls = []
        self.syms = {EX.GLOBAL_FLAG: GF, EX.CURLING: CURL, EX.TOUCH: TOUCH, EX.FLAG_SET: 0x900, EX.FLAG_REMOVE: 0x904, EX.FLAG_CHECK: 0x908, EX.JOB_LEVEL: 0x90c,
                     EX.SET_JOB_LEVEL: 0x910, '_ZN4ardq7TextAPI11extractTextEPci': 0x914}
        struct.pack_into('<H', self.ram, CURL + EX.CURLING_OPEN, 1)
    def export(self, n): return self.syms.get(n)
    def mem(self, a, n): return bytes(self.ram[a:a + n])
    def mem_w(self, a, b): self.ram[a:a + len(b)] = b
    def scratch(self, name, n): return 0x1800
    def read_cstr(self, a): return bytes(self.ram[a:self.ram.index(0, a)])
    def call(self, f, *args, label=None):
        self.calls.append((label, args))
        if f == 0x900: self.flags.add(args[1])
        elif f == 0x904: self.flags.discard(args[1])
        elif f == 0x908: return int(args[1] in self.flags)
        elif f == 0x90c: return self.ram[args[0] + 8 + args[1]]
        elif f == 0x910: self.ram[args[0] + 8 + args[1]] = args[2]
        elif f == 0x914: self.mem_w(args[0], (JOB_NAMES.get(args[1] & 0xffffff) or '<ERR>').encode() + b'\0')
        return 0


class ExtrasTab(unittest.TestCase):
    def extras(self, key):
        gm = EX.GM; EX.GM = types.SimpleNamespace(profile=lambda: types.SimpleNamespace(key=key)); self.addCleanup(setattr, EX, 'GM', gm)
        core = Core(); s = types.SimpleNamespace(g=types.SimpleNamespace(engine=types.SimpleNamespace(core=core)), panel_pages={}, panel_commands={}, notes=[])
        s.note = s.notes.append
        party = types.SimpleNamespace(characters=lambda: [(1, 'Rek'), (2, 'Olivier')], _status_infos=lambda: [(1, 0x1000), (2, 0x1400)])
        return EX.Extras(s, party), core, s

    def test_dq6_contest_and_arena_are_switches(self):
        x, core, s = self.extras('dq6')
        self.assertEqual(x.set_value({'id': 'contest', 'value': 5}), {'ok': True}); self.assertEqual(core.flags, {0x209, 0x20a, 0x20b, 0x20c})
        self.assertEqual(x.set_value({'id': 'contest', 'value': 2}), {'ok': True}); self.assertEqual(core.flags, {0x209})
        self.assertEqual(x.set_value({'id': 'arena', 'value': 3}), {'ok': True}); self.assertEqual(core.flags, {0x209, 0x275, 0x276, 0x277})
        sy = {v['id']: v for v in x.info()['systems']}
        self.assertEqual((sy['contest']['value'], sy['arena']['value']), (2, 3))
        for c in ({'id': 'contest', 'value': 9}, {'id': 'contest', 'value': 0}, {'id': 'arena', 'value': 9}, {'id': 'touch', 'value': 1}, {'id': 'arena', 'value': 'x'}):
            self.assertIn('error', x.set_value(c), c)

    def test_dq6_curling(self):
        x, core, s = self.extras('dq6')
        x.set_value({'id': 'curling_open', 'i': 3, 'value': 1}); x.set_value({'id': 'curling_open', 'i': 0, 'value': 0}); x.set_value({'id': 'curling_best', 'i': 2, 'value': 777})
        self.assertEqual(struct.unpack_from('<H', core.ram, CURL + EX.CURLING_OPEN)[0], 0b1000)
        self.assertEqual(struct.unpack_from('<H', core.ram, CURL + EX.CURLING_BEST + 4)[0], 777)
        self.assertIn('error', x.set_value({'id': 'curling_best', 'i': 6, 'value': 1}))

    def test_dq6_vocations(self):
        x, core, s = self.extras('dq6')
        self.assertEqual(x.set_job({'id': 2, 'job': 2, 'level': 8}), {'ok': True})
        j = x.jobs(); self.assertEqual(j['jobs'][:2], [(1, 'Guerrier'), (2, 'Artiste martial')]); self.assertEqual(len(j['jobs']), EX.JOBS)
        self.assertEqual(j['members'][1]['levels'][:3], [0, 8, 0])
        for c in ({'id': 3, 'job': 1, 'level': 1}, {'id': 1, 'job': 19, 'level': 1}, {'id': 1, 'job': 1, 'level': 9}): self.assertIn('error', x.set_job(c), c)

    def test_dq5_touch_and_dq4_none(self):
        x, core, s = self.extras('dq5')
        self.assertEqual(x.set_value({'id': 'touch', 'i': 1, 'value': 123}), {'ok': True})
        self.assertEqual(struct.unpack_from('<i', core.ram, TOUCH + EX.TOUCH_STRIDE)[0], 123); self.assertIsNone(x.info()['jobs'])
        self.assertIn('error', x.set_job({'id': 1, 'job': 1, 'level': 1}))
        x, core, s = self.extras('dq4'); self.assertEqual(x.info()['systems'], [])


if __name__ == '__main__':
    unittest.main()
