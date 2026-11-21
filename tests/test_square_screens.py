"""Square screens (the bestiary, the map overviews, the shop pictures): their messages go under them, at the bottom of the screen, drawn over everything (the
bestiary's reward message was under its list's buttons); the bestiary a quarter larger, its parchment kept on the screen; the maps a quarter larger, at most what
the screen holds.

    python3 tests/test_square_screens.py
"""
import os, sys, types, unittest

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)
from dq456_pc.core import engine as ENGINE
from dq456_pc.persistence import settings as RG
from dq456_pc.window.constants import MSG_BOTTOM

try:
    import pyglet
    pyglet.options['shadow_window'] = False
    from dq456_pc.window import menu_display as MD
    ERROR = None
except Exception as ex:
    MD = None; ERROR = ex


def window(typ, bottom, pictures=None):
    return types.SimpleNamespace(typ=typ, bottom=lambda: bottom, pictures=pictures)


@unittest.skipIf(MD is None, 'pyglet / window not importable here: %r' % (ERROR,))
class Square(unittest.TestCase):
    def game(self, part, windows, names, width=1280, height=720):
        j = types.SimpleNamespace(width=width, height=height, args=types.SimpleNamespace(original_message=False, ui_pos='center'), ui_k=1.0,
                                  engine=types.SimpleNamespace(state=types.SimpleNamespace(part=part)), ui_g=types.SimpleNamespace(f=windows),
                                  ui_msg=types.SimpleNamespace(type=0, bottom=lambda: -165.0), _type_name=lambda t: names.get(t))
        for n in ('_square_screen', '_dz_message', '_ui_custom', '_book_place', '_ui_place', '_ui_reference', 'book_frame'):
            setattr(j, n, types.MethodType(getattr(MD.MenusMixin, n), j))
        return j

    def test_message_under_the_square_screen(self):
        """a window lower than the message keeps it up in a town (it would be under it); the book, a map or a shop picture puts it at the bottom of the screen"""
        names = {28: 'MonsterEncyclopedia', 30: 'CityMap', 40: 'ShopList'}
        low = {12: window(28, -402.0)}
        self.assertEqual(self.game(ENGINE.PART_TOWN, {5: window(40, -390.0)}, names)._dz_message([5]), 0.0)
        self.assertEqual(self.game(ENGINE.PART_BESTIARY, low, names)._dz_message([12]), MSG_BOTTOM + 165.0)
        self.assertEqual(self.game(ENGINE.PART_TOWN, {3: window(30, -390.0)}, names)._dz_message([3]), MSG_BOTTOM + 165.0)
        self.assertEqual(self.game(ENGINE.PART_TOWN, {4: window(41, -390.0, pictures=[1])}, names)._dz_message([4]), MSG_BOTTOM + 165.0)
        self.assertFalse(self.game(ENGINE.PART_TOWN, {5: window(40, -390.0)}, names)._square_screen([5]))

    def test_book_a_quarter_larger_kept_on_the_screen(self):
        """the book's frame: its size around the bottom edge (BOOK_SCALE by default); larger than what keeps the parchment's top on the screen: that size, the bottom edge kept"""
        j = self.game(ENGINE.PART_BESTIARY, {}, {})
        s = 720 / 800.0; v = [RG.BOOK_SCALE, 0.0, 0.0]
        cx, cy, s2 = j._book_place('bottom', s, v)
        self.assertEqual((cx, cy, s2), (640.0, 400.0 * s * 1.25, s * 1.25))
        self.assertLessEqual(cy + MD.BOOK_TOP * s2, MD.SQUARE_FIT * 720.0)                        # (the parchment's top on the screen)
        cx, cy, s2 = j._book_place('bottom', 1.0, v)                                                 # (a larger interface: the book would leave the screen)
        self.assertAlmostEqual(cy + MD.BOOK_TOP * s2, MD.SQUARE_FIT * 720.0, places=6); self.assertAlmostEqual(cy - 400.0 * s2, 0.0, places=6)
        self.assertEqual(j.book_frame(), j._ui_reference())                                         # (before the book's window: the interface column)
        j.ui_book_tf = (640.0, 450.0, 1.125); self.assertEqual(j.book_frame(), (640.0, 450.0, 1.125))

    def test_maps_at_most_the_screen(self):
        """a map a quarter larger than before (SMALL_SCREEN_SCALE), at most SQUARE_FIT of the screen in height and in width, its buttons the same"""
        self.assertEqual(RG.SMALL_SCREEN_SCALE, 1.5 * 1.25)
        j = self.game(ENGINE.PART_TOWN, {}, {}); j._map_reference = lambda: (-225.0, 58.5, 225.0, 402.0)
        j._map_anchor = lambda name_str, wnd, s2, cx, cy: (0.0, 0.0)
        v = [RG.SMALL_SCREEN_SCALE, 0.0, 0.0]; s = 720 / 800.0
        self.assertAlmostEqual(j._ui_place('CityMap', None, 'top', s, v)[2], s * RG.SMALL_SCREEN_SCALE)          # (16/9: room enough)
        j.width = 600
        for name_str in ('CityMap', 'MapButton'):
            cx, cy, s2 = j._ui_place(name_str, None, 'top', s, v)
            self.assertAlmostEqual(s2, MD.SQUARE_FIT * 600 / 450.0); self.assertAlmostEqual(cy, 720 - 400.0 * s2)
        self.assertAlmostEqual(j._ui_place('Status', None, 'top', s, v)[2], s * RG.SMALL_SCREEN_SCALE)          # (another window: as set)


if __name__ == '__main__':
    unittest.main(verbosity=2)
