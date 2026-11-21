"""Side systems of the debug mode (the web panel's "Party" tab, "Side activities" and "Vocations"): the progress the story does not hold, through the game's own
data and functions.

- DQ VI's fashion contest: the rank (1 to 8) is the global switches 0x209 to 0x20f (`StoryStatus::getContestRank`: the highest one on, rank 1 without any).
- DQ VI's slime arena: the ranks won (0 to 8) are the global switches 0x275 to 0x27c, set or removed in order (`SlimeArenaData::checkArenaVictory` reads the
  highest one as the arena's rank).
- DQ VI's slime curling (`g_CurlingData`): the courses open (u16 at + 6, a bit per course, `setPlayFlag`), the best score of each (u16 at + 8 + 2 x course).
- DQ V's Slime Touch (`g_TouchData`): its ranking of 10 scores (i32 at + 8 x place; `TouchData::setRanking` inserts a score as the game does).
- DQ VI's vocations: each member's level in each of the 18 vocations (`HaveJob`, the member's status + 0x230: `getJobLevel` / `setJobLevel`, which also puts the
  number of fights that level needs); their names are the engine's (TextAPI kind 0xb).
The switches go through `GameFlag::set` / `remove` on `g_GlobalFlag`."""
import struct

from dq456_pc import games as GM
from dq456_pc.debug.catalog import engine_text
from dq456_pc.language import N_, tr

GLOBAL_FLAG = '_ZN6status12g_GlobalFlagE'
FLAG_SET, FLAG_REMOVE, FLAG_CHECK = '_ZN6status8GameFlag3setEj', '_ZN6status8GameFlag6removeEj', '_ZN6status8GameFlag5checkEj'
CONTEST_FLAGS = range(0x209, 0x210)                       # DQ VI: rank 2 to 8
ARENA_FLAGS = range(0x275, 0x27d)                         # DQ VI: ranks 1 to 8 won
CURLING, CURLING_OPEN, CURLING_BEST, CURLING_COURSES = '_ZN6status13g_CurlingDataE', 6, 8, 6
TOUCH, TOUCH_PLACES, TOUCH_STRIDE = '_ZN6status11g_TouchDataE', 10, 8
JOB_LEVEL, SET_JOB_LEVEL = '_ZN6status7HaveJob11getJobLevelEN3dq614DQ6_PLAYER_JOBE', '_ZN6status7HaveJob11setJobLevelEN3dq614DQ6_PLAYER_JOBEi'
HAVE_JOB, JOBS, JOB_NAME, JOB_LEVEL_MAX = 0x230 - 0x70, 18, 0xb000000, 8     # (HaveJob from the member's HaveStatusInfo: status + 0x230, HaveStatusInfo at + 0x70)
SCORE_MAX = 0xffff
SYSTEMS = {'dq5': ('touch',), 'dq6': ('contest', 'arena', 'curling_open', 'curling_best')}


