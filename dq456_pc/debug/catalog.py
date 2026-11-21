"""Scene catalog of the debug mode (the web panel's "Scenes" tab): go to a map, jump to a story step, read and change the story switches, start a fight.

- Maps: every town or dungeon of the game data (`assets/map/<name>.stg`), started as the game sends the hero through a link (GO_TO_EXIT), at the map's
  most obvious entrance (the one from the world map, or from the nearest map: `assets/map/map_link.bin`); the world map in front of the current place, as
  Evac does (GO_TO_FIELD).
- Story steps: the game's own table (`status::Status::setEventFlag(step)`: the global switches are cleared, then set as the table gives them for that step;
  `setEventParty(step)`: the party of that step). The games also give each step its map (DQ IV: `param/param_player_event1-4.dat`, DQ V / VI:
  `<game>ds_event_list.dat`, with a name in Japanese: shown in French, story_steps_fr.py): jumping there starts that map too. Only the switches and the party: items, levels and gold stay as they are.
- Switches: the three sets of 1,024 the scripts test (`status::g_GlobalFlag`, `g_AreaFlag`, `g_LocalFlag`), each bit readable and changeable.
- Items and gold: an item given as the game gives one it finds (`PartyStatus::giveItem`: the first member with room, otherwise the bag), gold added or taken as
  the scripts' own "gold" command does (`cmd_set_gold`: the game's limits apply).
- Fights. A boss (a row a script starts) is fought at its own place, as its script does: the map whose script starts it is loaded first, then
  `Encount::forceEventBrew(row)` (DQ IV: `BattleScriptManager::setEncountMap(row)` before it, as `cmd_encount` does): the backdrop is the place's own.
  Any other fight only where the game itself could start a random encounter at that moment, as its whistle does (ENCOUNT_ON...): never in a town,
  nor where a map or the story switches them off; then `Encount::forceEncount()`: a random encounter (its backdrop, the world map's ground included).

The calls that start a scene (startTown, startField, startBattle) are made during an engine step, just before GameUpdate: their packets (the fade) are then
read with the step's, as the game's own scripts do; outside a step they would be lost."""
import json, re, struct

from dq456_pc import games as GM
from dq456_pc.core import engine as ENGINE
from dq456_pc.debug import story_steps_fr as SF
from dq456_pc.language import N_, tr

FLAG_SETS = (('global', '_ZN6status12g_GlobalFlagE'), ('area', '_ZN6status10g_AreaFlagE'), ('local', '_ZN6status11g_LocalFlagE'))
FLAG_BYTES = 128                                     # GameFlag: 1,024 switches (GameFlag::clear zeroes 0x80 bytes, the same in the three games)
SET_EVENT_FLAG = '_ZN6status6Status12setEventFlagEj'          # static in the three games (step in w0)
SET_EVENT_PARTY = '_ZN6status6Status13setEventPartyEj'
STEP_COUNT_DQ4 = '_ZN5param9EventFlag5size_E'                  # DQ IV: rows of its step table
DQ4_EVENTS = ['assets/param/param_player_event%d.dat' % n for n in (1, 2, 3, 4)]          # DQ IV's steps (param::Event): rows of 156 bytes after a 4-byte check sum, step (u16), name (UTF-8, Japanese) at + 0x13, map at + 0x93; one row per party member, the first named
STEP_FIELD = {'dq5': (0, 0x10), 'dq6': (4, 0x16)}                # <game>ds_event_list.dat: offsets of the step number (u16) and of the name (UTF-8, Japanese) in a row; the map (capitals) follows
ENCOUNT = {'dq4': ('_ZN7Encount12getSingletonEv', '_ZN7Encount14forceEventBrewEi', '_ZN7Encount9forceBrewEi'),
           'dq5': ('_ZN3btl7Encount12getSingletonEv', '_ZN3btl7Encount14forceEventBrewEi', '_ZN3btl7Encount9forceBrewEi'),
           'dq6': ('_ZN3btl7Encount12getSingletonEv', '_ZN3btl7Encount14forceEventBrewEi', '_ZN3btl7Encount9forceBrewEi')}
SET_ENCOUNT_MAP = ('_ZN19BattleScriptManager12getSingletonEv', '_ZN19BattleScriptManager13setEncountMapEi')          # DQ IV's cmd_encount, mode 1, before forceEventBrew
ENCOUNT_OPS = {'dq4': (120, 136, 270, 356), 'dq5': (157, 158, 159), 'dq6': (96, 97, 98)}          # script commands that start a fight: + 4 the mode (1: event fight, forceEventBrew), + 8 the encounter row
ENCOUNT_ZONE = 0x8                                    # Encount + 8: the encounter row of the place where the hero stands (DQ IV: -1 in a town; DQ V / VI: 0 where nothing attacks)
IS_ENCOUNT_STAGE = '_ZN7Encount14isEncountStageEPc'   # DQ IV: does this map have random encounters (DQ V / VI: a stub that answers 1)
# An ordinary fight is called as the game's whistle does (status::BaseActionStatus::actionTypeNone, the three games): random encounters allowed on the map
# (StageStatus / StageAttribute::isEncount, which cmd_set_encount_stage_disable changes), not switched off by a script (the Encount byte that
# cmd_set_encount_disable clears), an encounter row where the hero stands; then Encount::forceEncount(): the next town / world map update starts the
# fight as a random encounter (its backdrop, the world map's ground included). The whistle's last test, the hero's state in a town
# (TownPlayerManager::isSaveAndBattleOK: false on the stairs he arrives by), is left out: a moment, not a place.
ENCOUNT_ON = {'dq4': 0x18, 'dq5': 0x10, 'dq6': 0x14}
STAGE_ENCOUNT = {'dq4': ('_ZN6status7g_StageE', '_ZNK6status11StageStatus9isEncountEv'),
                 'dq5': ('_ZN6status16g_StageAttributeE', '_ZNK6status14StageAttribute9isEncountEv'),
                 'dq6': ('_ZN6status16g_StageAttributeE', '_ZNK6status14StageAttribute9isEncountEv')}
