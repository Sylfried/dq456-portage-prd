"""Scene catalog of the debug mode (dq456_pc/debug/catalog.py): story steps read from an event list (shown in French), the switches (1,024 bits per set), the calls
that start a scene made during the next engine step, just before GameUpdate; maps entered at their most obvious entrance (map_link.bin), the world map in front of the
current place; a boss fought at its own place (its map first), any other fight only where the game itself could start a random encounter. Stand-in engine: the CI has
no game.

    python3 tests/test_debug_catalog.py
"""
import os, struct, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)
import pyglet
pyglet.options['shadow_window'] = False
from dq456_pc.core import engine as ENGINE
from dq456_pc.debug import catalog as CT
from dq456_pc.debug import service as DS
from dq456_pc.language import N_, tr
sys.path.insert(0, HERE)
from test_debug_recorder import Game

SYMS = {'_ZN7Encount9forceBrewEi': 0x910, '_ZN3btl7Encount9forceBrewEi': 0x910, '_ZN19BattleScriptManager12getSingletonEv': 0x914, '_ZN19BattleScriptManager13setEncountMapEi': 0x918,
        '_ZN7Encount14isEncountStageEPc': 0x91c, '_ZN6status12g_GlobalFlagE': 0x100, '_ZN6status10g_AreaFlagE': 0x180, '_ZN6status11g_LocalFlagE': 0x200, CT.SET_EVENT_FLAG: 0x900, CT.SET_EVENT_PARTY: 0x904,
        CT.STEP_COUNT_DQ4: 0x300, '_ZN7Encount12getSingletonEv': 0x908, '_ZN7Encount14forceEventBrewEi': 0x90c,
        '_ZN3btl7Encount12getSingletonEv': 0x908, '_ZN3btl7Encount14forceEventBrewEi': 0x90c,
        '_ZN7Encount12forceEncountEv': 0x920, '_ZN3btl7Encount12forceEncountEv': 0x920, '_ZN6status7g_StageE': 0x400, '_ZN6status16g_StageAttributeE': 0x400,
        '_ZNK6status11StageStatus9isEncountEv': 0x924, '_ZNK6status14StageAttribute9isEncountEv': 0x924}


def GM_key():
    from dq456_pc import games as GM
    return GM.profile().key


def table(rows, size):
    return b'\0' * 4 + struct.pack('<2I', len(rows), size) + b'\0' * 8 + b''.join(r.ljust(size, b'\0') for r in rows)


class Core:
    LIB_BASE = 0
    def __init__(self):
        self.ram = bytearray(0x1000); self.calls = []; struct.pack_into('<i', self.ram, 0x300, 347)
        from dq456_pc import games as GM
        flags = table([b'\0' * 8] * 10, 8); reader = type('R', (), {'read': lambda self, n: flags})()
        self.fs = type('F', (), {'names': {'assets/map/mbk1f1.stg': None, 'assets/map/caf1.stg': None, 'assets/leveldata/%sds_event_flag.dat' % GM.profile().key: reader}})()
    def export(self, n): return SYMS.get(n)
    def mem(self, a, n): return bytes(self.ram[a:a + n])
    def mem_w(self, a, b): self.ram[a:a + len(b)] = b
    encount_stage = 0; stage_encount = 1
    def cstr(self, b): return 0x900
    def scratch(self, name, n): return 0xa00
    def call(self, a, *args, label=None):
        self.calls.append((label, args))
        if 'getSingleton' in (label or ''): return 0x777
        if 'isEncountStage' in (label or ''): return self.encount_stage
        if label == 'isEncount': return self.stage_encount
        return 0


