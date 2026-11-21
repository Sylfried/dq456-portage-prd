"""Party and resources of the debug mode (the web panel's "Party" tab): the members' level and experience, who is in the party, the money of every kind, the play time,
every item at once. Everything goes through the game's own functions, on the window's thread between two engine steps (DebugService.call).

- Members: DQ IV's party list (`g_Party + 0x304`, character numbers) or DQ V / VI's (`PartyStatusData::getOrder` / `getPlayerStatusForCtrl`); a character's status is its
  HaveStatusInfo, its HaveStatus inside (DQ IV + 0x48, DQ V + 0xb8, DQ VI + 0x94): character number at + 0, experience at + 8, level at + 0xe (DQ IV) / + 0x13 (DQ V / VI).
  The level is set by `HaveStatusInfo::levelup(level)`, the call the game makes for a character joining at a given level: the stats of that level and the spells learnt
  up to it. The experience by `HaveStatusInfo::setExp`.
- Joining and leaving: the scripts' own commands (DQ IV `cmd_set_party_join` / `cmd_set_party_quit`, DQ V `cmdCharacterSetPartyJoin` / `...Quit`, DQ VI `cmdSetPartyJoin` /
  `...Quit`), so the town's followers are updated as when the story does it. The candidates are every character number the game has a name for (TextAPI, kind 5).
- Money: `BasePartyStatus::setGold` / `setBankMoney` / `setCasinoCoin` / `setMedalCoin` on `g_Party` (their own limits apply); play time: `GameStatus::setPlayTime` on `g_Game`.
- Every item: `HaveItemSack::add(item, 99)` on the bag (DQ IV `g_Party + 0x10`, DQ V / VI `+ 0x28`; checked against the class's virtual table), every item number the game
  names, those the game's own `HaveItemSack::fill` leaves out included."""
import struct

from dq456_pc import games as GM
from dq456_pc.core import engine as ENGINE
from dq456_pc.debug.catalog import TEXT, engine_text          # (TEXT: the tests' symbol table)
from dq456_pc.language import N_, tr

PARTY = '_ZN6status7g_PartyE'
GAME = '_ZN6status6g_GameE'
PLAYER_NAME = 0x5000000                              # TextAPI kind 5: a character's name by number
CHARACTER_MAX = {'dq4': 64, 'dq5': 0x200, 'dq6': 0x200}          # character numbers tried for names
HAVE_STATUS = {'dq4': 0x48, 'dq5': 0xb8, 'dq6': 0x94}            # HaveStatus inside HaveStatusInfo
LEVEL_AT = {'dq4': 0xe, 'dq5': 0x13, 'dq6': 0x13}                 # level byte in HaveStatus (+ 8: the experience, + 0: the character number)
LEVELUP = '_ZN6status14HaveStatusInfo7levelupEi'
SET_EXP = '_ZN6status14HaveStatusInfo6setExpEi'
JOIN = {'dq4': ('_Z18cmd_set_party_joinP16CommandParameter', '_Z18cmd_set_party_quitP16CommandParameter', 4),
        'dq5': ('_Z24cmdCharacterSetPartyJoinPv', '_Z24cmdCharacterSetPartyQuitPv', 0),
        'dq6': ('_ZN6script15cmdSetPartyJoinEPv', '_ZN6script15cmdSetPartyQuitEPv', 0)}          # (join, leave, offset of the character number in the command's block)
VALUES = {'dq4': {'gold': 0x2e0, 'bank': 0x2e4, 'coins': 0x2e8, 'medals': 0x2f0}, 'dq5': {'gold': 0x14, 'bank': 0x18, 'coins': 0x1c, 'medals': 0x20}}
VALUES['dq6'] = VALUES['dq5']                        # (g_Party offsets, read back after the setter)
SETTERS = {'gold': '_ZN6status15BasePartyStatus7setGoldEj', 'bank': '_ZN6status15BasePartyStatus12setBankMoneyEj',
           'coins': '_ZN6status15BasePartyStatus13setCasinoCoinEj', 'medals': '_ZN6status15BasePartyStatus12setMedalCoinEj'}