FORCE_ENCOUNT = {'dq4': '_ZN7Encount12forceEncountEv', 'dq5': '_ZN3btl7Encount12forceEncountEv', 'dq6': '_ZN3btl7Encount12forceEncountEv'}
ITEM_NAMES = 'assets/msg/en/b1000000.mpt'              # item names: how many there are (the names themselves are the engine's, see engine_text)
TEXT = {'dq4': '_ZN7TextAPI11extractTextEPci', 'dq5': '_ZN4ardq7TextAPI11extractTextEPci', 'dq6': '_ZN4ardq7TextAPI11extractTextEPci'}
ITEM_TEXT, MONSTER_TEXT = 0x4000000, 0x6000000       # TextAPI kinds: an item's name, a monster's name, by the game's own number (the message files are not numbered
                                                     # alike: an item's name is in entry number - 1, a DQ V / VI monster's too, a DQ IV monster's in entry number)
GIVE_ITEM = {'dq4': ('_ZN6status7g_PartyE', '_ZN6status11PartyStatus8giveItemEi'),          # PartyStatus::giveItem(item) on g_Party; DQ V / VI: a static function, the item in w0
             'dq5': (None, '_ZN6status18PartyStatusUtility8giveItemEi'), 'dq6': (None, '_ZN6status18PartyStatusUtility8giveItemEi')}
SET_GOLD = {'dq4': ('_Z12cmd_set_goldP16CommandParameter', 4), 'dq5': ('_Z10cmdSetGoldPv', 0), 'dq6': ('_ZN6script10cmdSetGoldEPv', 0)}          # the scripts' "gold" command: its two
# parameters (amount, then 0 = add, anything else = take) at this offset of the block it receives
ITEM_COUNT_MAX = 99
# Mini-games started from anywhere, as the game starts them: DQ IV / V the scripts' own command (cmd_mini_game / cmdMiniGame: 0 poker, 1 slot machines), DQ VI
# Global::setMinigame then startCasino (what its CmdMiniGame does), the other parts by their own Global::start... call.
MINIGAMES = {'dq4': (('poker', 'cmd', 0), ('slots', 'cmd', 1), ('monster_arena', 'stadium', None)),
             'dq5': (('poker', 'cmd', 0), ('slots', 'cmd', 1), ('slime_race', 'global', '_ZN6Global14startSlimeRaceEv'), ('slime_touch', 'global', '_ZN6Global15startSlimeTouchEv'),
                     ('tnt', 'hall', ('mdc1b1', 178))),
             'dq6': (('poker', 'casino', 0), ('slots', 'casino', 1), ('curling', 'global', '_ZN6Global12startCurlingEv'), ('slime_arena', 'call', '_ZN6script19cmdBattleSlimeArenaEv'))}
# DQ IV's monster arena: FightStadiumManager::setup (a match of the casino's table) then battleStart (its backdrop, its 4 monsters), as the casino's counter does;
# DQ VI's slime arena: the scripts' own fight (script::cmdBattleSlimeArena: the rank of the arena's switches, the party of the moment, the backdrop of the place);
# DQ V's T'n'T: its hall (the counter that sells the boards) with a T'n'T ticket (item 178) given, the board started by talking to the attendant as in the game.
FIELD_RETURN = {'dq4': '_ZN6Global9startTownEPKc', 'dq5': '_ZN6Global9startTownEi', 'dq6': '_ZN6Global9startTownEi'}          # see Catalog._field_return
START_FIELD = '_ZN6Global10startFieldEv'
SAVE_PARTY = {'dq4': ('_ZN18FieldPlayerManager12getSingletonEv', '_ZN18FieldPlayerManager17savePartyDrawInfoEv'),       # the followers' places, kept as a
              'dq5': ('_ZN3fld18FieldPlayerManager12getSingletonEv', '_ZN3fld18FieldPlayerManager17savePartyDrawInfoEv')}  # fight does (FieldSystem::execEncount)
STADIUM = ('_ZN19FightStadiumManager12getSingletonEv', '_ZN19FightStadiumManager5setupEv', '_ZN19FightStadiumManager11battleStartEv')
MINIGAME_CMD = {'dq4': ('_Z13cmd_mini_gameP16CommandParameter', 4), 'dq5': ('_Z11cmdMiniGamePv', 0)}          # (function, offset of the game number in its block)
START_BOOK = '_ZN6Global9startBookEv'                                   # copies of an item given at once
MONSTER_NAMES = 'assets/msg/en/b1002000.mpt'           # monster names: how many there are (the French patch writes its texts in the English slot)
ARRIVAL_STEPS = 900                                   # a boss's map must be shown, faded in and the player free within this many engine steps
MAP_LINK = 'assets/map/map_link.bin'                  # the game's links between maps (doors, stairs, exits): DQ IV by map name (NAME, DATA, HASH), DQ V / VI by map number (DATA, INDX)
FLOOR_LIST = 'assets/leveldata/%sds_floor_list.dat'   # DQ V / VI: map number (u16) and name (+ 4) of every map
FIELD = '<field>'                                     # the world map in the links (DQ IV: "world"; DQ V: map 999, DQ VI: 9999)
FIELD_NUMBERS = (999, 9999)
GO_TO_EXIT = {'dq4': ('_ZN3cmn14g_extraMapLinkE', '_ZN3cmn12ExtraMapLink16setExtraExitTownEPKci'),          # the game's own "go to that map, at that link" (floor exits,
              'dq5': ('_ZN3cmn14g_extraMapLinkE', '_ZN3cmn12ExtraMapLink16setExtraExitTownEii'),             # Evac): StageLink::setTownExitIndex(key), Global::startTown(map), the
              'dq6': ('_ZN3cmn14g_BasicMapLinkE', '_ZN3cmn12BasicMapLink24setDirectLinkTownSurfaceEii')}     # link's arrival state, the player locked until he has arrived (DQ IV: by name)
