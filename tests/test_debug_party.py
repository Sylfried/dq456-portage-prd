"""Debug mode, the "Party" tab (dq456_pc/debug/party.py): the members' level and experience, joining and leaving, the money of every kind, the play time, every item
at once, through the game's own functions. Stand-in engine: the CI has no game.

    python3 tests/test_debug_party.py
"""
import os, struct, sys, types, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, '..'))
from dq456_pc.core import engine as ENGINE
from dq456_pc.debug import party as PY

PARTY_AT, ORIG, GAME_AT, VT = 0x1000, 0x20000, 0x3000, 0x7000
SYMS = {PY.PARTY: PARTY_AT, '_ZN6status9PartyData15originalPlayer_E': ORIG, PY.GAME: GAME_AT, PY.SACK_VTABLE: VT, PY.TEXT['dq4']: 0x900, PY.LEVELUP: 0x904,
        PY.SET_EXP: 0x908, PY.SACK_ADD: 0x90c, PY.JOIN['dq4'][0]: 0x910, PY.JOIN['dq4'][1]: 0x914}
SYMS.update({v: 0x920 + i * 4 for i, v in enumerate(PY.SETTERS.values())}); SYMS[PY.PLAY_TIME[0]] = 0x940
SYMS.update({PY.HISTORY[0]: 0x5000, PY.history_fn('get', 'BattleCount', 'v'): 0x950, PY.history_fn('set', 'BattleCount', 'j'): 0x954, PY.history_fn('set', 'MaxDamage', 't'): 0x958})
NAMES = {2: 'Sofià', 3: 'Ragnar', 4: 'Alina'}


class Core:
    def __init__(self):
        self.ram = bytearray(0x80000); self.calls = []
        struct.pack_into('<2I', self.ram, PARTY_AT + ENGINE.PARTY_MEMBERS, 2, 3)                     # Sofià, Ragnar
        for n, lv, xp in ((2, 33, 324650), (3, 30, 172959)):
            hs = ORIG + ENGINE.PLAYER_STRIDE * n + ENGINE.PLAYER_HAVESTATUS
            struct.pack_into('<H', self.ram, hs, n); struct.pack_into('<I', self.ram, hs + 8, xp); self.ram[hs + PY.LEVEL_AT['dq4']] = lv
        struct.pack_into('<I', self.ram, PARTY_AT + PY.VALUES['dq4']['gold'], 631)
        struct.pack_into('<Q', self.ram, PARTY_AT + PY.SACK['dq4'], VT + 16)
    def export(self, n): return SYMS.get(n)
    def mem(self, a, n): return bytes(self.ram[a:a + n])
    def mem_w(self, a, b): self.ram[a:a + len(b)] = b
    def scratch(self, name, n): return 0x60000
    def cstr(self, b): self.mem_w(0x61000, bytes(b) + b'\0'); return 0x61000
    def read_cstr(self, a): return bytes(self.ram[a:self.ram.index(0, a)])
    def call(self, f, *args, label=None):
        self.calls.append((label, args))
        if label == 'BattleHistory::getBattleCount': return 51
        if label == 'TextAPI::extractText':
            t = NAMES.get(args[1] & 0xffffff) if args[1] & 0xff000000 == PY.PLAYER_NAME else None
            self.mem_w(args[0], (t or '<ERR>').encode() + b'\0')
        return 0


class Service:
    def __init__(self, core):
        self.g = types.SimpleNamespace(engine=types.SimpleNamespace(core=core)); self.panel_pages = {}; self.panel_commands = {}; self.notes = []
    def note(self, t): self.notes.append(t)