PLAY_TIME = ('_ZN6status10GameStatus11setPlayTimeEj', 4)          # g_Game + 4
HISTORY = ('_ZN6status15g_BattleHistoryE', (('AdventureTime', 'j'), ('BattleCount', 'j'), ('VictoryCount', 'j'), ('WipeoutCount', 'j'), ('EscapeCount', 'j'),
            ('MonsterCount', 'j'), ('TotalGold', 'j'), ('MaxDamage', 't'), ('HeroLevel', 'h'), ('Title', 't'), ('RestMonsterCount', 'h'), ('ChapterBattleCount', 'j'),
            ('ChapterWipeoutCount', 'j'), ('ChapterEscapeCount', 'j')))          # DQ IV's adventure record (status::BattleHistory, its getters and setters; the chapter's counters for the current chapter)
MASKS = {'j': 0xffffffff, 't': 0xffff, 'h': 0xff}
SACK = {'dq4': 0x10, 'dq5': 0x28, 'dq6': 0x28}
SACK_ADD = '_ZN6status12HaveItemSack3addEii'
SACK_VTABLE = '_ZTVN6status12HaveItemSackE'
ITEM_FULL = 99
LEVEL_MAX, EXP_MAX = 99, 9999999
VALUE_MAX = {'gold': 999999, 'bank': 99999000, 'play_time': 216000000 - 1}          # the games' own caps (their setters: BasePartyStatus::setGold..., GameStatus::setPlayTime, in 1/60 s)
COINS_MAX = {'dq4': 999999, 'dq5': 9999999, 'dq6': 9999999}; MEDALS_MAX = {'dq4': 99, 'dq5': 999, 'dq6': 999}
STAT_MAX = {'HeroLevel': LEVEL_MAX}                     # (DQ IV's record: otherwise its field's size, MASKS)


def history_fn(kind, field, arg):
    """Mangled name of BattleHistory::get<field>() / set<field>(type)."""
    return '_ZN6status13BattleHistory%d%s%sE%s' % (3 + len(field), kind, field, arg)