class Extras:
    """Registered on the debug service: the page `/api/extras` and the command `extra_set`."""

    def __init__(self, service, party=None):
        self.d = service; self.g = service.g; self.key = GM.profile().key; self.party = party
        service.panel_pages['/api/extras'] = self.info
        service.panel_commands.update({'extra_set': self.set_value, 'job_level': self.set_job})

    def _core(self): return self.g.engine.core

    def _x(self, name):
        a = self._core().export(name)
        if not a: raise RuntimeError(tr(N_('debug.catalog_missing')) % name)
        return a

    # --- switches -----------------------------------------------------------------------------------------------------------------------------------------------
    def _flag(self, n): core = self._core(); return bool(core.call(self._x(FLAG_CHECK), self._x(GLOBAL_FLAG), n, label='GameFlag::check') & 1)

    def _flags(self, flags, on_count):
        """The first `on_count` switches of `flags` on, the others off."""
        core = self._core(); gf = self._x(GLOBAL_FLAG)
        for i, n in enumerate(flags): core.call(self._x(FLAG_SET if i < on_count else FLAG_REMOVE), gf, n, label='GameFlag')

    # --- reading -------------------------------------------------------------------------------------------------------------------------------------------------
    def systems(self):
        core = self._core(); out = []
        for sid in SYSTEMS.get(self.key, ()):
            if sid == 'contest': out.append({'id': sid, 'kind': 'number', 'min': 1, 'max': 8, 'value': 1 + sum(self._flag(n) for n in CONTEST_FLAGS)})
            elif sid == 'arena': out.append({'id': sid, 'kind': 'number', 'min': 0, 'max': len(ARENA_FLAGS), 'value': sum(self._flag(n) for n in ARENA_FLAGS)})
            elif sid == 'curling_open':
                bits = struct.unpack('<H', core.mem(self._x(CURLING) + CURLING_OPEN, 2))[0]
                out.append({'id': sid, 'kind': 'bits', 'items': [[i, bool(bits >> i & 1)] for i in range(CURLING_COURSES)]})
            elif sid == 'curling_best':
                best = struct.unpack('<%dH' % CURLING_COURSES, core.mem(self._x(CURLING) + CURLING_BEST, 2 * CURLING_COURSES))
                out.append({'id': sid, 'kind': 'numbers', 'max': SCORE_MAX, 'items': [[i, v] for i, v in enumerate(best)]})
            elif sid == 'touch':
                t = self._x(TOUCH)
                out.append({'id': sid, 'kind': 'numbers', 'max': SCORE_MAX, 'items': [[i, struct.unpack('<i', core.mem(t + TOUCH_STRIDE * i, 4))[0]] for i in range(TOUCH_PLACES)]})
        return out

    def jobs(self):
        """DQ VI: [(job, name)] and each member's {'id', 'name', 'levels'}; None in the other games."""
        if self.key != 'dq6' or self.party is None: return None
        core = self._core(); get = self._x(JOB_LEVEL); names = dict(self.party.characters()); members = []
        for number, hsi in self.party._status_infos():
            members.append({'id': number, 'name': names.get(number) or '#%d' % number,
                            'levels': [core.call(get, hsi + HAVE_JOB, j, label='HaveJob::getJobLevel') & 0xff for j in range(1, JOBS + 1)]})
        return {'jobs': [(j, engine_text(core, self.key, JOB_NAME, j) or str(j)) for j in range(1, JOBS + 1)], 'members': members}

    def info(self, q=None):
        return {'game': self.key, 'systems': self.systems(), 'jobs': self.jobs()}

    # --- commands ------------------------------------------------------------------------------------------------------------------------------------------------
    def set_value(self, c):
        sid = c.get('id')
        try: v = int(c.get('value')); i = int(c.get('i', 0))
        except (TypeError, ValueError): return {'error': tr(N_('debug.party_value_bad')) % c.get('value')}
        info = next((s for s in self.systems() if s['id'] == sid), None)
        if info is None: return {'error': tr(N_('debug.party_value_bad')) % sid}
        core = self._core()
        if info['kind'] == 'number' and not info['min'] <= v <= info['max'] or info['kind'] == 'numbers' and not 0 <= v <= info['max'] \
                or info['kind'] in ('bits', 'numbers') and not 0 <= i < len(info['items']):
            return {'error': tr(N_('debug.party_value_bad')) % v}
        if sid == 'contest': self._flags(CONTEST_FLAGS, v - 1)
        elif sid == 'arena': self._flags(ARENA_FLAGS, v)
        elif sid == 'curling_open':
            a = self._x(CURLING) + CURLING_OPEN; bits = struct.unpack('<H', core.mem(a, 2))[0]
            core.mem_w(a, struct.pack('<H', bits | 1 << i if v else bits & ~(1 << i)))
        elif sid == 'curling_best': core.mem_w(self._x(CURLING) + CURLING_BEST + 2 * i, struct.pack('<H', v))
        elif sid == 'touch': core.mem_w(self._x(TOUCH) + TOUCH_STRIDE * i, struct.pack('<i', v))
        self.d.note(tr(N_('debug.extra_set')) % (sid, v)); return {'ok': True}

    def set_job(self, c):
        """DQ VI: member `id`'s level `level` (0 to 8) in vocation `job` (1 to 18)."""
        try: n, job, level = int(c.get('id')), int(c.get('job')), int(c.get('level'))
        except (TypeError, ValueError): return {'error': tr(N_('debug.party_value_bad')) % c.get('level')}
        if self.key != 'dq6' or self.party is None or not 1 <= job <= JOBS or not 0 <= level <= JOB_LEVEL_MAX: return {'error': tr(N_('debug.party_value_bad')) % level}
        hsi = next((h for number, h in self.party._status_infos() if number == n), None)
        if hsi is None: return {'error': tr(N_('debug.party_not_member')) % n}
        core = self._core(); core.call(self._x(SET_JOB_LEVEL), hsi + HAVE_JOB, job, level, label='HaveJob::setJobLevel')
        self.d.note(tr(N_('debug.extra_job')) % (n, job, level)); return {'ok': True}
