"""the casino and the bestiary (screens made for the phone's portrait screen, on a black background) show the blurred scenery of the place just left instead of black (the bestiary always,
whatever color the town left, with its parchment in front of it, as in the game); the game is
blurred behind the port's own screens: "Settings" menu and its pages, history, saves and language screens, except the Interface and Display pages (and never behind the game's own in-game menu).

1. Without APK: the rule (`black_background`), the blur, and when the copy is taken / the backdrop drawn (fake windows, no OpenGL call outside the cases).
2. Real window (DQ_APK, DQ_SAVES): save 1 (Alina's church), the slot machine opened as the furniture does it (`changeShopMenuPhase(27)`), "Yes": on the slot machine screen the sides of the window
   show the blurred church (not black); once the copy is forgotten (savestate loaded), black again. Then the "Settings" menu: the church blurred around its panel on the root page, sharp on Affichage.
    DQ_APK=<patched APKs folder> DQ_SAVES=<saves folder> python3 tests/test_minigame_backdrop.py        (about a minute)
"""
import json, os, subprocess, sys, tempfile, types, unittest
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import _paths
import pyglet
pyglet.options['shadow_window'] = False
if sys.platform.startswith('linux') and not os.environ.get('DISPLAY'): pyglet.options['headless'] = True
from dq456_pc.core import engine as ENGINE
from dq456_pc.window import backdrop as BD
from dq456_pc.render import screen_blur as SB
try:
    from dq456_pc.window import game as F
    from dq456_pc import main
except Exception as ex:
    F = None; IMPORT_ERROR = ex

APK, SAVES = os.environ.get('DQ_APK'), os.environ.get('DQ_SAVES')
L, H = 1280, 720
SHOT_BLURRED, FORGET, SHOT_BLACK = 470, 475, 480


class Rule(unittest.TestCase):
    def test_black_background(self):
        self.assertTrue(BD.black_background((0.0, 0.0, 0.0), False))
        self.assertTrue(BD.black_background((0.01, 0.0, 0.005), False))
        self.assertFalse(BD.black_background((0.2, 0.3, 0.8), False), 'fond de couleur : garde')
        self.assertFalse(BD.black_background((0.0, 0.0, 0.0), True), 'degrade BackColor : garde')

    def test_blur(self):
        a = np.zeros((40, 60, 3), np.uint8); a[:, ::2] = 255                      # stripes one pixel wide
        b = BD.blurred(a)
        self.assertEqual(b.shape, a.shape)
        self.assertLess(float(b[10:30, 10:50].std()), 10.0, 'the stripes are blended')
        self.assertAlmostEqual(float(b[10:30, 10:50].mean()), 127.5, delta=8.0)


class MenuBlur(unittest.TestCase):
    def test_weights(self):
        w = SB.gaussian_weights()
        self.assertEqual(len(w), SB.RADIUS + 1)
        self.assertAlmostEqual(w[0] + 2 * sum(w[1:]), 1.0, places=6)
        self.assertTrue(all(a > b for a, b in zip(w, w[1:])), 'decroissants')

    def test_pages(self):
        from dq456_pc.menus import pause as PA
        for page, blurred in (('root', True), ('history', True), ('sound', True), ('controls', True), ('states', True), ('quit', True), ('difficulty', True), ('cheats', True), ('interface', False), ('display', False)):
            p = types.SimpleNamespace(screen_page=page, SHARP_PAGES=PA.Pause.SHARP_PAGES)
            self.assertEqual(PA.Pause.blurs_game(p), blurred, page)

    def test_in_the_drawing(self):
        """The window blurs before drawing the menu (after the savestate thumbnail: it stays sharp), and before the title's Sauvegardes / language screens (source read as text: no display needed)."""
        src = open(os.path.join(HERE, '..', 'dq456_pc', 'window', 'game.py'), encoding='utf-8').read()
        i, j, k = src.index('self.pause.mini_tex = self._clone_thumbnail()'), src.index('if self.pause.blurs_game(): self._blur_window()'), src.index('self.pause.draw_all()')
        self.assertLess(i, j); self.assertLess(j, k)
        t = open(os.path.join(HERE, '..', 'dq456_pc', 'window', 'title_screen.py'), encoding='utf-8').read()
        a = t.index('def _draw_saves'); self.assertLess(t.index('self._blur_window()', a), t.index('self.port_screen.draw_all', a))