class Party:
    """Registered on the debug service: the page `/api/party` and its commands."""

    def __init__(self, service, catalog=None):
        self.d = service; self.g = service.g; self.key = GM.profile().key; self.catalog = catalog
        self._names = None
        service.panel_pages['/api/party'] = self.info
        service.panel_commands.update({'party_level': self.set_level, 'party_exp': self.set_exp, 'party_join': self.join, 'party_quit': self.quit,
                                       'value': self.set_value, 'items_max': self.items_max})

    # --- reading -------------------------------------------------------------------------------------------------------------------------------------------------
    def _core(self): return self.g.engine.core

    def _text(self, kind, number): return engine_text(self._core(), self.key, kind, number)

    def characters(self):
        """[(character number, name)]: every number the game names."""
        if self._names is None:
            out = []
            for n in range(1, CHARACTER_MAX[self.key]):
                t = self._text(PLAYER_NAME, n)
                if t: out.append((n, t))
            self._names = out
        return self._names

    def _status_infos(self):
        """[(character number, HaveStatusInfo address)] of the party, in its order, the wagon included."""
        c = self._core(); X = c.export; out = []
        if self.key == 'dq4':
            party, orig = X(PARTY), X('_ZN6status9PartyData15originalPlayer_E')
            for i in struct.unpack('<%dI' % ENGINE.PARTY_SLOTS, c.mem(party + ENGINE.PARTY_MEMBERS, 4 * ENGINE.PARTY_SLOTS)):
                if i: out.append(orig + ENGINE.PLAYER_STRIDE * i + ENGINE.PLAYER_HAVESTATUS - HAVE_STATUS['dq4'])
        else:
            pd_off, hsi_off = ENGINE.HEAL_V2[self.key][:2]
            pd = X(PARTY) + pd_off
            count, order, player = X('_ZN6status15PartyStatusData8getCountEv'), X('_ZN6status15PartyStatusData8getOrderEi'), X('_ZN6status15PartyStatusData22getPlayerStatusForCtrlEi')
            for i in range(min(c.call(count, pd, label='getCount') & 0xffffffff, 16)):
                ctrl = c.call(order, pd, i, label='getOrder') & 0xffffffff
                if ctrl != 0xffffffff: out.append(c.call(player, pd, ctrl, label='getPlayerStatusForCtrl') + hsi_off)
        res = []
        for hsi in out:
            hs = hsi + HAVE_STATUS[self.key]
            res.append((struct.unpack('<H', c.mem(hs, 2))[0], hsi))
        return res

    def members(self):
        c = self._core(); names = dict(self.characters()); out = []
        for number, hsi in self._status_infos():
            hs = hsi + HAVE_STATUS[self.key]
            level = c.mem(hs + LEVEL_AT[self.key], 1)[0]
            out.append({'id': number, 'name': names.get(number) or '#%d' % number, 'level': level, 'exp': struct.unpack('<I', c.mem(hs + 8, 4))[0],
                        'guest': level > LEVEL_MAX})          # (a guest the game gives no status: level 255, experience 0xffffffff; its level cannot be set)
        return out

    def values(self):
        c = self._core(); party = c.export(PARTY); out = {k: struct.unpack('<I', c.mem(party + off, 4))[0] for k, off in VALUES[self.key].items()}
        g = c.export(GAME)
        if g: out['play_time'] = struct.unpack('<I', c.mem(g + PLAY_TIME[1], 4))[0]
        h = c.export(HISTORY[0]) if self.key == 'dq4' else None
        for field, t in (HISTORY[1] if h else ()):
            f = c.export(history_fn('get', field, 'v'))
            if f: out['stat:' + field] = c.call(f, h, label='BattleHistory::get' + field) & MASKS[t]
        return out

    def limits(self):
        """{value key: [least, most]} that the panel's fields take (the game's own caps), and the members' level and experience."""
        out = {'level': [1, LEVEL_MAX], 'exp': [0, EXP_MAX], 'gold': [0, VALUE_MAX['gold']], 'bank': [0, VALUE_MAX['bank']], 'coins': [0, COINS_MAX[self.key]],
               'medals': [0, MEDALS_MAX[self.key]], 'play_time': [0, VALUE_MAX['play_time']]}
        for field, t in HISTORY[1]: out['stat:' + field] = [0, STAT_MAX.get(field, MASKS[t])]
        return out

    def info(self, q=None):
        return {'game': self.key, 'members': self.members(), 'characters': self.characters(), 'values': self.values(), 'limits': self.limits()}

    # --- commands ------------------------------------------------------------------------------------------------------------------------------------------------
    def _member(self, c):
        try: n = int(c.get('id'))
        except (TypeError, ValueError): n = None
        return next((hsi for number, hsi in self._status_infos() if number == n), None), n

    def _guest(self, hsi):
        return self._core().mem(hsi + HAVE_STATUS[self.key] + LEVEL_AT[self.key], 1)[0] > LEVEL_MAX

    def set_level(self, c):
        hsi, n = self._member(c)
        try: level = int(c.get('level'))
        except (TypeError, ValueError): level = 0
        if hsi is None: return {'error': tr(N_('debug.party_not_member')) % n}
        if not 1 <= level <= LEVEL_MAX: return {'error': tr(N_('debug.party_level_bad')) % c.get('level')}
        if self._guest(hsi): return {'error': tr(N_('debug.party_guest')) % n}
        core = self._core()
        if core.call(core.export(LEVELUP), hsi, level, label='HaveStatusInfo::levelup') is None: return {'error': tr(N_('debug.party_guest')) % n}
        self.d.note(tr(N_('debug.party_level')) % (n, level)); return {'ok': True}

    def set_exp(self, c):
        hsi, n = self._member(c)
        try: exp = int(c.get('exp'))
        except (TypeError, ValueError): exp = -1
        if hsi is None: return {'error': tr(N_('debug.party_not_member')) % n}
        if not 0 <= exp <= EXP_MAX: return {'error': tr(N_('debug.party_exp_bad')) % c.get('exp')}
        if self._guest(hsi): return {'error': tr(N_('debug.party_guest')) % n}
        core = self._core()
        if core.call(core.export(SET_EXP), hsi, exp, label='HaveStatusInfo::setExp') is None: return {'error': tr(N_('debug.party_guest')) % n}
        self.d.note(tr(N_('debug.party_exp')) % (n, exp)); return {'ok': True}

    def _command(self, which, c):
        try: n = int(c.get('id'))
        except (TypeError, ValueError): return {'error': tr(N_('debug.party_not_member')) % c.get('id')}
        if n not in dict(self.characters()): return {'error': tr(N_('debug.party_unknown')) % n}
        join, quit_, off = JOIN[self.key]; core = self._core(); f = core.export(join if which == 'join' else quit_)
        if not f: return {'error': tr(N_('debug.catalog_missing')) % (join if which == 'join' else quit_)}
        block = bytearray(16); struct.pack_into('<i', block, off, n)
        def run():
            p = core.scratch('debug_party_cmd', len(block)); core.mem_w(p, bytes(block)); core.call(f, p, label=join if which == 'join' else quit_)
        if self.catalog is not None: self.catalog.in_step.append(run)          # (during the next step, as the scripts run: the town's followers follow)
        else: run()
        self.d.note((tr(N_('debug.party_joined')) if which == 'join' else tr(N_('debug.party_left'))) % dict(self.characters())[n])
        return {'ok': True}

    def join(self, c): return self._command('join', c)

    def quit(self, c): return self._command('quit', c)

    def set_value(self, c):
        k = c.get('key')
        try: v = int(c.get('value'))
        except (TypeError, ValueError): return {'error': tr(N_('debug.party_value_bad')) % c.get('value')}
        least, most = self.limits().get(k, (0, 0xffffffff))
        if not least <= v <= most: return {'error': tr(N_('debug.party_value_range')) % (least, most)}
        core = self._core()
        if k == 'play_time': fn, obj = PLAY_TIME[0], core.export(GAME)
        elif k in SETTERS: fn, obj = SETTERS[k], core.export(PARTY)
        elif isinstance(k, str) and k.startswith('stat:') and self.key == 'dq4' and k[5:] in dict(HISTORY[1]):
            t = dict(HISTORY[1])[k[5:]]; fn, obj = history_fn('set', k[5:], t), core.export(HISTORY[0])
            if v > MASKS[t]: return {'error': tr(N_('debug.party_value_bad')) % v}
        else: return {'error': tr(N_('debug.party_value_bad')) % k}
        f = core.export(fn)
        if not f or not obj: return {'error': tr(N_('debug.catalog_missing')) % fn}
        core.call(f, obj, v & 0xffffffff, label=fn)
        self.d.note(tr(N_('debug.party_value')) % (k, self.values().get(k, v))); return {'ok': True}

    def items_max(self, c=None):
        """ITEM_FULL of every item the game names, into the bag."""
        core = self._core(); sack = core.export(PARTY) + SACK[self.key]; vt = core.export(SACK_VTABLE); add = core.export(SACK_ADD)
        if not add or not vt or struct.unpack('<Q', core.mem(sack, 8))[0] != vt + 16: return {'error': tr(N_('debug.party_no_sack'))}
        items = self.catalog.items() if self.catalog is not None else []
        for number, _name in items: core.call(add, sack, number, ITEM_FULL, label='HaveItemSack::add')
        self.d.note(tr(N_('debug.party_items_max')) % len(items)); return {'ok': True, 'items': len(items)}
