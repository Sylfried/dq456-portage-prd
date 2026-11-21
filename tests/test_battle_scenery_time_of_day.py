"""Battle scenery according to the world time (twilight or sunrise in the battle scenery).

Real engine (Unicorn): `StageStatus + 0xcc` = world time (0 to 3391, it advances while walking), `+ 0xc8` = time zone that `WorldLocation::setCurrentTimeZone` derives from it:
    0 to 127: zone 1; 128 to 1983: zone 2 (day); 1984 to 2239: zone 3 (evening); 2240 to 3263: zone 4 (night); 3264 to 3391: zone 1.
`StageStatus::getBtlMapName` (0x1a4580) replaces the last letter of the scenery with the `zone` byte of the byte table "d n d e n": zones 1 and 4 -> `_n`, zone 2 -> `_d`, zone 3 -> `_e`.
So there is NO dawn scenery: the start and end of the night (zone 1) reuse the night scenery. The evening one (`_e`) exists, is requested by the engine, and every world map scenery has one.
In battle the engine sends neither a tint (packet 33) nor a background color (packet 8): the color of the day, evening and night is in the assets (vertex colors + `_d`, `_e`, `_n` textures).
The battle BACKGROUND is the `bg_color` of the FETC block of the loaded scenery (`cmd_fld_load` -> `BackColor.SetColor`, read in libil2cpp): orange / red in the evening, dark blue at night (map_tint.fetc_background, Backgrounds).

    DQ_APK=<patched APKs folder> DQ_SAVES=<saves folder> python3 tests/test_battle_scenery_time_of_day.py        (DQ_MAP_CACHE=<map cache>: also checks the assets)"""
import os, sys, struct, tempfile, unittest, pickle, json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from dq456_pc.core import engine as ENGINE
from dq456_pc.world import data as DO
from dq456_pc.world import map_tint as TC

APK, SAVES, CACHE = os.environ.get('DQ_APK'), os.environ.get('DQ_SAVES'), os.environ.get('DQ_MAP_CACHE')
STAGE_ADDRESS = 0x23d430          # GOT: StageStatus (g_StageStatus)
CASES = [(64, 1, 'n'), (1000, 2, 'd'), (1984, 3, 'e'), (2239, 3, 'e'), (2240, 4, 'n'), (3300, 1, 'n')]      # (world time, expected zone, scenery letter)
WORLD_SCENERIES = ['btl_pl', 'btl_se', 'btl_sd', 'btl_mt', 'btl_po', 'btl_wd']


class Fallback(unittest.TestCase):
    def test_missing_variant_replaced_by_the_nearest(self):
        self.assertEqual(DO.fallback_sceneries('btlcc1_n'), ['btlcc1_e', 'btlcc1_d', 'btlcc1'])         # btlcc1 only has _d and _e in the data
        self.assertEqual(DO.fallback_sceneries('btl_pl_e'), ['btl_pl_d', 'btl_pl_n', 'btl_pl'])
        self.assertEqual(DO.fallback_sceneries('btl_pl_d'), ['btl_pl_e', 'btl_pl_n', 'btl_pl'])
        self.assertEqual(DO.fallback_sceneries('btl_se_dk'), ['btl_se_n', 'btl_se_d', 'btl_se_e', 'btl_se'])      # dark world: the engine asks for btl_se_dk, absent from the data (only pl, po, wd and tc1 have it)
        for name_str in ('btlcb2', 'ev01', '', None): self.assertEqual(DO.fallback_sceneries(name_str), [], name_str)

    def test_chosen_scenery_at_the_engines_time(self):
        """a backdrop chosen in the debug panel takes the time of day the engine asks when both have one, otherwise stays as chosen"""
        self.assertEqual(DO.with_time_of('btl_se_d', 'btl_pl_n'), 'btl_se_n'); self.assertEqual(DO.with_time_of('btltc1_d', 'btl_pl_dk'), 'btltc1_dk')
        for chosen, engine in (('btlca1', 'btl_pl_n'), ('btl_se_d', 'btlcb2'), ('btl_dk2', 'btl_pl_e')): self.assertEqual(DO.with_time_of(chosen, engine), chosen)