class FakeWindow(BD.BackdropMixin):
    def __init__(self): self.calls = []
    def get_framebuffer_size(self): self.calls.append('fb'); raise AssertionError('no copy expected')
    def _backdrop_texture(self): self.calls.append('tex'); return None
    def _paint_parchment(self): self.calls.append('parchment')


class When(unittest.TestCase):
    def e(self, part, m='mbk1f1'): return types.SimpleNamespace(part=part, game_map=m)

    def test_no_copy_outside_town_and_world_map(self):
        for part, m in ((ENGINE.PART_BATTLE, 'btl_01'), (ENGINE.PART_CASINO, 'ev05'), (ENGINE.PART_BESTIARY, 'mbk1f1'), (ENGINE.PART_TOWN, F.TITLE_MAP if F else 'title')):
            w = FakeWindow(); w._backdrop_keep(self.e(part, m)); self.assertEqual(w.calls, [], (part, m))

    def test_backdrop_only_for_minigames_on_black(self):
        for part in (ENGINE.PART_TOWN, ENGINE.PART_FIELD, ENGINE.PART_BATTLE):
            w = FakeWindow(); w._draw_backdrop(self.e(part), (0.0, 0.0, 0.0), False); self.assertEqual(w.calls, [], part)
        for part in ENGINE.MINIGAME_PARTS:
            if part == ENGINE.PART_BESTIARY: continue
            w = FakeWindow(); w._draw_backdrop(self.e(part), (0.3, 0.3, 0.3), False); self.assertEqual(w.calls, [], 'fond gris : %#x' % part)
            w = FakeWindow(); w._draw_backdrop(self.e(part), (0.0, 0.0, 0.0), False); self.assertEqual(w.calls, ['tex'], 'fond noir : %#x' % part)

    def test_bestiary(self):
        """The bestiary: the blurred scenery then the parchment, whatever the town left (its sky color, its BackColor gradient)."""
        for background, gradient in (((0.0, 0.0, 0.0), False), ((0.45, 0.58, 1.0), False), ((0.0, 0.0, 0.0), True)):
            w = FakeWindow(); w._draw_backdrop(self.e(ENGINE.PART_BESTIARY), background, gradient); self.assertEqual(w.calls, ['tex', 'parchment'], (background, gradient))

    def test_parchment_quad(self):
        """Over the book's window frame (450 units: the phone's width), 0.89 of its width in height, centered on it (window 1920 x 1080, frame of the interface column:
        607.5 px wide, parchment 540.7 px high); an enlarged frame, higher up, takes it along."""
        w = FakeWindow(); w.width, w.height = 1920, 1080; w.book_frame = lambda: (960.0, 540.0, 1080 / 800.0)
        x0, y0, x1, y1 = w.parchment_quad()
        self.assertAlmostEqual((x1 - x0) / 2 * 1920, 450 * 1080 / 800.0, places=3); self.assertAlmostEqual((y1 - y0) / 2 * 1080, 0.89 * 450 * 1080 / 800.0, places=3)
        self.assertAlmostEqual(x0 + x1, 0.0, places=6); self.assertAlmostEqual(y0 + y1, 0.0, places=6)
        w.book_frame = lambda: (225 * 1080 / 800.0, 540.0, 1080 / 800.0)                  # (interface on the left)
        x0, _y0, x1, _y1 = w.parchment_quad(); self.assertAlmostEqual(x0, -1.0, places=6)
        w.book_frame = lambda: (960.0, 600.0, 1.25 * 1080 / 800.0)                         # (the book a quarter larger, higher up)
        x0, y0, x1, y1 = w.parchment_quad(); self.assertAlmostEqual((x1 - x0) / 2 * 1920, 1.25 * 450 * 1080 / 800.0, places=3); self.assertAlmostEqual((y0 + y1) / 2, 600.0 / 540.0 - 1.0, places=6)

    def test_parchment_resource(self):
        from dq456_pc.menus import pre_game as AP
        self.assertEqual(AP.RESOURCES['ap_encyclopedia.png'], ('pack', 'Texture2D', 'encyclopedia'))
        self.assertEqual(BD.PARCHMENT_HEIGHT, 0.89)

    def test_forget(self):
        w = FakeWindow(); w.backdrop_serial = 7; w._backdrop_forget(); self.assertEqual(w.backdrop_serial, 0)

    @unittest.skipIf(F is None, 'window non importable ici')
    def test_in_the_game_window(self):
        self.assertTrue(issubclass(F.Game, BD.BackdropMixin))


