"""Square screens (the bestiary, the map overviews, the shop pictures): the book as it opens (a quarter larger, its parchment kept on the screen), the maps and the
pictures in its parchment's place; their messages between them and the interface under them (the book's page bar), drawn over everything (the bestiary's reward
message was under its list's buttons); the bestiary's monster cut at the phone's screen edges.

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
        for n in ('_square_screen', '_dz_message', '_ui_custom', '_book_place', '_ui_place', '_ui_reference', 'book_frame', 'square_frame', '_square_bottom'):
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

    def test_square_frame_is_the_book_as_it_opens(self):
        """the square screens' frame: the game's column a quarter larger at the windows' zoom, at most what keeps the parchment on the screen (height and width),
        its bottom edge on the screen's; the book's own: that frame with the player's settings; kept while the book closes, its default before it shows"""
        j = self.game(ENGINE.PART_BESTIARY, {}, {}); j.reg = {'interface_windows': {}}
        fit = MD.SQUARE_FIT * 720 / (400.0 + MD.BOOK_TOP)
        cx, cy, s = j.square_frame(); self.assertAlmostEqual(s, min(720 / 800.0 * MD.UI_ZOOM * RG.BOOK_SCALE, fit)); self.assertEqual(cx, 640.0); self.assertAlmostEqual(cy, 400.0 * s)
        j.width = 400; self.assertAlmostEqual(j.square_frame()[2], MD.SQUARE_FIT * 400 / 450.0); j.width = 1280          # (narrow: the parchment's width)
        self.assertEqual(j.book_frame(), j.square_frame())                                          # (before its window: its default place)
        j.reg['interface_windows'][MD.BOOK] = [RG.BOOK_SCALE / 2, 0.1, 0.0]
        self.assertAlmostEqual(j._book_place()[2], s / 2); self.assertAlmostEqual(j._book_place()[0], 640.0 + 128.0)
        j.ui_book_tf = (640.0, 450.0, 1.125); self.assertEqual(j.book_frame(), (640.0, 450.0, 1.125))
        j.ui_book_tf = None; j.engine.state.part = ENGINE.PART_TOWN; self.assertEqual(j.book_frame(), j._ui_reference())

    def test_maps_where_the_book_is(self):
        """a map at the book's parchment's place and width (its frame 450 units wide centered on the frame's center), its buttons with it; the player's size relative"""
        j = self.game(ENGINE.PART_TOWN, {}, {}); j._map_reference = lambda: (-225.0, 58.5, 225.0, 402.0)
        targets = []; j._map_anchor = lambda name_str, wnd, s2, cx, cy, target=None: (targets.append(target), (0.0, -230.25))[1]
        qx, qy, qs = j.square_frame()
        cx, cy, s2 = j._ui_place('WorldLargeMap', None, 'top', 0.9, [RG.SMALL_SCREEN_SCALE, 0.0, 0.0])
        self.assertEqual((cx, s2), (qx, qs)); self.assertAlmostEqual(cy, qy - 230.25 * qs); self.assertEqual(targets, [(qx, qy)])
        self.assertAlmostEqual(j._ui_place('MapButton', None, 'top', 0.9, [RG.SMALL_SCREEN_SCALE / 2, 0.0, 0.0])[2], qs / 2)
        self.assertAlmostEqual(j._ui_place('Status', None, 'top', 0.9, [1.5, 0.0, 0.0])[2], 0.9 * 1.5)          # (another window: as set)

    def test_message_between_the_book_and_its_page_bar(self):
        """the square screen's bottom edge and the interface under it: the book's parchment and its page bar; a map and the screen's edge; a shop picture and its lists"""
        book = types.SimpleNamespace(typ=28, rects=lambda: [(-200.0, -190.0, 200.0, 160.0), (-220.0, -402.0, 220.0, -340.0)], extent=lambda: (-402.0, 163.0))
        names = {28: 'MonsterEncyclopedia', 30: 'CityMap', 40: 'ShopList', 41: None}
        j = self.game(ENGINE.PART_BESTIARY, {12: book}, names); j.ui_followers = set(); j.ui_tf = {12: (640.0, 460.0, 1.15)}
        nm = {12: 'MonsterEncyclopedia'}
        lo, hi = j._square_bottom([(0, 12)], nm, set())
        self.assertAlmostEqual(lo, 460.0 - MD.BOOK_TOP * 1.15); self.assertAlmostEqual(hi, 460.0 - 340.0 * 1.15)
        pic = types.SimpleNamespace(typ=41, extent=lambda: (-100.0, 100.0)); lists = types.SimpleNamespace(typ=40, extent=lambda: (-390.0, -250.0))
        j = self.game(ENGINE.PART_TOWN, {4: pic, 5: lists}, names); j.ui_followers = set(); j.ui_tf = {4: (640.0, 400.0, 1.0), 5: (640.0, 360.0, 0.9)}
        self.assertEqual(j._square_bottom([(0, 4), (1, 5)], {4: None, 5: 'ShopList'}, {4}), (300.0, 360.0 - 250.0 * 0.9))
        j.ui_tf[5] = (640.0, 600.0, 0.9); self.assertEqual(j._square_bottom([(0, 4), (1, 5)], {4: None, 5: 'ShopList'}, {4}), (300.0, 0.0))          # (a window beside it: the screen's edge)

    def test_bestiary_monster_cut_at_the_phone_screen(self):
        """the bestiary's monster drawn within the phone's screen (450 x 800 units around the book's frame), in the drawing area's pixels"""
        from dq456_pc.window import game as WND
        self.assertEqual(WND.phone_clip((640.0, 400.0, 1.0), 1280, 720, (1280, 720)), (415, 0, 450, 720))
        self.assertEqual(WND.phone_clip((640.0, 400.0, 1.0), 1280, 720, (2560, 1440)), (830, 0, 900, 1440))

if __name__ == '__main__':
    unittest.main(verbosity=2)
