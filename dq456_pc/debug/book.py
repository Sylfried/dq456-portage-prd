"""Monster book of the debug mode (the web panel's "Book" tab): which monsters the book shows, each one or all of them at once (the book at 100 %).

The book is the game's `status::g_BattleResult`: one entry of 6 bytes per page of the book (u16 times defeated at + 0, u16 times its item was obtained at + 2,
a level byte at + 4; the game caps both counts at 999), and a bit per page saying that the monster was met (DQ IV at + 0x4ec, DQ V + 0x4bc, DQ VI + 0x4a0),
the bits the book's completion counts (`getEncountCount` / `getMonsterEncountCount`). A page's monster: DQ IV's own table (`g_BattleResult + 0x51c`, u16 per page),
DQ V / VI `BattleResult::getMonsterIndex(page)` (their book list). Its name is the engine's (TextAPI, kind 6).

- Met or not: the game's setter (`BattleResult::setEncount` / `setMonsterEncount`). A monster put in the book is also counted defeated once and its item obtained once
  when it had neither (the page then shows everything), a monster taken out has both counts set back to 0.
- Everything: every page met, its counts at least 1 (the book at 100 %); or the book emptied."""
import struct

from dq456_pc import games as GM
from dq456_pc.debug.catalog import MONSTER_TEXT, engine_text
from dq456_pc.language import N_, tr

RESULT = '_ZN6status14g_BattleResultE'
SEEN_BITS = {'dq4': 0x4ec, 'dq5': 0x4bc, 'dq6': 0x4a0}                   # the "met" bits of the book's pages
PAGES = {'dq4': 0xd2, 'dq5': 0xd2, 'dq6': 0xc5}                          # pages the game's count looks at (a DQ V page with no monster is left out)
SET_SEEN = {'dq4': '_ZN6status12BattleResult10setEncountEjb', 'dq5': '_ZN6status12BattleResult17setMonsterEncountEjb',
            'dq6': '_ZN6status12BattleResult17setMonsterEncountEjb'}
SEEN_COUNT = {'dq4': '_ZN6status12BattleResult15getEncountCountEv', 'dq5': '_ZN6status12BattleResult22getMonsterEncountCountEv',
              'dq6': '_ZN6status12BattleResult22getMonsterEncountCountEv'}
PAGE_MONSTER = '_ZN6status12BattleResult15getMonsterIndexEi'               # DQ V / VI: the monster of a page (record page + 1 of their book list)
DQ4_MONSTERS = 0x51c                                                       # DQ IV: u16 monster number per page
ENTRY = 6                                                                  # bytes per page: defeated (u16), item obtained (u16), level (u8)
COUNT_MAX = 999


class Book:
    """Registered on the debug service: the page `/api/book` and its commands."""

    def __init__(self, service):
        self.d = service; self.g = service.g; self.key = GM.profile().key
        self._pages = None
        service.panel_pages['/api/book'] = self.info
        service.panel_commands.update({'book_seen': self.set_seen, 'book_all': self.set_all})

    def _core(self): return self.g.engine.core

    def _result(self):
        a = self._core().export(RESULT)
        if not a: raise RuntimeError(tr(N_('debug.catalog_missing')) % RESULT)
        return a

    def pages(self):
        """[(page, monster number, name)] of the book's pages that have a monster the game names."""
        if self._pages is None:
            core = self._core(); br = self._result(); out = []; f = core.export(PAGE_MONSTER)
            for p in range(PAGES[self.key]):
                if self.key == 'dq4': mon = struct.unpack('<H', core.mem(br + DQ4_MONSTERS + 2 * p, 2))[0]
                else: mon = core.call(f, br, p, label='BattleResult::getMonsterIndex') if f else None
                name = engine_text(core, self.key, MONSTER_TEXT, mon) if mon is not None else None
                if name: out.append((p, mon, name[:1].upper() + name[1:]))
            self._pages = out
        return self._pages

    def _seen(self, br):
        n = PAGES[self.key]; raw = self._core().mem(br + SEEN_BITS[self.key], 4 * ((n + 31) // 32))
        words = struct.unpack('<%dI' % (len(raw) // 4), raw)
        return lambda p: bool(words[p >> 5] >> (p & 31) & 1)

    def info(self, q=None):
        core = self._core(); br = self._result(); seen = self._seen(br); rows = []
        for p, mon, name in self.pages():
            defeated, items = struct.unpack('<2H', core.mem(br + ENTRY * p, 4))
            rows.append([p, mon, name, seen(p), defeated, items])
        f = core.export(SEEN_COUNT[self.key])
        return {'game': self.key, 'pages': rows, 'seen': core.call(f, br, label='seen count') if f else sum(r[3] for r in rows), 'size': len(rows)}

    # --- commands ------------------------------------------------------------------------------------------------------------------------------------------------
    def _put(self, br, page, on):
        core = self._core(); core.call(core.export(SET_SEEN[self.key]), br, page, 1 if on else 0, label='BattleResult::setEncount')
        defeated, items = struct.unpack('<2H', core.mem(br + ENTRY * page, 4))
        if on: core.mem_w(br + ENTRY * page, struct.pack('<2H', min(max(defeated, 1), COUNT_MAX), min(max(items, 1), COUNT_MAX)))
        else: core.mem_w(br + ENTRY * page, struct.pack('<2H', 0, 0))

    def set_seen(self, c):
        try: page = int(c.get('page'))
        except (TypeError, ValueError): page = None
        known = {p: name for p, _m, name in self.pages()}
        if page not in known: return {'error': tr(N_('debug.book_page_bad')) % c.get('page')}
        on = bool(c.get('on', True)); self._put(self._result(), page, on)
        self.d.note((tr(N_('debug.book_in')) if on else tr(N_('debug.book_out'))) % known[page]); return {'ok': True}

    def set_all(self, c):
        on = bool(c.get('on', True)); br = self._result()
        for p, _m, _n in self.pages(): self._put(br, p, on)
        self.d.note(tr(N_('debug.book_all_in')) if on else tr(N_('debug.book_all_out'))); return {'ok': True}
