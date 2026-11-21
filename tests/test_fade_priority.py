"""the priority of the background fade plane, `Fade.SetFadePriority`, reproduced.

Read in `libil2cpp.so`: `MenuMessage.SetCanvasMesh(short no)` (0xCFA4B4), when the message window changes type, ends with `Fade.SetFadePriority(1, no == 0)` (0xCD036C) -> `FadePlane` 1
("FadeForBG") gets the shader `l_shader[priority]`, and `Fade`'s coroutine fills `l_shader` with Custom/FrontFade (0) and Custom/BackFade (1). So the background plane stays behind the windows while
the field message window is the current one (type 0) and goes in front of everything (Overlay queue) after a battle message window (types 1 to 3).

Without a window: `_draw_fade` with fake objects (order of the planes drawn), the source of the drawing.
    python3 tests/test_fade_priority.py
"""
import os, sys, types, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import pyglet
pyglet.options['shadow_window'] = False
if sys.platform.startswith('linux') and not os.environ.get('DISPLAY'): pyglet.options['headless'] = True
from dq456_pc.window import menu_display as MD


def fake(msg_type):
    st = types.SimpleNamespace(fade={'a': 0.5, 'name': 'front'}, background_fade={'a': 0.5, 'name': 'background'})
    eng = types.SimpleNamespace(state=st, ui=types.SimpleNamespace(msg={} if msg_type is None else {'type': msg_type}))
    w = types.SimpleNamespace(engine=eng, painted=[])
    w._paint_fade = lambda fd: w.painted.append(fd['name'])
    w._background_fade_in_front = lambda: MD.MenusMixin._background_fade_in_front(w)
    w._overlay_fade = lambda e, fd, background: False          # (no screen laid over the game opening or closing)
    return w


def frame(w):
    """The order of a frame: the background call (after the scene, before the windows), then the windows, then the front call."""
    MD.MenusMixin._draw_fade(w, True); w.painted.append('windows'); MD.MenusMixin._draw_fade(w)
    return w.painted


class Priority(unittest.TestCase):
    def test_field_message_window(self):
        for t in (None, 0, 0x10000):
            self.assertEqual(frame(fake(t)), ['background', 'windows', 'front'], t)

    def test_battle_message_windows(self):
        for t in (1, 2, 3):
            self.assertEqual(frame(fake(t)), ['windows', 'background', 'front'], t)

    def test_without_interface(self):
        w = fake(0); w.engine = types.SimpleNamespace(state=w.engine.state)
        self.assertEqual(frame(w), ['background', 'windows', 'front'])

    def test_every_frame_pairs_the_two_calls(self):
        """Each drawing path calls the background plane before the windows and the front plane after them (the background plane drawn in front relies on it)."""
        for name in ('game.py', 'capture_effects.py'):
            with open(os.path.join(HERE, '..', 'dq456_pc', 'window', name), encoding='utf-8') as fh: src = fh.read()
            lines = src.splitlines()
            ortho = sum(1 for i, line in enumerate(lines) if 'self._draw_fade()' in line and any(w in lines[i - 1] for w in ('self._draw_endroll(', 'self._draw_slime_touch(')))
            self.assertEqual(src.count('self._draw_fade(True)') + ortho, src.count('self._draw_fade()'), name)          # (DQ V / VI end credits and Slime Touch: the scene camera is off, only the orthographic layer and the front plane are drawn)


if __name__ == '__main__':
    unittest.main(verbosity=2)