class Catalog(unittest.TestCase):
    def setUp(self):
        self.g = Game(); self.g.args.debug_port = 0
        e = self.g.engine; e.core = Core()
        e.run_step_calls = lambda: [fn() for fn in (e.step_calls() or [])]          # (Engine._step_calls: right before GameUpdate)
        e.start_town = lambda name: e.core.calls.append(('startTown', (name,)))
        e.start_field = lambda: e.core.calls.append(('startField', ()))
        self.d = DS.DebugService(self.g); self.c = self.d.catalog
        self.c._bosses = [(157, ['Maître Kung'], 'caf1')]

    def test_pure(self):
        raw = bytearray(128); raw = CT.with_flag(bytes(raw), 0, True); raw = CT.with_flag(raw, 37, True); raw = CT.with_flag(raw, 1023, True)
        self.assertEqual(CT.flag_bits(raw), [0, 37, 1023]); self.assertEqual(CT.flag_bits(CT.with_flag(raw, 37, False)), [0, 1023])
        rows = [b'', struct.pack('<HHHH', 0, 0, 5, 1) + 'ライフコッド'.encode() + b'\0' + b'M01UM4F1\0', struct.pack('<HHHH', 0, 0, 5, 1) + 'autre'.encode() + b'\0' + b'C01UF3\0']
        self.assertEqual(CT.named_steps(table(rows, 64), 4, 8), {5: ('ライフコッド', 'm01um4f1')})          # (the first row of a step is kept)
        mpt = b'MPT0' + struct.pack('<6I', 0, 1002001, 1002002, 2, 0x20, 12) + b'\0' * 4 + struct.pack('<3H', 1, 9, 0) + struct.pack('<3H', 2, 12, 3) + b'@4gluant@' + b'\0\0\0' + b'@4Meurtor@\0\0'
        self.assertEqual(CT.mpt_entries(mpt), {1: '@4gluant@', 2: '@4Meurtor@\x00\x00'[:12]})

    def test_map_links(self):
        # DQ V / VI: a town (1) with a door to its inn (2) and an exit to the world map (999); the world map leads into it at its key 0x70001
        def v2(records):
            data = b''.join(struct.pack('<3I', *r) for _m, recs in records for r in recs); index = b''; off = 0
            for m, recs in records: index += struct.pack('<3H', m, len(recs), off); off += 12 * len(recs)
            return b'DATA' + struct.pack('<I', len(data)) + data + b'INDX' + struct.pack('<I', 2 + len(index)) + struct.pack('<H', len(records)) + index + b'\0' * 8
        data = v2([(1, [(0x10002, 0x10002, 2), (0x70000, 0x7000b, 999)]), (2, [(0x10002, 0x10002, 1)]), (999, [(0x7000b, 0x70001, 1), (0x7000c, 0x70001, 5)])])
        links = CT.map_links(data, {1: 'town', 2: 'inn'})
        self.assertIn(('town', 0x70000, 0x7000b, CT.FIELD), links)
        self.assertEqual(CT.entrances(links), {'town': 0x70001, 'inn': 0x10002})       # (the world map's own key into the town, not the town's)
        self.assertEqual(CT.world_exits(links), {'town': 0x7000b, 'inn': 0x7000b})     # (the inn: out through the town)
        rows = [struct.pack('<HH', n, 1) + name.encode().ljust(12, b'\0') for n, name in ((1, 'town'), (2, 'inn'))]
        self.assertEqual(CT.floor_names(table(rows, 16)), {1: 'town', 2: 'inn'})
        # DQ IV: by name (NAME, DATA, HASH), u16 keys
        names = b'cbout\0cbf1\0world\0'; off = {'cbout': 8, 'cbf1': 14, 'world': 19}
        e1 = struct.pack('<2H', off['cbout'], 2) + struct.pack('<3H', 0x1001, 0x1001, off['cbf1']) + struct.pack('<3H', 0x7001, 0x7020, off['world'])
        e2 = struct.pack('<2H', off['cbf1'], 1) + struct.pack('<3H', 0x1001, 0x1001, off['cbout'])
        e3 = struct.pack('<2H', off['world'], 1) + struct.pack('<3H', 0x7020, 0x7001, off['cbout'])
        body = e1 + e2 + e3; hashes = struct.pack('<4H', 0, len(e1), len(e1) + len(e2), 0xffff)
        data = (b'NAME' + struct.pack('<I', len(names)) + names + b'DATA' + struct.pack('<I', len(body)) + body
                + b'HASH' + struct.pack('<I', 2 + len(hashes)) + struct.pack('<H', 4) + hashes + b'\0' * 8)
        links = CT.map_links(data)
        self.assertEqual(CT.entrances(links), {'cbout': 0x7001, 'cbf1': 0x1001})
        self.assertEqual(CT.world_exits(links), {'cbout': 0x7020, 'cbf1': 0x7020})

    def test_dq4_steps(self):
        rec = lambda step, name, m: (struct.pack('<H', step).ljust(0x13, b'\0') + name.encode().ljust(0x80, b'\0') + m.ljust(9, b'\0')).ljust(156, b'\0')
        data = b'\0' * 4 + rec(0, '序章', b'mlb1a') + rec(0, '', b'') + rec(1, '１章スタート', b'caf1')
        self.assertEqual(CT.dq4_steps([data]), {0: ('序章', 'mlb1a'), 1: ('１章スタート', 'caf1')})
        from dq456_pc.debug import story_steps_fr as SF
        self.assertEqual(sorted(SF.STEPS['dq4']), list(range(347)))

    def test_steps_maps_switches(self):
        self.assertEqual(self.c.maps(), ['caf1', 'mbk1f1'])
        if not ENGINE.ENGINE_V2: self.assertEqual(len(self.c.steps()), 347)
        self.assertEqual(self.c.set_flag({'set': 'global', 'n': 40, 'on': True}), {'ok': True})
        self.assertEqual(self.c.flags()['global'], [40])
        self.assertIn('error', self.c.set_flag({'set': 'nothing', 'n': 1}))
        self.assertIn('error', self.c.goto_map({'map': 'nowhere'}))

    def entered(self, name):
        calls = self.g.engine.core.calls
        return ('startTown', (name,)) in calls or any(l and ('setExtraExitTown' in l or 'setDirectLinkTownSurface' in l) for l, a in calls)

    def test_scene_calls_during_the_step(self):
        e = self.g.engine; self.c._entrances = {'caf1': 0x1001}; self.c._numbers = {'caf1': 7}; self.c._exits = {}
        SYMS.update({CT.GO_TO_EXIT[k][1]: 0x930 for k in CT.GO_TO_EXIT}); SYMS.update({CT.GO_TO_EXIT[k][0]: 0x500 for k in CT.GO_TO_EXIT})
        self.assertEqual(self.c.goto_map({'map': 'CAF1'}), {'ok': True})
        self.assertFalse(self.entered('caf1'))                                          # nothing yet: during the next step
        e.run_step_calls()
        self.assertTrue(self.entered('caf1')); self.assertEqual(self.g.sound.quieted, 1)          # (the looping sound effects of the place left stopped)
        (label, args), = [(l, a) for l, a in e.core.calls if l and 'Town' in l]
        self.assertEqual((args[0], args[2]), (0x500, 0x1001))                           # (the game's own link object, the map's entrance key)
        self.assertEqual(self.c.in_step, [])

    def test_boss_at_its_place(self):
        e = self.g.engine; c = self.c; c._places = {157: 'caf1'}
        e.state.game_map = 'mbk1f1'; self.g.map_name = 'mbk1f1'
        self.assertEqual(c.battle({'row': 157}), {'ok': True, 'map': 'caf1'})       # from a town: allowed for a boss
        e.run_step_calls()
        self.assertTrue(self.entered('caf1'))
        c.tick(); self.assertEqual(c.in_step, [])                                      # (its map not shown yet)
        e.state.game_map = 'caf1'; self.g.map_name = 'caf1'; c.tick()
        e.run_step_calls()
        labels = [l for l, a in e.core.calls]
        self.assertIn(('Encount::forceEventBrew', (0x777, 157)), e.core.calls)
        if not ENGINE.ENGINE_V2: self.assertLess(labels.index('BattleScriptManager::setEncountMap'), labels.index('Encount::forceEventBrew'))
        self.assertIsNone(c.pending)

    def test_ordinary_fight_only_where_the_game_allows_one(self):
        e = self.g.engine; c = self.c; core = e.core; c._places = {157: 'caf1'}; key = GM_key()
        on = 0x777 + CT.ENCOUNT_ON[key]; core.ram[on] = 1; core.encount_stage = 1
        struct.pack_into('<i', core.ram, 0x777 + CT.ENCOUNT_ZONE, -1 if not ENGINE.ENGINE_V2 else 0)
        self.assertIn(tr(N_('debug.catalog_nothing_attacks')), c.battle({'row': ''})['error'])                 # nothing attacks there
        struct.pack_into('<i', core.ram, 0x777 + CT.ENCOUNT_ZONE, 86)
        core.stage_encount = 0; self.assertIn(tr(N_('debug.catalog_map_no_random')), c.battle({'row': ''})['error'])          # a town: the map has none
        core.stage_encount = 1; core.ram[on] = 0; self.assertIn(tr(N_('debug.catalog_story_no_random')), c.battle({'row': ''})['error'])   # switched off by a script
        core.ram[on] = 1
        r = c.battle({'row': ''}); self.assertEqual(r, {'ok': True, 'row': 86})       # the place's own group, as a random encounter
        e.run_step_calls()
        self.assertIn(('Encount::forceEncount', (0x777,)), core.calls)
        r = c.battle({'row': '12'}); self.assertEqual(r, {'ok': True, 'row': 12})      # another group: Encount + 8 for that fight...
        e.run_step_calls()
        self.assertEqual(struct.unpack_from('<i', core.ram, 0x777 + CT.ENCOUNT_ZONE)[0], 12)
        e.state.part = ENGINE.PART_BATTLE; c.tick()                                    # ... then the place's again
        self.assertEqual(struct.unpack_from('<i', core.ram, 0x777 + CT.ENCOUNT_ZONE)[0], 86)
        self.assertIn('error', c.battle({'row': '157'}))                               # (not from a battle)

    def test_story_step(self):
        steps = self.c.steps()
        r = self.c.story_step({'step': 3})
        self.assertTrue(r.get('ok'), r)
        labels = [l for l, a in self.g.engine.core.calls]
        self.assertIn(CT.SET_EVENT_FLAG, labels); self.assertIn(CT.SET_EVENT_PARTY, labels)
        self.assertIn('error', self.c.story_step({'step': len(steps)}))

    def test_items_and_gold(self):
        """an item given by the game's own function (DQ IV: a method of g_Party; DQ V / VI: static), gold through the scripts' "gold" command (amount, then 0 = add / 1 = take)"""
        key = GM_key(); core = self.g.engine.core; obj, fn = CT.GIVE_ITEM[key]; gold, off = CT.SET_GOLD[key]
        SYMS.update({fn: 0x940, gold: 0x944, '_ZN6status7g_PartyE': 0x600})
        self.c._items = [(12, 'Herbe médicinale')]
        self.assertEqual(self.c.give_item({'item': 12, 'count': 3}), {'ok': True})
        given = [a for l, a in core.calls if l == 'PartyStatus::giveItem']
        self.assertEqual(given, [(0x600, 12) if obj else (12,)] * 3)
        for bad in ({'item': 0}, {'item': 'x'}, {'item': 12, 'count': 100}): self.assertIn('error', self.c.give_item(bad))
        self.assertEqual(self.c.give_gold({'amount': '-250'}), {'ok': True})
        self.assertEqual(core.calls[-1], ('cmd_set_gold', (0xa00,)))
        self.assertEqual(struct.unpack_from('<2i', core.ram, 0xa00 + off), (250, 1))
        self.c.give_gold({'amount': 5000}); self.assertEqual(struct.unpack_from('<2i', core.ram, 0xa00 + off), (5000, 0))
        self.assertIn('error', self.c.give_gold({'amount': '1.5'}))

    def test_item_names(self):
        """the names the engine gives (TextAPI::extractText, by the game's own number: entry 0 of the file is item 1's), ";" and "$" removed, "<ERR" left out;
        as many numbers as the message file has entries, plus one"""
        core = self.g.engine.core; SYMS[CT.TEXT[GM_key()]] = 0x930
        names = {1: 'bâton de ;cyprès', 2: 'massue de chêne', 3: 'épée lon$gue', 4: 'not tried'}
        def call(a, *args, label=None):
            if label == 'TextAPI::extractText':
                kind, n = args[1] & 0xf000000, args[1] & 0xffffff
                t = names.get(n, '<ERR %x.%d>' % (kind, n)) if kind == CT.ITEM_TEXT else 'monster %d' % n
                core.ram[args[0]:args[0] + 64] = (t.encode() + b'\0').ljust(64, b'\0')
            return 0
        core.call = call; core.read_cstr = lambda a: bytes(core.ram[a:core.ram.index(0, a)])
        texts = ['@4bâton de ;cyprès@5', '@0F@2la @3une @4massue de ;chêne@5les @6', '@0M@2le @3un @4@5']
        enc = [t.encode() + b'\0' * (-len(t.encode()) % 4) for t in texts]; table = b''; body = b''
        for i, t in enumerate(texts):
            table += struct.pack('<3H', i, len(t.encode()), len(body) // 4); body += enc[i]
        table += b'\0' * (-len(table) % 4)
        mpt = b'MPT0' + struct.pack('<6I', 0, 0, 2, len(texts), 28, len(table)) + table + body
        reader = type('R', (), {'read': lambda self, n: mpt})()
        self.g.engine.core.fs.names[CT.ITEM_NAMES] = reader
        self.assertEqual(self.c.items(), [(1, 'bâton de cyprès'), (2, 'massue de chêne'), (3, 'épée longue')])
        self.assertEqual(self.c.info()['items'], self.c.items())
        self.g.engine.core.fs.names[CT.MONSTER_NAMES] = reader; self.c._monsters = None          # (info() above read them without the file)
        self.assertEqual(self.c.monster_names(), {n: 'monster %d' % n for n in range(4)})
        del SYMS[CT.TEXT[GM_key()]]

    def test_any_group_here_minigames_book_backdrop(self):
        """any encounter row fought here (Encount::forceBrew, the scripts' ordinary fight, during the next step), a mini-game or the monster book from here, the backdrop drawn
        behind the next fights (drawing only)"""
        e = self.g.engine; core = e.core; key = GM_key(); e._global = lambda: 0x880
        SYMS.update({CT.START_BOOK: 0x960, '_ZN6Global11setMinigameEi': 0x964, '_ZN6Global11startCasinoEv': 0x968})
        if key in CT.MINIGAME_CMD: SYMS[CT.MINIGAME_CMD[key][0]] = 0x96c
        e.state.part = ENGINE.PART_TOWN
        self.assertEqual(self.c.battle_here({'row': '12'}), {'ok': True, 'row': 12})
        e.run_step_calls()
        self.assertIn(('Encount::forceBrew', (0x777, 12)), core.calls)
        self.assertIn('error', self.c.battle_here({'row': 'x'}))
        self.assertEqual(self.c.minigame({'id': 'poker'}), {'ok': True}); e.run_step_calls()
        if key in CT.MINIGAME_CMD:
            (label, args), = [c for c in core.calls if c[0] == CT.MINIGAME_CMD[key][0]]
            self.assertEqual(struct.unpack_from('<i', core.ram, args[0] + CT.MINIGAME_CMD[key][1])[0], 0)          # (0: poker)
        self.assertIn('error', self.c.minigame({'id': 'nothing'}))
        self.assertEqual(self.c.open_book(), {'ok': True}); e.run_step_calls()
        self.assertIn(('Global::startBook', (0x880,)), core.calls)
        self.c._backdrops = ['btl_pl_d', 'btlcc1_d']
        self.assertEqual(self.c.set_backdrop({'name': 'BTLCC1_D'}), {'ok': True}); self.assertEqual(self.g.battle_scenery, 'btlcc1_d')
        self.assertIn('error', self.c.set_backdrop({'name': 'town1'}))
        self.c.set_backdrop({'name': ''}); self.assertIsNone(self.g.battle_scenery)
        e.state.part = ENGINE.PART_BATTLE
        for bad in (self.c.battle_here({'row': '3'}), self.c.minigame({'id': 'poker'}), self.c.open_book()): self.assertIn('error', bad)          # (not from a battle)

    def test_arenas_and_tnt(self):
        """DQ IV's monster arena (FightStadiumManager: a match, then its fight), DQ VI's slime arena (the scripts' own fight), DQ V's T'n'T (its hall, a ticket given)"""
        e = self.g.engine; core = e.core; e.state.part = ENGINE.PART_FIELD; e._global = lambda: 0x880
        SYMS.update({CT.STADIUM[0]: 0x970, CT.STADIUM[1]: 0x974, CT.STADIUM[2]: 0x978, '_ZN6script19cmdBattleSlimeArenaEv': 0x97c})
        self.c.key = 'dq4'; self.assertEqual(self.c.minigame({'id': 'monster_arena'}), {'ok': True}); e.run_step_calls()
        self.assertEqual([c[0] for c in core.calls[-3:]], list(CT.STADIUM)); self.assertEqual(core.calls[-1][1], (0x777,))          # (on the manager getSingleton gave)
        self.c.key = 'dq6'; self.assertEqual(self.c.minigame({'id': 'slime_arena'}), {'ok': True}); e.run_step_calls()
        self.assertEqual(core.calls[-1], ('_ZN6script19cmdBattleSlimeArenaEv', ()))
        self.c.key = 'dq5'; given = []; entered = []
        self.c.give_item = lambda c: given.append(c); self.c.start_town = lambda m: entered.append(m)
        self.assertEqual(self.c.minigame({'id': 'tnt'}), {'ok': True}); e.run_step_calls()
        self.assertEqual((given, entered), ([{'item': 178, 'count': 1}], ['mdc1b1']))
        self.assertIn('error', self.c.minigame({'id': 'monster_arena'}))                                                    # (not a DQ V game)

    def test_panel_commands_registered(self):
        for k in ('goto_map', 'goto_world', 'story_step', 'flag', 'battle', 'give_item', 'gold', 'battle_here', 'minigame', 'book', 'backdrop'): self.assertIn(k, self.d.panel_commands)
        self.assertIn('/api/catalog', self.d.panel_pages)
        self.assertEqual(set(self.c.info({'only': 'flags'})), {'map', 'flags'})


if __name__ == '__main__':
    unittest.main()