FIELD_TYPE = {'dq4': ('_ZN3cmn14g_extraMapLinkE', '_ZN3cmn12ExtraMapLink20getFieldTypeBySymbolEi'),          # the world map a place's symbol is on (the game's
              'dq5': ('_ZN3cmn14g_extraMapLinkE', '_ZN3cmn12ExtraMapLink20getFieldTypeBySymbolEi'),          # field type: DQ IV 3 worlds, DQ V 4, DQ VI 4)
              'dq6': ('_ZN3cmn14g_BasicMapLinkE', '_ZN3cmn12BasicMapLink20getFieldTypeBySymbolEi')}
SYMBOL_MASK = {'dq4': 0xfff, 'dq5': 0xffff, 'dq6': 0xffff}           # a link key to the world map: the place's symbol, its kind above (0x7000 DQ IV, 0x70000 DQ V / VI)
ARRIVAL = 'Arrivée : '                                                # (the story steps' names of a first arrival somewhere)
GO_TO_FIELD = {'dq4': ('_ZN3cmn14g_extraMapLinkE', '_ZN3cmn12ExtraMapLink17setExtraExitFieldEiRN3dss12Fix32Vector3E'),      # the game's own "out to the world map at
               'dq5': ('_ZN3cmn14g_extraMapLinkE', '_ZN3cmn12ExtraMapLink17setExtraExitFieldEiRN2ar12Fix32Vector3E'),       # that symbol" (Evac): StageLink::setFieldSymbolIndex,
               'dq6': ('_ZN3cmn14g_BasicMapLinkE', '_ZN3cmn12BasicMapLink20setDirectFieldSymbolEiRN2ar12Fix32Vector3E')}    # the world of that symbol, Global::startField


def level_rows(data):
    """Rows of an args::ExcelBinaryData file (20-byte header, + 4 row count, + 8 row size)."""
    n, size = struct.unpack_from('<2I', data, 4)
    return [data[20 + i * size:20 + (i + 1) * size] for i in range(n) if 20 + (i + 1) * size <= len(data)]


def named_steps(data, field, name_at):
    """{step: (name, map)} from an event list: the rows that carry a name (UTF-8, at `name_at`) and a map (capitals, the next string); one step can have several rows: the first is kept."""
    out = {}
    for r in level_rows(data):
        if len(r) <= name_at or not r[name_at]: continue
        end = r.find(b'\0', name_at)
        try: name = r[name_at:end].decode('utf-8')
        except UnicodeDecodeError: continue
        maps = re.findall(rb'[A-Z][A-Z0-9_]{2,}', r[end:])
        if not name or not maps: continue
        step = struct.unpack_from('<H', r, field)[0]
        out.setdefault(step, (name, maps[0].decode('ascii').lower()))
    return out


def dq4_steps(files):
    """{step: (name, map)} from DQ IV's param_player_event files."""
    out = {}
    for data in files:
        for off in range(4, len(data) - 155, 156):
            r = data[off:off + 156]; step = struct.unpack_from('<H', r, 0)[0]; name = r[0x13:0x93].split(b'\0')[0]
            if not name or step in out: continue
            try: out[step] = (name.decode('utf-8'), r[0x93:0x9c].split(b'\0')[0].decode('ascii').lower() or None)
            except UnicodeDecodeError: pass
    return out


def engine_text(core, key, kind, number):
    """The name the game shows for (kind, number): TextAPI::extractText(buffer, kind | number), what its name macros call; ";" (the patch's line break) and
    "$" (break with a hyphen) removed. None when the game has no such name ("<ERR ...>") or no such function."""
    f = core.export(TEXT[key])
    if not f: return None
    buf = core.scratch('engine_text', 256); core.mem_w(buf, b'\0' * 16)
    core.call(f, buf, kind | number, label='TextAPI::extractText')
    t = core.read_cstr(buf).decode('utf-8', 'replace').replace(';', '').replace('$', '').strip()
    return t if t and not t.startswith('<ERR') else None


def mpt_entries(data):
    """A message file of the game (`MPT0`: size, first number, last, count, table offset, table size; table of (index, length, offset / 4)) -> {index: text}."""
    if data[:4] != b'MPT0': return {}
    _size, _first, _last, count, toff, tsize = struct.unpack_from('<6I', data, 4); base = toff + tsize; out = {}
    for i in range(count):
        idx, ln, off = struct.unpack_from('<3H', data, toff + 6 * i)
        out[idx] = data[base + off * 4:base + off * 4 + ln].decode('utf-8', 'replace')
    return out


def link_chunks(data):
    """{tag: offset} of the chunks of map_link.bin (tag, u32 size, payload; a zero tag ends the file)."""
    out = {}; p = 0
    while p + 8 <= len(data) and data[p:p + 4] != b'\0\0\0\0':
        out[data[p:p + 4]] = p; p += 8 + struct.unpack_from('<I', data, p + 4)[0]
    return out


def map_links(data, names=None):
    """[(map, its link key, key in the destination, destination map)] from map_link.bin, the world map as FIELD. DQ IV (`names` None): records of 6 bytes
    (key, key at the destination, destination name offset in NAME) under each map name; DQ V / VI: records of 12 bytes (key, key at the destination,
    destination number) under each map number, `names` {number: name}. The key at the destination is what the game sets (StageLink::townExitIndex_)
    before Global::startTown when the hero walks through the link: the town puts him on that surface of its collision."""
    c = link_chunks(data); out = []
    if names is None:
        base = c[b'NAME']; body = c[b'DATA'] + 8; h = c[b'HASH']
        n = struct.unpack_from('<H', data, h + 8)[0]
        def name(o):
            s = data[base + o:data.index(b'\0', base + o)].decode('ascii', 'replace') if o >= 8 else None          # (offset 0: no map, a special link)
            return FIELD if s == 'world' else s
        for o in sorted(set(struct.unpack_from('<%dH' % n, data, h + 10))):
            if o == 0xffff: continue
            no, count = struct.unpack_from('<2H', data, body + o)
            out += [(name(no), k, dk, name(dn)) for k, dk, dn in (struct.unpack_from('<3H', data, body + o + 4 + 6 * i) for i in range(count))]
    else:
        body = c[b'DATA'] + 8; ix = c[b'INDX']
        def name(m): return FIELD if m in FIELD_NUMBERS else names.get(m)
        for i in range(struct.unpack_from('<H', data, ix + 8)[0]):
            m, count, off = struct.unpack_from('<3H', data, ix + 10 + 6 * i)
            out += [(name(m), k, dk, name(d)) for k, dk, d in (struct.unpack_from('<3I', data, body + off + 12 * j) for j in range(count))]
    return [r for r in out if r[0] is not None and r[3] is not None]


