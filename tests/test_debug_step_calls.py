"""The debug panel's scene starts keep their packets and leave no looping sound behind.

  python3 tests/test_debug_step_calls.py

1. Engine._step_calls: the calls queued for a step (Engine.step_calls: the panel's startTown, forceBrew...) are made right before GameUpdate. Their packets went on from
   where the last step had left the engine's output cursors, then GameUpdate rewound the cursors and wrote over them: a fight started from the panel kept the
   place's music (Global::startBattle's SoundManager::battlePlay was lost). The cursors are rewound first, as GameUpdate does, and these packets read before it.
2. Sound.stop_loops: the looping sound effects in progress are stopped (a jump skips the scripts that stop them: DQ V's ticking clock at the start); the others go on.
"""
import os, struct, sys, types, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from dq456_pc.core import engine as ENGINE
from dq456_pc import sound as SOUND


class StepCalls(unittest.TestCase):
    def engine(self):
        e = ENGINE.Engine.__new__(ENGINE.Engine); log = []
        e.core = types.SimpleNamespace(outp=0x1000, outf=0x9000, mem_w=lambda a, b: log.append(('w', a, struct.unpack('<Q', b)[0])))
        e.pk = types.SimpleNamespace(ci=0x50, cf=0x58, begin=lambda: log.append('begin'), packets=lambda: log.append('read') or [[37, [0x230a], []]])
        return e, log

    def test_nothing_queued(self):
        e, log = self.engine(); self.assertEqual(e._step_calls(), []); self.assertEqual(log, [])
        e.step_calls = lambda: []; self.assertEqual(e._step_calls(), []); self.assertEqual(log, [])

    def test_cursors_rewound_then_the_calls_then_their_packets(self):
        e, log = self.engine(); e.step_calls = lambda: [lambda: log.append('call')]
        self.assertEqual(e._step_calls(), [[37, [0x230a], []]])
        self.assertEqual(log, [('w', 0x50, 0x1004), ('w', 0x58, 0x9000), 'begin', 'call', 'read', 'begin'])


class StopLoops(unittest.TestCase):
    def test_only_the_looping_effects_stop(self):
        s = SOUND.Sound.__new__(SOUND.Sound); s.fanfare = None; stopped = []
        def sfx(ident, looping):
            p = types.SimpleNamespace(pause=lambda: None, delete=lambda: stopped.append(ident))
            return SOUND._SFX(ident, p, 0.0, 'effects', 1.0, looping=looping)
        s.sfx = [sfx(12, True), sfx(40, False), sfx(77, True)]
        s.stop_loops()
        self.assertEqual(sorted(stopped), [12, 77]); self.assertEqual([x.ident for x in s.sfx], [40])


if __name__ == '__main__':
    unittest.main()
