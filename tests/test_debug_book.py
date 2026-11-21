"""Debug mode, the "Book" tab (dq456_pc/debug/book.py): which monsters the monster book shows, one at a time or all at once, in the game's own data
(`status::g_BattleResult`: 6 bytes per page, a "met" bit per page). Stand-in engine: the CI has no game.

    python3 tests/test_debug_book.py

(Real-engine check: DQ IV save 4, DQ V save 2, DQ VI save 1: the pages' names match the book; one page added / taken out; the whole book: the game's own count
says 210 / 202 / 197 pages, and DQ IV's book menu lists every monster.)
"""
import os, struct, sys, types, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, '..'))
from dq456_pc.debug import book as BK
from dq456_pc.debug import catalog as CT

BR = 0x1000
NAMES = {0: 'gluant', 3: 'londuleux', 20: 'rat volant'}                  # (monster numbers of DQ IV's first pages; any other one has no name)


class Core:
    def __init__(self, key):
        self.key = key; self.ram = bytearray(0x4000); self.calls = []
        self.syms = {BK.RESULT: BR, CT.TEXT[key]: 0x900, BK.SET_SEEN[key]: 0x904, BK.SEEN_COUNT[key]: 0x908, BK.PAGE_MONSTER: 0x90c}
        for p, mon in enumerate((0, 3, 20)): struct.pack_into('<H', self.ram, BR + BK.DQ4_MONSTERS + 2 * p, mon)
        for p in range(3, BK.PAGES['dq4']): struct.pack_into('<H', self.ram, BR + BK.DQ4_MONSTERS + 2 * p, 999)
        struct.pack_into('<2H', self.ram, BR + BK.ENTRY * 1, 7, 2)                                       # (page 1: defeated 7 times, its item twice)
        self.set_bit(1, True)
    def set_bit(self, p, on):
        a = BR + BK.SEEN_BITS[self.key] + 4 * (p >> 5); w = struct.unpack_from('<I', self.ram, a)[0]
        struct.pack_into('<I', self.ram, a, w | 1 << (p & 31) if on else w & ~(1 << (p & 31)))
    def export(self, n): return self.syms.get(n)
    def mem(self, a, n): return bytes(self.ram[a:a + n])
    def mem_w(self, a, b): self.ram[a:a + len(b)] = b
    def scratch(self, name, n): return 0x3000
    def read_cstr(self, a): return bytes(self.ram[a:self.ram.index(0, a)])
    def call(self, f, *args, label=None):
        self.calls.append((label, args))
        if label == 'TextAPI::extractText':
            self.mem_w(args[0], (NAMES.get(args[1] & 0xffffff) or '<ERR>').encode() + b'\0')
        elif label == 'BattleResult::getMonsterIndex': return (0, 3, 20)[args[1]] if args[1] < 3 else 999          # (DQ V: the pages past its list, a monster with no name)
        elif label == 'BattleResult::setEncount': self.set_bit(args[1], args[2])
        elif label == 'seen count': return sum(struct.unpack('<I', self.mem(BR + BK.SEEN_BITS[self.key] + 4 * (p >> 5), 4))[0] >> (p & 31) & 1 for p in range(BK.PAGES[self.key]))
        return 0


class Service:
    def __init__(self, core):
        self.g = types.SimpleNamespace(engine=types.SimpleNamespace(core=core)); self.panel_pages = {}; self.panel_commands = {}; self.notes = []
    def note(self, t): self.notes.append(t)


class BookTab(unittest.TestCase):
    def book(self, key):
        gm = BK.GM; BK.GM = types.SimpleNamespace(profile=lambda: types.SimpleNamespace(key=key)); self.addCleanup(setattr, BK, 'GM', gm)
        core = Core(key); s = Service(core); return BK.Book(s), core, s

    def test_pages_named_by_the_engine(self):
        for key in ('dq4', 'dq5'):
            b, core, s = self.book(key)
            self.assertEqual(b.pages(), [(0, 0, 'Gluant'), (1, 3, 'Londuleux'), (2, 20, 'Rat volant')], key)          # (pages with no named monster left out)
            i = b.info()
            self.assertEqual(i['pages'][1], [1, 3, 'Londuleux', True, 7, 2]); self.assertEqual((i['seen'], i['size']), (1, 3))
        self.assertIn('/api/book', s.panel_pages); self.assertEqual(set(s.panel_commands), {'book_seen', 'book_all'})

    def test_one_page_in_and_out(self):
        b, core, s = self.book('dq6')
        self.assertEqual(b.set_seen({'page': 2, 'on': True}), {'ok': True})
        self.assertEqual(b.info()['pages'][2][3:], [True, 1, 1])                                             # (put in: defeated once, its item once)
        self.assertEqual(b.set_seen({'page': 1, 'on': True}), {'ok': True}); self.assertEqual(b.info()['pages'][1][3:], [True, 7, 2])          # (its own counts kept)
        self.assertEqual(b.set_seen({'page': 1, 'on': False}), {'ok': True}); self.assertEqual(b.info()['pages'][1][3:], [False, 0, 0])
        self.assertIn('error', b.set_seen({'page': 3})); self.assertIn('error', b.set_seen({'page': 'x'}))
        self.assertEqual(len(s.notes), 3)

    def test_whole_book(self):
        b, core, s = self.book('dq4')
        b.set_all({'on': True}); self.assertEqual(b.info()['seen'], 3); self.assertTrue(all(r[3] and r[4] and r[5] for r in b.info()['pages']))
        b.set_all({'on': False}); self.assertEqual(b.info()['seen'], 0); self.assertTrue(all(r[3:] == [False, 0, 0] for r in b.info()['pages']))


if __name__ == '__main__':
    unittest.main()