class BackgroundFETC(unittest.TestCase):
    @staticmethod
    def map_of(r, g, b, a=0):
        """Minimal .map file: a FETC block (40 bytes) whose first 4 useful bytes are r, g, b, a."""
        return b'FETC' + struct.pack('<I', 0x28) + bytes([r, g, b, a]) + bytes(28)

    def test_color_is_read_from_fetc_block(self):
        self.assertEqual(TC.fetc_background(self.map_of(192, 52, 0)), (192 / 255.0, 52 / 255.0, 0.0))        # btl_mt_e: the orange red of the sunset
        self.assertEqual(TC.fetc_background(self.map_of(0, 0, 0)), (0.0, 0.0, 0.0))

    def test_fetc_alpha_is_ignored(self):
        self.assertEqual(TC.fetc_background(self.map_of(10, 20, 30, 0)), TC.fetc_background(self.map_of(10, 20, 30, 255)))        # alpha 0 in the 140 sceneries, BackColor shader without blending

    def test_no_fetc_block_no_color(self):
        self.assertIsNone(TC.fetc_background(b'FHDR' + struct.pack('<I', 0x98) + bytes(0x90)))
        self.assertIsNone(TC.fetc_background(b''))

    def test_unreadable_or_absent_cache_gives_none(self):
        d = tempfile.mkdtemp(prefix='backgrounds_')
        self.assertIsNone(TC.read_backgrounds(d))
        with open(os.path.join(d, TC.BACKGROUNDS_FILE), 'w') as fh: fh.write('not json')
        self.assertIsNone(TC.read_backgrounds(d))
        with open(os.path.join(d, TC.BACKGROUNDS_FILE), 'w') as fh: json.dump({'backgrounds': {'btl_pl_e': [0.5, 0.25, 0.0]}}, fh)
        self.assertEqual(TC.read_backgrounds(d), {'btl_pl_e': (0.5, 0.25, 0.0)})

    def test_backgrounds_serve_cache_then_leave_estimate_if_missing(self):
        d = tempfile.mkdtemp(prefix='backgrounds_')
        with open(os.path.join(d, TC.BACKGROUNDS_FILE), 'w') as fh: json.dump({'backgrounds': {'btl_pl_e': [0.5, 0.25, 0.0]}}, fh)
        class Fake: cache = d
        f = TC.Backgrounds(Fake())
        self.assertEqual(f.color('btl_pl_e'), (0.5, 0.25, 0.0))
        self.assertIsNone(f.color('btl_inconnu_d'))