class PartyTab(unittest.TestCase):
    def setUp(self):
        self.gm = PY.GM; PY.GM = types.SimpleNamespace(profile=lambda: types.SimpleNamespace(key='dq4')); self.addCleanup(setattr, PY, 'GM', self.gm)
        self.core = Core(); self.s = Service(self.core)
        cat = types.SimpleNamespace(in_step=[], items=lambda: [(1, 'massue de chêne'), (158, 'objet de test')])
        self.p = PY.Party(self.s, cat); self.cat = cat

    def test_registered(self):
        self.assertIn('/api/party', self.s.panel_pages)
        for k in ('party_level', 'party_exp', 'party_join', 'party_quit', 'value', 'items_max'): self.assertIn(k, self.s.panel_commands)

    def test_members_and_values(self):
        i = self.p.info()
        self.assertEqual(i['members'], [{'id': 2, 'name': 'Sofià', 'level': 33, 'exp': 324650, 'guest': False}, {'id': 3, 'name': 'Ragnar', 'level': 30, 'exp': 172959, 'guest': False}])
        self.assertEqual(i['characters'], [(2, 'Sofià'), (3, 'Ragnar'), (4, 'Alina')])
        self.assertEqual(i['values']['gold'], 631)

    def test_level_and_experience_through_the_game(self):
        hsi = ORIG + ENGINE.PLAYER_STRIDE * 3 + ENGINE.PLAYER_HAVESTATUS - PY.HAVE_STATUS['dq4']
        self.assertEqual(self.p.set_level({'id': 3, 'level': 50}), {'ok': True})
        self.assertIn(('HaveStatusInfo::levelup', (hsi, 50)), self.core.calls)
        self.assertEqual(self.p.set_exp({'id': 3, 'exp': 1000}), {'ok': True})
        self.assertIn(('HaveStatusInfo::setExp', (hsi, 1000)), self.core.calls)
        for bad in ({'id': 3, 'level': 0}, {'id': 3, 'level': 100}, {'id': 9, 'level': 5}): self.assertIn('error', self.p.set_level(bad))

    def test_guest_has_no_level(self):
        """a guest (DQ IV's Doc in the wagon: level 255, experience 0xffffffff) is shown as such; setting its level or experience is refused, and so is a call the
        engine could not finish (the port's core answers None)"""
        hs = ORIG + ENGINE.PLAYER_STRIDE * 2 + ENGINE.PLAYER_HAVESTATUS; self.core.ram[hs + PY.LEVEL_AT['dq4']] = 255
        self.assertTrue(self.p.members()[0]['guest'])
        self.assertIn('error', self.p.set_level({'id': 2, 'level': 5})); self.assertIn('error', self.p.set_exp({'id': 2, 'exp': 5}))
        self.assertFalse([c for c in self.core.calls if c[0] in ('HaveStatusInfo::levelup', 'HaveStatusInfo::setExp')])
        call = self.core.call; self.core.call = lambda f, *a, label=None: None if label == 'HaveStatusInfo::levelup' else call(f, *a, label=label)
        self.assertIn('error', self.p.set_level({'id': 3, 'level': 5}))

    def test_join_and_quit_during_the_step(self):
        self.assertEqual(self.p.join({'id': 4}), {'ok': True})
        self.assertFalse([c for c in self.core.calls if c[0] == PY.JOIN['dq4'][0]])          # (during the next engine step)
        self.cat.in_step.pop()()
        (label, args), = [c for c in self.core.calls if c[0] == PY.JOIN['dq4'][0]]
        self.assertEqual(struct.unpack_from('<i', self.core.ram, args[0] + PY.JOIN['dq4'][2])[0], 4)
        self.assertIn('error', self.p.quit({'id': 99}))

    def test_values_through_the_setters(self):
        self.assertEqual(self.p.set_value({'key': 'bank', 'value': 5000000}), {'ok': True})
        self.assertIn((PY.SETTERS['bank'], (PARTY_AT, 5000000)), self.core.calls)
        self.p.set_value({'key': 'play_time', 'value': 216000})
        self.assertIn((PY.PLAY_TIME[0], (GAME_AT, 216000)), self.core.calls)
        for bad in ({'key': 'bank', 'value': -1}, {'key': 'nothing', 'value': 1}, {'key': 'gold', 'value': 'x'}): self.assertIn('error', self.p.set_value(bad))

    def test_hero_and_log_name(self):
        """the hero's name, which is the adventure log's: read where the game keeps it, set through the setter its log's name entry calls (DQ VI: its default hero name too)"""
        SYMS.update({PY.HERO['dq4'][0]: 0x6000, PY.HERO['dq4'][3]: 0x960})
        self.core.mem_w(0x6000 + PY.HERO['dq4'][2], 'Sofià'.encode() + b'\0')
        self.assertEqual(self.p.info()['hero'], 'Sofià'); self.assertEqual(self.p.info()['hero_chars'], PY.HERO_CHARS); self.assertIn('hero_name', self.s.panel_commands)
        self.assertEqual(self.p.set_hero_name({'name': ' Élodie '})['ok'], True)
        (label, args), = [c for c in self.core.calls if c[0] == PY.HERO['dq4'][3]]
        self.assertEqual((args[0], self.core.read_cstr(args[1]).decode()), (0x6000, 'Élodie'))
        for bad in ('', 'x' * (PY.HERO_CHARS + 1), 'a@b', '50%'): self.assertIn('error', self.p.set_hero_name({'name': bad}))
        PY.GM = types.SimpleNamespace(profile=lambda: types.SimpleNamespace(key='dq6')); p6 = PY.Party(Service(self.core)); self.core.calls.clear()
        SYMS.update({PY.HERO['dq6'][0]: 0x6100, PY.HERO['dq6'][3]: 0x964, PY.HERO_DQ6_DEFAULT[0]: 0x6800, PY.HERO_DQ6_DEFAULT[1]: 0x968})
        p6.set_hero_name({'name': 'Rek'})
        self.assertEqual([(l, a[0]) for l, a in self.core.calls], [(PY.HERO['dq6'][3], 0x6100 + PY.HERO['dq6'][1]), (PY.HERO_DQ6_DEFAULT[1], 0x6800)])

    def test_limits_are_the_games_caps(self):
        """each value's field takes what the game's setter keeps (gold 999,999, DQ IV's medals 99...), the panel is told them; more is refused"""
        lim = self.p.info()['limits']
        self.assertEqual((lim['gold'], lim['bank'], lim['coins'], lim['medals'], lim['level'], lim['exp']), ([0, 999999], [0, 99999000], [0, 999999], [0, 99], [1, 99], [0, 9999999]))
        self.assertEqual((lim['play_time'], lim['stat:MaxDamage'], lim['stat:HeroLevel']), ([0, 215999999], [0, 0xffff], [0, 99]))
        self.assertEqual(self.p.set_value({'key': 'gold', 'value': 999999}), {'ok': True})
        for bad in ({'key': 'gold', 'value': 1000000}, {'key': 'medals', 'value': 100}, {'key': 'play_time', 'value': 216000000}): self.assertIn('error', self.p.set_value(bad))

    def test_dq4_adventure_record(self):
        """DQ IV's record (battles, escapes, highest damage...): read through its getters, written through its setters, within their type"""
        self.assertEqual(self.p.values()['stat:BattleCount'], 51)
        self.assertEqual(self.p.set_value({'key': 'stat:BattleCount', 'value': 777}), {'ok': True})
        self.assertIn((PY.history_fn('set', 'BattleCount', 'j'), (0x5000, 777)), self.core.calls)
        self.assertIn('error', self.p.set_value({'key': 'stat:MaxDamage', 'value': 70000}))          # (a 16-bit counter)

    def test_every_item_into_the_bag(self):
        self.assertEqual(self.p.items_max(), {'ok': True, 'items': 2})
        adds = [a for l, a in self.core.calls if l == 'HaveItemSack::add']
        self.assertEqual(adds, [(PARTY_AT + PY.SACK['dq4'], 1, 99), (PARTY_AT + PY.SACK['dq4'], 158, 99)])          # (the items the game keeps for debugging too)
        struct.pack_into('<Q', self.core.ram, PARTY_AT + PY.SACK['dq4'], 0)
        self.assertIn('error', self.p.items_max())                                                        # not the bag: nothing written


if __name__ == '__main__':
    unittest.main()
