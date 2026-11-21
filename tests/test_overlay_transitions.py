"""the screens laid over the game (bestiary, casino, mini-games) open and close with a blur instead of a fade to black: the place blurs and darkens to the blurred backdrop, then the
screen comes out of the blur (and back when it closes).

Without a window: `BackdropMixin._overlay_fade` with fake objects (the transition's phases, what replaces the black fade, when it ends).
    python3 tests/test_overlay_transitions.py
"""
import os, sys, types, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
from dq456_pc.core import engine as ENGINE

try:
    import pyglet
    pyglet.options['shadow_window'] = False
    if sys.platform.startswith('linux') and not os.environ.get('DISPLAY'): pyglet.options['headless'] = True
    from dq456_pc.window import backdrop as BD
    ERROR = None
except Exception as ex:
    BD = None; ERROR = ex

FIELD = ENGINE.PART_FIELD


def fade(a, end, col=(0.0, 0.0, 0.0)): return {'name': 'front', 'a': a, 'end': end, 'col': col}


class Fake(BD.BackdropMixin if BD else object):
    def __init__(self):
        self.engine = types.SimpleNamespace(core=None, frames=0, state=types.SimpleNamespace(part=FIELD, fade=fade(0.0, 0.0), background_fade=fade(0.0, 0.0)))
        self.blurs = []; self.screen_blur = types.SimpleNamespace(apply=lambda W, H, **kw: self.blurs.append(kw))
    def get_framebuffer_size(self): return (1280, 720)
    def _slime_touch_up(self): return None
    def step(self, part, a, end, col=(0.0, 0.0, 0.0)):
        e = self.engine.state; e.part = part; e.fade = fade(a, end, col); self.engine.frames += 1; del self.blurs[:]
        front = self._overlay_fade(e, e.fade, False); back = self._overlay_fade(e, e.background_fade, True)
        return front, back


@unittest.skipIf(BD is None, 'pyglet / window not importable here: %r' % (ERROR,))
class Transitions(unittest.TestCase):
    def test_fade_black(self):
        self.assertTrue(BD.fade_black(fade(0.5, 1.0)))
        self.assertFalse(BD.fade_black(fade(0.5, 1.0, (1.0, 1.0, 1.0))), 'fondu blanc : garde')

    def test_open_then_close(self):
        w = Fake(); BOOK = BD.OVERLAY_PARTS[0]; dark = lambda a: 1.0 - (1.0 - BD.BACKDROP_LUM) * a
        w.overlay_opening = True                                                   # Global::startBook
        self.assertEqual(w.step(FIELD, 0.5, 1.0), (True, True), 'la place se floute (pas de noir)')
        self.assertAlmostEqual(w.blurs[0]['mix'], 0.5); self.assertAlmostEqual(w.blurs[0]['lum'], dark(0.5))
        self.assertEqual(w.step(BOOK, 0.6, 0.0), (True, True)); self.assertAlmostEqual(w.blurs[0]['lum'], 1.0, msg="l'ecran sort du flou")
        self.assertEqual(w.step(BOOK, 0.0, 0.0), (False, False), 'fin de l\'ouverture'); self.assertIsNone(w.overlay_tr)
        self.assertEqual(w.step(BOOK, 0.3, 1.0), (True, True), 'fermeture'); self.assertEqual(w.overlay_tr['phase'], 'close')
        self.assertEqual(w.step(FIELD, 0.5, 0.0), (True, True)); self.assertAlmostEqual(w.blurs[0]['lum'], dark(0.5))
        self.assertEqual(w.step(FIELD, 0.0, 0.0), (False, False), 'fin de la fermeture'); self.assertIsNone(w.overlay_tr)

    def test_other_fades_stay(self):
        w = Fake()
        self.assertEqual(w.step(FIELD, 0.5, 1.0), (False, False), 'fondu au noir ordinaire (porte, combat) : garde')
        w.overlay_opening = True
        self.assertFalse(w.step(FIELD, 0.5, 1.0, (1.0, 1.0, 1.0))[0], 'fondu blanc : garde')

    def test_forgotten(self):
        w = Fake(); w.overlay_opening = True; w.step(FIELD, 1.0, 1.0)
        for _ in range(BD.TRANSITION_STEPS + 1): w.step(FIELD, 1.0, 1.0)
        self.assertIsNone(w.overlay_tr, 'une transition qui ne finit pas est oubliee')


if __name__ == '__main__':
    unittest.main()