def probe(out_dir):
    """Session on save 1: slot machine opened at loop 200 (as TownFurnitureManager::bootSlot), "Yes" at 380; screenshots at SHOT_BLURRED, then at SHOT_BLACK after the copy is forgotten."""
    sys.argv = ['window.py', '--apk', APK, '--saves-read', SAVES, '--saves-write', tempfile.mkdtemp(prefix='sv_fond_'), '--settings', os.path.join(tempfile.mkdtemp(prefix='rg_fond_'), 'r.json'),
                '--slot', '1', '--cache', _paths.ui_cache('/tmp/uicache_fond'), '--msaa', '0', '--width', str(L), '--height', str(H), '--no-preparation', '--no-sound', '--no-logos',
                '--test-keys', '380:SPACE', '--shots-loop', '%d,%d' % (SHOT_BLURRED, SHOT_BLACK), '--shot', os.path.join(out_dir, 's.png'), '--images', str(SHOT_BLACK + 40)]
    loop = F.Game._loop; parts = {}
    def loop2(self, dt):
        eng = self.engine
        if self.n_loop == 200:
            c = eng.core
            wm = c.call(c.export('_ZN27MaterielMenu_WINDOW_MANAGER12getSingletonEv'), label='wm'); c.mem_w(wm + 0x10, b'\0' * 4)
            c.call(c.export('_ZN6window13CommandWindow19changeShopMenuPhaseEi'), c.export('_ZN16TownWindowSystem11m_singletonE'), 27, label='slot')
        if self.n_loop == FORGET: self._backdrop_forget()
        if self.n_loop in (SHOT_BLURRED - 1, SHOT_BLACK - 1):                    # (the screenshot of loop n is taken during the next call)
            parts[self.n_loop + 1] = [eng.state.part, eng.state.game_map]
            with open(os.path.join(out_dir, 'parts.json'), 'w') as f: json.dump(parts, f)
        return loop(self, dt)
    F.Game._loop = loop2
    main.main()


MENU_SHARP, MENU_OPEN, MENU_ROOT, MENU_DISPLAY, MENU_SHOT_DISPLAY = 290, 300, 330, 340, 370


def probe_menu(out_dir):
    """Session on save 1 (the church): screenshot without menu, then the "Settings" menu on its root page, then on Affichage."""
    sys.argv = ['window.py', '--apk', APK, '--saves-read', SAVES, '--saves-write', tempfile.mkdtemp(prefix='sv_fond_'), '--settings', os.path.join(tempfile.mkdtemp(prefix='rg_fond_'), 'r.json'),
                '--slot', '1', '--cache', _paths.ui_cache('/tmp/uicache_fond'), '--msaa', '0', '--width', str(L), '--height', str(H), '--no-preparation', '--no-sound', '--no-logos',
                '--shots-loop', '%d,%d,%d' % (MENU_SHARP, MENU_ROOT, MENU_SHOT_DISPLAY), '--shot', os.path.join(out_dir, 'm.png'), '--images', str(MENU_SHOT_DISPLAY + 40)]
    loop = F.Game._loop; pages = {}
    def loop2(self, dt):
        if self.n_loop == MENU_OPEN: self.pause.open_menu()
        if self.n_loop == MENU_DISPLAY: self.pause._go('display')()                       # (as the Affichage button does)
        if self.n_loop in (MENU_ROOT - 1, MENU_SHOT_DISPLAY - 1):
            pages[self.n_loop + 1] = self.pause.screen_page if self.pause.opened else None
            with open(os.path.join(out_dir, 'pages.json'), 'w') as f: json.dump(pages, f)
        return loop(self, dt)
    F.Game._loop = loop2
    main.main()