def floor_names(data):
    """{map number: name} of a DQ V / VI floor list (rows of 16 bytes: u16 number, name at + 4)."""
    out = {}
    for r in level_rows(data):
        n = struct.unpack_from('<H', r, 0)[0]; name = r[4:].split(b'\0')[0].decode('ascii', 'replace')
        if n and name: out[n] = name
    return out


def entrances(links):
    """{map: key} of each map's most obvious entrance: where the hero arrives from the world map, or else from the map nearest to it (fewest links
    away; the smallest key between equals; a map the links don't join to the world map: its smallest key)."""
    adj = {}
    for m, _k, _dk, d in links: adj.setdefault(m, set()).add(d); adj.setdefault(d, set()).add(m)
    depth = {FIELD: 0}; todo = [FIELD]
    for m in todo:
        for d in sorted(adj.get(m, ())):
            if d not in depth: depth[d] = depth[m] + 1; todo.append(d)
    best = {}
    for m, _k, dk, d in links:
        if d != m and d != FIELD: best[d] = min(best.get(d, (len(adj) + 1, dk)), (depth.get(m, len(adj)), dk))
    return {m: k for m, (_depth, k) in best.items()}


def world_exits(links):
    """{map: key on the world map} of the place each map leads out to: its own link to the world map (the smallest key), or else the nearest map's."""
    adj = {}; out = {}
    for m, _k, dk, d in links:
        adj.setdefault(m, set()).add(d); adj.setdefault(d, set()).add(m)
        if d == FIELD and m != FIELD: out[m] = min(out.get(m, dk), dk)
    todo = sorted(out)
    for m in todo:
        for d in sorted(adj.get(m, ())):
            if d != FIELD and d not in out: out[d] = out[m]; todo.append(d)
    return out


def place_name(maps, label):
    """(name, map) of a place on the world map that `maps` lead out to: the first arrival among their story steps' names (`label` {map: first step's name}),
    else the first named one (its place, without the parenthesis), else its first map."""
    named = [(label[m], m) for m in sorted(maps) if m in label]
    for n, m in named:
        if n.startswith(ARRIVAL): return n[len(ARRIVAL):], m
    return (named[0][0].split(' (')[0], named[0][1]) if named else (min(maps), min(maps))