@unittest.skipUnless(APK and SAVES, 'real engine: DQ_APK=<folder of the patched APKs> DQ_SAVES=<folder of the saves>')
class RealEngine(unittest.TestCase):
    @staticmethod
    def encounter(hour):
        """-> (zone read, scenery name, tints and backgrounds received at the start of the battle) for an encounter on the world map at this world time (slot 4)."""
        eng = ENGINE.Engine(APK, SAVES, tempfile.mkdtemp(prefix='sv_heure_'))
        for _ in range(3): eng.step(0, 0)
        eng.load_save(4)
        for i in range(100): eng.step(0, 0x10 if i % 10 == 0 else 0)
        if eng.state.part != ENGINE.PART_FIELD:
            eng.start_field()
            for i in range(120): eng.step(0, 0)
        c = eng.core
        g = struct.unpack('<Q', bytes(c.mem(eng.L + STAGE_ADDRESS, 8)))[0]
        c.mem_w(g + 0xcc, struct.pack('<i', hour))
        c.call(eng.E('_ZN3cmn13WorldLocation18setCurrentTimeZoneEv'), c.call(eng.E('_ZN3cmn13WorldLocation12getSingletonEv'), label='wl'), label='zone')
        zone = struct.unpack('<I', bytes(c.mem(g + 0xc8, 4)))[0]
        c.call(eng.E('_ZN7Encount12forceEncountEv'), c.call(eng.E('_ZN7Encount12getSingletonEv'), label='enc'), label='force')
        received_list = []; n0 = None
        for i in range(1500):
            pkt = eng.step([0x4, 0x8][(i // 20) % 2], 0)
            if n0 is not None: received_list += [(t, list(f[:3])) for t, a, f in pkt if t in (8, 33)]
            if eng.state.part == ENGINE.PART_BATTLE and n0 is None: n0 = eng.frames
            if n0 is not None and eng.frames - n0 > 40: break
        return zone, eng.state.game_map, received_list

    def test_engine_picks_day_evening_or_night_by_world_hour(self):
        for hour, zone_att, letter in CASES:
            zone, game_map, received_list = self.encounter(hour)
            self.assertEqual(zone, zone_att, 'heure %d' % hour)
            self.assertEqual(game_map, 'btl_pl_' + letter, 'heure %d : zone %d' % (hour, zone))
            self.assertEqual(received_list, [], 'hour %d: the engine does not tint the battle scenery and sends no background' % hour)


@unittest.skipUnless(APK, 'real resources: DQ_APK=<folder of the patched APKs>')
class RealBackgrounds(unittest.TestCase):
    """The 140 battle sceneries of the data pack (reading the pack: several seconds, ~1 GB: to be run via engine_slot.sh)."""
    @classmethod
    def setUpClass(cls):
        cls.data = DO.Data(os.path.join(APK, 'split_UnityDataAssetPack.apk'), tempfile.mkdtemp(prefix='real_backgrounds_'), log=lambda *a: None)
        cls.backgrounds = TC.compute_backgrounds(cls.data)

    def test_140_sceneries_have_background(self):
        self.assertEqual(len(self.backgrounds), 140)
        self.assertEqual(TC.read_backgrounds(self.data.cache), self.backgrounds)                         # and the cache is read back as is

    def test_evening_background_is_warmer_and_night_darker(self):
        def warmth(c): return c[0] - c[2]
        for base in ('btl_pl', 'btl_se', 'btl_sd', 'btl_mt', 'btl_wd'):
            j, e, n = (self.backgrounds['%s_%s' % (base, x)] for x in 'den')
            self.assertGreater(warmth(e), warmth(j) + 0.15, base + ': the evening background is warmer (orange) than the day one')
            self.assertLess(sum(n), sum(j), base + ': the night background is darker than the day one')

    def test_known_values(self):
        r = lambda name_str: tuple(round(255 * x) for x in self.backgrounds[name_str])
        self.assertEqual(r('btl_pl_d'), (77, 186, 88)); self.assertEqual(r('btl_mt_e'), (192, 52, 0)); self.assertEqual(r('btl_se_n'), (8, 38, 107)); self.assertEqual(r('btl_sd_e'), (255, 115, 33))


@unittest.skipUnless(CACHE and os.path.isdir(CACHE), 'assets: DQ_MAP_CACHE=<map cache>')
class Assets(unittest.TestCase):
    @staticmethod
    def scenery(name_str):
        with open(os.path.join(CACHE, DO.MAP_PREFIX + '%s.pkl' % name_str), 'rb') as fh: return pickle.load(fh)

    def test_each_world_map_scenery_has_its_three_variants(self):
        for base in WORLD_SCENERIES:
            for letter in 'den': self.assertTrue(os.path.exists(os.path.join(CACHE, DO.MAP_PREFIX + '%s_%s.pkl' % (base, letter))), '%s_%s' % (base, letter))

    def test_evening_redder_night_darker_and_bluer_than_day(self):
        def average(name_str):
            c = self.scenery(name_str); r = [g[2][:, :3].mean(0) for g in c['groups'].values()]
            return sum(r) / len(r)                                      # average vertex color: the tint of the moment is baked into the asset
        for base in WORLD_SCENERIES:
            j, s, n = average(base + '_d'), average(base + '_e'), average(base + '_n')
            self.assertGreater(j.mean(), s.mean(), base + ': the evening is darker than the day')
            self.assertGreater(s.mean(), n.mean(), base + ': the night is darker than the evening')
            self.assertGreater(s[0] - s[2], j[0] - j[2] + 0.1, base + ': the evening is redder (red - blue)')
            self.assertGreater(j[0] - j[2], n[0] - n[2] + 0.05, base + ': the night is bluer (red - blue)')


if __name__ == '__main__': unittest.main()