def edges(path):
    """Detail of the left strip of a screenshot (outside the menu's panels: the root's list starts at 8 % of the width): mean of the absolute differences between neighbouring pixels."""
    from PIL import Image
    a = np.asarray(Image.open(path).convert('L')).astype(float)[H // 6:5 * H // 6, :int(L * 0.07)]
    return float(np.abs(np.diff(a, axis=0)).mean() + np.abs(np.diff(a, axis=1)).mean())


@unittest.skipUnless(APK and SAVES and F is not None, 'real engine: DQ_APK=<folder of the patched APKs> DQ_SAVES=<folder of the saves>')
class RealMenu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix='fond_menu_')
        env = dict(os.environ, PYGLET_HEADLESS='1', PYGLET_AUDIO='silent')
        r = subprocess.run([sys.executable, '-W', 'ignore', os.path.abspath(__file__), '--sonde-menu', cls.dir], capture_output=True, text=True, timeout=900, env=env)
        if not os.path.exists(os.path.join(cls.dir, 'pages.json')): raise RuntimeError('probe without a result: ' + r.stdout[-600:] + r.stderr[-600:])
        cls.pages = json.load(open(os.path.join(cls.dir, 'pages.json')))
        cls.e = {n: edges(os.path.join(cls.dir, 'm_b%d.png' % n)) for n in (MENU_SHARP, MENU_ROOT, MENU_SHOT_DISPLAY)}

    def test_pages(self):
        self.assertEqual(self.pages, {str(MENU_ROOT): 'root', str(MENU_SHOT_DISPLAY): 'display'})

    def test_root_page_blurs_the_game(self):
        self.assertLess(self.e[MENU_ROOT], 0.25 * self.e[MENU_SHARP], self.e)

    def test_display_page_keeps_it_sharp(self):
        self.assertGreater(self.e[MENU_SHOT_DISPLAY], 0.45 * self.e[MENU_SHARP], self.e)


@unittest.skipUnless(APK and SAVES and F is not None, 'real engine: DQ_APK=<folder of the patched APKs> DQ_SAVES=<folder of the saves>')
class RealSlotMachine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix='fond_machine_')
        env = dict(os.environ, PYGLET_HEADLESS='1', PYGLET_AUDIO='silent')
        r = subprocess.run([sys.executable, '-W', 'ignore', os.path.abspath(__file__), '--sonde', cls.dir], capture_output=True, text=True, timeout=900, env=env)
        if not os.path.exists(os.path.join(cls.dir, 'parts.json')): raise RuntimeError('probe without a result: ' + r.stdout[-600:] + r.stderr[-600:])
        cls.parts = json.load(open(os.path.join(cls.dir, 'parts.json')))

    def sides(self, n):
        from PIL import Image
        a = np.asarray(Image.open(os.path.join(self.dir, 's_b%d.png' % n)).convert('RGB')).astype(float)
        band = np.concatenate([a[H // 4:3 * H // 4, :L // 8], a[H // 4:3 * H // 4, -L // 8:]], axis=1)          # left and right eighths, middle half of the height (the slot machine is in the centre)
        return float(band.mean()), float(band.std())

    def test_on_the_slot_machine(self):
        for n in (SHOT_BLURRED, SHOT_BLACK): self.assertEqual(self.parts[str(n)], [ENGINE.PART_CASINO, 'ev05'], n)

    def test_sides_show_the_blurred_scenery(self):
        mean, std = self.sides(SHOT_BLURRED)
        self.assertGreater(mean, 25.0, 'cotes noirs (moyenne %.1f)' % mean)
        self.assertGreater(std, 4.0, 'plain sides (standard deviation %.1f): not the scenery' % std)

    def test_without_copy_black_as_before(self):
        mean, std = self.sides(SHOT_BLACK)
        self.assertLess(mean, 3.0, 'without a copy: black (mean %.1f)' % mean)


if __name__ == '__main__':
    if '--sonde' in sys.argv:
        probe(sys.argv[sys.argv.index('--sonde') + 1])
    elif '--sonde-menu' in sys.argv:
        probe_menu(sys.argv[sys.argv.index('--sonde-menu') + 1])
    else:
        unittest.main(verbosity=2)