def flag_bits(raw):
    """128 bytes of a GameFlag -> sorted numbers of the switches that are on (bit n: word n >> 5, bit n & 31, little-endian)."""
    words = struct.unpack('<%dI' % (len(raw) // 4), raw)
    return [w * 32 + b for w, v in enumerate(words) for b in range(32) if v >> b & 1]


def with_flag(raw, n, on):
    """The GameFlag bytes with switch n on or off."""
    words = list(struct.unpack('<%dI' % (len(raw) // 4), raw))
    w, b = n >> 5, n & 31
    words[w] = words[w] | (1 << b) if on else words[w] & ~(1 << b)
    return struct.pack('<%dI' % len(words), *words)


class Catalog:
    """Registered on the debug service: the panel's page `/api/catalog` and its commands."""

    def __init__(self, service):
        self.d = service; self.g = service.g; self.key = GM.profile().key
        self.in_step = []                                 # calls to make during the next engine step (just before GameUpdate)
        self._steps = None; self._maps = None; self._bosses = None
        self.g.engine.step_calls = self._take_in_step        # (Engine.step makes them right before GameUpdate and keeps their packets)
        self.pending = None                               # a boss waiting for its map: (row, map, engine frame of the request)
        self.restore = None                               # an ordinary fight of another row than the place's: (the place's row, engine frame of the request)
        service.ticks.append(self.tick)
        self._field_return()
        service.panel_pages['/api/catalog'] = self.info
        service.panel_commands.update({'goto_map': self.goto_map, 'goto_world': self.goto_world, 'story_step': self.story_step, 'flag': self.set_flag, 'battle': self.battle,
                                         'give_item': self.give_item, 'gold': self.give_gold, 'battle_here': self.battle_here, 'minigame': self.minigame, 'book': self.open_book,
                                         'backdrop': self.set_backdrop})

    def _field_return(self):
        """A mini-game started from the world map (the panel's) ends with `Global::startTown` of the map it came from (DQ IV "field", DQ V 999, DQ VI the
        world's own number): the world map is no town (no stage: the engine stopped on FldStage::getCameraLimitL). While `from_field` is set (the mini-game was
        started on the world map), that call becomes `Global::startField` (same Global), the world map again; the game itself only starts these games from a
        town, which it gets back to as before."""
        self.from_field = False
        try:
            core = self.g.engine.core; a, f = core.export(FIELD_RETURN[self.key]), core.export(START_FIELD)
            if not a or not f: return
            def redirect(uc, addr, size, user):
                if self.from_field == 'armed': self.from_field = False; uc.reg_write(core.C.UC_ARM64_REG_PC, f)
            import unicorn
            core.uc.hook_add(unicorn.UC_HOOK_CODE, redirect, None, a, a); self.g.engine._flush_tb('field return')
        except Exception as ex: self.d.note('catalog: %s' % ex)

    def _take_in_step(self):
        """The calls queued for this step (Engine.step_calls), each one's error noted instead of stopping the step."""
        calls, self.in_step = self.in_step, []
        def guarded(fn):
            def run():
                try: fn()
                except Exception as ex: self.d.note('catalog: %s' % ex)
            return run
        return [guarded(fn) for fn in calls]

    # --- reading ---------------------------------------------------------------------------------------------------------------------------------------------
    def maps(self):
        if self._maps is None:
            fs = self.g.engine.core.fs
            self._maps = sorted(n[len('assets/map/'):-4] for n in (fs.names if fs is not None else ()) if n.startswith('assets/map/') and n.endswith('.stg'))
        return self._maps

    def steps(self):
        """[(step, name or None, map or None)] of the game's step table."""
        if self._steps is None:
            c = self.g.engine.core; fs = c.fs; named = {}
            if self.key in STEP_FIELD and fs is not None:
                name = 'assets/leveldata/%sds_event_list.dat' % self.key
                if name in fs.names: named = named_steps(fs.names[name].read(name), *STEP_FIELD[self.key])
                flags = 'assets/leveldata/%sds_event_flag.dat' % self.key
                count = len(level_rows(fs.names[flags].read(flags))) if flags in fs.names else (max(named) + 1 if named else 0)
            else:
                a = c.export(STEP_COUNT_DQ4)
                count = struct.unpack('<i', c.mem(a, 4))[0] if a else 0
                if fs is not None and all(n in fs.names for n in DQ4_EVENTS): named = dq4_steps(fs.names[n].read(n) for n in DQ4_EVENTS)
            french = SF.STEPS.get(self.key, {})
            self._steps = [(n, french.get(n), named.get(n, (None, None))[1]) for n in range(count)]          # (the Japanese name replaced by its French one)
        return self._steps

    def entrances(self):
        """{map: link key} of each map's most obvious entrance ({} if the links can't be read)."""
        if getattr(self, '_entrances', None) is None:
            found = {}; numbers = {}
            try:
                fs = self.g.engine.core.fs; d = fs.names[MAP_LINK].read(MAP_LINK); names = None
                if self.key != 'dq4': n = FLOOR_LIST % self.key; names = floor_names(fs.names[n].read(n)); numbers = {v: k for k, v in names.items()}
                links = map_links(d, names); found = entrances(links); self._exits = world_exits(links)
            except Exception as ex: self.d.note(tr(N_('debug.catalog_links_unreadable')) % ex); self._exits = {}
            self._numbers = numbers; self._entrances = found
        return self._entrances

    def start_town(self, name):
        """The map, the hero at its most obvious entrance, as the game sends him through a link (GO_TO_EXIT; key -1, a map without link: at its origin).
        Global::startTown(name) alone when DQ V / VI's map number is unknown. The looping sound effects of the place left are stopped (quiet)."""
        self.quiet(); key = self.entrances().get(name, -1); core = self.g.engine.core; obj, fn = GO_TO_EXIT[self.key]
        if self.key == 'dq4': where = core.cstr(name.encode())
        else:
            where = self._numbers.get(name)
            if where is None: self.g.engine.start_town(name); return
        core.call(core.export(fn), core.export(obj), where, key & 0xffffffff, label=fn)

    def quiet(self):
        """Stops the looping sound effects in progress (Sound.stop_loops): a jump skips the scripts that would have stopped them (DQ V's ticking clock at the start)."""
        s = getattr(self.g, 'sound', None)
        if s is not None: s.stop_loops()

    def boss_places(self):
        """{encounter row: map} of the fights the scripts start (the script file is the map's: `assets/script/<map>.bin`; the map that starts it most often).
        `_modes` {row: mode}: how the script starts it (1: forceEventBrew, the event fight; 0: forceBrew), the event fight when both are seen."""
        if getattr(self, '_places', None) is None:
            from collections import Counter
            from dq456_pc.randomizer import items as IT
            fs = self.g.engine.core.fs; ops = ENCOUNT_OPS[self.key]; seen = {}; modes = {}
            for n in sorted(fs.names if fs is not None else ()):
                if not (n.startswith('assets/script/') and n.endswith('.bin')): continue
                for _p, op, raw in IT.script_commands(fs.names[n].read(n)):
                    if op in ops and len(raw) >= 12 and struct.unpack_from('<i', raw, 4)[0] in (0, 1):
                        mode, row = struct.unpack_from('<2i', raw, 4)
                        seen.setdefault(row, Counter())[n[len('assets/script/'):-4]] += 1 + 100 * mode          # (the map of an event fight first)
                        modes[row] = max(modes.get(row, 0), mode)
            self._places = {row: c.most_common(1)[0][0] for row, c in seen.items()}; self._modes = modes
        return self._places

    def bosses(self):
        """[(encounter row, [boss monsters], map)] of the fights the scripts start (randomizer's catalog; the map whose script starts it)."""
        if self._bosses is None:
            try:
                from dq456_pc.randomizer import encounters as RN
                core = self.g.engine.core
                if self.key in RN.V2:
                    fs = core.fs; names = [n for n in fs.names if n.startswith('assets/script/')]
                    cat = RN.Catalog.from_files(lambda n: fs.names[n].read(n), names, self.key)
                else: cat = RN.Catalog.from_core(core)
                self._encounter_catalog = cat
                places = self.boss_places(); names = self.monster_names()
                self._bosses = sorted((row, [names.get(m, str(m)) for m in sorted({m for m, *_ in boss})], places.get(row)) for row, (sp, boss, others) in cat.bosses.items())
            except Exception as ex: self._bosses = []; self.d.note('catalog bosses: %s' % ex)
        return self._bosses

    def encounters(self):
        """[(encounter row, [its monsters' names])] of every random encounter group of the game (randomizer's catalog)."""
        if getattr(self, '_encounters', None) is None:
            self.bosses(); cat = getattr(self, '_encounter_catalog', None); names = self.monster_names()
            from dq456_pc.randomizer import encounters as RN
            self._encounters = [(row, [names.get(m, str(m)).replace('$', '') for m in sorted(set(slots)) if m != RN.NONE]) for row, slots in sorted(cat.zones.items())] if cat else []
        return self._encounters

    def _engine_names(self, kind, file):
        """{number: name} of every number the engine names (engine_text), up to one past the last entry of the game's message file for that kind."""
        try:
            fs = self.g.engine.core.fs; last = max(mpt_entries(fs.names[file].read(file)), default=-1)
        except Exception: return {}
        core = self.g.engine.core; out = {}
        for n in range(last + 2):
            t = engine_text(core, self.key, kind, n)
            if t: out[n] = t
        return out

    def monster_names(self):
        """{monster number: name}, the names the game shows (the French patch's), {} if unreadable."""
        if getattr(self, '_monsters', None) is None: self._monsters = self._engine_names(MONSTER_TEXT, MONSTER_NAMES)
        return self._monsters

    def items(self):
        """[(item number, name)], the names the game shows (the French patch's), [] if unreadable; number 0 (no item) left out."""
        if getattr(self, '_items', None) is None: self._items = sorted((n, t) for n, t in self._engine_names(ITEM_TEXT, ITEM_NAMES).items() if n)
        return self._items

    def flags(self):
        c = self.g.engine.core; out = {}
        for name, sym in FLAG_SETS:
            a = c.export(sym)
            out[name] = flag_bits(bytes(c.mem(a, FLAG_BYTES))) if a else None
        return out

    def info(self, q=None):
        if q and q.get('only') == 'flags': return {'map': self.g.engine.state.game_map, 'flags': self.flags()}          # (what changes: asked again while the tab is open)
        return {'game': self.key, 'map': self.g.engine.state.game_map, 'maps': self.maps(), 'worlds': self.worlds(), 'steps': self.steps(), 'flags': self.flags(),
                'bosses': self.bosses(), 'items': self.items(), 'encounters': self.encounters(), 'minigames': [m[0] for m in MINIGAMES.get(self.key, ())],
                'backdrops': self.backdrops(), 'backdrop': getattr(self.g, 'battle_scenery', None),
                'in_battle': self.g.engine.state.part == ENGINE.PART_BATTLE}

    # --- commands ---------------------------------------------------------------------------------------------------------------------------------------------
    def goto_map(self, c):
        name = str(c.get('map', '')).strip().lower()
        if name not in self.maps(): return {'error': tr(N_('debug.catalog_unknown_map')) % name}
        self.in_step.append(lambda: self.start_town(name))
        self.d.note((tr(N_('debug.catalog_map')) if name in self.entrances() else tr(N_('debug.catalog_map_origin'))) % name)
        return {'ok': True}

    def give_item(self, c):
        """`count` copies of item `item`, each given as the game gives an item it finds (the first member with room, otherwise the bag)."""
        try: item, n = int(c.get('item')), int(c.get('count') or 1)
        except (TypeError, ValueError): return {'error': tr(N_('debug.catalog_item_bad')) % c.get('item')}
        if not 0 < item < 0x10000 or not 1 <= n <= ITEM_COUNT_MAX: return {'error': tr(N_('debug.catalog_item_bad')) % item}
        core = self.g.engine.core; obj, fn = GIVE_ITEM[self.key]; f = core.export(fn)
        if not f: return {'error': tr(N_('debug.catalog_missing')) % fn}
        for _ in range(n):
            if obj: core.call(f, core.export(obj), item, label='PartyStatus::giveItem')
            else: core.call(f, item, label='PartyStatus::giveItem')
        self.d.note(tr(N_('debug.catalog_item_given')) % (dict(self.items()).get(item, '#%d' % item), n))
        return {'ok': True}

    def give_gold(self, c):
        """Gold added (`amount` > 0) or taken (< 0) through the scripts' own command."""
        try: amount = int(c.get('amount'))
        except (TypeError, ValueError): return {'error': tr(N_('debug.catalog_gold_bad')) % c.get('amount')}
        core = self.g.engine.core; fn, off = SET_GOLD[self.key]; f = core.export(fn)
        if not f: return {'error': tr(N_('debug.catalog_missing')) % fn}
        block = bytearray(16); struct.pack_into('<2i', block, off, abs(amount), 1 if amount < 0 else 0)
        p = core.scratch('catalog_gold', len(block)); core.mem_w(p, bytes(block))
        core.call(f, p, label='cmd_set_gold')
        self.d.note(tr(N_('debug.catalog_gold')) % amount)
        return {'ok': True}

    def worlds(self):
        """[(world, [(link key, place, map)])]: the game's world maps (the field type of each place's symbol, FIELD_TYPE) and on each the places that maps
        lead out to (world_exits), named by place_name, sorted by name."""
        if getattr(self, '_worlds', None) is None:
            self.entrances(); core = self.g.engine.core; obj, fn = FIELD_TYPE[self.key]; f = core.export(fn); label = {}; places = {}; by = {}
            for _n, name, m in self.steps():
                if m and name and m not in label: label[m] = name
            for m, k in self._exits.items(): places.setdefault(k, []).append(m)
            for k, maps in sorted(places.items()):
                t = core.call(f, core.export(obj), k & SYMBOL_MASK[self.key], label=fn) if f else 0
                by.setdefault(t or 0, []).append((k,) + place_name(maps, label))
            self._worlds = [(t, sorted(v, key=lambda p: (p[1].lower(), p[2]))) for t, v in sorted(by.items())]
        return self._worlds

    def goto_world(self, c):
        """The world map, the hero in front of the place `symbol` (a link key of worlds(): any world of the game), or else of the place he is in (or the nearest
        place that leads out to it), as the game's Evac does (GO_TO_FIELD); Global::startField() alone from the world map or from a place no link joins to it."""
        self.entrances(); core = self.g.engine.core; chosen = c.get('symbol') not in (None, '')
        if chosen:
            try: key = int(c['symbol'])
            except (TypeError, ValueError): return {'error': tr(N_('debug.catalog_world_bad')) % c.get('symbol')}
            place = next((p for _t, ps in self.worlds() for k, p, _m in ps if k == key), None)
            if place is None: return {'error': tr(N_('debug.catalog_world_bad')) % key}
            note = tr(N_('debug.catalog_world_at')) % place
        else:
            here = self.g.engine.state.game_map if self.g.engine.state.part == ENGINE.PART_TOWN else None; key = self._exits.get(here)
            note = tr(N_('debug.catalog_world')) if key is None else tr(N_('debug.catalog_world_from')) % here
        def go():
            self.quiet()
            if key is None: self.g.engine.start_field(); return
            obj, fn = GO_TO_FIELD[self.key]; pos = core.scratch('field_pos', 12); core.mem_w(pos, bytes(12))
            core.call(core.export(fn), core.export(obj), key & SYMBOL_MASK[self.key], pos, label=fn)
        self.in_step.append(go); self.d.note(note)
        return {'ok': True}

    def story_step(self, c):
        """setEventFlag(step) and setEventParty(step); with `go` and a known map, that map is started too."""
        step = int(c.get('step', -1)); steps = self.steps()
        if not 0 <= step < len(steps): return {'error': tr(N_('debug.catalog_step_range')) % (len(steps) - 1)}
        core = self.g.engine.core
        for sym in (SET_EVENT_FLAG, SET_EVENT_PARTY):
            a = core.export(sym)
            if a: core.call(a, step, label=sym)
        m = steps[step][2]
        if c.get('go') and m and m in self.maps(): self.in_step.append(lambda: self.start_town(m))
        self.d.note(tr(N_('debug.catalog_step_map')) % (step, m) if c.get('go') and m else tr(N_('debug.catalog_step')) % step)
        return {'ok': True, 'map': m}

    def set_flag(self, c):
        kind = c.get('set'); n = int(c.get('n', -1)); on = bool(c.get('on', True))
        sym = dict(FLAG_SETS).get(kind)
        if sym is None or not 0 <= n < FLAG_BYTES * 8: return {'error': tr(N_('debug.catalog_switch_bad')) % (kind, n)}
        core = self.g.engine.core; a = core.export(sym)
        core.mem_w(a, with_flag(bytes(core.mem(a, FLAG_BYTES)), n, on)); self.d.note((tr(N_('debug.catalog_switch_on')) if on else tr(N_('debug.catalog_switch_off'))) % (kind, n))
        return {'ok': True}

    def _encount(self):
        core = self.g.engine.core
        return core.call(core.export(ENCOUNT[self.key][0]), label='Encount::getSingleton')

    def why_no_fight_here(self):
        """None if the game itself could start a random encounter here and now (the whistle's conditions), else why not (in French, for the panel)."""
        e = self.g.engine.state; core = self.g.engine.core; k = self.key
        if e.part not in (ENGINE.PART_TOWN, ENGINE.PART_FIELD): return tr(N_('debug.catalog_not_now'))
        obj, fn = STAGE_ENCOUNT[k]
        if not core.call(core.export(fn), core.export(obj), label='isEncount') & 1: return tr(N_('debug.catalog_map_no_random'))
        enc = self._encount()
        if core.mem(enc + ENCOUNT_ON[k], 1)[0] != 1: return tr(N_('debug.catalog_story_no_random'))
        row = struct.unpack('<i', core.mem(enc + ENCOUNT_ZONE, 4))[0]
        if row in (-1, 0) or (k == 'dq4' and e.part == ENGINE.PART_TOWN and not core.call(core.export(IS_ENCOUNT_STAGE), enc, core.cstr((e.game_map or '').encode()), label='Encount::isEncountStage')):
            return tr(N_('debug.catalog_nothing_attacks'))
        return None

    def battle(self, c):
        """A boss: at its own place (boss_fight). Another fight only where the game itself could start a random encounter (why_no_fight_here), as its
        whistle does: Encount::forceEncount() during the next step; the place's own group, or the row asked (Encount + 8 for that fight, then the place's)."""
        raw = str(c.get('row', '')).strip(); row = int(raw) if raw.lstrip('-').isdigit() else None
        if row is not None and row in dict((r, m) for r, _mons, m in self.bosses()): return self.boss_fight(row)
        why = self.why_no_fight_here()
        if why: return {'error': tr(N_('debug.catalog_no_fight')) % why}
        core = self.g.engine.core; enc = self._encount(); here = struct.unpack('<i', core.mem(enc + ENCOUNT_ZONE, 4))[0]
        if row is None or row == here: row = here
        else: self.restore = (here, self.g.engine.frames)
        def start():
            if row != here: core.mem_w(enc + ENCOUNT_ZONE, struct.pack('<i', row))
            core.call(core.export(FORCE_ENCOUNT[self.key]), enc, label='Encount::forceEncount')
        self.in_step.append(start); self.d.note(tr(N_('debug.catalog_fight_row')) % row)
        return {'ok': True, 'row': row}

    def battle_here(self, c):
        """Any encounter row, here, from a town too: Encount::forceBrew(row), the scripts' own ordinary fight (cmd_encount, mode 0), during the next step; the place's backdrop."""
        raw = str(c.get('row', '')).strip()
        if not raw.lstrip('-').isdigit(): return {'error': tr(N_('debug.catalog_row_bad')) % raw}
        row = int(raw); e = self.g.engine.state
        if e.part not in (ENGINE.PART_TOWN, ENGINE.PART_FIELD): return {'error': tr(N_('debug.catalog_not_now'))}
        core = self.g.engine.core
        self.in_step.append(lambda: core.call(core.export(ENCOUNT[self.key][2]), self._encount(), row, label='Encount::forceBrew'))
        self.d.note(tr(N_('debug.catalog_fight_row')) % row)
        return {'ok': True, 'row': row}

    def minigame(self, c):
        """A mini-game started from here (MINIGAMES), during the next step."""
        m = next((m for m in MINIGAMES.get(self.key, ()) if m[0] == c.get('id')), None)
        if m is None: return {'error': tr(N_('debug.catalog_minigame_bad')) % c.get('id')}
        if self.g.engine.state.part not in (ENGINE.PART_TOWN, ENGINE.PART_FIELD): return {'error': tr(N_('debug.catalog_not_now'))}
        eng = self.g.engine; core = eng.core; _id, kind, arg = m
        self.from_field = 'pending' if eng.state.part == ENGINE.PART_FIELD and kind in ('cmd', 'casino', 'global') else False          # (its end goes back to the world map: _field_return)
        def start():
            self._save_party()
            if kind == 'cmd':
                fn, off = MINIGAME_CMD[self.key]; block = bytearray(16); struct.pack_into('<i', block, off, arg)
                p = core.scratch('catalog_minigame', len(block)); core.mem_w(p, bytes(block)); core.call(core.export(fn), p, label=fn)
            elif kind == 'casino':
                core.call(core.export('_ZN6Global11setMinigameEi'), eng._global(), arg, label='Global::setMinigame')
                core.call(core.export('_ZN6Global11startCasinoEv'), eng._global(), label='Global::startCasino')
            elif kind == 'stadium':
                st = core.call(core.export(STADIUM[0]), label=STADIUM[0])
                for fn in STADIUM[1:]: core.call(core.export(fn), st, label=fn)
            elif kind == 'call': core.call(core.export(arg), label=arg)
            elif kind == 'hall':
                self.give_item({'item': arg[1], 'count': 1}); self.start_town(arg[0])
            else: core.call(core.export(arg), eng._global(), label=arg)
        self.in_step.append(start); self.d.note(tr(N_('debug.catalog_minigame')) % m[0])
        return {'ok': True}

    def backdrops(self):
        """The battle sceneries of the game data (its prepared list of maps: the names that start with "btl")."""
        if getattr(self, '_backdrops', None) is None:
            try:
                with open(self.g.data._prep_file(), encoding='utf-8') as f: self._backdrops = sorted(n for n in json.load(f)['maps'] if n.startswith('btl'))
            except Exception: self._backdrops = []
        return self._backdrops

    def set_backdrop(self, c):
        """The scenery drawn behind the next fights (empty: each fight's own). Drawing only: the engine's fight is the same."""
        name = str(c.get('name') or '').strip().lower() or None
        if name is not None and name not in self.backdrops(): return {'error': tr(N_('debug.catalog_unknown_map')) % name}
        self.g.battle_scenery = name
        self.d.note(tr(N_('debug.catalog_backdrop')) % (name or '-')); return {'ok': True}

    def open_book(self, c=None):
        """The monster book, from here (Global::startBook, as the menu does)."""
        if self.g.engine.state.part not in (ENGINE.PART_TOWN, ENGINE.PART_FIELD): return {'error': tr(N_('debug.catalog_not_now'))}
        eng = self.g.engine
        def start(): self._save_party(); eng.core.call(eng.core.export(START_BOOK), eng._global(), label='Global::startBook')
        self.in_step.append(start)
        self.d.note(tr(N_('debug.catalog_book'))); return {'ok': True}

    def _save_party(self):
        """On the world map, the followers' places kept before leaving it (as the game does before a fight): without it they walk back from stale places."""
        eng = self.g.engine; fns = SAVE_PARTY.get(self.key)
        if eng.state.part != ENGINE.PART_FIELD or not fns: return
        core = eng.core; get, save = core.export(fns[0]), core.export(fns[1])
        m = get and save and core.call(get, label=fns[0])
        if m: core.call(save, m, label=fns[1])

    def boss_fight(self, row):
        """The boss of that row at its own place: its map is loaded first if needed, the fight starts once the map is shown and the player free (tick)."""
        place = self.boss_places().get(row)
        if place is None or place not in self.maps(): return {'error': tr(N_('debug.catalog_boss_no_map')) % row}
        e = self.g.engine.state
        if e.part not in (ENGINE.PART_TOWN, ENGINE.PART_FIELD): return {'error': tr(N_('debug.catalog_not_now'))}
        self.pending = (row, place, self.g.engine.frames)
        if e.game_map != place: self.in_step.append(lambda: self.start_town(place))
        self.d.note(tr(N_('debug.catalog_boss')) % (row, place))
        return {'ok': True, 'map': place}

    def tick(self):
        """Each loop frame: the place's encounter row back once an ordinary fight of another row has started; a boss waiting for its map starts when
        the map is shown, faded in and the player free. A mini-game started on the world map: armed once its own part has started, forgotten once the
        world map or a town is back (whatever brought it back: only the mini-game's end may become the world map, see _field_return)."""
        if self.from_field:
            p = self.g.engine.state.part; outside = p not in (ENGINE.PART_FIELD, ENGINE.PART_TOWN)
            if self.from_field == 'pending' and outside: self.from_field = 'armed'
            elif self.from_field == 'armed' and not outside: self.from_field = False
        if self.restore is not None:
            here, asked = self.restore; eng = self.g.engine
            if eng.state.part == ENGINE.PART_BATTLE or eng.frames - asked > ARRIVAL_STEPS:
                self.restore = None; eng.core.mem_w(self._encount() + ENCOUNT_ZONE, struct.pack('<i', here))
        if self.pending is None: return
        row, place, asked = self.pending; g = self.g; eng = g.engine; e = eng.state
        if eng.frames - asked > ARRIVAL_STEPS: self.pending = None; self.d.note(tr(N_('debug.catalog_boss_not_reached')) % (row, place)); return
        if e.game_map != place or e.part != ENGINE.PART_TOWN or getattr(g, 'map_name', None) != place: return
        if eng.flags() & (ENGINE.FADE_FLAG | ENGINE.PLAYER_LOCKED_FLAG) or getattr(e, 'load_wait', None) is not None or self.in_step: return
        self.pending = None; core = eng.core
        event = getattr(self, '_modes', {}).get(row, 1) == 1
        def start():
            if not event: core.call(core.export(ENCOUNT[self.key][2]), self._encount(), row, label='Encount::forceBrew'); return          # (the script's mode 0)
            if self.key == 'dq4':                                    # (cmd_encount, mode 1: the event battle map of the row, then the fight)
                bsm = core.call(core.export(SET_ENCOUNT_MAP[0]), label='BattleScriptManager::getSingleton')
                core.call(core.export(SET_ENCOUNT_MAP[1]), bsm, row, label='BattleScriptManager::setEncountMap')
            core.call(core.export(ENCOUNT[self.key][1]), self._encount(), row, label='Encount::forceEventBrew')
        self.in_step.append(start)
