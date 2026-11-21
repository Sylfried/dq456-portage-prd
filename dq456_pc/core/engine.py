"""The game engine (libdq4core.so, ARM64) running in an emulator + decoding of what it asks of Unity."""
import math, os, random, re, struct, zipfile, unicorn
import numpy as np
from dq456_pc import games as GM
from dq456_pc.core import emu_core as T
from dq456_pc.core import engine_layout as LY
from dq456_pc.core import game_priority as PJ
from dq456_pc.core import lying_pose as PC
from dq456_pc.core import encounters as RE
from dq456_pc.core import endroll as ER
from dq456_pc.core import slime_touch as ST
from dq456_pc.core import slime_curling as SC
from dq456_pc.core import sugoroku as SG
from dq456_pc.core import pad_modes as PM
from dq456_pc.core import world_mesh as WM
from dq456_pc.core.pktlog import PacketLog
from dq456_pc.core.legacy_state import OLD_ATTRIBUTES, renamed_attributes as _renamed_attributes, renamed_dict_keys as _renamed_dict_keys, renamed_interface_keys as _renamed_interface_keys     # noqa: F401 (names of the savestates recorded before the switch to English; OLD_ATTRIBUTES is read through this module by the tests)
from dq456_pc.core.history import Sensor
from dq456_pc.world.fbx import AnimPlayer
from dq456_pc.battle.battle_history import BattleSensor
from dq456_pc.render import capture_effects as EF
from dq456_pc.persistence.write_dir import atomic_write
from dq456_pc.persistence.game_rules import FIRST_PORT_UID
from dq456_pc.randomizer import items as IT
from dq456_pc.randomizer import encounters as RE_
from dq456_pc.menus.message_font import MessageFont, read_font

FIELD_SCALE = (0.25, 4.0)               # bounds of the world map scale (packet 30; the engine only sends 1.0 to 1.25: the bound only serves to discard an outlier value)
CUR_I, CUR_F = 0x258278, 0x258280          # write cursors (integers / floats) in the library
PART_INITIAL = 0x3                         # start menu (GameStartPart: log list, creation, deletion, license)
PART_TOWN = 0xc                         # town, dungeon, tower (3D scenery)
PART_BATTLE = 0xd
SYMBOL_PRIO = -45.0                      # FieldSymbol: height of a place icon on the world map (Unity position y; Field.SetSymbolPrio changes it, the camera looks down from y = 200)
PART_FIELD = 0xe                         # FieldPart: world map
PART_CASINO = 0xf                        # Global::startCasino: slot machine (map ev05) and poker (map ev04), windows of types 44-48
PART_BESTIARY = 0x10                    # Global::startBook: monster book (window of type 28); keeps the town map
PART_CURLING = LY.PART_EXTRA + 0xb       # DQ VI's Global::startCurling: slime curling (a 3D scene of its own, meshes of packets 21 / 22, ports of category 5)
PART_SLIME_RACE = LY.PART_EXTRA + 9      # DQ V's Global::startSlimeRace: the slime race (a town-like scene, map ev01, and its betting windows)
PHONE_SCENE_PARTS = (PART_CURLING, PART_SLIME_RACE)        # mini-games whose scene is composed for the phone's portrait screen: the window shows its whole height, the sides blurred
TITLE_MAP = GM.profile().title_map      # title screen map of the running game (DQ IV: sky, logo, island, dragon; a scene without a hero)
ENGINE_V2 = GM.profile().key != 'dq4'   # Dragon Quest V / VI: the newer engine and its Unity layer (see engine_layout; event cameras, free camera of cmd_fld_load, map background color)
BATTLE_YAW = 0.0 if GM.profile().key == 'dq6' else 180.0      # SceneCamera.Update without a target (battles): Euler(-x, -(y + 180), z) in DQ IV and V; DQ VI's has no + 180 (its engine sends the yaw already turned)


MESH_523 = (523, (323.5, 180.0, -5.33921))       # DQ VI, NativeManager.cmd_mesh_shader bit 30: (model, Euler angles in degrees) of MeshChara523 (Quaternion.Euler of 5.64614, pi, -0.0931868 rad)
RIDER_OBJECT = 38                       # DQ V, NativeManager.Update: the map object that carries the first character while cmd_fld_etc's kept value is 0
SCENE_DISTANCE_ADJUST = 4.0             # SceneCamera.s_distanceAdjust (.cctor): after LookAt, the camera advances by 4 units along its axis
SCENE_LOOK_DOWN = 0.2                   # SceneCamera.s_lookDownAdjust (.cctor): the camera raises its pitch by 0.2 degree


def scene_distance_adjust(game_map, tall):
    """SceneCamera.s_distanceAdjust: 4 units. DQ VI, NativeManager.cmd_camera: on a screen taller than 16:9 (DisplayFix.IsLargeAspectRatio; `tall`: the port's portrait window narrower than 9:16,
    which shows more above and below like those phones) the maps e13din* get 7 units: the camera comes closer and the edges of their scenery stay out of the picture."""
    return 7.0 if tall and FLD_ETC_GAME == 'dq6' and (game_map or '').startswith('e13din') else SCENE_DISTANCE_ADJUST


def scene_camera_position(st):
    """Camera.main's position after SceneCamera.Update, from the engine's last camera packets (state `st`). With a target (towns, monster book): the engine's position, the camera turned toward
    the target, its pitch raised by lookDownAdjust, then advanced by distanceAdjust along its axis. Without target (battles, casino, the free camera of packet 11 or of a map load): the engine's
    position as is. None before the first camera packet."""
    if st.cam_pos is None: return None
    pos = tuple(float(x) for x in st.cam_pos)
    if st.cam_free or st.cam_target is None or st.part == PART_BATTLE or (st.part == PART_CASINO and st.cam_rot is not None): return pos
    d = [float(t) - p for p, t in zip(pos, st.cam_target)]; n = math.sqrt(sum(x * x for x in d))
    if n < 1e-9: return pos
    yaw = math.atan2(d[0], d[2]); pitch = math.asin(max(-1.0, min(1.0, -d[1] / n))) - math.radians(st.cam_down if st.cam_down is not None else SCENE_LOOK_DOWN)
    fw = (math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch))
    dist = scene_distance_adjust(getattr(st, 'game_map', None), getattr(st, 'tall_screen', False))
    return tuple(p + f * dist for p, f in zip(pos, fw))


def billboard2_pull(distance, nominal=39.55):
    """DQ VI, Chara.normalBillboard2: how far (world units) the character's panel is pulled toward the camera, for a camera `distance` units away from the character:
    max(2.3 x distance / s_distance - 1.8, 0.375), s_distance = the scene camera's nominal distance (Global.CameraInitParams[0]: 39.55). Chara.normalBillboard: 0."""
    return max(2.3 * float(distance) / nominal - 1.8, 0.375)


def battle_camera_euler(rot, yaw=None):
    """Euler angles (degrees) of the battle camera for the rotation `rot` the engine sent (packet 10, bit 2): SceneCamera.Update without a target."""
    ax, ay, az = rot
    return -ax, -(ay + (BATTLE_YAW if yaw is None else yaw)), az


def particle_vertices(p):
    """DQ V / VI particles (engine.State.particles) -> (positions (n, 3), texture coordinates (n, 2), colors (n, 4) 0..1) of the triangles' vertices, in Unity's
    world: Transform.position + localScale x vertex (no rotation). The uvs of a particle go below 0 (texture repeated, its rows counted from the top): each
    quad is moved up by whole textures so it reads the same texels."""
    idx = p.get('idx')
    if idx is None or not len(idx) or p.get('v') is None: return None
    v = p['v'][idx].astype(np.float32); uv = p['uv'][idx].astype(np.float32); col = p['col'][idx].astype(np.float32) / 255.0
    xyz = np.asarray(p['pos'], np.float32) + v * np.asarray(p['scale'], np.float32)
    q = len(idx) // 6
    if q:
        quad = uv[:q * 6, 1].reshape(q, 6)
        uv[:q * 6, 1] -= np.repeat(np.floor(quad.min(1) + 1e-6), 6)
    return xyz, uv, col


TITLE_RETURN_STEPS = 300                 # safeguard: engine steps after "Yes" (the game's fade lasts 30) without the title's map: the return to title is considered over
MINIGAME_PARTS = (PART_CASINO, PART_BESTIARY, PART_CURLING)        # engine "full screen" parts that draw their elements with dynamic meshes (packets 21 / 22) using their own camera
MSGDBG = bool(os.environ.get('DQ_MSGDBG'))     # (test) log of the engine message states
FBXLOG = bool(os.environ.get('DQ_FBXLOG'))     # (test) log of packets 32 (title dragon: position, rotation, color, sequence)
GLOBAL_PTR = 0x23d438                       # (DQ IV) Global singleton; the engine reads it through engine_layout (g_Global)
RESOURCE_NAME = 0x258290                    # (DQ IV) name of the map resource requested from Unity (e.g. m/ma/in/mak1f1); read through strings[0] (engine_layout.map_name)
SCREEN_STATE = 0xd8                           # Global+0xd8: copy, on every GameUpdate, of the first integer of the output buffer (state word shared with Unity); its bit 1 = "screen effect in progress" (Global::IsScreenEffectEnd waits for it to drop)
G_IS_AMAZON = 0x24f684                      # engine byte g_isAmazon: at 1, the "Sauv. cloud" cell of the start menu does not exist
# Name of a monster's copy of a character (MsgVar::extract_var: the string chosen by the language index through the byte offsets at 0xc9190, ";" = the place of the name;
# languages 1, 3, 4 put it before the name, 0 and 2 after). The French texts run as language 1, whose prefix is the English "copy of ;": replaced in memory by the French patch's own words.
COPY_PREFIX = 0xc9170
COPY_PREFIX_V2 = {'dq5': 0x14a96e, 'dq6': 0x1555bc}      # the same string in DQ V / VI (MsgVar::extract_var reads it through the same table; the name is then written at the same + 0xb8)
COPY_PREFIX_EN = b'copy of ;\x00\xec\x9d'      # (+ the first 2 bytes of the Korean string that follows: language 2, never read by the French texts)
COPY_PREFIX_FR = b"double de ;\x00"         # the words of the French patch for these copies (monster names of `b1002000.mpt`: "double de ;Ragnar", "double de Kiryl", "double d'Alina")
COPY_ELISION = b"double d'"                    # before a vowel, as the patch writes "double d'Alina" (hook at MsgVar::extract_var + COPY_NAME_DONE)
COPY_NAME_DONE = 0xb8                          # MsgVar::extract_var + 0xb8 (`ldrb w8, [x20, #0x12]`): the name has just been written after the prefix, x19 = its start
LDRB_W8_X20_12 = 0x39404a88
# Names with an article (%a02nnn in the texts: "%0%a02230 de Ragnar augmente de 1 !"). The engine's name tables give each name its articles (@2 "le / la / l'", @3, @5, @6) and the text asks for one
# with the form (00 the name alone, 02 definite singular, 03 definite plural, 04 / 05 indefinite); the local function before TextHook::extractDefaultText copies the article of the form, then the name.
# The status names of `b1006000.mpt` (Force, Agilité, Max. PV...) have no article (@1 only): the French patch's seed messages, which ask for one, said "Force de Ragnar augmente de 1 !"
# Current adventure log ("journal"): MaterielMenu_SAVE + 0x24 (0 to 2; -1 = new adventure) is the log the church's list writes in cyan. The engine puts the log there when the start menu loads it
# (MaterielMenu_LOAD::menuUpdate), -1 for a new adventure (MaterielMenu_LOAD::rootUpdate) and the log just written at the church (MaterielMenu_SAVE::commandUpdate); the quick save's "Resume" leaves the
# value as it is (0 at start: log 1 in cyan) and a savestate brings back the value of its own moment. See Engine._journal_hooks.
JOURNAL_MENU, JOURNAL_OFF = 'gMaterielMenu_SAVE', 0x24
JOURNAL_STORES = (('_ZN17MaterielMenu_LOAD10menuUpdateEv', 0x258, 0x79004928), ('_ZN17MaterielMenu_LOAD10rootUpdateEv', 0x1c4, 0x79004909),
                  ('_ZN17MaterielMenu_SAVE13commandUpdateEv', 0x2f4, 0x79004a68))      # (function, offset, `strh w.., [x.., #0x24]`)
RESUME_LOADED = ('_ZN17MaterielMenu_LOAD10menuUpdateEv', 0x22c, 0x360001a0)            # `tbz w0, #0` just after loadbank(3) (the quick save's "Resume"): w0 = success
QUICK_SAVE_BANK = 3                          # bank of the quick save (data4.dat), written by TownMenu_OPERATION_ROOT through SaveLoad::savebank
# Experience and gold of a battle: PartyStatus + 0x2f8 / + 0x2fc, summed while the monsters fall, shared out by PartyStatus::reflectBattleExp / reflectBattleGold just after the last one (before the
# messages "X reçoit N points d'expérience !", "... pièces d'or !", which show these values). The hooks at their entry multiply them ("Experience gained", "Gold gained": Engine.exp_rate / gold_rate).
REWARDS = (('_ZN6status11PartyStatus16reflectBattleExpEv', 0x2f8, 0xa9bc7bfd, 'exp_rate'), ('_ZN6status11PartyStatus17reflectBattleGoldEv', 0x2fc, 0xd00003e9, 'gold_rate'))      # (function, field, first instruction, rate)
# Damage of a hit, spell or breath: BaseActionStatus::actionTypeDamage(target) reads the value the action computed for it (BaseActionStatus::playerEffectValue_ when the target is a member of the
# party, its + 8 = 0; monsterEffectValue_ for a monster, + 8 = 1), takes it from the target's HP and keeps it for the message ("Inflige N points de dégâts..."). The hook at its entry multiplies the value
# ("Damage taken by the party / by enemies": Engine.party_damage_rate / enemy_damage_rate), the one at its only `ret` puts the action's value back (each target is multiplied once, from the game's value).
DAMAGE_FN, DAMAGE_RET = '_ZN6status16BaseActionStatus16actionTypeDamageEPNS_15CharacterStatusE', 0x1d4
DAMAGE_WORDS = (0xa9bd7bfd, 0xd65f03c0)          # first instruction (stp x29, x30, [sp, #-0x30]!), ret
DAMAGE_VALUES = {0: ('_ZN6status16BaseActionStatus18playerEffectValue_E', 'party_damage_rate'), 1: ('_ZN6status16BaseActionStatus19monsterEffectValue_E', 'enemy_damage_rate')}
# Random encounters: Encount::exec, at each step on a map with encounters, runs its CountDown (Encount + 0x134): CountDown::exec takes 2 x its + 0x10 (the step's value for the terrain,
# CountDown::setTileIdRate) from its + 0; below 0, a battle. Its only caller is Encount::exec. The hook at its entry multiplies the step's value ("Random encounters" No: 0, no battle; otherwise
# "Encounter rate": Engine.encounter_rate), the one at its `ret` puts the game's value back.
COUNTDOWN_FN, COUNTDOWN_RET = '_ZN9CountDown4execEv', 0x10
def _adr_target(core, at):
    """Address that the `adr` instruction at `at` computes, or None if it is not an `adr`."""
    w, = struct.unpack('<I', core.mem(at, 4))
    if (w & 0x9f000000) != 0x10000000: return None
    imm = (((w >> 5) & 0x7ffff) << 2) | ((w >> 29) & 3)
    return at + (imm - (1 << 21) if imm & (1 << 20) else imm)


def _adrp_add_target(core, at):
    """Address that `adrp xN, page` at `at` and `add xN, xN, #imm` right after compute, or None."""
    i1, i2 = struct.unpack('<2I', core.mem(at, 8))
    if i1 & 0x9f000000 != 0x90000000 or i2 & 0xffc00000 != 0x91000000 or (i1 & 0x1f) != (i2 & 0x1f) or ((i2 >> 5) & 0x1f) != (i1 & 0x1f): return None
    page = (((i1 >> 5) & 0x7ffff) << 2 | (i1 >> 29) & 3) << 12
    if page & (1 << 32): page -= 1 << 33
    return (at & ~0xfff) + page + ((i2 >> 10) & 0xfff)


def _bl_target(core, at):
    """Function that the `bl` at `at` calls (through a PLT stub `adrp x16 ; ldr x17, [x16, #n] ; add ; br x17`: the address in its table entry); None if it is not a `bl`."""
    u32 = lambda a: struct.unpack('<I', core.mem(a, 4))[0]
    w = u32(at)
    if w & 0xfc000000 != 0x94000000: return None
    t = at + ((((w & 0x3ffffff) ^ 0x2000000) - 0x2000000) << 2)
    i1, i2, i4 = u32(t), u32(t + 4), u32(t + 12)
    if i1 & 0x9f00001f != 0x90000010 or i2 & 0xffc003ff != 0xf9400211 or i4 != 0xd61f0220: return t
    page = (((i1 >> 5) & 0x7ffff) << 2 | (i1 >> 29) & 3) << 12
    if page & (1 << 32): page -= 1 << 33
    return struct.unpack('<Q', core.mem((t & ~0xfff) + page + ((i2 >> 10) & 0xfff) * 8, 8))[0]


FURNITURE_INIT = ('_ZN20TownFurnitureManager10initializeEv', 0xa9bb7bfd)      # randomizer, items: its entry (the map's furniture table is loaded, TownStageManager + 0 -> block 0, not read yet)
STAGE_SINGLETON = '_ZN16TownStageManager11m_singletonE'
PARTY_GOLD = 0x2e0                                                            # status::g_Party + 0x2e0: the gold (cmd_set_gold reads it, BasePartyStatus::setGold writes it)
COMMAND_FN = ('_Z15CommandFunctionPv', 0xa9be7bfd, 0x64)                       # its entry: x0 = the script command about to run (u16 number at + 0, arguments from + 4); + 0x64 its only `ret`
RET = 0xd65f03c0
BREW = ('_ZN7Encount4brewEv', ((0, 0xb9400808), (0x1c0, 0xaa1303e0)), 0x1c0)                       # Encount::brew: entry (`ldr w8, [x0, #8]`: the zone), the `mov x0, x19` before execThinning
RANDOM_BREWS = (0x1e9b3c, 0x1f4b4c)                                         # brew's return addresses for a random encounter (FieldSystem::execEncount, Encount::execDungeon); the story's battles come from forceBrew / forceEventBrew
FORCED_BREWS = (0x1f52d4, 0x1f5318)                                         # brew's return addresses in Encount::forceBrew / forceEventBrew (the scripts' fights: bosses, story fights; forceBrew: the mimics too)
RNG_STATE = 0x24b838                                                        # dss::rand's state (u32, from the library's start)
ENCOUNT_ZONE, ENCOUNT_GROUPS = 8, 0x34                                      # Encount + 8: the place's zone; + 0x34: the four groups' monsters, + 0x44 their counts
DROP_CHECK = ('_ZN6status12MonsterParty13checkDropItemEi', ((0x114, 0xb944faa8), (0x118, 0xb97eae69), (0x120, 0x35000069), (0x128, 0xb93eae60)), 0x120, 0x128)     # randomizer, drops:
                                                                            # MonsterParty::checkDropItem, `ldr w8, [x21, #0x4f8]` (the monster), `ldr w9, [x19, #0x3eac]` (getDropItem's result), the `cbnz w9`
                                                                            # that skips the chapter 3 finds, the `str w0, [x19, #0x3eac]` of getSpecialDropItem's result
DROP_ITEM, DROP_MONSTER = 0x3eac, 0x3eb0                                    # MonsterParty: the item the fight drops, its monster
BESTIARY_REWARD = ('_ZN29MaterielMenu_PICTUREBOOK_ROOT24checkCompletePictureBookEv', 0x54, ((0x48, 0x52927c01), (0x50, 0x72a00081)))     # `bl addCasinoCoin` with w1 = 300000 (mov / movk just before)
ITEM_DATA, ITEM_STRIDE, ITEM_FLAGS, ITEM_SPECIAL = 0x50, 32, 0x1d, 0x10    # status::excelParam + 0x50 -> the item table (ExcelParam::getItemData), 32 bytes per item; + 0x1d bit 4: an item found with a jingle
SPECIAL_JINGLE = ('_ZN12SoundManager11playRestartEi', 48)                   # (TownFurnitureItem::setSecondMessage waits and soundStart plays BGM 48 with the music resumed; the scripts' gifts of such items: cmd_message_with_sound(.., 48, 180))
SCRIPT_SINGLETON = ('_ZN12ScriptSystem12getSingletonEv', 0x1c)                # `adr x0, <the ScriptSystem>` there: + 8 the loaded script file, + 0x18 its size
# DQ V / VI, randomizer (items.py, GAMES): TownFurnitureManager::initialize's entry; ScriptSystem::getSingleton + 0x1c `adrp x0` / + 0x20 `add x0`: the ScriptSystem (+ 8 the loaded script file,
# its size: the heap block ReadFileAlloc took for it); MonsterParty::checkDropItem: (offset, `str w0, [x.., #..]` that keeps getDropItem's result in w0, register of the monster); ar::rand's state.
FURNITURE_INIT_V2 = 0xa9bc7bfd
SCRIPT_SINGLETON_V2 = 0x1c
DROP_CHECK_V2 = {'dq5': (0x1ec, 0xb93fce60, 21), 'dq6': (0x1c0, 0xb907b2c0, 20)}
RNG_STATE_V2 = {'dq5': 0x30f8c4, 'dq6': 0x32c9e8}
# DQ V / VI, randomizer (encounters.py): btl::Encount::brew's entry (zone at + 8), the place where the four groups are complete (offset, instruction; monsters at Encount + mon, counts at + n:
# DQ V before EncountScreenOver::setup fits them on the screen, DQ VI after it stores them), and where it is called from: a random encounter (execField, execDungeon), a script's fight (forceBrew;
# its rows: the boss fights and the story's other fights).
BREW_V2 = {'dq5': dict(entry=0xa9bd7bfd, groups=(0x58, 0xaa1503e2), mon=0x1c, n=0x2c, random=(('_ZN3btl7Encount9execFieldEv', 0x34), ('_ZN3btl7Encount11execDungeonEv', 0x34)),
                       forced=(('_ZN3btl7Encount9forceBrewEi', 0x2c),)),
           'dq6': dict(entry=0xa9bd7bfd, groups=(0x48, 0x7100027f), mon=0x20, n=0x30, random=(('_ZN3btl7Encount9execFieldEv', 0x34), ('_ZN3btl7Encount11execDungeonEv', 0x34)),
                       forced=(('_ZN3btl7Encount9forceBrewEi', 0x34),))}
COUNTDOWN_WORDS = (0xb9401008, 0xd65f03c0)        # ldr w8, [x0, #0x10]; ret
# The game's unique ID (status::g_Game + 8, kept whole, 32 bits, in the adventure log: profile::Profile::collectDATA_PARTY / deliverDATA_PARTY). A new game draws it among 256 values only
# (GameStatus::resetUniqueID = dss::rand(0x100), called by MaterielMenu_LOAD::makeDiary just after the name entry, and by the debug start): the port writes a 32-bit one instead, never below 256,
# so that each game is told apart from the others (rules of the game, persistence/game_rules.py) and a game whose ID is below 256 was started without the port's rules.
UNIQUE_ID_STORE = ('_ZN6status10GameStatus13resetUniqueIDEv', 0x18, 0xb9000a60)          # `str w0, [x19, #8]`: w0 = the value drawn by the game
CASINO_WINS = (('_ZN11Casino_Slot16getResultAllCoinEv', 0x64, 0xa9424ff4, None),               # slot machine: the payout of the lines bet, summed in x0 and Casino_Slot + 8 (ldp x20, x19, [sp, #0x20])
               ('_ZN27MaterielMenuPokerChangecard13pokerDealCardEv', 0xf4, 0xb9002668, 8),      # poker: bet x the hand's multiple, before it is kept (str w8, [x19, #0x24])
               ('_ZN27MaterielMenuPokerChangecard13messageUpdataEv', 0x12c, 0xb9002661, 1),     # (str w1, [x19, #0x24]: the cards changed)
               ('_ZN27MaterielMenuPokerSelectcard9menuSetupEv', 0x40, 0xb9002a69, 9),           # double-up games: their stake, bet x multiple again (str w9 / w8, [x19, #0x28])
               ('_ZN25MaterielMenuPokerHiandlow9menuSetupEv', 0x7c, 0xb9002a68, 8),
               ('_ZN26MaterielMenu_FIGHT_STADIUM9menuSetupEv', 0x1b0, 0xb90026c8, 8))         # monster arena: bet x odds / 10, 0 when lost (str w8, [x22, #0x24], Global + 0x24)
# DQ V / VI: the same wins at their own places ((function, offset, instruction, register); ('slot', o): x0 = the slot machine's payout, also kept in x19 + o)
CASINO_WINS_V2 = {'dq5': (('_ZN6casino11Casino_Slot16getResultAllCoinEv', 0xbc, 0xa9454ff4, ('slot', 0x10)),              # ldp x20, x19 after the payout (lines x bet) is kept
                          ('_ZN4menu27MaterielMenuPokerChangecard13pokerDealCardEv', 0x108, 0xb9002668, 8),               # str w8, [x19, #0x24]: bet x the hand's multiple
                          ('_ZN4menu27MaterielMenuPokerChangecard13messageUpdataEv', 0x120, 0xb9402a69, 8),               # ldr w9, [x19, #0x28] right after the same product (kept at + 0x24, then the high-low game's stake)
                          ('_ZN4menu26MaterielMenu_FIGHT_STADIUM9menuSetupEv', 0x1a8, 0xb90006c8, 8)),                    # monster arena: str w8, [x22, #4] (bet x odds / 10, 0 when lost)
                  'dq6': (('_ZN6casino15CasinoSlotFrame16getResultAllCoinEv', 0x7c, 0xa9414ff4, ('slot', 0x14)),
                          ('_ZN4menu27MaterielMenuPokerChangecard13pokerDealCardEv', 0x100, 0xb9002668, 8),
                          ('_ZN4menu27MaterielMenuPokerChangecard13messageUpdataEv', 0x118, 0xb9402a69, 8),
                          ('_ZN4menu27MaterielMenuPokerSelectcard9menuSetupEv', 0x48, 0xb9002a68, 8))}                   # double-up (choose a card): its stake, bet x multiple again (str w8, [x19, #0x28])
NAME_FORM_DONE = -0x140                      # TextHook::extractDefaultText - 0x140: `strb wzr, [x9]` (the name is in the buffer x22, x9 = its end; w21 = message number, x23 = the name table)
STRB_WZR_X9 = 0x3900013f
MSGVAR_FORM = 0x2587e4                       # form of the variable being written (MsgVar::extract_var stores it)
FORM_DEFINITE = 2
TEXTHOOK_ARTICLE = 0x20                      # name table + 0x20: pointer to the @2 article of the current name (empty or null: none)
STATUS_NAMES = 1006                          # message number // 1000 of the status names (b1006000.mpt)
STATUS_DEFINITE_FR = {'Force': 'la Force', 'Agilité': "l'Agilité", 'Vitalité': 'la Vitalité', 'Sagesse': 'la Sagesse', 'Chance': 'la Chance', 'Attaque': "l'Attaque",
                      'Défense': 'la Défense', 'Max. PV': 'le maximum de PV', 'Max. PM': 'le maximum de PM'}       # (the patch's own words: "graine de Force", "l'Agilité", "Son maximum de PV augmente")
NAMES_MSG = 'assets/msg/en/b1002000.mpt'   # monster names with their articles ("@2" = definite article): "le", "la", "l'" in the French texts, "the" in the English ones
RETURN_TITLE_MESSAGES = {'dq4': (0xcba2f, 0xcba33), 'dq5': (0xcba1e, 0xcba20), 'dq6': (0xcba03, 0xcba05)}      # b0834000.mpt of each game: the "Retour a l'ecran titre" question of the original Settings (TownMenu_OPERATION_SETTING::menuUpdate: `mov w1, #0xba2f` / `#0xba1e` / `#0xba03`, `movk w1, #0xc, lsl #16`, then TownMenu_MESSAGE::addMessage(int)) and the file's last message, "Voulez-vous vraiment quitter l'application ?" (834095 / 834099, 834078 / 834080, 834051 / 834053). A number of another game shows "ERROR <number>" in the box
MSG_RETURN_TITLE, MSG_QUIT_APP = RETURN_TITLE_MESSAGES[GM.profile().key]      # (the Pause menu's "Return to title screen" and the Esc menu's "Quit the game" open the same Yes / No box)
MESSAGE_RESULT = 0x10                        # TownMenu_MESSAGE + 0x10: box answer (1 = Yes, 2 = No; reset to 0 on opening); read by TownMenu_OPERATION_SETTING::menuUpdate (state 5)
BATTLE_EFFECT = 4                            # packet 34 (cmd_screen_effect): value & 0xf = 4, Capture.BattleEffect (the screen chunks fall)
FLD_ETC_GAME = GM.profile().key           # packet LY.FLD_ETC: DQ V and DQ VI give it different meanings (see State.apply)
EFFECT_KIND, EFFECT_FLAG = (0xff, 0x8000) if ENGINE_V2 else (0xf, 0x80)      # packet 34: the effect number, and the bit of bReverse / bMorning / release (DQ IV: value & 0xf, bit 7; DQ V / VI: value & 0xff, bit 15)
SCREEN_SEPIA = 8                             # DQ V / VI packet 34, effect 8 (Capture.SCREEN_EFFECT_SEPIA): the Sepia image effect on the scene camera; with bit 15: Sepia.Release
SCREEN_MASK = 11                             # DQ VI packet 34, effect 11 (Capture.SetupMaskEffect): the screen masked around a centered disc; with bit 15: ReleaseMaskEffect
SCREEN_SPOTLIGHT = 7                         # DQ V / VI packet 34, effect 7: Spotlight (the screen darkened around a lit circle, see Engine._spotlight)
BATTLE_EFFECT_FRAMES = 57                    # Capture.BattleEffect: the counter goes from 0 to divY (56) inclusive, one game frame each
BLACK_SCREEN_MAX = 1800                       # frames (30 Hz) of black screen after the start of a journey (Capture.Tabi) before giving up waiting for the arrival (safeguard: the game always launches it, well before)
LOADING_COVERED = 0.9                    # opacity (0 to 1) of a fade plane from which the screen is "covered": a map load that arrives then is shown only once the scene is in place (State.loading)
LOADING_MIN, LOADING_PLACEMENT, LOADING_MAX = 1, 2, 6      # engine frames after packet 0: at least 1 (the engine places the camera and characters on the next frame, on 529 maps), 2 at most to wait for the visible characters' positions, 6: safeguard (never a longer wait)
LOAD_WAIT_MAX = 240                    # frames (30 Hz) of black screen before giving up waiting for the battle scene to load
BATTLE_ENTRY_WINDOW = 12                  # frames after a battle scene loads (packet 0, bit 8) during which a fade launched by the engine is the battle arrival fade (State.battle_transition)
BATTLE_RETURN_WINDOW = 12                  # frames after leaving the battle part during which a fade launched by the engine is the fade back to the map
TRANSITION_GUARD = 8                        # frames during which the battle transition stays "in progress" after one of its fades ends (the engine changes scene 1 to 3 frames later, then launches the next fade)
LIB_APK = GM.profile().library          # the running game's engine (games.py)
EXIT_COUNTDOWN = 0x258254                  # library byte read by Global::NativeExec: countdown (in frames) of the exit sound effect; startTown, startField, ChangeGamePart, startFirstTown and startDebugTown arm it to 15 when the game leaves a town or the world map,
                                            # NativeExec decrements it every frame and, at 0, calls TownSystem::playTownExitSE / FieldSystem::playFieldExitSE
                                            # (sound effect 305: only if the old scene is still alive, its byte 0 = 1); see Engine._exit_countdown

# keys (mask sent to GameUpdate): up, down, left, right checked; A/B to be confirmed
UP, DOWN, LEFT, RIGHT, A, B = 0x1, 0x2, 0x4, 0x8, 0x10, 0x20
MENU_DIRS = ({UP: 0x1, DOWN: 0x2, LEFT: 0x4, RIGHT: 0x8} if ENGINE_V2 else {UP: 0x2, DOWN: 0x1, LEFT: 0x8, RIGHT: 0x4})          # a menu window's own direction keys (MenuPort.Send gamepad, MenuItem + 0x4a, read by MenuItem::check50_NEW_PAD_UP to check80_NEW_PAD_RIGHT):
                                                                                                                              # DQ IV up 2, down 1, left 8, right 4; DQ V / VI up 1, down 2, left 4, right 8 (sent the DQ IV way, their engine cursors went the other way)
SCROLL_MANUAL = 4                       # OptionStatus.battleSpeed 4: battle messages turned by hand (BattleAutoFeed: -1)
CAM_G, CAM_D = 0x10, 0x20                  # on a town field, bits 4 and 5 of the held keys are not A / B but the game's camera buttons (window::NormalControl::checkCamera reads (Global + 0xe0) >> 4 & 3): 1 turns left,
                                           # 2 right, 3 (both) returns the camera to north; the engine itself applies limits, locks and scenes (TownPlayerManager::setCameraRot)
                                           # and rotates the walking directions and the sprite rows
CAM_STEP = 360.0 * 0x200 / 65536.0          # degrees of a game camera rotation (TownCamera::rotateL / rotateR: 0x200 out of 65536 per turn), one per engine step while the button is held: 84.4 degrees per second
ROT_CAM_RIGHT, ROT_CAM_LEFT, ROT_CAM_NORTH, ROT_CAM_FINISHED = 1, 2, 3, 4      # CAMERA_ROT_TYPE: TownPlayerManager + 0x698 (requested rotation, waiting for the next setCameraRot); 3 / 4 = return to north in progress / finished
OBJ_MESSAGE = 0x252330                     # SDrawCTRL object (message text canvas) in the library
CANVAS_L, CANVAS_H = 512, 512            # texture of Unity's message canvas (MenuMessage: Texture2D(512, 512)); the engine writes to it in a loop
SHORTS_STACK = 4096                         # actual size allocated for the message queue (Unity announces 1089)
MSG_LINES = 0x2523a0                    # (u16) number of visible lines of the message window - 1 (engine setting)
MSG_ACTIVE, MSG_STATE, MSG_CURRENT, MSG_END, MSG_SPEED = 0x25238e, 0x25238c, 0x2523cc, 0x2523ce, 0x2523b0      # ui_MsgLoop: message open (byte), state (1 reading a code, 2 writing the letters, 3 / 5 waiting for a key), index of the current letter and of the end of line, letters per frame / 4 of the window type
MSG_PAUSE = 6                            # ui_MsgLoop state between two pages while the engine waits before the next one (measured: 90 frames after a level-up jingle, before "X passe au niveau N !")
UI_MSGLOOP_WRITE = 0x128                # ui_MsgLoop + 0x128: `sxth w20, w8` after `lsl w8, w8,` (state 2: number of letters written in this frame)
SXTH_W20_W8 = 0x13003d14
BATTLE_MANUAL = 4                          # OptionStatus.battleSpeed: 0 to 3 = wait of 12 / 22 / 32 / 42 frames between two battle messages (BattleAutoFeed::setMessageSpeed), 4 = no automatic advance
FADE_FLAG, PLAYER_LOCKED_FLAG = 0x1, 0x4        # Global.Flag (shared state word, Engine.flags): a fade is in progress; the game has locked the player (event, scene: Global.PLAYER_LOCK)
BGM_PLAY_FLAG = 0x200 if GM.profile().key == 'dq6' else 0          # DQ VI: the engine raises it when it starts a music track (SoundManager::setBgmPlayFlag) and Unity drops it once the track no longer exists (Sound.SystemUpdate: SdSoundSystem_SoundCtrl_IsExist);
                                                                   # cmn::CommonSoundManager::execSound waits for it after a jingle: the church's log, the inn, the miracles, the job change, the medal king... never went on. DQ IV and V time their jingles themselves
SYSTEM_BUSY_FLAG = 0x80 if ENGINE_V2 else 0          # DQ V / VI: the town / field system is in another state than the player's (twn:: / fld::CommandObjectDefine*System::set*: message, menu, shop, map link, part change...; setPlayer / setNormal drop it). DQ IV has no such bit
HUD_HIDDEN_FLAGS = 0x30047 | SYSTEM_BUSY_FLAG        # TouchPad.IsEnable(bit): (Global.Flag & (bit | mask)) == bit, mask 0x47 in DQ IV (fade, screen effect, player lock, locked interface) and 0xc4 in DQ V / VI (player lock, locked interface, busy system); the port keeps the fade and screen effect bits for the three games
NOT_FREE_FLAGS = PLAYER_LOCKED_FLAG | SYSTEM_BUSY_FLAG          # the player does not have control of the character (Game.player_control)
ENCOUNT_BREW_END = 0x1c0                   # Encount::brew() + 0x1c0: `mov x0, x19`, start of the common tail (the random group is complete in Encount + 0x34 (4 monster numbers) and + 0x44 (4 counts))
ENCOUNT_BREW_RET = 0x200                   # ... and the `ret` that closes the function
MOV_X0_X19, RET = 0xaa1303e0, 0xd65f03c0
ENCOUNT_MONSTERS, ENCOUNT_COUNTS = 0x34, 0x44
HAVESTATUS_LEVELUP_END = 0x3f8             # status::HaveStatus::levelup(bool) + 0x3f8: `tbz w20,, ...` (w20 = the argument; true: the character regains all their HP and MP, their max HP/max MP having just been recalculated)
MENU_MANAGER, OPENED_MENUS, MENU_GIFT_EXP, MENU_GIFT_EXP_STATE, MENU_GIFT_EXP_WAIT = 0x252078, 8, 0x24c730, 0x30, 2      # MenuManager: pointer to the manager (at +8, up to 8 open menus, one pointer each); gMaterielMenu_EXTRA_PRESENT_EXP (experience gift): state at +0x30, 2 = last wait (see awaits_touch)
TBZ_W20_0 = 0x36000094
# DQ V / VI: the same rules at their own places (GM.profile().key -> spec). Their battle totals are read (victory message) before being shared out: the first reader multiplies them.
REWARD_READERS_V2 = ('_ZN6status15BasePartyStatus12getBattleExpEv', '_ZN6status15BasePartyStatus13getBattleGoldEv')      # `ldr w0, [x0, #8]` / `#0xc`; x0 = the party
REWARD_WORDS_V2 = (0xb9400800, 0xb9400c00)
BATTLE_EXP_V2, BATTLE_GOLD_V2 = 0x8, 0xc   # BasePartyStatus + 8: the battle's experience, + 0xc: its gold (+ 0x14: the party's gold)
DAMAGE_V2 = {'dq5': ('_ZN6status16BaseActionStatus16actionTypeDamageEPNS_15CharacterStatusES2_', 0x308, 8),
             'dq6': ('_ZN6status16BaseActionStatus16actionTypeDamageEPNS_15CharacterStatusES2_', 0x2b4, 0x30)}      # (function, its only `ret`, the target's kind: x1 + offset, 0 party member, 1 monster)
COUNTDOWN_FN_V2 = '_ZN3btl16EncountCountDown4execEv'          # btl::EncountCountDown::exec: the same code as DQ IV's CountDown::exec (step value at + 0x10)
LEVELUP_V2 = {'dq5': (0x3f0, 0x36000089, 9), 'dq6': (0x39c, 0x360000a8, 8)}      # HaveStatus::levelup(bool) + offset: `tbz w<reg>, #0` on the argument reloaded from the stack, before HP <- max HP, MP <- max MP
HEAL_V2 = {'dq5': (0x4e0, 0x10, '_ZN6status14HaveStatusInfo5setHpEt', '_ZN6status14HaveStatusInfo5setMpEt'),
           'dq6': (0x4c8, 0x70, '_ZN6status14HaveStatusInfo5setHpEs', '_ZN6status14HaveStatusInfo5setMpEs')}      # (PartyStatusData in g_Party, HaveStatusInfo in a PlayerStatus, setHp, setMp)
UI_MSGLOOP_V2 = {'dq5': 0x2c4, 'dq6': 0x2b8}         # ui_MsgLoop + offset: `sxth w20, w10` (the same letter count as DQ IV's, in w10)
SXTH_W20_W10 = 0x13003d54
PARAMS_V2 = {'dq5': (0x2bfdbc, 0x71001a9f, 20, 6), 'dq6': (0x26feb0, 0x7100191f, 8, 6)}      # Miscellaneous menu (TownMenu_OPERATION_ROOT / TownMenuOperationRoot::menuUpdate): `cmp w<reg>, #6` (button 6 = "Settings"), library offset
UNIQUE_ID_EMPTY_V2 = ('_ZN6status10GameStatus13resetUniqueIDEv', 0xd65f03c0)          # DQ VI: resetUniqueID is a lone `ret` (no ID drawn): the port gives one (x0 = g_Game, ID at + 8, kept in the adventure log)
PARTY_MEMBERS, PARTY_SLOTS = 0x304, 13     # status::g_Party + 0x304: the numbers of the party's members (13 u32, 0 = empty slot; the wagon included)
PLAYER_STRIDE, PLAYER_HAVESTATUS = 0x530, 0x58      # PartyData::originalPlayer_: one PlayerStatus per character number (0x530 bytes), its HaveStatus at + 0x58 (HaveStatusInfo + 0x10, then + 0x48)
SLOT_MENU = {'dq4': ('gMaterielMenu_SLOT', 0x24), 'dq5': ('_ZN4menu18gMaterielMenu_SLOTE', 0x1c), 'dq6': ('_ZN4menu17gMaterielMenuSlotE', 0xf0)}          # the slot machine menu object and its state (int)
SLOT_PAYING = 3                            # its state while it pays a win out token by token (menuUpdate / statusUpdate -> showEffect)
GLOBAL_STADIUM = 0x1c                      # Global + 0x1c (u32): 1 during a monster arena fight of the casino (the party only bets on it), still 1 when that battle part ends
RET_A64 = 0xd65f03c0
PLAYTIME_MOV_W1, PLAYTIME_BL, PLAYTIME_STUB, PLAYTIME_GOT, MOV_W1_2 = 0x14, 0x20, 0x227d90, 0x240968, 0x52800041
PLAYTIME_STUB_WORDS = (0xb00000d0, 0xf944b611, 0x9125a210, 0xd61f0220)      # the 4 instructions of the PLT stub 0x227d90
PLAYTIME_V2 = {'dq5': 0x15c, 'dq6': 0x198}     # DQ V / VI: GameUpdate + offset: `bl GameStatus::addPlayTime` (through its PLT stub), `mov w1, #2` two instructions before
# Global::NativeExec + 0x14: `mov w1,`; + 0x20: `bl GameStatus::addPlayTime` (via the PLT stub 0x227d90: x0 = status::g_Game, w1 = 2, void):
# once per engine step, +2 units (1/60 s) of play time
MAGIC_CHARSELECT_BL, MAGIC_WITHOUT_TARGET_BL, MAGIC_EXECUSE_STUB = 0x428, 0x188, 0x2269c0      # TownMenu_MAGIC_CHARSELECT::menuUpdate + 0x428 and TownMenu_MAGIC_ROOT::useMagicNoTarget + 0x188: `bl UseAction::execUse` (via the PLT stub 0x2269c0) of the field Magic menu
MAGIC_MP, MAGIC_RESULT, MAGIC_ACTION = 0x7e, 0x210, 0x1d8        # PlayerStatus + 0x7e: current MP; UseActionParam + 0x210: list of result messages (ends with 0), + 0x1d8: action number
MAGIC_HEAL_SOUND, SUB_W8_W8_C4 = 0x100, 0x51031108          # TownMenu_MAGIC_CHARSELECT::menuUpdate + 0x100: `sub w8, w8, #0xc4` (w8 = action number; if the result is <= 2, it is a healing spell and the engine plays sound effect 501, `playSe(0x1f5)`, whatever its effect)
MAGIC_TARGET_COUNT, MAGIC_LOOP_END = 0x257f84, 0x2e0       # gTownMenuItemUseManager + 0xaa: number of targets whose result the engine displays (and for which it plays the healing sound effect, once each); useMagicNoTarget + 0x2e0: exit of this loop
MSG_NOTHING_HAPPENS = 0xc3d6b             # identifier of the engine message "Mais rien ne se passe." (failure field of the action table: Resurrection, Antipoison, Sifflet...; the message of "Heal all" when nobody needs it,
                                          # TownMenu_OPERATION_ROOT::menuUpdate + 0xbf8): displayed by the "spell that costs MP with no effect" rule; also an engine version marker (action 201)
MESSAGE_BUFFER = 0x2582c0                      # g_current_fld+0x50 : pointer to GameUpdate's input buffer (c.inp), the list of Unity -> engine messages: 0 = camera, 1 = end of animation, terminated by a negative integer (GameUpdate resets it to -1 on exit)
STAFF_TEX = (1, 2)                          # identifiers given to the engine for the end credits' two textures (what Texture2D.GetNativeTexturePtr returns in Unity: any distinct numbers other than 0)
STAFF_SIZE = 512                            # side of those textures (the engine reads it from the first integer of the message)
ANIM_DURATION = 18                            # frames (30 Hz) before telling the engine that an object animation (door...) is finished
ANIM_ENTRY = (0x20, 0x24)                     # size of an entry of FLDObject's map object table (DQ IV, DQ V / VI); + 0x14 bit 0 = common animation playing (FLDObject::SetCommonAnimationEnd clears it)
ORIGIN_AFTER = 3                            # frames after its creation after which an animated character without a position is placed at the origin (see State.apply, packet 13)


def _lay(engine):
    """The engine_layout.Layout of an engine; DQ IV's fixed one for an engine built without its library (tests, fake engines)."""
    lay = getattr(engine, 'layout', None)
    return lay if lay is not None else LY.Layout.fixed(engine.L)


class Silent:
    def __init__(self): self.lines = []
    def __call__(self, *a):
        self.lines.append(' '.join(str(x) for x in a)); del self.lines[:-200]


class GameCore(T.Core):
    """Core + saves: reads from a real folder, persistent writes (never into the originals)."""
    read_folder = None
    write_folder = None

    def h_fopen(self, path, mode, *a):
        p = self.read_cstr(path).decode('latin-1'); m = self.read_cstr(mode).decode()
        mm = re.search(r'(data\d+\.dat)$', p)
        if mm and os.environ.get('DQ_SAVEDBG'): print('fopen %s mode %s' % (p, m), flush=True)           # (test) when the engine opens a save file
        if mm:
            name_str = mm.group(1)
            if 'w' in m or 'a' in m:
                r = super().h_fopen(path, mode, *a)
                if r: self.files[r]['persist'] = os.path.join(self.write_folder, name_str)
                return r
            for d in (self.write_folder, self.read_folder):
                if d and os.path.exists(os.path.join(d, name_str)):
                    self.real_files[p] = os.path.join(d, name_str); break
        return super().h_fopen(path, mode, *a)

    write_failures = None                    # save files the engine could not write (list of sentences, read and emptied by the window: small message + log)
    written_saves = None                     # adventure logs written since the history last looked (numbers 1..9: data<n>.dat), read and emptied by History
    journals_changed = False                 # an adventure log was written (saved, copied, deleted): the window looks which games they hold (SnapshotsMixin.follow_journals)

    atomic_write = staticmethod(atomic_write)                  # (persistence/write_dir.py: shared with the save import)

    read_only = False                        # a battle replay is being played: the engine's save files are not written (a replay must not change the real game)

    def h_fclose(self, h, *a):
        f = self.files.get(h)
        if f and f.get('persist') and not self.read_only:
            ex = self.atomic_write(f['persist'], bytes(f['d']))
            mm = re.search(r'data(\d+)\.dat$', f['persist'])
            if ex is None and mm:
                if self.written_saves is None: self.written_saves = []
                self.written_saves.append(int(mm.group(1))); self.journals_changed = True
            if ex is not None:                # the engine continues (it thinks it wrote, as before): the window warns the player
                if self.write_failures is None: self.write_failures = []
                self.write_failures.append('%s : %r' % (os.path.basename(f['persist']), ex))
        return super().h_fclose(h, *a)


class State:
    """What Unity would know on screen: decoding of the engine's packets."""
    fade_speed = 1.0                        # fades to black last 1 / fade_speed of their original duration: FIXED at 1 (game duration), no longer a player setting (tests vary it)
    sepia = False                              # packet 1 (cmd_map_effect_sepia: FldObject.calculateExecSepia): the town scenery switches to the `Custom/fld_*_sepia` shaders (flashbacks) until the next map load; class attribute: a savestate recorded does not have this attribute
    field_cam = None; field_meshes = None; field_type = 0      # (class attributes: savestates recorded before DQ VI's world map)
    particles = None                         # DQ V / VI ParticleSystem (packet LY.PARTICLE, see Engine._particle): {'field', 'pos', 'scale', 'v', 'uv', 'col'} while the system exists; class attribute: savestates recorded before it
    map_background = False                   # DQ V / VI: the background is the loaded map's FETC color (cmd_fld_load) until a packet 8; class attribute: a savestate recorded does not have this attribute
    field_scale = 1.0                        # packet 30, integer < 0: Field.SetScale(scale): the world map's orthographic camera sees scale x more of the field (balloon: 1.0 to 1.25, sent again every frame by FieldData::setOffset); class attribute: a savestate recorded does not have this attribute
    spotlight = None                             # DQ V / VI packet 34, effect 7 (Spotlight): {'pos': engine x, y, z, 'torch', 'size', 'light', 'side' (r, g, b, a), 'visible'} while it exists (see Engine._spotlight); class attribute: savestates recorded before it
    screen_mask = False                          # DQ VI packet 34, effect 11: the screen black around a centered disc (Custom/ScreenMask, texture etc/ccmsk: render/spotlight.py); class attribute: savestates recorded before it
    screen_sepia = False                         # DQ V / VI packet 34, effect 8: the whole scene in sepia (Custom/Sepia: gray = mean of r, g, b, plus (0.109375; -0.0546875; 0)), drawn under the interface; class attribute: savestates recorded before it
    screen_effect = None                         # packet 34, values 3 (journey), 5 (lamp), 6 (Riremito): Capture class effect in progress {'n': frames elapsed, 'duration': engine frames, 'frames': original MoveNext calls, 'kind', 'inverse', 'morning'} (see Engine._start_screen_effect); class attribute: a savestate recorded does not have this attribute
    black_screen = None                          # end of a journey's departure (Capture.Tabi: both layers go to alpha 0, the game camera stays cut off): {'n': frames elapsed} as long as the screen stays black, until the end of the arrival (see Engine._screen_effect)
    chara_queue = 0                         # DQ V packet LY.FLD_ETC with bit 30 (Chara.SetPriorityShader(n)): the characters' render queue is 3000 + n (their shadows' 2999 + n) until the next map load (cmd_fld_load puts 0 back); the transparent scenery of priority <= n is drawn before them (window: _paint_scenery); class attribute: savestates recorded before it
    fld_paused = False                      # DQ VI packet LY.FLD_ETC, case 3 (FldObject.Pause(value & 0x1000)): FldObject.Update returns at once while it is set: the map's movements, mesh sequences, material colors, palettes and camera movements stand still (menu open, travel door, well, screen effects); class attribute: savestates recorded before it
    tall_screen = False                     # the window is a portrait one narrower than 9:16 (set by the window when it draws a town): DQ VI's maps e13din* then bring the camera closer (scene_distance_adjust)
    rider_object = None                     # DQ V packet LY.FLD_ETC with the value 0: the map object (38, the mine cart of dkb3cev) the first character is placed on every frame (MapObjects.rider_position), until the next map load; class attribute: savestates recorded before it
    cam_sent = False                        # a camera packet (cmd_camera) arrived since the last step's messages: NativeManager.Update answers with the camera's position on the next frame (Engine._camera_message); class attribute: savestates recorded before it
    cam_key = False                         # DQ VI packet LY.FLD_ETC, case 0: a view placed by FldObject.MoveToEventCameraFirstKey holds until the engine's next camera packet (class attribute: savestates recorded before it)
    obj_requests = ()                       # DQ VI packet LY.FLD_ETC, case 1 (NativeManager.cmd_fld_etc: m_anim_list.Add((value >> 8) | 0x4000)): map objects whose displayed position the engine reads back every frame (Engine._map_object_messages), until the next map load; class attribute: savestates recorded before it
    floats = ()                             # packet 30, integer >= 0: Field.SetFloatingObject(slot): this character's panel (the balloon: slot 12) is enlarged by `field_scale` (Field.FloatingObject.SetScale), so it keeps its on-screen size when the field is seen from farther away; its shadow is not touched
    def __init__(self):
        self.game_map = None; self.cam_pos = None; self.cam_target = None; self.cam_rot = None
        self.symbols = {}                            # world map: icons (packet 29) idx -> {genre, x, y (top-left corner in px), alpha, visible}
        self.models = {}; self.meshes = {}       # battle: slot -> model id (texture m<id>); slot -> dynamic mesh (packet 22)
        self.mesh_state = {}                           # battle: slot -> {'shader', 'vis', 'col', 'off'} (packets 21, 23, 24, 25, 26: MeshChara on the Unity side)
        self.cam_near = None; self.cam_far = None; self.cam_yoff = None; self.cam_free = False; self.cam_down = None      # packet 11: near, far, yOffset (None = Global.CameraInitParams values), free camera (no target), lookDownAdjust (None = 0.2 from SceneCamera's .cctor)
        self.cam_fov = None                           # packet 11 (camera_sub): nominal field of view in degrees (Unity multiplies it by 4/3, SceneCamera.Update); None = battle value (48)
        self.pad_blink = False                     # packet 19 with a slot >= 16 (TouchPad.SetPadBlink): the game makes the central disc of the D-pad blink (orange ball and halo) as long as an action is possible / awaited; icon number < 0 = end
        self.icons = {}                              # packets 19/20 (Riseup): slot (0..15) -> {'id' (32 x 32 cell of the MenuTexture sheet), 'pos', 'alpha'}: "!" bubble above a character
        self.sprites = {}; self.loaded_maps = 0; self.object_cmds = []; self.background = (0.0, 0.0, 0.0); self.top_background = None; self.bottom_background = None       # background: average of the two tints of packet 8; top_background / bottom_background: tints of the top and bottom of the screen (BackColor, gradient; None = no packet 8); object_cmds: orders on map objects (packets 2 to 5 and 7), emptied by the window: (kind, object, values...)
        self.part = 0; self.field = None        # game part (0xc town, 0xe world map); world map: tiles to display
        self.field_cam = None; self.field_meshes = {}; self.field_type = 0      # DQ VI: world map camera, {opt: position of the tile mesh} (packet 28), Field.Init's type (packet 27: texture field<type>)
        self.meshes_seen = set()                # battle: slots whose mesh (packet 22) arrived in THIS frame: Unity draws in immediate mode, no packet = nothing on screen (blinking of a hit received, defeated monster)
        self.fade = self._fade_plane(); self.background_fade = self._fade_plane(True)      # (Fade.Awake creates plane 0 opaque black, plane 1 clear: the port's plane 0 starts clear, its opacity is Unity's from its first fade-in, see _fade) Fade.FadePlane: current opacity, target opacity, step per frame, remaining frames, color, frames spent fully black. TWO planes (Fade.l_fade, bit 14 of packet 9's integer = Fade.l_targetLayer): 0 = "FadeForMenu" (shader Custom/FrontFade, Overlay queue: in front of everything, windows and messages included); 1 = "FadeForBG" (Custom/BackFade, Background queue: in front of the scene, behind the windows)
        self.sounds = []; self.music = None       # packets 35/36/37: pending sound orders (type, value), emptied by the window every frame; last requested music (None = stop)
        self.effect = None                         # battle start screen effect in progress: {'n': frames elapsed} (the engine waits for it to end, see Engine._battle_effect)
        self.loading = None                    # map load (packet 0) while the screen is covered by a fade: {'n': engine frames since packet 0, 'cam': packet 10 received since} as long as the scene is not in place; the window then draws only the fade (see update_loading)
        self.fade_ratio = (-99, 1.0)             # last fade to black launched by the engine: (engine frame, played duration / original duration); 1.0 = game duration (see Engine._exit_countdown)
        self.entry_window = 0; self.return_window = 0; self.transition_guard = 0      # battle transition (see battle_transition): remaining frames of the "battle arrival" window, of the "return to the map" window, of the guard between two fades
        self.load_wait = None                # {'base': number of scene loads already seen, 'n': frames waited}: black screen until the battle scene has arrived
        self.world = None                         # entire world map read from the engine: {'ids': tiles (H x L) after animation, 'l', 'h'}; None outside the world map
        self.tint = (1.0, 1.0, 1.0)             # packet 33 (cmd_map_color_rate): scenery tint (FldObject.m_color, or Field.SetColor on the world map): multiplies the color of each scenery material (except "tz_" textures: lamps, candles, waves); 1, 1, 1 = neutral
        self.sleeping_height = 0.0                          # packet 17 (Chara.SetSleepNear): body height of lying characters (Y component of s_lieOffset, static of Chara: it survives map changes); 0 as long as the engine has sent nothing
        self.fbx = {}; self.image = 0             # animated 3D models (packets 31 / 32: the title dragon): slot -> {'id', 'vis', 'pos', 'rot', 'scl', 'col', 'anim': [(engine frame, sequence number, force)]}; frame: number of the current engine step (filled in by Engine.step)

    @staticmethod
    def _fade_plane(known=False): return {'a': 0.0, 'end': 0.0, 'step': 0.0, 'n': 0, 'col': (0.0, 0.0, 0.0), 'black': 0, 'battle': False, 'known': known}      # 'battle': battle transition fade (game duration, see battle_transition); 'known': the plane's opacity is Unity's (see _fade)

    def __setstate__(self, d):                  # states recorded before the two fade planes (and before the switch to English: see OLD_ATTRIBUTES)
        self.__dict__.update(_renamed_dict_keys(_renamed_attributes(d))); self.__dict__.setdefault('background_fade', self._fade_plane()); self.__dict__.setdefault('fbx', {}); self.__dict__.setdefault('image', 0); self.__dict__.setdefault('tint', (1.0, 1.0, 1.0)); self.__dict__.setdefault('loading', None); self.__dict__.setdefault('sleeping_height', 0.0); self.__dict__.setdefault('top_background', None); self.__dict__.setdefault('bottom_background', None)
        self.__dict__.setdefault('entry_window', 0); self.__dict__.setdefault('return_window', 0); self.__dict__.setdefault('transition_guard', 0); self.__dict__.setdefault('fade_ratio', (-99, 1.0))

    def fade_in_progress(self): return self.fade['n'] >= 1 or self.background_fade['n'] >= 1      # Fade.FadePlane.CheckFade: remaining frames > 0 on one of the two planes

    def battle_transition(self):
        """Is the game in a battle transition: start screen effect (the "cascade"), black screen waiting for the scene, battle arrival and exit fades, fade back to the map (and the 1 to 3 frames
        between two of these fades)? its duration in frames follows "Transition speed" (State._fade, Engine._start_battle_effect) like any fade; its clock stays the game's for the cascade and, on the return to the
        map, while the player is locked or between two fades (Game._speed_factor: as soon as the player is free, accelerated walking and the accelerator take effect), whereas "Battle speed" acts from the end of the cascade (black screen, arrival fade, messages) up to and including the end fade."""
        return (self.effect is not None or self.load_wait is not None or self.transition_guard > 0
                or (self.fade['n'] >= 1 and bool(self.fade.get('battle'))) or (self.background_fade['n'] >= 1 and bool(self.background_fade.get('battle'))))

    def tick_transition(self):
        """One game frame (after tick_fade): the windows and the battle transition guard run down."""
        if self.entry_window > 0: self.entry_window -= 1
        if self.return_window > 0: self.return_window -= 1
        if self.transition_guard > 0: self.transition_guard -= 1

    def apply(self, packets, read_name):
        self.meshes_seen = set()
        covered = max(self.fade['a'], self.background_fade['a']) >= LOADING_COVERED; do_load = False; cam_view = False      # screen already covered by a fade at the time of the map load?
        for t, a, f in packets:
            if t == 0:
                do_load = True; cam_view = False
                if a and a[0] & 0x100: self.entry_window = BATTLE_ENTRY_WINDOW          # battle scene (cmd_fld_load, bit 8): the fade launched right after is the battle arrival fade
                self.game_map = read_name(); self.loaded_maps += 1; self.sprites.clear(); self.background = (0.0, 0.0, 0.0); self.top_background = self.bottom_background = None; self.models.clear(); self.meshes.clear(); self.mesh_state.clear(); self.object_cmds = [('map',)]; self.cam_rot = None; self.cam_fov = None; self.cam_near = None; self.cam_far = None; self.cam_yoff = None; self.cam_free = False; self.symbols.clear(); self.icons.clear(); self.fbx.clear(); self.tint = (1.0, 1.0, 1.0); self.field_scale = 1.0; self.floats = (); self.sepia = False; self.obj_requests = (); self.chara_queue = 0; self.fld_paused = False; self.rider_object = None; self.cam_key = False      # new map: fresh FldObject, white m_color (FldObject.init_fld); the engine sends the map tint again after packet 0
                if ENGINE_V2 and a and a[0] >> 8 == 2: self.cam_free = True          # NativeManager.cmd_fld_load of DQ V / VI: mode (value >> 8) 2 = SceneCamera.s_targetCamera = false (position + angles, as for a title)
                self.map_background = ENGINE_V2           # ... and BackColor.SetColor(FETC color of the map) right after FldObject.Load: until a packet 8, the map's own background color (window: _clear_color)
            elif t == 1: self.sepia = True           # cmd_fld_color (D12C98) = cmd_map_effect_sepia: no argument; FldObject.calculateExecSepia (CDD9E0) replaces the shader of each scenery material with "<name>_sepia" (not the characters')
            elif t == 8 and len(f) >= 6:
                self.map_background = False
                self.background = tuple((f[i] + f[i + 3]) / 2 for i in range(3))     # clear color: average of the two tints (fallback, off-screen rendering); the background itself is a vertical gradient
                self.top_background = tuple(float(f[i]) for i in range(3)); self.bottom_background = tuple(float(f[i + 3]) for i in range(3))      # cmd_back_color (RVA 0xD14F44): 6 floats -> BackColor.SetColor(top, bottom, 0) (0xCC43F8): top = vertices 0 and 1 of the full screen quadrilateral, bottom = vertices 2 and 3
            elif t == 33 and len(f) >= 3:           # cmd_map_color_rate (D14E44): r, g, b; FldObject.m_color = (r, g, b, 1) (town and battle scenery: FldObject.update_material_color, every frame, Material.color = _OrgColor (1, 1, 1, alpha) x m_color, except materials whose texture has "tz_" in its name); on the world map: Field.SetColor (tiles and symbols)
                self.tint = (float(f[0]), float(f[1]), float(f[2]))
            elif t == 27:                           # cmd_field_load: Field.Init, fresh material (white color); the engine sends the world map tint afterwards
                self.tint = (1.0, 1.0, 1.0); self.field_scale = 1.0; self.floats = ()
                self.field_type = a[0] if a else 0; self.field_meshes = {}; self.field_cam = None          # (Field.Init resets the original camera size; the engine registers the balloon (packet 30, slot 12) right after, on every world map load)
                self.symbols.clear()                  # the place icons too: FieldData::setup (CWorldMap::setup) rebuilds the symbol table of the world being loaded and resets the state of each one, so the engine sends them all again
                                                      # with their full data; the old icons are never hidden one by one (a world with fewer places left the extra ones of the previous world on screen until the next map load)
            elif t == 30 and a:                     # cmd_field_scale (D12794): integer >= 0 = Field.SetFloatingObject(Chara of this slot); integer < 0 = Field.SetScale(float)
                if a[0] >= 0x4000 and FLD_ETC_GAME == 'dq6': self.floats = tuple(x for x in self.floats if x != a[0] & 0xff)          # DQ VI: Field.RemoveFloatingObject(Chara of slot value & 0xff)
                elif a[0] >= 0:
                    if a[0] not in self.floats: self.floats = self.floats + (a[0],)
                elif f and 0.0 < f[0] < 1e6: self.field_scale = min(max(float(f[0]), FIELD_SCALE[0]), FIELD_SCALE[1])         # (FieldData::setOffset: 1 + offset / 128, offset <= 32: from 1.00 to 1.25)
            elif t == 28 and len(a) >= 4:           # FieldData::drawBlock: 16x16 tile window of the world map (cols x rows) around the camera
                self.field = {'x0': a[0], 'y0': a[1], 'cols': a[2], 'tiles': a[4:4 + a[3]], 'cam': (f[0], f[1]) if len(f) >= 2 else None}
            elif t == 28 and len(a) == 1:           # DQ VI (NativeManager.cmd_field): opt; bit 0 clear: Field.SetPosition(camera x, y) then the position of the tile mesh opt >> 8 (Field.SetIndexList: its texture
                opt = a[0]; fl = list(f)                # coordinates come from FieldData::setMeshInfo(uv, opt), see Engine._world_mesh); bit 0 set: the mesh position only
                if not opt & 1 and len(fl) >= 2: self.field_cam = (fl[0], fl[1]); fl = fl[2:]
                if len(fl) >= 3:
                    if self.field_meshes is None: self.field_meshes = {}
                    self.field_meshes[opt] = (fl[0], fl[1], fl[2])
            elif t == 29 and a:                     # FieldData::draw_symbol: idx | genre << 8 + [x, y, alpha]; 0x8000 | idx = hide; 0xc000 | idx = show again; 0x9000 | idx = scale and priority (below)
                v = a[0]; i = v & 0xff
                if v & 0x8000 and v & 0x1000:                      # NativeManager.cmd_field_symbol: bit 12 with bit 15 (tested before bit 14) = Field.SetSymbolScale (localScale = (s, 1, s), from the top-left corner) then Field.SetSymbolPrio
                    s = self.symbols.get(i)                             # (position y = prio, -45 by default: the higher, the closer to the camera); sent only by FieldData::setKekaiSymbol, every frame, for the
                    vals = f[:2] if len(f) >= 2 else (s.get('tail') if s else None)          # rings of the barrier of Nadiria (kind 19); its two floats come in the stream right after the 3 of the symbol itself
                    if s is not None and vals: s['scale'] = float(vals[0]); s['prio'] = float(vals[1])
                elif v & 0xc000 == 0xc000:
                    if i in self.symbols: self.symbols[i]['visible'] = True
                elif v & 0x8000:
                    if i in self.symbols: self.symbols[i]['visible'] = False
                elif len(f) >= 3:
                    old = self.symbols.get(i)
                    self.symbols[i] = {'kind': (v >> 8) & 0x7f, 'x': f[0], 'y': f[1], 'alpha': f[2], 'visible': True,
                                       'scale': old['scale'] if old and 'scale' in old else 1.0, 'prio': old['prio'] if old and 'prio' in old else SYMBOL_PRIO, 'tail': tuple(f[3:5]) if len(f) >= 5 else None}
            elif t == 12 and len(a) >= 2:           # cmd_chara_load: slot, identifier (DQ IV: sheet number, + 4096 on the world map; DQ V / VI: texture | field mode 0x1000 | rows << 16 | mesh << 20, see hd.MESHES_V2)
                if a[1] >= 0: self.sprites[a[0]] = {'id': (a[1] & 0x7f1fff) if ENGINE_V2 else a[1], 'pos': None, 'col': (1, 1, 1, 1), 'pose': 0, 'dir': 0, 'visible': True, 'scale': 1.0, 'created': self.image}
                else: self.sprites.pop(a[0], None)
            elif t == 14 and len(a) >= 2:           # cmd_chara_on_off: Chara.SetVisible(value != 0) = Renderer.enabled of the character (and its shadow)
                if a[0] in self.sprites: self.sprites[a[0]]['visible'] = a[1] != 0
            elif t == 16 and a:                     # cmd_chara_sleep_pose (D132FC): character = v & 0xff; bits 8-9: Chara.SetSleepPose(bit 8); bit 10: Chara.SetSleepDir(v >> 12) (see lying_pose.py)
                v = a[0]; s = self.sprites.get(v & 0xff)
                if s is not None:
                    if v & 0x300:
                        s['sleeping'] = bool(v & 0x100); s.pop('billboard2', None)          # (Chara.SetSleepPose sets the panel's placement again: lying, or Chara.normalBillboard)
                        if s['sleeping']: s['sleeping_rot'] = (0.0, 0)          # SetSleepPose(true): quadrant 0, identity rotation (the direction follows in the same packet)
                    if v & 0x400: s['sleeping_rot'] = PC.packet_direction(v)
            elif t == 17 and len(a) >= 2:           # cmd_chara_sleep_near (D133E4): (defNear, offset); Chara.SetSleepNear: height of the lying body
                self.sleeping_height = PC.packet_height(a[1], GM.profile().key)
            elif t == 18 and a:                     # cmd_chara_pose (D13474): v = character | number << 8: Chara.SetTexture(number) = the character changes sprite sheet (TownCharacterDraw::changePose)
                s = self.sprites.get(a[0] & 0xff)
                if s is not None and (a[0] >> 8) >= 0: s['id'] = (a[0] >> 8) | (s['id'] & 4096)
            elif t == 15 and a:                     # cmd_chara_shadow (D13150): character = v & 0xff; bits 8-9: shadow visible (bit 8); bits 10-11: separate shadow (bit 10); bit 12: shadow position (3 floats,
                v = a[0]; s = self.sprites.get(v & 0xff)        # SetShadowPosition); bit 13: opacity (SetShadowAlpha); bit 14: shadow position on the world map (SetFieldShadowPosition)
                if s is not None:
                    fl = list(f)
                    if v & 0x300: s['shadow'] = bool(v & 0x100)
                    if v & 0xc00: s['shadow_separate'] = bool(v & 0x400)
                    if v & 0x1000 and len(fl) >= 3: s['shadow_pos'] = tuple(fl[:3]); fl = fl[3:]
                    if v & 0x2000 and fl: s['shadow_alpha'] = float(fl[0]); fl = fl[1:]
                    if FLD_ETC_GAME == 'dq6':                                   # DQ VI: no SetFieldShadowPosition; bit 15 = Chara.SetupHue(ice = bit 14, amount): the character's shader becomes Custom/CharaHueIce or CharaHueGray
                        if v & 0x8000 and fl: s['hue'] = (bool(v & 0x4000), float(fl[0]))          # (frozen or petrified characters); bit 14 alone = Chara.SetHue(amount): _Hue = amount, _UnHue = 1 - amount
                        elif v & 0x4000 and fl and s.get('hue'): s['hue'] = (s['hue'][0], float(fl[0]))
                    elif v & 0x4000 and len(fl) >= 3: s['shadow_field'] = tuple(fl[:3])          # SetFieldShadowPosition: (x, y, height) in map pixels; the balloon: 32 pixels (2 tiles) south of its feet
            elif t == 19 and len(a) >= 2 and a[0] >= 16:            # cmd_rise_icon_load with a slot >= 16: not a bubble, the D-pad blinking (TouchPad.SetPadBlink); number < 0 = stop
                self.pad_blink = a[1] >= 0
            elif t == 19 and len(a) >= 2 and 0 <= a[0] < 16:        # cmd_rise_icon_load: slot, icon number (< 0 = release); Riseup object, Custom/Riseup material on the MenuTexture sheet
                if a[1] >= 0: self.icons[a[0]] = {'id': a[1], 'pos': None, 'alpha': 1.0}
                else: self.icons.pop(a[0], None)
            elif t == 20 and a and len(f) >= 4:     # cmd_rise_icon: slot; x, y, z (SetPosition) then alpha (Material.color = 1, 1, 1, alpha)
                ic = self.icons.get(a[0])
                if ic is not None: ic['pos'] = tuple(f[:3]); ic['alpha'] = f[3]
            elif t == 9 and a:                      # cmd_fade: duration in frames = v & 0x7ff; bit 12: fade out to a color (otherwise fade in); bit 11: color r, g, b as floats; bit 13: white (otherwise black); bit 14: background plane "FadeForBG" (behind the windows; otherwise plane "FadeForMenu", in front of everything: see State._fade)
                self._fade(a[0], f)
            elif t == 5 and a:                      # cmd_fld_common_anim: object = v & 0xffff, common animation number = v >> 16 (FldObject.SetCommonAnimation)
                self.object_cmds.append(('anim', a[0] & 0xffff, (a[0] >> 16) & 0xffff))
            elif t == 6 and a and 0 <= a[0] < 0x1000:   # cmd_fld_event_anim: event animation number (FEAN, FldObject.SetEventAnimation); >= 0x1000: SetCameraNo(v & 0x3fff)
                self.object_cmds.append(('event', a[0]))
            elif t == 6 and a and a[0] >= 0x1000 and ENGINE_V2:      # FldObject.SetCameraNo(v & 0x3fff): a camera movement of the map (FEMV) places the view (map_objects.event_camera). DQ IV: kept without effect, as before (not checked on its maps)
                self.object_cmds.append(('camera', a[0] & 0x3fff))
                if not a[0] & 0x3fff: self.cam_key = True          # (camera 0: the movement's last pose holds until the engine's next camera packet, see map_objects.event_camera)
            elif t == 7 and a:                      # cmd_fld_on_off: object = v & 0xffff, flag = v >> 16 (SetMapObjOnOff: non-zero flag = object hidden)
                self.object_cmds.append(('onoff', a[0] & 0xffff, (a[0] >> 16) & 0xffff))
            elif t == 2 and a:                      # cmd_fld_alpha: object = v & 0xffff, level (0..31) = v >> 16 & 0xff (SetMapObjAlpha: opacity = level / 31)
                self.object_cmds.append(('alpha', a[0] & 0xffff, (a[0] >> 16) & 0xff))
            elif t == 3 and a and len(f) >= 3:      # cmd_fld_pos: object; x, y, z
                self.object_cmds.append(('pos', a[0] & 0xffff, tuple(f[:3])))
            elif t == 4 and a and len(f) >= 2:      # cmd_fld_rot: object; rotation x, y (z = 0)
                self.object_cmds.append(('rot', a[0] & 0xffff, tuple(f[:2])))
            elif t == 10 and a:
                self.cam_sent = True
                if self.cam_key: self.cam_key = False; self.object_cmds.append(('camera_engine',))      # (SceneCamera.Update places the camera again: the view left by MoveToEventCameraFirstKey ends)
                if do_load: cam_view = True
                elif self.loading is not None: self.loading['cam'] = True
                fl = list(f)
                if a[0] & 1 and len(fl) >= 3: self.cam_pos = tuple(fl[:3]); fl = fl[3:]
                if a[0] & 2 and len(fl) >= 3: self.cam_target = tuple(fl[:3]); fl = fl[3:]
                if a[0] & 4 and len(fl) >= 3: self.cam_rot = tuple(fl[:3])        # battle: rotation (pitch, yaw, roll) in degrees
            elif t == 21 and len(a) >= 2:           # DSSAObject::send_unity_load(slot, model id); id < 0 = release
                if a[1] >= 0:
                    self.models[a[0]] = a[1]
                    self.mesh_state[a[0]] = {'shader': 3 if a[1] > 999 else 1, 'visible': True, 'col': (1.0, 1.0, 1.0), 'off': (0.0, 1.0) if ENGINE_V2 else (0.0, 0.0)}      # MeshChara..ctor: shader 1 (DssaMonster0) except id > 999 (DssaCharaTransparent); white color; DQ V / VI: texture scale (1, -1), offset (0, 1)
                else: self.models.pop(a[0], None); self.meshes.pop(a[0], None); self.mesh_state.pop(a[0], None)
            elif t == 31 and len(a) >= 2:           # cmd_fbx_load: slot, model number ("fbx/c<number>", FbxObject); < 0 = release. The model is created hidden, scale 1, and turned by Euler(0, 180, 0): NativeManager.cmd_fbx_load ends with FbxObject.SetRotation(0, 180, 0) (0xD13610). A packet 32 with bit 14 replaces that rotation (the title dragon); the battle spells never send one, so they are seen from that side
                if a[1] >= 0: self.fbx[a[0]] = {'id': a[1], 'visible': False, 'pos': (0.0, 0.0, 0.0), 'rot': (0.0, 180.0, 0.0), 'scl': (1.0, 1.0, 1.0), 'col': (1.0, 1.0, 1.0, 1.0), 'anim': []}
                else: self.fbx.pop(a[0], None)
            elif t == 32 and a:                     # cmd_fbx: slot = v & 0xff; bits 8-9: visibility (bit 8 = visible); 10: force the animation; 11: scale (3 floats); 12: color (4); 13: position (3); 14: rotation, degrees (3); 15: animation (one integer: sequence)
                v = a[0]; m = self.fbx.get(v & 0xff); fl = list(f)
                if FBXLOG: print('FBX frame %d packet 32: v=%#x ints=%s floats=%s' % (self.image, v & 0xffff, list(a[1:]), [round(x, 4) for x in fl]), flush=True)         # (test) DQ_FBXLOG=1: all packets 32 (position, rotation, scale, color, sequence)
                if m is not None:
                    if v & 0x300: m['visible'] = bool(v & 0x100)
                    if v & 0x800 and len(fl) >= 3: m['scl'] = tuple(fl[:3]); m['scl_set'] = True; fl = fl[3:]          # (scl_set: until then Unity keeps the prefab's localScale; the window uses it for the screen pictures)
                    if v & 0x1000 and len(fl) >= 4: m['col'] = tuple(fl[:4]); m['col_set'] = True; fl = fl[4:]          # (FbxObject.SetColor: Material.color of all its materials; until then each material keeps its own)
                    if v & 0x2000 and len(fl) >= 3: m['pos'] = tuple(fl[:3]); fl = fl[3:]
                    if v & 0x4000 and len(fl) >= 3: m['rot'] = tuple(fl[:3]); fl = fl[3:]
                    if v & 0x8000 and len(a) >= 2: m['anim'].append((self.image, int(a[1]), bool(v & 0x400)))
            elif t == LY.FLD_ETC and a and FLD_ETC_GAME == 'dq5':      # DQ V NativeManager.cmd_fld_etc: bit 30 = Chara.SetPriorityShader(value & 0xffff) (the engine sends it after the load of the towns of its table: 11 for most of them);
                if a[0] & 0x40000000: self.chara_queue = a[0] & 0xffff      # bit 29 = FldObject.FixProblems(map number): three maps, applied when the map is read (world.data.FIX_PROBLEMS);
                elif not a[0] & 0x20000000: self.rider_object = RIDER_OBJECT if a[0] == 0 else None      # any other value is kept (-1 at each map load) and 0 (the engine: map 332, dkb3cev) makes NativeManager.Update place the first character on map object 38 every frame
            elif t == LY.FLD_ETC and a and FLD_ETC_GAME == 'dq6':      # DQ VI NativeManager.cmd_fld_etc: case = value & 0xff
                case = a[0] & 0xff
                if case == 1:                                           # from now on, every frame, NativeManager.Update sends back the displayed position of map object value >> 8 (FldObject.GetMapObjDispPos):
                    self.obj_requests = self.obj_requests + (((a[0] >> 8) | 0x4000),)          # the engine places characters on it (Murdaw's room: the party floats and turns around the room on the circling objects)
                elif case == 3: self.fld_paused = bool(a[0] & 0x1000)   # FldObject.Pause
                elif case == 0:                                         # FldObject.MoveToEventCameraFirstKey(step = value >> 18, steps = value >> 8 & 0x3ff): the view goes from where it stands to the first key of
                    self.object_cmds.append(('camera_key', (a[0] >> 18) & 0x3fff, (a[0] >> 8) & 0x3ff)); self.cam_key = True      # the map's first camera movement (script::CmdSetEventCamera; map_objects.camera_first_key)
                elif case == 2:                                         # Chara.ChangeBillboardCalc for every character loaded at that moment (the engine: after the load of map 1402, e01din2): a standing character's
                    for sp in self.sprites.values():                    # panel is placed by Chara.normalBillboard2 from now on (pulled toward the camera, see billboard2_pull), until the character is loaded again
                        if not sp.get('sleeping'): sp['billboard2'] = True
                # (case 4: FldObject.FixSlotPosition (nothing at 9:16), 5: the characters updated at once: nothing to reproduce)
            elif t == LY.MESH_ORTHO and a:           # DQ V / VI cmd_mesh_ortho: the slot's mesh goes to layer 11, the one SceneCamera.s_DSSAOrthoCamera draws (2D over the scene: logos, title texts)
                if a[0] in self.mesh_state: self.mesh_state[a[0]]['ortho'] = True
            elif t == 23 and len(a) >= 2:           # cmd_mesh_on_off: slot, visible
                if a[0] in self.mesh_state: self.mesh_state[a[0]]['visible'] = bool(a[1])
            elif t == 24 and a:                     # cmd_mesh_shader: slot | shader number << 8 (0 Chara, 1 DssaMonster0, 2 DssaMonster1, 3 DssaCharaTransparent)
                if a[0] & 0x40000000 and FLD_ETC_GAME == 'dq6':               # DQ VI (args::DSSACharacter::setup, model 523, instead of its load): the slot gets a new MeshChara523 = a MeshChara of model 523, white,
                    slot = a[0] & 0xff; self.models[slot] = MESH_523[0]         # that MeshChara523.SetBillboard never turns toward the camera: it keeps the rotation given here
                    self.mesh_state[slot] = {'shader': 1, 'visible': True, 'col': (1.0, 1.0, 1.0), 'off': (0.0, 1.0), 'rot': MESH_523[1]}
                elif (a[0] & 0xff) in self.mesh_state: self.mesh_state[a[0] & 0xff]['shader'] = a[0] >> 8
            elif t == 25 and a and len(f) >= 3:     # cmd_mesh_color: slot; r, g, b (Material.color, alpha 1)
                if a[0] in self.mesh_state: self.mesh_state[a[0]]['col'] = tuple(f[:3])
            elif t == 26 and a and len(f) >= 2:     # cmd_mesh_tex_offset: slot; u, v (Material.SetTextureOffset)
                if a[0] in self.mesh_state: self.mesh_state[a[0]]['off'] = tuple(f[:2])
            elif t == 11 and a:        # cmd_camera_sub: bit 0 near, 1 far, 2 fov, 4 yOffset, 5 lookDownAdjust (one float each, in bit order), 7 free camera (s_targetCamera = false)
                k = 0
                for bit, name_str in ((0, 'cam_near'), (1, 'cam_far'), (2, 'cam_fov'), (4, 'cam_yoff'), (5, 'cam_down')):
                    if a[0] >> bit & 1 and k < len(f):
                        v = f[k]; k += 1
                        setattr(self, name_str, min(max(v, 0.0), 78.0) if name_str == 'cam_fov' else v)       # SceneCamera.SetFOV2: clamped to 78
                if a[0] & 128: self.cam_free = True
            elif t == 22 and len(a) >= 2:           # dynamic mesh: [slot, vertex count, colors (1 byte/vertex, 4 per integer), indices...] + floats [x, y, z, (u, v, x, y, z) * vertex count]
                nv = a[1]; nc = (nv + 3) // 4; idx = a[2 + nc:]
                if len(f) >= 3 + 5 * nv and idx:
                    v = [f[3 + 5 * i:8 + 5 * i] for i in range(nv)]
                    self.meshes[a[0]] = {'pos': tuple(f[:3]), 'v': v, 'idx': list(idx), 'col': list(a[2:2 + nc])}       # col: one byte per quad (4 vertices), 255 = opaque
                    self.meshes_seen.add(a[0])
                    if ENGINE_V2 and a[0] in self.mesh_state: self.mesh_state[a[0]]['visible'] = True          # DQ V / VI: NativeManager.cmd_mesh ends with MeshChara.SetVisible(true) (DQ V's Slime Touch hides all its meshes, then draws some)
            elif t in (35, 36, 37) and a:           # cmd_se_play / cmd_se_stop / cmd_bgm (Sound.PlaySE, StopSE, PlayBGM; value >= 4096 = StopBGM)
                self.sounds.append((t, a[0]))
                if t == 37 and ENGINE_V2 and a[0] >= 0x4000 and a[0] & 0x1000: pass                 # DQ V / VI: level of the current track (sound.BGM_LEVEL): the music goes on
                elif t == 37 and ENGINE_V2: self.music = None if a[0] >= 0x4000 or a[0] < 0 else (self.music if a[0] & 0x1000 else a[0] & 0xff)      # DQ V / VI: track = value & 0xff (fade bits 8-11, layer bit 13, restart bit 12: sound.MUSIC_V2)
                elif t == 37: self.music = a[0] if 0 <= a[0] < 4096 else None
            elif t == 13 and a:
                fl = list(f); flags = a[0]; s = self.sprites.get(flags & 0x7ff)
                if s is None: continue
                if flags & 0x800 and fl:                # cmd_chara (D12EA8): bit 11 = Chara.SetScale(scale): Transform.localScale = 1.8 x scale on the character's panel (not on its shadow)
                    if s['pos'] is not None: s['scale'] = float(fl[0])      # (a character's 1st update, the one that places it, carries a throwaway starting value: 0.0253 to 0.08 then 1.0 in one frame; observed on 529 maps, 1062 characters: ignored)
                    fl = fl[1:]
                if flags & 0x1000: s['col'] = tuple(fl[:4]); fl = fl[4:]
                if flags & 0x2000 or flags & 0x8000: s['pos'] = tuple(fl[:3]); fl = fl[3:]      # 0x8000: same position, world map version (pixels)
                if flags & 0x4000 and len(a) > 1:
                    s['pose'] = a[1] & 0xff; s['dir'] = (a[1] >> 8) & 0xff
                    if s['pos'] is None and self.image - s.get('created', 0) >= ORIGIN_AFTER: s['pos'] = (0.0, 0.0, 0.0)       # the engine only sends a position if it differs from the one it believes is already set (its record of the character: (0, 0, 0)):
                                                                          # an animated character that has not received one after a few frames is at the origin (Chara created at (0, 0, 0) by Unity); mqtf3, mom2f1, msm1f1, slot 8: the NPC
                                                                          # in the middle of the room was not drawn. (The others' positions arrive 1 frame after creation: no one-frame appearance at the origin.)
        if do_load:         # map load: drawing held back (see update_loading) if the screen is covered, either by a fade already there or by the fade-in launched with this load (it restarts from opacity 1)
            if covered or any(fd['a'] >= LOADING_COVERED and fd['end'] == 0.0 and fd['n'] >= 1 for fd in (self.fade, self.background_fade)): self.loading = {'n': -1, 'cam': cam_view}
            else: self.loading = None

    def _fade(self, v, f):
        """Fade.SetFadeIn / SetFadeOutBlack / SetFadeOutWhite / SetFadeOutColor (FadePlane.Command): fade-out = toward opacity 1 from the current opacity;
        fade-in = toward 0 from the plane's CURRENT opacity, as Unity (FadePlane.Command reads the material's alpha): a fade-in on a plane already clear shows nothing. DQ VI, the end of the prologue:
        after the white fade and its fade-in, the engine asks a second fade-in of the front plane when the hero falls from his bed: it was started again from opaque white, a bright white flash.
        Until its first fade-in, plane 0 still starts it from opaque: Unity creates it opaque black (Fade.Awake), the port clear (the start of the game; an old savestate has no 'known').
        Duration <= 1 frame: immediate."""
        script = bool(v & PJ.SCRIPT_FADE_MARK)                                          # fade launched by a game script command (mark set by Engine._fade_hooks): its duration is the script's
        d = v & 0x7ff; fd = self.background_fade if v & 0x4000 else self.fade; fd['black'] = 0      # bit 14: Fade.l_targetLayer (NativeManager.cmd_fade D14D10), the plane that receives the command
        if getattr(self, 'no_fade', False):                                                # (a transition the port leaves out: DQ V's T'n'T shops and inns, Engine._sugoroku_service_fade) the plane stays clear and the fade is over at once
            fd['a'] = 0.0; fd['end'] = 0.0; fd['n'] = 0; fd['step'] = 0.0; fd['battle'] = False; fd['known'] = True; return
        fd['battle'] = self.part == PART_BATTLE or self.entry_window > 0 or self.return_window > 0        # battle fade (arrival in the scene, exit, return to the map): mark for battle_transition (its duration follows "Transition speed" like any fade to black)
        d0 = d; known = fd.get('known', False)
        if not v & (0x800 | 0x2000): d = PJ.fade_duration(d, State.fade_speed, script)       # shorter fade to black (map change, battle arrival, battle end, return to the map): "Transition speed" setting (not for a script fade)
        self.fade_ratio = (self.image, (d / d0) if d0 > 1 and v & 0x1000 else 1.0)      # shortened fade to black: the engine's exit sound effect countdown follows (Engine._exit_countdown)
        if v & 0x1000:
            fd['col'] = tuple(f[:3]) if v & 0x800 and len(f) >= 3 else ((1.0, 1.0, 1.0) if v & 0x2000 else (0.0, 0.0, 0.0))
            fd['end'] = 1.0
            if d <= 1: fd['a'] = 1.0; fd['n'] = 0; fd['step'] = 0.0
            else: fd['step'] = (1.0 - fd['a']) / d; fd['n'] = d
        else:
            fd['end'] = 0.0
            if d <= 1: fd['a'] = 0.0; fd['n'] = 0; fd['step'] = 0.0
            else:
                if not known: fd['a'] = 1.0
                fd['step'] = -fd['a'] / d; fd['n'] = d
            fd['known'] = True
            if fd is self.fade and self.effect is not None: self.effect['fade_in'] = True          # the battle's fade-in came during the cascade (DQ V / VI fights started by a script): see Engine._battle_effect

    def sharp_black_fade(self):
        """Fade.SetFadeOutBlack(0) on plane 0 (in front of everything): opaque, black, immediately (end of the lamp and Riremito effects); it stays that way until a fade-in started by the engine."""
        fd = self.fade; fd['black'] = 0; fd['col'] = (0.0, 0.0, 0.0); fd['end'] = 1.0; fd['a'] = 1.0; fd['n'] = 0; fd['step'] = 0.0; fd['battle'] = False; fd['known'] = True

    def current_fade_out_black(self, frames):
        """Fade.SetFadeOutBlack(frames) on plane 0 (DQ VI well, departure): from the plane's current opacity to opaque black in `frames` equal steps."""
        fd = self.fade; fd['black'] = 0; fd['col'] = (0.0, 0.0, 0.0); fd['end'] = 1.0; fd['battle'] = False; fd['known'] = True
        if frames <= 1: fd['a'] = 1.0; fd['n'] = 0; fd['step'] = 0.0
        else: fd['step'] = (1.0 - fd['a']) / frames; fd['n'] = frames

    def current_fade_in(self, frames):
        """Fade.SetFadeIn(frames) on plane 0 (arrival of a trip, Capture.Tabi): from the plane's CURRENT opacity to 0, in `frames` equal steps (FadePlane.Command, IN): no visible effect if the plane is transparent, and
        the "fade in progress" bit of Global.Flag stays raised for `frames` frames (CheckFade), as in the game."""
        fd = self.fade; fd['black'] = 0; fd['end'] = 0.0; fd['step'] = -fd['a'] / frames; fd['n'] = frames; fd['known'] = True

    def update_loading(self):
        """End of the drawing hold after a map load (once per engine frame, after `apply`). The viewer has seen only packet 0 (new scenery) at the load frame: camera of the previous scene,
        characters without a position, no tint and no object orders; the engine sends the camera (packet 10) and the positions (packet 13) on the next frame (59 maps tried, 529 maps counted: always the offset of 1).
        While `loading` exists, the window draws only the fade planes (black of the covered screen) and the fade-ins are suspended (`tick_fade`): the game's fade keeps its full duration, it just starts
        one frame later, on a complete scene. Ready: at least LOADING_MIN frames, camera received, and visible characters all placed (at most LOADING_PLACEMENT waiting frames); safeguard LOADING_MAX."""
        ch = self.loading
        if ch is None: return
        ch['n'] += 1                                                       # engine frames since the load (0 at the load frame: own count, no frame difference: a loaded state can reset the frame counter to zero)
        places = all(sp['pos'] is not None for sp in self.sprites.values() if sp.get('visible', True))
        world = self.part != PART_FIELD or self.field is not None      # world map: also wait for its tiles (packet 28)
        if ch['n'] >= LOADING_MAX or (ch['n'] >= LOADING_MIN and world and ch['cam'] and (places or ch['n'] >= LOADING_PLACEMENT)): self.loading = None

    def tick_fade(self):
        """FadePlane.Update: once per game frame, for each of the two planes. Safeguard (front plane only): a screen that stays fully black for more than 6 s (fade-in started by Unity itself, not by the engine) is reopened;
        the background plane has none: the engine keeps it black by itself (chapter title, 160 frames) and reopens it with a command."""
        for fd, guard in ((self.fade, True), (self.background_fade, False)):
            if self.loading is not None and fd['end'] == 0.0 and fd['n'] >= 1: continue      # drawing held back after a load: a fade-in only starts with the scene in place
            if fd['n'] >= 1:
                fd['n'] -= 1
                if fd['n'] < 1:
                    fd['a'] = fd['end']
                    if fd.get('battle') and fd['end'] >= 1.0: self.transition_guard = TRANSITION_GUARD      # battle black exit finished: the engine changes scene within 1 to 3 frames, then launches the fade-in; the clock must not change between the two
                else: fd['a'] += fd['step']
            if guard and fd['a'] >= 0.999 and fd['n'] < 1:
                fd['black'] += 1
                if fd['black'] > 180: fd['end'] = 0.0; fd['step'] = -1.0 / 15; fd['n'] = 15; fd['black'] = 0
            else: fd['black'] = 0


DBG = bool(os.environ.get('DQ_UIDBG'))


class Interface:
    """What the Unity interface remembers of the engine's messages: one window per port (list of texts, cursor, open or not).
    Messages of a port (after the first byte = number of arguments): [1,..] opens; [7,0] closes; [11,line,column] announces
    that the port's next message is the text of that cell; [4,i] cursor on line i; [12,line,x] and [10,..] settings."""
    shop_type = 0                       # MenuPortItem.s_shopType: last [14, type] received (static of the Unity interface, shared by every port)
    def __setstate__(self, state): self.__dict__.update(_renamed_interface_keys(_renamed_attributes(state)))

    staff = None               # end credits (port of category 3, Unity's StaffrollPort): {'port', 'live', 'visible', 'offset', 'ready', 'float_next'}; None before the first (old savestates have no such attribute)
    endroll = None             # DQ V / VI end credits (the same category: EndrollPort): {'port', 'type', 'live', 'state' (core/endroll.py, None until the window has given the layouts)}
    slime_touch = None         # DQ V's Slime Touch (port of category 5: SlimeTouchPort): {'port', 'live', 'state' (core/slime_touch.py, None until the window has given the prefabs), 'pending'}
    curling = None             # DQ VI's slime curling (ports of category 5: SlimeCurlingPort, a touch panel and a position converter): core/slime_curling.py

    def __init__(self):
        self.f = {}; self.waiting = {}; self.order = 0
        self.msg = {'type': 0, 'y': 0, 'feed': False, 'shake': 0, 'speaker': '', 'speaker_pending': False}      # message window (port 1): window type, canvas scroll, end-of-page cursor, speaker name ("qui"); qui_att: the next packet on the port carries this name
        self.damage = []           # damage numbers to make hop on the monsters: (monster, height, damage), emptied by the viewer
        self.system = []          # port 0 messages addressed to the host: (code, arguments...); 999 = quit (MenuPortSendQuit), 6 = portal, 4 / 5 = volumes, 7 = name size, 8 = gamepad
        self.obs = None            # optional observer (viewer): open_menu(port, type), shutdown(port), param_int/param_str/code/selection

    def win(self, port):
        if port not in self.f: self.f[port] = {'cells': {}, 'visible': False, 'cur': 0, 'order': 0, 'indicators': {}, 'type': None, 'act': False, 'req': False, 'draw': False, 'force': False, 'ign': False, 'sv': True}
        return self.f[port]

    def apply(self, entries):
        if DBG:
            for port, a in entries:
                if port in (0, 1) or port >= 12: print('MSG frame %s port %d %s' % (getattr(self, 'image', '?'), port, a[:10]), flush=True)
        for port, a in entries:
            cu = self.curling
            if cu is not None and port in cu['ports']: SC.notice(cu, port, a); continue          # SlimeCurlingPort.NoticeReceive: touch panel on / off, a point of the world to place on the screen
            sl = self.slime_touch
            if sl is not None and sl['live'] and port == sl['port']:                         # SlimeTouchPort.NoticeReceive: touch boxes, CGs drawn, CGs played
                if sl['state'] is not None: ST.notice(sl['state'], a)
                else: sl['pending'].append(list(a))
                continue
            er = self.endroll
            if er is not None and er['live'] and port == er['port']:                         # EndrollPort.NoticeReceive: [0] shows the credits from the top of the screen
                if er['state'] is not None: ER.notice(er['state'], a)
                else: er['pending'].append(list(a))
                continue
            st = self.staff
            if st is not None and st['live'] and port == st['port']:                         # StaffrollPort.NoticeReceive: [1] then a float (two shorts, low one first) = the offset of the two textures' V coordinate (+1/256 per frame = 2 pixels)
                if len(a) == 1 and a[0] == 1: st['float_next'] = True
                elif len(a) == 2 and st['float_next']: st['float_next'] = False; st['offset'] = struct.unpack('<f', struct.pack('<HH', a[0] & 0xffff, a[1] & 0xffff))[0]
                continue
            if port == 1:                                       # message window: drawn from the engine's canvas; MenuMessage.NoticeReceive: 0 type, 1 cursor, 2 shake, 3 scroll, 4 speaker name
                if self.msg['speaker_pending']:                         # [4, 1] announced a name: the next packet on the port is its text (one integer per character, like the menu cell texts)
                    self.msg['speaker_pending'] = False; self.msg['speaker'] = ''.join(chr(v & 0xffff) for v in a if v).strip()
                    continue
                if len(a) >= 2:
                    c, v = a[0], a[1]
                    if c == 0: self.msg['type'] = v
                    elif c == 1: self.msg['feed'] = (v & 0xffff) == 1
                    elif c == 3: self.msg['y'] = v
                    elif c == 4:                                # [4, 0]: no name (frame without tab); [4, 1]: the name follows (frame with tab, only for the field window: MenuMessage.NoticeReceive, command 4)
                        if (v & 0xffff) != 0: self.msg['speaker_pending'] = True
                        else: self.msg['speaker'] = ''
                continue
            if port == 0:
                if len(a) >= 2 and a[0] == 0: self.system.append(tuple(a[1:]))
                if len(a) >= 2 and 2 <= a[0] < 32:
                    if a[1] == 3:
                        self.win(a[0])['sv'] = bool(a[2]) if len(a) > 2 else True                  # [window, 3, visible]: MenuPort.SetVisible
                        if self.staff is not None and self.staff['port'] == a[0]: self.staff['visible'] = bool(a[2]) if len(a) > 2 else True
                    elif a[1] == 2:                                                                # [window, 2]: closed
                        f = self.win(a[0]); f['act'] = f['visible'] = False; f['closed'] = True          # (the engine's stop messages can set "act" again: "ferme" stays until the next creation)
                        if self.staff is not None and self.staff['port'] == a[0]: self.staff['live'] = self.staff['visible'] = False
                        if self.endroll is not None and self.endroll['port'] == a[0]: self.endroll['live'] = False
                        if self.slime_touch is not None and self.slime_touch['port'] == a[0]: self.slime_touch['live'] = False
                        if self.curling is not None and a[0] in self.curling['ports']: SC.close_port(self.curling, a[0])
                        if self.obs: self.obs.shutdown(a[0])
                    elif a[1] == 1 and len(a) >= 4:                                                # [window, 1, category, type]: creates a menu of the given type (MenuPortItemTable.Make_*)
                        if a[2] == 3 and ENGINE_V2: self.endroll = {'port': a[0], 'type': a[3], 'live': True, 'state': None, 'pending': []}          # DQ V / VI: EndrollPort (type = section of the credits)
                        elif a[2] == ST.PORT_CATEGORY and ENGINE_V2 and GM.profile().key == 'dq5': self.slime_touch = {'port': a[0], 'live': True, 'state': None, 'pending': []}          # DQ V: SlimeTouchPort
                        elif a[2] == SC.PORT_CATEGORY and ENGINE_V2 and GM.profile().key == 'dq6':                          # DQ VI: SlimeCurlingPort (type 0 = touch panel, 1 = position converter)
                            if self.curling is None or not self.curling['live']: self.curling = SC.new()
                            SC.add_port(self.curling, a[0], a[3])
                        elif a[2] == 3: self.staff = {'port': a[0], 'live': True, 'visible': False, 'offset': 0.0, 'ready': False, 'float_next': False}          # category 3 = StaffrollPort (1 = MenuPortItem, 2 = MenuMessage)
                        f = self.win(a[0]); f['type'] = a[3]; f['cells'] = {}; f['indicators'] = {}; f['cur'] = 0; f['ints'] = {}; f['layout'] = None; f['lists'] = {}; f['next_list'] = None
                        f['act'] = f['req'] = f['draw'] = f['force'] = f['ign'] = False; f['sv'] = True; f['visible'] = False; f['pending'] = None; f['plates'] = {}; f['n_codes'] = {}; f['closed'] = False
                        if self.obs: self.obs.open_menu(a[0], a[3])
                continue
            f = self.win(port)
            if f.get('closed'):                                  # MenuPortSystem.internalUpdate (D10A54): [port, 2] destroys the element (`s_ports[port] = null`), the port messages received afterwards (same frame: ports 12 to 31 are read after port 0)
                self.waiting.pop(port, None)                    # no longer have a recipient (`if (item != null) item.NoticeReceive`); before, the end of the log list (5, 6, 4) reopened the closed window for one frame: purple "simple" box
                continue
            if f['type'] == 43:                                 # Make_BattleHoppingNumber: [12, 0, 0] then [monster, height, damage] (BattleMenu_setHoppingNumbers)
                if f.get('damage_pending'):
                    f['damage_pending'] = False
                    if len(a) >= 3: self.damage.append(tuple(a[:3]))
                    continue
                if len(a) >= 3 and a[0] == 12: f['damage_pending'] = True; continue
            if port in self.waiting:                            # text of the announced cell
                lookup_key = self.waiting.pop(port); txt = ''.join(chr(v & 0xffff) for v in a if v); f['cells'][lookup_key] = txt
                if f['type'] == 25 and lookup_key == (0, 0) and GM.profile().key == 'dq6': f['next_list'] = 'veil'          # DQ VI, ICityMap b__2 (int (0, 0) = block size << 16 | blocks per row): after the picture's name, MenuPort.Receive gives one bit row per block row
                if self.obs: self.obs.param_str(port, lookup_key[0], lookup_key[1], txt)
                continue
            if f.get('next_list') is not None:                      # map window (type 25): the list of numbers that follows the engine's codes 3 (discovered places) and 5 (veil) (MenuPort.Receive of the Make_CityMap functions), not a command
                f.setdefault('lists', {})[f['next_list']] = [v & 0xffff for v in a]; f['next_list'] = None
                continue
            if f.get('arrow_pending') is not None:                  # DQ V's T'n'T arrows: the array that follows an arrow's position or turn code (sugoroku.py)
                SG.receive(f, a); continue
            if f['type'] == 31 and f.get('pending'):               # monster plate: messages that follow a code > 0 (name, then [x, y, ...])
                i, stage = f['pending']
                if stage == 'name': f['plates'][i]['name'] = ''.join(chr(v & 0xffff) for v in a if v); f['pending'] = (i, 'params')
                else:
                    f['plates'][i]['params'] = list(a); f['pending'] = None
                    if self.obs and hasattr(self.obs, 'plate'): self.obs.plate(port, i, f['plates'][i]['name'], f['plates'][i]['params'], f['plates'][i]['code'])
                continue
            if not a: continue
            cmd = a[0]
            if cmd != 7 and not f['act']:                                               # MenuPortItem.NoticeReceive: first command -> parts activated (all shown again, see ui_menu.Window.reactivate), drawing forced
                f['act'] = True; f['force'] = True; f['since_act'] = {}
                if self.obs and hasattr(self.obs, 'reactivate'): self.obs.reactivate(port)
            if cmd == 11 and len(a) >= 3: self.waiting[port] = (a[1], a[2])
            elif cmd == 1:
                if port >= 12: f['cells'] = {}; f['indicators'] = {}; f['cur'] = 0; f['ints'] = {}; f['lists'] = {}; f['next_list'] = None           # dynamic window: fresh content
                self.order += 1; f['order'] = self.order
            elif cmd == 2 and len(a) >= 3: f['grid'] = tuple(a[1:4])                 # setItemMenu: columns, rows, number of cells of the engine's cursor
            elif cmd == 6: f['inp'] = True                                              # input request (every frame while the menu waits for a key)
            elif cmd == 5: f['req'] = True                                             # draw request: the engine resends it every frame as long as the window must be visible
            elif cmd == 7: f['act'] = False                                            # stop: parts deactivated
            elif cmd == 8 and len(a) >= 2: f['ign'] = (a[1] & 0xffff) != 0            # ignore draw requests (always visible)
            elif cmd == 14 and len(a) >= 2:                                            # MenuItem::setShopType: MenuPortItem.s_shopType (static), read by the windows built afterwards (Make_Casino_GiftList: 7 rows for 6 / 13)
                v = a[1] & 0xffff; self.shop_type = v - 0x10000 if v & 0x8000 else v
                if self.obs: self.obs.shop_type = self.shop_type
            elif cmd == 4 and len(a) >= 2:
                f['cur'] = a[1]
                if self.obs: self.obs.selection(port, a[1])
            elif cmd == 10 and len(a) >= 5:                    # setItemParamInt [id, no, low, high]: 32-bit value
                v = (a[3] & 0xffff) | ((a[4] & 0xffff) << 16); v = v - (1 << 32) if v & 0x80000000 else v
                f.setdefault('ints', {})[(a[1], a[2])] = v                                   # kept to replay the window (savestates)
                if self.obs: self.obs.param_int(port, a[1], a[2], v)
            elif cmd == 12 and len(a) >= 3:
                f['indicators'][a[1]] = a[2]; f.setdefault('since_act', {})[a[1]] = a[2]; f.setdefault('n_codes', {})[a[1]] = f.get('n_codes', {}).get(a[1], 0) + 1          # number of times each code has arrived (the title: TapToStart.CountReset = code 0 of port 12)
                if f['type'] == 25 and a[1] in (3, 5): f['next_list'] = a[1]               # ICityMap: code 3 = discovered places (x + 256 y, one per number), code 5 = veil (16 numbers: one bit per 10 x 10 pixel unexplored block)
                if f['type'] == SG.ARROW_TYPE and ENGINE_V2 and GM.profile().key == 'dq5': SG.code(f, a[1], a[2])          # DQ V's T'n'T arrows: position, turn, frame (sugoroku.py)
                if f['type'] == 31 and a[1] < 4 and a[2] >= 1:                                  # MonsterNamePlate.ReceiveParameters(code >= 1)
                    f.setdefault('plates', {})[a[1]] = {'code': a[2]}; f['pending'] = (a[1], 'name')
                elif f['type'] == 31 and a[1] == 5:                                             # plate layout (number of plates)
                    f['layout'] = a[2]
                    if self.obs and hasattr(self.obs, 'arrange'): self.obs.arrange(port, a[2])
                elif self.obs: self.obs.code(port, a[1], a[2])

        for f in self.f.values():                              # MenuPortItem.Update: visible as long as the engine requests drawing every frame
            if not f['ign']:
                if f['force']: f['force'] = False; f['draw'] = f['req']
                elif f['draw'] != f['req']: f['draw'] = f['req']
            f['req'] = False; f['input'] = f.get('inp', False); f['inp'] = False
            f['visible'] = f['act'] and f['sv'] and (f['draw'] or f['ign'])

    def visible_ones(self):
        return sorted(((p, f) for p, f in self.f.items() if f['visible'] and f['cells']), key=lambda x: x[1]['order'])


def reward(v, rate):
    """A battle's experience or gold, or the damage of a hit, `v` multiplied by `rate` ("Experience gained", "Gold gained", "Damage taken by..."): rounded to the nearest, at least 1 when there was some, at most 2^31 - 1."""
    if v <= 0 or rate == 1.0: return v
    return max(1, min(0x7fffffff, int(v * rate + 0.5)))


def starts_with_vowel(name):
    """Does this name (UTF-8 bytes, ";" break markers ignored) start with a vowel, accented or not (A, E, I, O, U, Y)? For the French elision of a copy's name."""
    import unicodedata
    t = name.replace(b';', b'').decode('utf-8', 'replace')
    return bool(t) and unicodedata.normalize('NFD', t[0])[0].upper() in 'AEIOUY'


class Engine:
    end_roll = None                          # end of the game started by start_the_end: 'credits' (until isStaffRollEnd), 'the end' (until isEndTheEnd), None
    input_log = None                         # battle being recorded for its replay: one (held, decl, menu messages, scroll_mode, menu that had the input) per step, None = not recording (History starts and stops it)
    _menus_this_step = ()
    ignored_types = ()                       # (window) window types the viewer replaces by its own control (arena "Quit", type 71): they never have the input (active_menu, input_menus)
    hold_messages = False                    # battle messages held on "manual" (write_scroll_mode); the window no longer sets it: the messages pass during the jingles, as in the game
    _hold_saved = None                       # value of the message speed before the hold (-1: an imposed value, given back by scroll_mode)
    _menu_active = None                      # port of the menu that waits for the keys when the first menu message of this step is sent (recorded with the step: the replay sends a choice only to that menu)
    battle_heal = False                      # option "HP and MP restored at the end of a battle" (false = like the game): see _battle_end_heal
    battle_intro = False                     # a battle's entry cascade has just started (set by _start_battle_effect, read and cleared by History: the replay of a battle starts from there)
    exp_rate = 1.0                           # "Experience gained" (Esc menu > Difficulty): factor of a won battle's experience (1 = the game); see _reward_hooks
    gold_rate = 1.0                          # "Gold gained": the same for its gold
    casino_rate = 1.0                        # "Casino tokens won": factor of the tokens won at the slot machines, at poker and at the monster arena (1 = the game); see _casino_hooks
    box_quits = False                        # the title box asks to quit the application instead (MSG_QUIT_APP, request_return_to_title(quit_app=True))
    quit_requested = False                   # ... and its "Yes" was answered: the window quits (Game._step)
    new_game_uid = None                      # unique ID the port has just given to a new game (see _unique_id_hook), until the window takes it (Game: GameRules.update)
    party_damage_rate = 1.0                  # "Damage taken by the party": factor of the damage of a hit, spell or breath on a member (1 = the game); see _damage_hooks
    enemy_damage_rate = 1.0                  # "Damage taken by enemies": the same on a monster
    encounter_rate = 1.0                     # "Random encounters" / "Encounter rate": factor of the encounter countdown's step (0 = no random battle, 1 = the game); see _encounter_hooks
    quick_journal = None                     # adventure log of the last quick save (0 to 2), given back by its "Resume" (see _journal_hooks); kept between sessions by the window (settings)
    on_quick_save = None                     # function(log) called at each quick save (the window keeps the log in the settings)
    journal_chosen = False                   # the engine (or load_save) has set the current adventure log in this session: a savestate keeps it

    layout = None                            # engine_layout.Layout of the running game (an engine built without its library, in the tests: DQ IV's fixed layout, see _lay)

    def record_inputs(self, on):
        """Starts (True) or stops (False) the recording of the inputs; -> the steps recorded so far (the list is kept by the caller)."""
        log = self.input_log
        self.input_log = [] if on else None; self._menus_this_step = []; self._menu_active = None
        return log

    def __init__(self, apk_folder, read_folder, write_folder, language=1):
        self.log = Silent()
        self.missing_hooks = []               # settings / hooks that the engine of THIS apk did not let install (see _record_missing); the window announces them once (Game._report_missing_hooks)
        lib = zipfile.ZipFile(os.path.join(apk_folder, 'split_config.arm64_v8a.apk')).read(LIB_APK)
        pack = os.path.join(apk_folder, 'split_UnityDataAssetPack.apk')
        self.apk_data = pack
        GameCore.read_folder, GameCore.write_folder = read_folder, write_folder
        c = self.core = GameCore(lib, T.ApkFs([pack]), self.log, real_files={'/dq/assets.apk': pack})
        self.french_texts = self._french_texts(pack)
        self.E = c.export; self.L = c.LIB_BASE
        self._widen_symbols()
        if not c.run_init(): raise RuntimeError('C++ constructors failed')
        self.layout = LY.Layout(c, ENGINE_V2, GM.profile().key)      # what differs between the engines of the three games (packet numbers, game part, static variables)
        c.call(self.E('DevInit'), c.cstr('/dq/assets.apk'), c.cstr('/dq/'), language, 0)          # (4th argument: DQ V's `deviceInfo`, 0 = phone; the other engines ignore it)
        self.buf1, self.buf2 = c.malloc(0x2000), c.malloc(0x2000)          # interface <-> engine message queues (allocated by the caller)
        c.call(self.E('MenuPortInit'), 32, self.buf1, 129, self.buf2, 1089)
        BUFFER_SIZE = 0x40000
        c.inp, c.outp, c.outf, c.strb = c.malloc(BUFFER_SIZE), c.malloc(BUFFER_SIZE), c.malloc(BUFFER_SIZE), c.malloc(0x10000)
        if self.E('ParticleSystemInit'):                     # DQ V / VI: particle buffers (positions and scales, indices, vertices, uvs, colors), allocated by Unity
            c.particles = [c.malloc(0x10000) for _ in range(5)]
            c.call(self.E('ParticleSystemInit'), *c.particles)
        c.call(self.E('GameInit'), c.inp, c.outp, c.outf, c.strb)
        if self.layout.is_amazon: c.mem_w(self.layout.is_amazon, b'\x01')              # start menu: no "Sauv. cloud" (Google Play Games / Square Enix server absent on PC); the original removes the same cell for the Amazon version
        self.patch_copy_prefix()
        self.pk = PacketLog(c, self.layout)
        self.state = State()
        self.msgs_ui = []                        # engine -> interface messages (menus), last frame
        self.ui = Interface()                    # menu windows remembered
        self.frames = 0; self._countdown = 0; self._countdown_armed = -99; self._countdown_done = True      # exit sound effect countdown (see _exit_countdown)
        # map object animations (doors...): the engine waits for Unity to finish them (bit0 of byte +0x14 of the entry)
        self.anims = []                          # [(due frame, object index)]
        self.anim_duration = None                   # (object, number) -> frames before the common animation ends (given by the window, from the map data); None = ANIM_DURATION
        self.fldobj = 0
        self.bgm_alive = None                       # is a music track still playing? (given by the window: Sound.bgm_alive; without it no track ever plays, see BGM_PLAY_FLAG)
        self.map_obj_pos = None                     # map object number -> its displayed world position (x, y, z), or None (given by the window, see _map_object_messages)
        self.fbx_clips = None                       # 3D model number -> {clip: {'dur', 'wrap'}} (provided by the window); None in the function = model not yet read; function absent = no known animation (immediate end)
        self.text_asset = None                      # name -> bytes of a TextAsset of the game (given by the window): DQ V / VI end credits' layouts
        self.prefab_clips = None                    # Resources name of a prefab -> {clip: length in seconds} (given by the window): DQ V's Slime Touch
        self._messages_sent = False
        self.fbx_playing = []                       # NativeManager.m_anim_list (3D model animations launched by packet 32): slots, in launch order
        self.msg_visible = False                 # message window displayed? (MenuPort::SetVisible on the message port)
        def _sv(uc, a, sz, u):
            port = uc.reg_read(c.XREG[0]) & 0xffffffffffff
            if port and _lay(self).obj_message and port == struct.unpack('<Q', c.mem(_lay(self).obj_message + _lay(self).canvas['port'], 8))[0]:
                self.msg_visible = bool(uc.reg_read(c.XREG[1]) & 0xff)
                if not self.msg_visible: self._text_accumulator = 0.0; self.ui.msg['speaker'] = ''; self.ui.msg['speaker_pending'] = False        # MenuMessage.SetVisible(false) clears the name text (m_who)
        hv = c.export('_ZN8MenuPort10SetVisibleEb')
        c.uc.hook_add(unicorn.UC_HOOK_CODE, _sv, None, hv, hv)
        self.fielddata = 0; self._grid = None; self._grid_frame = -999; self._orient = None
        self.boot_cam_pos = None                   # the camera position the engine starts with (its first camera packet, before any game is loaded)
        self.back_to_title_state = None            # return to title: None, 'requested' (to be launched at the next step), 'dialog' (the game's Yes / No box is waiting for the answer) or 'fading' ("Yes": until the title's map); see request_return_to_title
        self._title_steps = 0
        self.request_pause = False               # the player chose "Settings" in the Miscellaneous menu: the viewer opens its Pause menu (see _divert_params)
        self._divert_params()
        def _fd(uc, a, sz, u): self.fielddata = uc.reg_read(c.XREG[0]) & 0xffffffffffff          # FieldData::drawBlock(this): the object that holds the world map
        hfd = c.export('_ZN9FieldData9drawBlockEv')
        c.uc.hook_add(unicorn.UC_HOOK_CODE, _fd, None, hfd, hfd)
        def _sca(uc, a, sz, u): self.fldobj = uc.reg_read(c.XREG[0])
        uc_hook = c.export('_ZN9FLDObject18SetCommonAnimationEii')
        c.uc.hook_add(unicorn.UC_HOOK_CODE, _sca, None, uc_hook, uc_hook)
        self._text_hooks()
        self._fade_hooks()
        self._level_hooks()
        self._forced_monster_hooks()
        self._copy_name_hooks()
        self._status_article_hooks()
        self._journal_hooks()
        self._reward_hooks()
        self._damage_hooks()
        self._encounter_hooks()
        self._casino_hooks()
        self._item_hooks()
        self._encounter_rando_hooks()
        self._unique_id_hook()
        self._game_time_hooks()
        self._magic_hooks()
        self.sensor = Sensor(self)             # dialogue history: hook on TextBuffer::cat (installed before the first frame: Unicorn keeps the blocks already translated)
        self.battle_sensor = BattleSensor.for_(self)       # battle history: read-only hooks (defeated enemies, gold, experience; see battle_history.py)
        if not self.sensor.active: self._record_missing("dialogue history: hook not installed, the history will stay empty")
        if not self.battle_sensor.active: self._record_missing("battle history: hooks not installed, the battle history will stay empty")
        for _ in range(3): self.step(0, 0)       # the title runs for a few frames (cursors initialized)
        self._create_canvas()

    def _flush_tb(self, what):
        """Flushes the blocks already translated by Unicorn after a hook that rewrites code has been placed (`ctl_flush_tb`). Without this flush, a block translated BEFORE the placement keeps running as is and the hook
        has no effect. The call was inside a `try/except: pass`: if a version of Unicorn no longer exposed it, the setting stopped working **silently**. Now it is reported, like any missing hook."""
        try: self.core.uc.ctl_flush_tb()
        except Exception as ex: self._record_missing('%s: ctl_flush_tb unavailable (%s): the setting may have no effect until the engine retranslates this code' % (what, ex))

    def _record_missing(self, text):
        """A setting or a hook could not be installed (different engine instruction: another APK version). The game continues with the original behavior; the line goes to the console and to `missing_hooks`
        (without duplicates: reapply_fixes passes through here again after a savestate is loaded), from there the window notes it in window_errors.log and warns the player with an on-screen message."""
        print(text, flush=True)
        if text not in self.missing_hooks: self.missing_hooks.append(text)

    def _text_hooks(self):
        """Text speed of dialogues and scrolling in battles (player settings, Esc menu).
        - Text: ui_MsgLoop writes, on every engine frame (30 Hz), `speed x 4` letters (speed = word at MSG_SPEED, copy of the window type's array: 1 for dialogues, 100 for battle windows that
          display their line all at once). A hook on `sxth w20, w8` replaces this number with the setting's (`text_speed`, letters per frame, fractions accumulated; 4 = like the game: the hook does nothing).
        - Battles: BattleAutoFeed reads OptionStatus.battleSpeed (g_Option + 5, the game's "Message speed" option, saved in the save file) on every message: `scroll_mode` (None = nothing is touched)
          is written at that address before each frame; a change made by the player in the game's Settings window (setBattleSpeed called from TownMenu_OPERATION_SETTING::menuUpdate) is
          reported to `scroll_callback(value)` (loading a save, for its part, goes through Profile::deliverDATA_PARTY and triggers nothing)."""
        c = self.core
        self.text_speed = 4.0; self._text_accumulator = 0.0
        self.scroll_mode = None; self.scroll_callback = None; self._scroll_origin = None
        try:
            a = c.export('_Z10ui_MsgLoopv')
            off, word, reg = (UI_MSGLOOP_V2[GM.profile().key], SXTH_W20_W10, 10) if ENGINE_V2 else (UI_MSGLOOP_WRITE, SXTH_W20_W8, 8)
            if a and struct.unpack('<I', c.mem(a + off, 4))[0] == word:        # other engine version: nothing is touched
                def _vt(uc, ad, sz, u):
                    game = uc.reg_read(c.XREG[reg]) & 0xffff                                # letters the engine is about to write (window speed word x 4)
                    v = PJ.letters_per_frame(self.text_speed, game)                      # player setting only while the game is at its standard speed (4); any other value (100 for battle windows, speed set by the game) is the game's own
                    if v == game: return                                                    # game speed: unchanged (also when the setting is "as in the game", 4)
                    self._text_accumulator += v; n = int(self._text_accumulator); self._text_accumulator -= n
                    uc.reg_write(c.XREG[reg], min(n, 4000))
                c.uc.hook_add(unicorn.UC_HOOK_CODE, _vt, None, a + off, a + off)
            else: self._record_missing('text speed: unexpected ui_MsgLoop, setting has no effect')
        except Exception as ex: self._record_missing('text speed not installed: %s' % (ex,))
        try:
            sb = c.export('_ZN6status12OptionStatus14setBattleSpeedEc'); mu = c.export('_ZN26TownMenu_OPERATION_SETTING10menuUpdateEv')
            if sb and mu:
                def _sb(uc, ad, sz, u):
                    if self.scroll_mode is not None and self.scroll_callback is not None and mu <= uc.reg_read(c.XREG[30]) < mu + 0x400: self.scroll_callback(uc.reg_read(c.XREG[1]) & 0xff)
                c.uc.hook_add(unicorn.UC_HOOK_CODE, _sb, None, sb, sb)
        except Exception as ex: self._record_missing('battle text scrolling: hook not installed: %s' % (ex,))
        try:                                                                  # kerning of letter pairs that are too far apart (MessageFont.kern)
            put = c.export('_ZN9SDrawCTRL7PutCharEt')
            if not put: raise ValueError('SDrawCTRL::PutChar not found')
            font = MessageFont(data=read_font(zipfile.ZipFile(self.apk_data))); last = {}; xy = _lay(self).canvas['cursor']          # last[canvas] = (line, write position after the last glyph, its code)
            def _kern(uc, ad, sz, u):
                obj = uc.reg_read(c.XREG[0]); code = uc.reg_read(c.XREG[1]) & 0xffff
                adv = font.advance(code)
                if adv is None: last.pop(obj, None); return                   # space, tab, line break, "$": the next glyph is the first of a word
                x, y = struct.unpack('<2H', c.mem(obj + xy, 4)); before = last.get(obj)
                if before is not None and before[:2] == (y, x):                    # glyph placed right after the previous one (on the same line, no space)
                    k = font.kern(before[2], code)
                    if k: x -= k; c.mem_w(obj + xy, struct.pack('<H', x))
                last[obj] = (y, x + adv, code)
            c.uc.hook_add(unicorn.UC_HOOK_CODE, _kern, None, put, put); self._flush_tb('text kerning')
        except Exception as ex: self._record_missing('text kerning not installed: %s' % (ex,))

    def _level_hooks(self):
        """Option "HP and MP restored on level up" (`level_heal`, false = like the game). Every level-up goes through `HaveStatus::levelup(bool)`: it recalculates the stats, then, if the argument is true
        (the case of `debugLevelup`), sets HP and MP back to their maximum (`tbz w20,` at +0x3f8: HP <- max HP, MP <- max MP). A hook on that instruction forces the bit when the option is active; the rest of the calculation is the game's."""
        c = self.core; self.level_heal = False
        try:
            a = c.export('_ZN6status10HaveStatus7levelupEb')
            off, word, reg = LEVELUP_V2[GM.profile().key] if ENGINE_V2 else (HAVESTATUS_LEVELUP_END, TBZ_W20_0, 20)
            if not a or struct.unpack('<I', c.mem(a + off, 4))[0] != word:
                self._record_missing('HP/MP on level up: unexpected HaveStatus::levelup, option has no effect'); return
            def _heal(uc, ad, sz, u):
                if self.level_heal: uc.reg_write(c.XREG[reg], uc.reg_read(c.XREG[reg]) | 1)
            c.uc.hook_add(unicorn.UC_HOOK_CODE, _heal, None, a + off, a + off)
            self._flush_tb('HP/MP on level up')
        except Exception as ex: self._record_missing('HP/MP on level up: hook not installed: %s' % (ex,))

    def _forced_monster_hooks(self):
        """(test) `force_monster` (command line `--force-monster N`, 0 = off): every RANDOM encounter is a single monster N. `Encount::brew()` builds the group in `Encount + 0x34` (monster numbers) and `+ 0x44` (counts);
        a hook on the common tail (the group is complete there) replaces it. Scripted battles (bosses, events) do not go through `brew`, so they stay as they are."""
        c = self.core; self.force_monster = 0
        if ENGINE_V2: return          # (a test option of Dragon Quest IV's engine: DQ V / VI build their groups elsewhere)
        try:
            a = c.export('_ZN7Encount4brewEv')
            u32 = lambda x: struct.unpack('<I', c.mem(x, 4))[0]
            if not a or u32(a + ENCOUNT_BREW_END) != MOV_X0_X19 or u32(a + ENCOUNT_BREW_RET) != RET:
                self._record_missing('forced monster: unexpected Encount::brew, --force-monster has no effect'); return
            def _force(uc, ad, sz, u):
                if not self.force_monster: return
                p = uc.reg_read(c.XREG[19]) & 0xffffffffffff
                c.mem_w(p + ENCOUNT_MONSTERS, struct.pack('<4I', self.force_monster, 0, 0, 0)); c.mem_w(p + ENCOUNT_COUNTS, struct.pack('<4I', 1, 0, 0, 0))
            c.uc.hook_add(unicorn.UC_HOOK_CODE, _force, None, a + ENCOUNT_BREW_END, a + ENCOUNT_BREW_END)
            self._flush_tb('forced monster')
        except Exception as ex: self._record_missing('forced monster: hook not installed: %s' % (ex,))

    def _game_time_hooks(self):
        """Save play time on real time. The engine adds 2 units (1/60 s) per step (`GameStatus::addPlayTime(2)` in `Global::NativeExec`): at x1
        (30 steps per second) that is real time, but accelerated walking and fast battle make `a` times more steps per second and the play time (and the history timestamp, which reads it) runs `a` times too fast.
        `clock_speed` (written by the viewer before each step: acceleration factor of the game clock, walking AND battle; 1 = normal) > 1: the hook skips the call (returns directly to the next instruction, nothing
        else is touched: x0 and w1 are no longer used) for the fraction 1 - 1 / a of the steps (`Passer`), evenly. At 1 (or less, slowed down): the original game, identical."""
        c = self.core; self.clock_speed = 1.0; self.game_time = RE.Passer()
        try:
            ad = c.export('_ZN6status10GameStatus11addPlayTimeEj')
            u32 = lambda a: struct.unpack('<I', c.mem(a, 4))[0]; u64 = lambda a: struct.unpack('<Q', c.mem(a, 8))[0]
            if ENGINE_V2:                                                                                      # DQ V / VI: the call is in GameUpdate (the engine's step)
                f = c.export('GameUpdate'); off = PLAYTIME_V2.get(GM.profile().key)
                if not f or not ad or off is None or u32(f + off - 8) != MOV_W1_2 or _bl_target(c, f + off) != ad:
                    self._record_missing('play time: unexpected GameUpdate, game speed changes how fast play time runs'); return
                site = f + off
            else:
                f = c.export('_ZN6Global10NativeExecEv')
                w = u32(f + PLAYTIME_BL) if f else 0
                stub = struct.unpack('<4I', c.mem(c.LIB_BASE + PLAYTIME_STUB, 16))                              # (the PLT stub: adrp x16 ; ldr x17, [x16, #0x968] ; add x16, x16, #0x968 ; br x17: its entry in the address table, 0x240968, contains addPlayTime)
                if not f or not ad or u32(f + PLAYTIME_MOV_W1) != MOV_W1_2 or w & 0xfc000000 != 0x94000000 or f + PLAYTIME_BL + ((((w & 0x3ffffff) ^ 0x2000000) - 0x2000000) << 2) != c.LIB_BASE + PLAYTIME_STUB \
                        or stub != PLAYTIME_STUB_WORDS or u64(c.LIB_BASE + PLAYTIME_GOT) != ad:
                    self._record_missing('play time: unexpected NativeExec, game speed changes how fast play time runs'); return
                site = f + PLAYTIME_BL
            def _tj(uc, a, sz, u):
                v = self.clock_speed
                if v <= 1.0: return                                                          # x1: the game as is
                p = self.game_time; p.factor = 1.0 / v
                if not p.allows(): uc.reg_write(unicorn.arm64_const.UC_ARM64_REG_PC, a + 4)       # call skipped
            c.uc.hook_add(unicorn.UC_HOOK_CODE, _tj, None, site, site)
            self._flush_tb('play time')
        except Exception as ex: self._record_missing('play time: hook not installed: %s' % (ex,))

    def _magic_hooks(self):
        """Spells of the Magic menu (outside battle) that cost MP but spend none: the engine spends MP only if the spell had an effect (`BaseAction::useMp`), but for heals it announces the success message anyway
        (the action table gives the same text for success and failure: "Les blessures de X guerissent !" at full HP). The Magic menu launches the spell through `UseAction::execUse` in two places (choosing a
        target: `TownMenu_MAGIC_CHARSELECT::menuUpdate`; group spell or no target: `TownMenu_MAGIC_ROOT::useMagicNoTarget`) then displays `param + 0x1f8` ("X lance le sort Y !") and the list `param + 0x210`.
        A hook before the call records the caster's MP, a hook after compares: if the spell costs MP (`excelParam` table), the MP has not dropped and the game announces its success message (fields 1 / 3 of the
        message table) or none, the list becomes the game's message "Mais rien ne se passe." (`MSG_NOTHING_HAPPENS`, the one of "Heal all" when nobody needs it; only once, even for a group). The game's own failure messages (Rappel, Antipoison, Evac'...) and battle do not change.
        Sound effects (measured on the real engine): casting plays sound effect 306 (the same for all spells: it always remains); the result message then plays heal sound effect 501 (`menuUpdate` + 0x110, for actions 196 to 198: Premiers secours, Soins
        partiels, Soins complets, without checking whether the heal was used): when the spell had no effect, this second sound effect is cut (a single `sub` modified before the instruction: the comparison output becomes 3, above 2). A group spell with no effect already no longer plays it (empty target loop)."""
        c = self.core; self._magic = None; self._magic_mute = False; self._magic_without_effect = False
        if ENGINE_V2: return          # DQ V / VI: the Magic menu checks the target first (MenuStatusInfo::isPlayerCondition) and says the spell would be of no use, without casting it: nothing to change
        try:
            cs, nt = c.export('_ZN25TownMenu_MAGIC_CHARSELECT10menuUpdateEv'), c.export('_ZN19TownMenu_MAGIC_ROOT16useMagicNoTargetEv')
            msg, exc = c.export('_ZN6status17BaseActionMessage12actionParam_E'), c.export('_ZN6status10excelParamE')
            u32 = lambda a: struct.unpack('<I', c.mem(a, 4))[0]; u64 = lambda a: struct.unpack('<Q', c.mem(a, 8))[0]
            def is_exec_use(a):                                                            # `bl` to the UseAction::execUse stub?
                w = u32(a); return w & 0xfc000000 == 0x94000000 and a + ((((w & 0x3ffffff) ^ 0x2000000) - 0x2000000) << 2) == c.LIB_BASE + MAGIC_EXECUSE_STUB
            sites = [(cs + MAGIC_CHARSELECT_BL, False), (nt + MAGIC_WITHOUT_TARGET_BL, True)] if cs and nt and msg and exc else []
            if not sites or not all(is_exec_use(a) for a, _ in sites) or u32(u64(msg) + 0x48 * 201 + 0x14) != MSG_NOTHING_HAPPENS:
                self._record_missing('ineffective spells: unexpected Magic menu, the success message is kept'); return
            def _before(uc, ad, sz, u):
                p = uc.reg_read(c.XREG[0]) & 0xffffffffffff; caster = u64(p); action = u32(p + MAGIC_ACTION)
                cost = c.mem(u64(exc + 0x70) + 0x48 * action + 0x3a, 1)[0]                  # UseAction::getUseMp
                self._magic = (p, caster, struct.unpack('<H', c.mem(caster + MAGIC_MP, 2))[0], cost, action) if caster and cost else None; self._magic_without_effect = False
            def place(group):
                def _after(uc, ad, sz, u):
                    m, self._magic = self._magic, None
                    if not m: return
                    p, caster, mp, cost, action = m
                    if struct.unpack('<H', c.mem(caster + MAGIC_MP, 2))[0] < mp: return        # MP spent: the spell had an effect
                    e = u64(msg) + 0x48 * action; r = u32(p + MAGIC_RESULT)
                    if r and r not in (u32(e + 4), u32(e + 0xc)): return                    # the game already has its failure message
                    c.mem_w(p + MAGIC_RESULT, struct.pack('<II', MSG_NOTHING_HAPPENS, 0))
                    if not group: self._magic_without_effect = True                              # the healing sound effect of the next step is cut off (see _sound)
                    if group: c.mem_w(self.L + MAGIC_TARGET_COUNT, b'\x01'); self._magic_mute = True      # a single copy of the message (the engine shows it once per living target)
                return _after
            for (a, group) in sites:
                c.uc.hook_add(unicorn.UC_HOOK_CODE, _before, None, a, a); c.uc.hook_add(unicorn.UC_HOOK_CODE, place(group), None, a + 4, a + 4)
            def _end(uc, ad, sz, u):                                                       # exit from the target loop: no more healing sound effect pending (menuUpdate plays one per remaining target) if nothing happened
                if self._magic_mute: self._magic_mute = False; c.mem_w(self.L + MAGIC_TARGET_COUNT, b'\x00')
            c.uc.hook_add(unicorn.UC_HOOK_CODE, _end, None, nt + MAGIC_LOOP_END, nt + MAGIC_LOOP_END)
            if u32(cs + MAGIC_HEAL_SOUND) == SUB_W8_W8_C4:
                def _sound(uc, ad, sz, u):                                                   # before `sub w8, w8, #0xc4`: the spell had no effect -> w8 = 0xc7 (result 3 > 2): no healing sound effect
                    if self._magic_without_effect: self._magic_without_effect = False; uc.reg_write(c.XREG[8], 0xc7)
                c.uc.hook_add(unicorn.UC_HOOK_CODE, _sound, None, cs + MAGIC_HEAL_SOUND, cs + MAGIC_HEAL_SOUND)
            else: self._record_missing('ineffective spells: unexpected healing sound, it is kept')
            self._flush_tb('ineffective spells')
        except Exception as ex: self._record_missing('ineffective spells: hook not installed: %s' % (ex,))

    def _fade_hooks(self):
        """Fades started by a game SCRIPT command (fade_in / fade_out: `__cmd_fade_in::initialize`, `__cmd_fade_out::initialize`, which call Global::fadeIn / fadeOutBlack... then UnitySetFade with the script's duration,
        and themselves wait for that number of frames: `isEnd` counts the frames): packet 9 receives the PJ.SCRIPT_FADE_MARK mark, and State._fade does not shorten this fade ("transition
        transitions" setting: it applies to the engine's fades, map changes..., not to those a script has fixed). Without the mark, the shortened fade would leave the screen black until the end of the script's wait."""
        c = self.core; self._script_fade = False
        if ENGINE_V2: return          # DQ V / VI: State.fade_speed stays at 1 (no "Transition speed" setting any more), so the mark would change no duration
        try:
            ini = [c.export('_ZN13__cmd_fade_in10initializeEPc'), c.export('_ZN14__cmd_fade_out10initializeEPc')]; usf = c.export('_Z12UnitySetFadei')
            if not all(ini) or not usf: self._record_missing('script fades: engine functions not found, setting applied to all fades'); return
            def end_of(a):                                                       # address after the `ret` of the function
                for k in range(80):
                    if struct.unpack('<I', c.mem(a + 4 * k, 4))[0] == 0xd65f03c0: return a + 4 * (k + 1)
                return None
            ends = [end_of(a) for a in ini]
            if None in ends or struct.unpack('<I', c.mem(usf + 8, 4))[0] != 0x5280012a:        # UnitySetFade: `mov w10,` (packet type) at +8; other engine version: nothing is touched
                self._record_missing('script fades: unexpected UnitySetFade, setting applied to all fades'); return
            def _entry(uc, ad, sz, u): self._script_fade = True
            def _exit(uc, ad, sz, u): self._script_fade = False
            def _packet(uc, ad, sz, u):
                if self._script_fade:
                    self._script_fade = False
                    uc.reg_write(c.XREG[0], (uc.reg_read(c.XREG[0]) & 0xffffffff) | PJ.SCRIPT_FADE_MARK)
            for a, f in zip(ini, ends):
                c.uc.hook_add(unicorn.UC_HOOK_CODE, _entry, None, a, a); c.uc.hook_add(unicorn.UC_HOOK_CODE, _exit, None, f - 4, f - 4)
            c.uc.hook_add(unicorn.UC_HOOK_CODE, _packet, None, usf, usf)
            self._flush_tb('script fades')                                  # (blocks already translated before the hooks)
        except Exception as ex: self._record_missing('script fades: hook not installed: %s' % (ex,))

    def _option(self, offset):
        """Address of a byte of status::g_Option (OptionStatus: +1 music volume, +2 sound effect volume (0 to 4), +5 battle message speed)."""
        ad = self.core.export('_ZN6status8g_OptionE')
        return (ad if ad else self.L + 0x25b564) + offset

    def game_options(self):
        """(music volume, sound effect volume, battle message speed) of the game's options (Settings window, saved in the save file)."""
        c = self.core
        return c.mem(self._option(1), 1)[0], c.mem(self._option(2), 1)[0], c.mem(self._option(5), 1)[0]

    def impose_scroll_mode(self, code):
        """Battle message scrolling imposed on every frame (code 0 to 3: wait of 12 / 22 / 32 / 42 frames, 4: manual); None: the game's value is returned (the one from the save, or chosen in its Settings)."""
        if code is None:
            if self.scroll_mode is not None and self._scroll_origin is not None: self.core.mem_w(self._option(5), bytes([self._scroll_origin]))
            self.scroll_mode = None; self._scroll_origin = None
        else:
            if self.scroll_mode is None: self._scroll_origin = self.game_options()[2]
            self.scroll_mode = int(code) & 0xff; self.core.mem_w(self._option(5), bytes([self.scroll_mode]))

    def write_scroll_mode(self):
        """Before each engine frame: the imposed value is written; if the game had set another one (loading a save or a savestate, Settings window), that is the one that will be returned.
        `hold_messages` (set by the window while a jingle or the victory fanfare plays in battle): the battle messages are put on "manual" (4) so that none moves on by itself; when it is
        lowered, the value from before comes back (the imposed one, or the game's own), without being taken for a change made by the game."""
        ad = self._option(5)
        if self.hold_messages:
            if self._hold_saved is None: self._hold_saved = -1 if self.scroll_mode is not None else self.core.mem(ad, 1)[0]
            self.core.mem_w(ad, bytes([SCROLL_MANUAL])); return
        if self._hold_saved is not None:
            saved, self._hold_saved = self._hold_saved, None
            if self.scroll_mode is None: self.core.mem_w(ad, bytes([saved])); return
            self.core.mem_w(ad, bytes([self.scroll_mode])); return
        if self.scroll_mode is None: return
        cur = self.core.mem(ad, 1)[0]
        if cur != self.scroll_mode: self._scroll_origin = cur
        self.core.mem_w(ad, bytes([self.scroll_mode]))

    def message_state(self):
        """(test) (open message, ui_MsgLoop state, current letter, end of line, window type speed)."""
        c = self.core; m = _lay(self).msg_state; f = _lay(self).msg; g = lambda off: struct.unpack('<H', c.mem(m + off, 2))[0]
        return c.mem(m + f['active'], 1)[0], g(0), g(f['current']), g(f['end']), g(f['speed'])

    def reapply_fixes(self):
        """Puts back the fixes that the viewer places in the engine's memory (widened locations window, no "Sauv. cloud", "Settings" button, original walking speeds). Called after a savestate is loaded
        (savestates.apply): the library's original image is reloaded first, and a savestate recorded by a version that did not yet have a fix does not contain it. Each one is idempotent."""
        self._widen_symbols()
        if _lay(self).is_amazon: self.core.mem_w(_lay(self).is_amazon, b'\x01')
        self.reset_speeds()
        self.patch_params()
        self.patch_copy_prefix()
        self.back_to_title_state = None            # a savestate loaded during the return-to-title box: the box no longer exists (engine memory replaced)

    @staticmethod
    def _french_texts(pack):
        """True if the game's texts are French (the French patch): the first monster's definite article ("@2") is French. The engine itself does not know it (the French texts run as language 1, English)."""
        try:
            d = zipfile.ZipFile(pack).read(NAMES_MSG); i = d.find(b'@2')
            return i >= 0 and (d[i + 2:i + 5] in (b'le ', b'la ') or d[i + 2:i + 4] == b"l'")
        except Exception: return False

    def patch_copy_prefix(self):
        """French texts: the name of a monster's copy of a character ("copy of ;Alina": Le feutocopieur se transforme en portrait craché d'Alina) becomes "double de ;Kiryl", "double d'Alina", the
        words the French patch uses for these copies (its monster names). The English string is checked before writing; the 2 extra bytes overwrite the start of the Korean string (language 2, never
        read with these texts). English texts: nothing. Called again after a savestate is loaded (original library image)."""
        if not self.french_texts: return
        c = self.core; ad = self.L + (COPY_PREFIX_V2.get(GM.profile().key, 0) if ENGINE_V2 else COPY_PREFIX); cur = bytes(c.mem(ad, len(COPY_PREFIX_EN)))
        if cur == COPY_PREFIX_EN: c.mem_w(ad, COPY_PREFIX_FR)
        elif cur != COPY_PREFIX_FR: self._record_missing('copy name: unexpected string in MsgVar::extract_var, a monster\'s copy of a character keeps its original name')

    def _copy_name_hooks(self):
        """French texts: elision of the copy's name before a vowel ("double de ;Alina" -> "double d'Alina", as in the French patch's monster names). Hook at MsgVar::extract_var + 0xb8, where the name
        has just been written after the prefix (x19 = its start): the name is moved back 2 bytes and x19 follows it. Instruction checked, otherwise not installed (the name keeps "de")."""
        if not self.french_texts: return
        c = self.core
        try:
            a = c.export('_ZN6MsgVar11extract_varEPci')
            if not a or struct.unpack('<I', c.mem(a + COPY_NAME_DONE, 4))[0] != LDRB_W8_X20_12:
                self._record_missing("copy name: unexpected MsgVar::extract_var, no elision (double de Alina)"); return
            pre = COPY_PREFIX_FR[:-1]; n_pre = len(pre)
            def _elide(uc, ad, sz, u):
                x19 = uc.reg_read(c.XREG[19]) & 0xffffffffffff
                if bytes(c.mem(x19 - n_pre, n_pre)) != pre: return
                raw = bytes(c.mem(x19, 64)); name = raw[:raw.find(b'\x00')] if b'\x00' in raw else raw
                if not starts_with_vowel(name): return
                c.mem_w(x19 - n_pre, COPY_ELISION + name + b'\x00')
                uc.reg_write(c.XREG[19], x19 - n_pre + len(COPY_ELISION))
            c.uc.hook_add(unicorn.UC_HOOK_CODE, _elide, None, a + COPY_NAME_DONE, a + COPY_NAME_DONE)
            self._flush_tb('copy name')
        except Exception as ex: self._record_missing('copy name: elision hook not installed: %s' % (ex,))

    def _status_article_hooks(self):
        """French texts: a status name asked with its definite article (%a02230 of the seed messages) gets it: "La Force de Ragnar augmente de 1 !", "Le maximum de PV de Ragnar augmente de
        5 points !" (the %0 before the variable capitalizes it). Hook where the name table has just written the name (NAME_FORM_DONE): only for the definite form, a table entry without an
        article and a name of STATUS_DEFINITE_FR. Instruction checked, otherwise not installed (the name stays without its article)."""
        if not self.french_texts or ENGINE_V2: return          # (DQ V / VI: the French patch gives these names their article, "@2la Force", "@2l'Agilité"...)
        c = self.core
        try:
            f = c.export('_ZN8TextHook18extractDefaultTextEPci')
            if not f or struct.unpack('<I', c.mem(f + NAME_FORM_DONE, 4))[0] != STRB_WZR_X9:
                self._record_missing('status names: unexpected TextHook code, no article (Force de Ragnar augmente)'); return
            names = {k.encode('utf-8'): v.encode('utf-8') for k, v in STATUS_DEFINITE_FR.items()}
            def _article(uc, ad, sz, u):
                if struct.unpack('<i', c.mem(self.L + MSGVAR_FORM, 4))[0] != FORM_DEFINITE or (uc.reg_read(c.XREG[21]) & 0xffffffff) // 1000 != STATUS_NAMES: return
                tab = uc.reg_read(c.XREG[23]); art, = struct.unpack('<Q', c.mem(tab + TEXTHOOK_ARTICLE, 8))
                if art and c.mem(art, 1)[0] not in (0, 0x40): return                        # (the table has its own article)
                start = uc.reg_read(c.XREG[22]); end = uc.reg_read(c.XREG[9])
                new = names.get(bytes(c.mem(start, end - start))) if 0 < end - start < 64 else None
                if new is None: return
                c.mem_w(start, new); uc.reg_write(c.XREG[9], start + len(new))
            c.uc.hook_add(unicorn.UC_HOOK_CODE, _article, None, f + NAME_FORM_DONE, f + NAME_FORM_DONE)
            self._flush_tb('status names')
        except Exception as ex: self._record_missing('status names: article hook not installed: %s' % (ex,))

    def _journal_address(self):
        """Address of the current adventure log (see JOURNAL_MENU); None for an engine that keeps it elsewhere (Dragon Quest V / VI: not in MaterielMenu_SAVE)."""
        a = None if ENGINE_V2 else self.core.export(JOURNAL_MENU)
        return a + JOURNAL_OFF if a else None

    def journal(self):
        """Current adventure log (0 to 2), -1 for a new adventure (see JOURNAL_MENU); None if unknown."""
        a = self._journal_address()
        return struct.unpack('<h', self.core.mem(a, 2))[0] if a else None

    def set_journal(self, n):
        a = self._journal_address()
        if a: self.core.mem_w(a, struct.pack('<h', n)); self.journal_chosen = True

    def _journal_hooks(self):
        """The log the player is on stays the one of the church's list (in cyan): `journal_chosen` notes that the engine set it (start menu, new adventure, church), a savestate then keeps it
        (savestates.apply); the quick save remembers its log (`quick_journal`, kept by the window in the settings) and its "Resume" puts it back. Instructions checked, otherwise not installed."""
        c = self.core; self.journal_chosen = False
        if ENGINE_V2: return          # DQ V / VI: the log in cyan is the last one saved, which the engine keeps in a file (SaveLoad::setSaveBank / getSaveBank: data9), not in memory: a savestate does not change it
        try:
            for name, off, word in JOURNAL_STORES + (RESUME_LOADED,):
                a = c.export(name)
                if not a or struct.unpack('<I', c.mem(a + off, 4))[0] != word: self._record_missing('adventure log: unexpected %s, the church may show another log in cyan' % name); return
            def _chosen(uc, ad, sz, u): self.journal_chosen = True
            for name, off, word in JOURNAL_STORES: c.uc.hook_add(unicorn.UC_HOOK_CODE, _chosen, None, c.export(name) + off, c.export(name) + off)
            def _quick_save(uc, ad, sz, u):
                if uc.reg_read(c.XREG[0]) & 0xffffffff != QUICK_SAVE_BANK: return
                self.quick_journal = self.journal()
                if self.on_quick_save: self.on_quick_save(self.quick_journal)
            sb = c.export('_ZN7profile8SaveLoad8savebankEiNS_8SAVETYPEE'); c.uc.hook_add(unicorn.UC_HOOK_CODE, _quick_save, None, sb, sb)
            def _resumed(uc, ad, sz, u):
                if uc.reg_read(c.XREG[0]) & 1 and self.quick_journal is not None: self.set_journal(self.quick_journal)
            r = c.export(RESUME_LOADED[0]) + RESUME_LOADED[1]; c.uc.hook_add(unicorn.UC_HOOK_CODE, _resumed, None, r, r)
            self._flush_tb('adventure log')
        except Exception as ex: self._record_missing('adventure log: hooks not installed: %s' % (ex,))

    def _reward_hooks(self):
        """"Experience gained" and "Gold gained": at the entry of PartyStatus::reflectBattleExp / reflectBattleGold (x0 = the party), the battle's total is multiplied before the game shares it
        out (levels gained included) and shows it. Rounded to the nearest, at least 1 when there was some. Instructions checked, otherwise not installed (the game's amounts).
        DQ V / VI: the victory message reads the totals before they are shared out: the first of BasePartyStatus::getBattleExp / getBattleGold to be called after the totals change
        multiplies both (once per battle: the values it leaves are remembered)."""
        c = self.core
        if ENGINE_V2: return self._reward_hooks_v2()
        try:
            for name, off, word, attr in REWARDS:
                a = c.export(name)
                if not a or struct.unpack('<I', c.mem(a, 4))[0] != word: self._record_missing('rewards: unexpected %s, %s has no effect' % (name, attr)); continue
                def _scale(uc, ad, sz, u, off=off, attr=attr):
                    r = getattr(self, attr)
                    if r == 1.0: return
                    p = uc.reg_read(c.XREG[0]); v, = struct.unpack('<I', c.mem(p + off, 4))
                    if v: c.mem_w(p + off, struct.pack('<I', reward(v, r)))
                c.uc.hook_add(unicorn.UC_HOOK_CODE, _scale, None, a, a)
            self._flush_tb('rewards')
        except Exception as ex: self._record_missing('rewards: hooks not installed: %s' % (ex,))

    _rewards_done = None                     # DQ V / VI: (party, experience, gold) left by the last multiplication (a new battle's totals differ: multiplied again)

    def _reward_hooks_v2(self):
        c = self.core
        try:
            addrs = [c.export(n) for n in REWARD_READERS_V2]
            if not all(addrs) or [struct.unpack('<I', c.mem(a, 4))[0] for a in addrs] != list(REWARD_WORDS_V2):
                self._record_missing('rewards: unexpected BasePartyStatus::getBattleExp / getBattleGold, exp_rate and gold_rate have no effect'); return
            def _scale(uc, ad, sz, u):
                if self.exp_rate == 1.0 and self.gold_rate == 1.0: return
                p = uc.reg_read(c.XREG[0]); ex, gd = struct.unpack('<II', c.mem(p + BATTLE_EXP_V2, 8))
                if self._rewards_done == (p, ex, gd): return
                ex, gd = reward(ex, self.exp_rate), reward(gd, self.gold_rate)
                c.mem_w(p + BATTLE_EXP_V2, struct.pack('<II', ex, gd)); self._rewards_done = (p, ex, gd)
            for a in addrs: c.uc.hook_add(unicorn.UC_HOOK_CODE, _scale, None, a, a)
            self._flush_tb('rewards')
        except Exception as ex: self._record_missing('rewards: hooks not installed: %s' % (ex,))

    def _damage_hooks(self):
        """"Damage taken by the party / by enemies": at the entry of BaseActionStatus::actionTypeDamage (x0 = the target, + 8: 0 party member, 1 monster), the value of the action for that kind of
        target is multiplied (rounded to the nearest, at least 1); at its `ret`, the action's value comes back (another target of the same spell starts again from it). The HP lost and the message
        follow. Instructions checked, otherwise not installed (the game's damage)."""
        c = self.core; held = []
        fn, ret, target, kind_off = (DAMAGE_FN, DAMAGE_RET, 0, 8) if not ENGINE_V2 else (DAMAGE_V2[GM.profile().key][:2] + (1, DAMAGE_V2[GM.profile().key][2]))      # (DQ V / VI: actionTypeDamage(actor, target))
        try:
            a = c.export(fn)
            if not a or struct.unpack('<2I', bytes(c.mem(a, 4)) + bytes(c.mem(a + ret, 4))) != DAMAGE_WORDS: self._record_missing('damage: unexpected BaseActionStatus::actionTypeDamage, the damage settings have no effect'); return
            values = {k: (c.export(name), attr) for k, (name, attr) in DAMAGE_VALUES.items()}
            def _scale(uc, ad, sz, u):
                kind = struct.unpack('<i', c.mem(uc.reg_read(c.XREG[target]) + kind_off, 4))[0]; g, attr = values.get(kind, (None, None))
                r = getattr(self, attr) if attr else 1.0
                if g is None or r == 1.0: held.append(None); return
                v, = struct.unpack('<i', c.mem(g, 4)); held.append((g, v))
                if v > 0: c.mem_w(g, struct.pack('<i', reward(v, r)))
            def _back(uc, ad, sz, u):
                h = held.pop() if held else None
                if h: c.mem_w(h[0], struct.pack('<i', h[1]))
            c.uc.hook_add(unicorn.UC_HOOK_CODE, _scale, None, a, a); c.uc.hook_add(unicorn.UC_HOOK_CODE, _back, None, a + ret, a + ret)
            self._flush_tb('damage')
        except Exception as ex: self._record_missing('damage: hooks not installed: %s' % (ex,))

    def _encounter_hooks(self):
        """"Random encounters" and "Encounter rate": at the entry of CountDown::exec (x0 = the countdown of Encount), its step value (+ 0x10) is multiplied (0: the countdown never reaches 0,
        no random battle); at its `ret`, the game's value comes back. The battles of the story do not go through it. Instructions checked, otherwise not installed (the game's encounters)."""
        c = self.core; held = []
        try:
            a = c.export(COUNTDOWN_FN_V2 if ENGINE_V2 else COUNTDOWN_FN)
            if not a or struct.unpack('<2I', bytes(c.mem(a, 4)) + bytes(c.mem(a + COUNTDOWN_RET, 4))) != COUNTDOWN_WORDS: self._record_missing('encounters: unexpected CountDown::exec, the encounter settings have no effect'); return
            def _scale(uc, ad, sz, u):
                r = self.encounter_rate
                if r == 1.0: held.append(None); return
                p = uc.reg_read(c.XREG[0]); v, = struct.unpack('<i', c.mem(p + 0x10, 4)); held.append((p, v))
                c.mem_w(p + 0x10, struct.pack('<i', int(round(v * r))))
            def _back(uc, ad, sz, u):
                h = held.pop() if held else None
                if h: c.mem_w(h[0] + 0x10, struct.pack('<i', h[1]))
            c.uc.hook_add(unicorn.UC_HOOK_CODE, _scale, None, a, a); c.uc.hook_add(unicorn.UC_HOOK_CODE, _back, None, a + COUNTDOWN_RET, a + COUNTDOWN_RET)
            self._flush_tb('encounters')
        except Exception as ex: self._record_missing('encounters: hooks not installed: %s' % (ex,))

    items_plan = None                                                       # randomizer, items: function (the game's ID) -> its plan (randomizer/items.py) or None; set by the window
    _plan_uid = None; _plan = None

    def _items_plan(self):
        """The items plan of the game being played (read once per game ID; forget_items_plan: read again)."""
        f = self.items_plan
        if f is None: return None
        uid = self.game_uid()
        if uid != self._plan_uid:
            self._plan_uid = uid
            try: self._plan = f(uid) if uid else None
            except Exception as ex: self._plan = None; print('Randomizer: items plan not made (%r)' % (ex,), flush=True)
        return self._plan

    _reward_pending = None; _jingle_pending = False; last_drop = None

    def _reward_after(self):
        """After GameUpdate: the bestiary's reward that the plan changed into gold, an item or a mini medal (its tokens were set to 0 by the hook) is given here, by the game's own functions
        (PartyStatus::giveItem: the first member alive with room, otherwise the bag; addPlayerMedalCoin; setGold)."""
        if self._jingle_pending:                                                # a gift the plan turned into an item the game finds with a jingle, whose scene has none (_item_hooks)
            self._jingle_pending = False
            try: self.core.call(self.E(SPECIAL_JINGLE[0]), SPECIAL_JINGLE[1], label='playRestart')
            except Exception as ex: self._record_missing('randomizer: jingle not played: %s' % (ex,))
        content, self._reward_pending = self._reward_pending, None
        if content is None or content[0] == 'empty': return
        c = self.core; party = c.export('_ZN6status7g_PartyE')
        if content[0] == 'gold':
            g, = struct.unpack('<I', c.mem(party + PARTY_GOLD, 4)); c.call(self.E('_ZN6status15BasePartyStatus7setGoldEj'), party, g + content[1], label='setGold')
        elif content[1] == IT.MEDAL: c.call(self.E('_ZN6status15BasePartyStatus18addPlayerMedalCoinEi'), party, 1, label='addPlayerMedalCoin')
        else: c.call(self.E('_ZN6status11PartyStatus8giveItemEi'), party, content[1], label='giveItem')
        if content[0] == 'item' and self.special_item(content[1]): c.call(self.E(SPECIAL_JINGLE[0]), SPECIAL_JINGLE[1], label='playRestart')

    def special_item(self, item):
        """Does the game find this item with its jingle (its flag + 0x1d bit 4: the key items, the Zenithian equipment, a few rare ones, the mini medal, the Yggdrasil leaf and dew)?"""
        try:
            c = self.core; t, = struct.unpack('<Q', c.mem(c.export('_ZN6status10excelParamE') + ITEM_DATA, 8))
            return bool(t) and 0 < item < 0x100 and bool(c.mem(t + ITEM_STRIDE * item + ITEM_FLAGS, 1)[0] & ITEM_SPECIAL)
        except Exception: return False

    def forget_items_plan(self):
        """A game's rules have just been written (a new game): its plans are read again at the next use."""
        self._plan_uid = None; self._plan = None; self._enc_uid = None; self._enc_plan = None

    encounters_plan = None                                                  # randomizer, encounters: function (the game's ID) -> its plan (randomizer/encounters.py) or None; set by the window
    _enc_uid = None; _enc_plan = None; _brew = None; last_brew = None; random_brews = forced_brews = frozenset()

    def _encounters_plan(self):
        f = self.encounters_plan
        if f is None: return None
        uid = self.game_uid()
        if uid != self._enc_uid:
            self._enc_uid = uid
            try: self._enc_plan = f(uid) if uid else None
            except Exception as ex: self._enc_plan = None; print('Randomizer: encounters plan not made (%r)' % (ex,), flush=True)
        return self._enc_plan

    def _item_hooks_v2(self):
        """Randomizer, items, Dragon Quest V / VI (items.py, GAMES): the same three places as DQ IV's (_item_hooks), with their own layout. The furniture table is the map's own (TownStageManager + 0 ->
        its first row: 0x14 bytes a row, the count at + 0x12); a gift's command has its arguments from + 8 (a gold or token gift: only its amount); a drop is changed where checkDropItem keeps it (w0).
        No bestiary reward (items.py). Instructions checked, otherwise not installed (the game's items)."""
        c = self.core; G = IT.game(); key = GM.profile().key
        try:
            a = c.export(FURNITURE_INIT[0]); tsm = c.export(STAGE_SINGLETON)
            if not a or not tsm or struct.unpack('<I', c.mem(a, 4))[0] != FURNITURE_INIT_V2: self._record_missing('randomizer: unexpected TownFurnitureManager::initialize, the items of the containers are not randomized')
            else:
                def _furniture(uc, ad, sz, u):
                    p = self._items_plan()
                    if not p or not p['furniture']: return
                    t = struct.unpack('<Q', c.mem(tsm, 8))[0]
                    if not t: return
                    n = c.mem(t + 0x12, 1)[0]; rows = bytes(c.mem(t, n * IT.V2_ROW))
                    for i in range(n):
                        r = i * IT.V2_ROW; msg, = struct.unpack_from('<I', rows, r); flag, = struct.unpack_from('<H', rows, r + 0xe)
                        content = p['furniture'].get(flag) if flag else None
                        if content is not None and not msg: c.mem_w(t + r + 6, struct.pack('<3H', *IT.row_values(content, True)))
                c.uc.hook_add(unicorn.UC_HOOK_CODE, _furniture, None, a, a)
            f = c.export(COMMAND_FN[0]); g = c.export(SCRIPT_SINGLETON[0])
            script = _adrp_add_target(c, g + SCRIPT_SINGLETON_V2) if f and g and struct.unpack('<I', c.mem(f, 4))[0] == COMMAND_FN[1] else None
            if script is None: self._record_missing('randomizer: unexpected CommandFunction or ScriptSystem::getSingleton, the gifts are not randomized')
            else:
                seen = {}; ops = G['ops']; A = G['args']
                def _command(uc, ad, sz, u):
                    p = self._items_plan()
                    if not p or not p['gifts']: return
                    x = uc.reg_read(c.XREG[0])
                    if struct.unpack('<H', c.mem(x, 2))[0] not in ops: return
                    base, = struct.unpack('<Q', c.mem(script + 8, 8)); size = c.heap_blocks.get(base, 0)
                    if not size or not base <= x < base + size: return
                    sid = seen.get((base, size))
                    if sid is None: sid = seen[(base, size)] = IT.script_key(size, bytes(c.mem(base, min(size, IT.SCRIPT_HEAD))))          # (the script loaded with the map: its file is recognized once)
                    hit = p['gifts'].get(sid, {}).get(x - base)
                    if hit is None: return
                    content, count, room = hit; w = IT.gift_command(content, count, room, ops, A)
                    if w is not None: c.mem_w(x, struct.pack('<H', w[0])); c.mem_w(x + (A if w[0] == ops[0] else 4), w[1])          # (an item's arguments from + 8, gold and tokens from + 4)
                c.uc.hook_add(unicorn.UC_HOOK_CODE, _command, None, f, f)
            d = c.export('_ZN6status12MonsterParty13checkDropItemEi'); off, word, reg = DROP_CHECK_V2[key]; rng = c.LIB_BASE + RNG_STATE_V2[key]
            if not d or struct.unpack('<I', c.mem(d + off, 4))[0] != word: self._record_missing('randomizer: unexpected MonsterParty::checkDropItem, the monster drops are not randomized')
            else:
                def _dropped(uc, ad, sz, u):
                    p = self._items_plan()
                    if not p or not p.get('loot'): return
                    item = uc.reg_read(c.XREG[0]) & 0xffffffff
                    if not item: return
                    state, = struct.unpack('<I', c.mem(rng, 4)); new = IT.loot(p, uc.reg_read(c.XREG[reg]) & 0xffffffff, item, state)
                    self.last_drop = (uc.reg_read(c.XREG[reg]) & 0xffffffff, item, new)
                    if new != item: uc.reg_write(c.XREG[0], new)
                c.uc.hook_add(unicorn.UC_HOOK_CODE, _dropped, None, d + off, d + off)
            self._flush_tb('randomizer')
        except Exception as ex: self._record_missing('randomizer: hooks not installed: %s' % (ex,))

    def _encounter_rando_hooks(self):
        """Randomizer, encounters and bosses (randomizer/encounters.py). At the entry of Encount::brew, a random encounter or a script's fight is told by its return address
        (FieldSystem::execEncount, Encount::execDungeon; Encount::forceBrew / forceEventBrew) and the game's random state is noted; a script's fight changes only when it is one of the
        plan's boss fights (the story's other fights and the mimics: never touched). Just before execThinning, the four groups get the plan's monsters (a random encounter's monsters
        shuffled, groups built at random, now and then a boss; a boss fight's groups): the game then fits them on the screen itself. Instructions checked, otherwise not installed
        (the game's encounters)."""
        if ENGINE_V2: return self._encounter_rando_hooks_v2()
        c = self.core; L = c.LIB_BASE
        try:
            a = c.export(BREW[0])
            if not a or any(struct.unpack('<I', c.mem(a + o, 4))[0] != w for o, w in BREW[1]): self._record_missing('randomizer: unexpected Encount::brew, the encounters are not randomized'); return
            self.random_brews = {L + r for r in RANDOM_BREWS}                   # (return addresses of a random encounter, of a script's fight; the tests add the one of their own call)
            self.forced_brews = {L + r for r in FORCED_BREWS}
            def _entry(uc, ad, sz, u):
                self._brew = None
                lr = uc.reg_read(c.XREG[30]); forced = lr in self.forced_brews
                if not forced and lr not in self.random_brews: return
                p = self._encounters_plan()
                if not p: return
                e = uc.reg_read(c.XREG[0]); state, = struct.unpack('<I', c.mem(L + RNG_STATE, 4)); zone, = struct.unpack('<i', c.mem(e + ENCOUNT_ZONE, 4))
                if forced and zone not in p.get('boss_map', {}): return                # (a script's fight: only a boss fight changes; the story's fights and the mimics stay)
                self._brew = {'plan': p, 'state': state, 'zone': zone, 'forced': forced}
            def _groups(uc, ad, sz, u):
                b, self._brew = self._brew, None
                if b is None: return
                e = uc.reg_read(c.XREG[19]); raw = struct.unpack('<8i', c.mem(e + ENCOUNT_GROUPS, 32))
                old = list(zip(raw[:4], raw[4:]))
                new = RE_.boss_groups(b['plan'], b['zone'], b['state']) if b['forced'] else RE_.groups_for(b['plan'], b['zone'], old, b['state'])
                if new != old: c.mem_w(e + ENCOUNT_GROUPS, struct.pack('<8i', *([m for m, _n in new] + [n for _m, n in new])))
                self.last_brew = (b['zone'], old, new)                                    # (what the last random encounter or boss fight became: for the tests and the log)
            c.uc.hook_add(unicorn.UC_HOOK_CODE, _entry, None, a, a); c.uc.hook_add(unicorn.UC_HOOK_CODE, _groups, None, a + BREW[2], a + BREW[2])
            self._flush_tb('randomizer, encounters')
        except Exception as ex: self._record_missing('randomizer, encounters: hooks not installed: %s' % (ex,))

    def _encounter_rando_hooks_v2(self):
        """Randomizer, encounters and bosses, Dragon Quest V / VI (encounters.py, BREW_V2): as DQ IV's (_encounter_rando_hooks); the groups are rewritten where brew has them complete
        (DQ V: before the game fits them on the screen). The caller is told by brew's return address (each checked: a `bl` to brew just before it)."""
        c = self.core; key = GM.profile().key; B = BREW_V2[key]; rng = c.LIB_BASE + RNG_STATE_V2[key]
        try:
            a = c.export('_ZN3btl7Encount4brewEv'); u32 = lambda x: struct.unpack('<I', c.mem(x, 4))[0]
            def sites(lst):
                out = set()
                for name, off in lst:
                    f = c.export(name)
                    if not f or _bl_target(c, f + off - 4) != a: return None
                    out.add(f + off)
                return out
            rand, forced = sites(B['random']) if a else None, sites(B['forced']) if a else None
            if not a or u32(a) != B['entry'] or u32(a + B['groups'][0]) != B['groups'][1] or not rand or not forced:
                self._record_missing('randomizer: unexpected Encount::brew, the encounters are not randomized'); return
            self.random_brews, self.forced_brews = rand, forced
            def _entry(uc, ad, sz, u):
                self._brew = None
                lr = uc.reg_read(c.XREG[30]); is_forced = lr in self.forced_brews
                if not is_forced and lr not in self.random_brews: return
                p = self._encounters_plan()
                if not p: return
                e = uc.reg_read(c.XREG[0]); state, = struct.unpack('<I', c.mem(rng, 4)); zone, = struct.unpack('<i', c.mem(e + ENCOUNT_ZONE, 4))
                if not zone or (is_forced and zone not in p.get('boss_map', {})): return
                self._brew = {'plan': p, 'state': state, 'zone': zone, 'forced': is_forced, 'e': e}
            def _groups(uc, ad, sz, u):
                b, self._brew = self._brew, None
                if b is None: return
                e = b['e']; ms = struct.unpack('<4i', c.mem(e + B['mon'], 16)); ns = struct.unpack('<4i', c.mem(e + B['n'], 16))
                old = list(zip(ms, ns))
                new = RE_.boss_groups(b['plan'], b['zone'], b['state']) if b['forced'] else RE_.groups_for(b['plan'], b['zone'], old, b['state'])
                if new != old: c.mem_w(e + B['mon'], struct.pack('<4i', *[m for m, _n in new])); c.mem_w(e + B['n'], struct.pack('<4i', *[n for _m, n in new]))
                self.last_brew = (b['zone'], old, new)
            c.uc.hook_add(unicorn.UC_HOOK_CODE, _entry, None, a, a); c.uc.hook_add(unicorn.UC_HOOK_CODE, _groups, None, a + B['groups'][0], a + B['groups'][0])
            self._flush_tb('randomizer, encounters')
        except Exception as ex: self._record_missing('randomizer, encounters: hooks not installed: %s' % (ex,))

    def _item_hooks(self):
        """Randomizer, items (randomizer/items.py). At the entry of TownFurnitureManager::initialize, the rows of the map's furniture table (loaded with the map, not read yet) get the contents
        of the game's plan, by "taken" flag (one flag = one container for the whole game); at the entry of CommandFunction, a gift's command (cmd_set_item / cmd_set_gold, by script file and
        place) is rewritten just before it runs. Both are written from the plan, never from what memory holds: a map set up twice, a command run twice change nothing. In
        MonsterParty::checkDropItem, a drop the game has just rolled (getDropItem's, or a chapter 3 find of getSpecialDropItem) is drawn again (items.loot) before it is kept: the rates
        stay the game's. A game without the randomizer (no plan) is never touched. Instructions checked, otherwise not installed (the game's items)."""
        if ENGINE_V2: return self._item_hooks_v2()
        c = self.core
        try:
            a = c.export(FURNITURE_INIT[0]); tsm = c.export(STAGE_SINGLETON)
            if not a or not tsm or struct.unpack('<I', c.mem(a, 4))[0] != FURNITURE_INIT[1]: self._record_missing('randomizer: unexpected TownFurnitureManager::initialize, the items of the containers are not randomized')
            else:
                def _furniture(uc, ad, sz, u):
                    p = self._items_plan()
                    if not p or not p['furniture']: return
                    b = struct.unpack('<Q', c.mem(tsm, 8))[0]
                    if not b or struct.unpack('<I', c.mem(b, 4))[0] != IT.FURNITURE_MAGIC: return
                    t = b + 4; n = c.mem(t + 0x16, 1)[0]; rows = bytes(c.mem(t, n * IT.ROW))
                    for i in range(n):
                        r = i * IT.ROW; msg, _door, flag = struct.unpack_from('<3H', rows, r + 0xe)
                        content = p['furniture'].get(flag)
                        if content is not None and rows[r + 0x14] and rows[r + 0x14] not in IT.SCENE_KINDS and not msg: c.mem_w(t + r + 6, struct.pack('<4H', *IT.row_values(content)))
                c.uc.hook_add(unicorn.UC_HOOK_CODE, _furniture, None, a, a)
            f = c.export(COMMAND_FN[0]); g = c.export(SCRIPT_SINGLETON[0])
            script = _adr_target(c, g + SCRIPT_SINGLETON[1]) if f and g and struct.unpack('<I', c.mem(f, 4))[0] == COMMAND_FN[1] else None
            if script is not None and struct.unpack('<I', c.mem(f + COMMAND_FN[2], 4))[0] != RET: script = None
            if script is None: self._record_missing('randomizer: unexpected CommandFunction or ScriptSystem::getSingleton, the gifts are not randomized')
            else:
                seen = {}; running = []
                def _command(uc, ad, sz, u):
                    p = self._items_plan()
                    if not p or not p['gifts']: return
                    x = uc.reg_read(c.XREG[0])
                    if struct.unpack('<H', c.mem(x, 2))[0] not in (IT.OP_ITEM, IT.OP_GOLD, IT.OP_COIN): return
                    base, = struct.unpack('<Q', c.mem(script + 8, 8)); size, = struct.unpack('<I', c.mem(script + 0x18, 4))
                    if not base <= x < base + size: return
                    sid = seen.get((base, size))
                    if sid is None: sid = seen[(base, size)] = IT.script_key(size, bytes(c.mem(base, min(size, IT.SCRIPT_HEAD))))          # (the script loaded with the map: its file is recognized once)
                    hit = p['gifts'].get(sid, {}).get(x - base)
                    if hit is None: return
                    content, count, room = hit; w = IT.gift_command(content, count, room)
                    if content[0] == 'item' and self.special_item(content[1]):                  # an item found with a jingle: its jingle, unless the scene already plays it (a gift of such an item)
                        op0, was = struct.unpack('<Hxxi', c.mem(x, 8))
                        if not (op0 == IT.OP_ITEM and self.special_item(was)): self._jingle_pending = True
                    if w is not None: c.mem_w(x, struct.pack('<H', w[0])); c.mem_w(x + 4, w[1]); return
                    r = c.scratch('randomizer gift', 16); c.mem_w(r, IT.redirect(content, c.mem(x + 2, 2)))          # (a gold gift that gives an item: the game runs a cmd_set_item of the port instead,
                    uc.reg_write(c.XREG[0], r); running.append((x, r))                                                #  its flags copied back to the original at the `ret`)
                def _back(uc, ad, sz, u):
                    if running: x, r = running.pop(); c.mem_w(x + 2, c.mem(r + 2, 2))
                c.uc.hook_add(unicorn.UC_HOOK_CODE, _command, None, f, f); c.uc.hook_add(unicorn.UC_HOOK_CODE, _back, None, f + COMMAND_FN[2], f + COMMAND_FN[2])
            b = c.export(BESTIARY_REWARD[0])
            if not b or any(struct.unpack('<I', c.mem(b + o, 4))[0] != w for o, w in BESTIARY_REWARD[2]): self._record_missing('randomizer: unexpected checkCompletePictureBook, the bestiary reward is not randomized')
            else:
                def _bestiary(uc, ad, sz, u):
                    p = self._items_plan(); content = p.get('bestiary') if p else None
                    if content is None: return
                    uc.reg_write(c.XREG[1], content[1] if content[0] == 'coins' else 0)                 # (tokens: the plan's amount; anything else: no token, given after the step: _reward_after)
                    if content[0] != 'coins': self._reward_pending = content
                c.uc.hook_add(unicorn.UC_HOOK_CODE, _bestiary, None, b + BESTIARY_REWARD[1], b + BESTIARY_REWARD[1])
            d = c.export(DROP_CHECK[0]); L = c.LIB_BASE
            if not d or any(struct.unpack('<I', c.mem(d + o, 4))[0] != w for o, w in DROP_CHECK[1]): self._record_missing('randomizer: unexpected MonsterParty::checkDropItem, the monster drops are not randomized')
            else:
                def _drop(monster, item):
                    p = self._items_plan()
                    if not p or not p.get('loot') or not item: return item
                    state, = struct.unpack('<I', c.mem(L + RNG_STATE, 4)); new = IT.loot(p, monster, item, state)
                    self.last_drop = (monster, item, new); return new                    # (what the last drop became: for the tests and the log)
                def _dropped(uc, ad, sz, u):                                              # (getDropItem's drop: w9, w8 its monster; also in the party, + 0x3eac)
                    item = uc.reg_read(c.XREG[9]) & 0xffffffff; new = _drop(uc.reg_read(c.XREG[8]) & 0xffffffff, item)
                    if new != item: uc.reg_write(c.XREG[9], new); c.mem_w(uc.reg_read(c.XREG[19]) + DROP_ITEM, struct.pack('<i', new))
                def _found(uc, ad, sz, u):                                                # (a chapter 3 find: w0, about to be kept; its monster already in the party)
                    item = uc.reg_read(c.XREG[0]) & 0xffffffff
                    monster, = struct.unpack('<i', c.mem(uc.reg_read(c.XREG[19]) + DROP_MONSTER, 4)); new = _drop(monster, item)
                    if new != item: uc.reg_write(c.XREG[0], new)
                c.uc.hook_add(unicorn.UC_HOOK_CODE, _dropped, None, d + DROP_CHECK[2], d + DROP_CHECK[2]); c.uc.hook_add(unicorn.UC_HOOK_CODE, _found, None, d + DROP_CHECK[3], d + DROP_CHECK[3])
            self._flush_tb('randomizer')
        except Exception as ex: self._record_missing('randomizer: hooks not installed: %s' % (ex,))

    def _casino_hooks(self):
        """"Casino tokens won": where the game computes a win (CASINO_WINS: the slot machine's payout, poker's bet x multiple, also in its double-up games, the monster arena's bet x odds),
        the amount is multiplied (rounded to the nearest, at least 1) before it is shown and paid. Bought tokens, the bestiary's reward and the bets do not change. Instructions checked one by one,
        otherwise that site is not installed (the game's amount)."""
        c = self.core
        try:
            for name, off, word, reg in (CASINO_WINS_V2[GM.profile().key] if ENGINE_V2 else CASINO_WINS):
                a = c.export(name)
                if not a or struct.unpack('<I', c.mem(a + off, 4))[0] != word: self._record_missing('casino: unexpected %s, its wins are not multiplied' % name); continue
                def _scale(uc, ad, sz, u, reg=reg):
                    r = self.casino_rate
                    if r == 1.0: return
                    if isinstance(reg, tuple):                                     # DQ V / VI slot machine: x0 = the payout, also kept in x19 + reg[1]
                        p = uc.reg_read(c.XREG[19]); v = uc.reg_read(c.XREG[0]) & 0xffffffff
                        if 0 < v < 0x80000000: v = reward(v, r); uc.reg_write(c.XREG[0], v); c.mem_w(p + reg[1], struct.pack('<I', v))
                        return
                    if reg is None:                                                # slot machine: x0 = the payout, also kept in Casino_Slot + 8 (x19), when lines were bet (+ 4)
                        p = uc.reg_read(c.XREG[19]); lines, = struct.unpack('<i', c.mem(p + 4, 4)); v = uc.reg_read(c.XREG[0]) & 0xffffffff
                        if lines > 0 and 0 < v < 0x80000000: v = reward(v, r); uc.reg_write(c.XREG[0], v); c.mem_w(p + 8, struct.pack('<I', v))
                        return
                    v = uc.reg_read(c.XREG[reg]) & 0xffffffff
                    if 0 < v < 0x80000000: uc.reg_write(c.XREG[reg], reward(v, r))
                c.uc.hook_add(unicorn.UC_HOOK_CODE, _scale, None, a + off, a + off)
            self._flush_tb('casino')
        except Exception as ex: self._record_missing('casino: hooks not installed: %s' % (ex,))

    def _unique_id_hook(self):
        """A new game: the unique ID the game has just drawn (0 to 255) is replaced by a 32-bit one, never below 256 (UNIQUE_ID_STORE), kept in `new_game_uid` for the window. Instruction checked, otherwise nothing."""
        c = self.core
        if ENGINE_V2 and GM.profile().key == 'dq6': return self._unique_id_hook_empty()
        try:
            name, off, word = UNIQUE_ID_STORE; a = c.export(name)
            if not a or struct.unpack('<I', c.mem(a + off, 4))[0] != word: self._record_missing('game rules: unexpected %s, a new game keeps the game\'s own ID' % name); return
            def _new_id(uc, ad, sz, u):
                v = random.SystemRandom().randrange(FIRST_PORT_UID, 1 << 32)          # (the game's own random numbers are not used: its draws stay those of the original)
                uc.reg_write(c.XREG[0], v); self.new_game_uid = v
            c.uc.hook_add(unicorn.UC_HOOK_CODE, _new_id, None, a + off, a + off)
            self._flush_tb('game rules')
        except Exception as ex: self._record_missing('game rules: hook not installed: %s' % (ex,))

    def _unique_id_hook_empty(self):
        """DQ VI: GameStatus::resetUniqueID (called when a new adventure is made) draws nothing; the port writes its ID there (g_Game + 8, saved and read back by the adventure log)."""
        c = self.core
        try:
            name, word = UNIQUE_ID_EMPTY_V2; a = c.export(name)
            if not a or struct.unpack('<I', c.mem(a, 4))[0] != word: self._record_missing('game rules: unexpected %s, a new game has no ID' % name); return
            def _new_id(uc, ad, sz, u):
                v = random.SystemRandom().randrange(FIRST_PORT_UID, 1 << 32)
                c.mem_w(uc.reg_read(c.XREG[0]) + 8, struct.pack('<I', v)); self.new_game_uid = v
            c.uc.hook_add(unicorn.UC_HOOK_CODE, _new_id, None, a, a)
            self._flush_tb('game rules')
        except Exception as ex: self._record_missing('game rules: hook not installed: %s' % (ex,))

    def in_game(self):
        """A game is being played: not the title screen (its map, in town part, before a game is loaded) nor the start menu (adventure logs)."""
        s = self.state
        return s.part in (PART_TOWN, PART_BATTLE, PART_FIELD, PART_CASINO, PART_BESTIARY) and not (s.part == PART_TOWN and s.game_map == TITLE_MAP)

    def game_uid(self):
        """The unique ID of the game being played (status::g_Game + 8), or None if it cannot be read."""
        try:
            g = getattr(self, '_g_game', None)
            if g is None: g = self._g_game = self.core.export('_ZN6status6g_GameE')
            return struct.unpack('<I', self.core.mem(g + 8, 4))[0] if g else None
        except Exception: return None

    PARTICLE_MAX = 196                       # ParticleSystem.c_max: 4 vertices and 6 indices per particle

    def _particle(self, v):
        """DQ V / VI cmd_particle (NativeManager): ParticleSystem.s_funcs[v & 0xff](v). The engine (cmn::CommonParticleEmitter: snow, petals, leaves, sparkles) writes the
        particles in the five arrays Unity gave ParticleSystemInit (core.particles: position and scale, indices, vertices, uvs, colors) then sends:
        0 c00_init_town / 1 c01_init_field (layer 9): creates the object (Custom/Particle, texture etc/particles) then 2; 2 c02_updateWithTriangles: position, scale,
        vertices, uvs, colors and the first v >> 8 indices; 3 c03_update: position, vertices, uvs, colors (nothing without an object: Unity's s_self is null); 4 c04_release."""
        c = self.core; st = self.state; func, n = v & 0xff, v >> 8
        if func == 4: st.particles = None; return
        if func in (0, 1): st.particles = {'field': func == 1, 'pos': (0.0, 0.0, 0.0), 'scale': (1.0, 1.0, 1.0), 'idx': np.zeros(0, np.int32)}
        p = st.particles
        if p is None or func not in (0, 1, 2, 3) or not getattr(c, 'particles', None): return
        P = c.particles; m = self.PARTICLE_MAX * 4
        ps = struct.unpack('<6f', c.mem(P[0], 24))
        p['pos'] = ps[:3]
        if func != 3:
            p['scale'] = ps[3:]
            k = min(max(n, 0), self.PARTICLE_MAX * 6)
            p['idx'] = np.clip(np.frombuffer(c.mem(P[1], 4 * k), '<i4').astype(np.int32), 0, m - 1)
        p['v'] = np.frombuffer(c.mem(P[2], 12 * m), '<f4').reshape(m, 3).copy()
        p['uv'] = np.frombuffer(c.mem(P[3], 8 * m), '<f4').reshape(m, 2).copy()
        p['col'] = np.frombuffer(c.mem(P[4], 4 * m), np.uint8).reshape(m, 4).copy()

    # FieldData::drawSymbol: the three constants of its window (add w<d>, w<base>, #imm, one after the other) in each engine -> the widened ones.
    # DQ IV and V have 114 icon slots on the Unity side (Field.c_symbolsMax): 1600 pixels. DQ VI only has 21: a window as large as that filled them with far away places
    # (in table order) and the nearby ones were no longer shown; its window (hero - 128 - a .. hero - 128 + b in x, hero - 230 - a .. hero - 230 + c in y; a, b, c the
    # constants) only grows to what a 21:9 screen shows: 365 px on the left (half width + an icon), 304 on the right, 170 below
    SYMBOL_WINDOWS = {(0xc0, 0x1c0, 0x314): (1600, 1600, 1600), (0x80, 0x180, 0x248): (1600, 1600, 1600), (0x60, 0x160, 0x26e): (0xf0, 0x1b0, 0x190)}

    def _widen_symbols(self):
        """FieldData::drawSymbol shows the world map's locations (towns, caves...) only in the window of the original portrait screen around the hero
        (DQ IV: x from -192 to +448, y from -192 to +788 pixels, plus a margin): in 16/9 the locations at the sides appeared only once very close. The three constants of
        the function (three consecutive add w<d>, w<base>, #imm) are raised (SYMBOL_WINDOWS): the window covers everything the viewer can show."""
        c = self.core
        try:
            a = c.export('_ZN9FieldData10drawSymbolEv')
            words = [struct.unpack('<I', c.mem(a + 4 * i, 4))[0] for i in range(64)]
            def adds(i):                                 # immediates of three consecutive "add w, w<same>, #imm" (32 bits, no shift) from instruction i, else None
                ws = words[i:i + 3]
                if len(ws) < 3 or any(w & 0xffc00000 != 0x11000000 for w in ws) or len({(w >> 5) & 0x1f for w in ws}) != 1: return None
                return tuple((w >> 10) & 0xfff for w in ws)
            for i in range(len(words) - 2):
                imms = adds(i)
                if imms in self.SYMBOL_WINDOWS.values(): return                  # already widened (reapply_fixes)
                if imms in self.SYMBOL_WINDOWS:
                    for k, imm in enumerate(self.SYMBOL_WINDOWS[imms]): c.mem_w(a + 4 * (i + k), struct.pack('<I', (words[i + k] & ~(0xfff << 10)) | (imm << 10)))
                    return
            self._record_missing('places window not widened: unexpected FieldData::drawSymbol, world map places only appear in the original window')        # other engine version: nothing is touched
        except Exception as ex:
            self._record_missing('places window not widened: %s' % (ex,))

    # TownMenu_OPERATION_ROOT::menuUpdate (list of the Miscellaneous menu): "cmp w8," (0x16ef28) followed by "b.ne" to the "unknown button" exit;
    # button 7 ("Settings") closes the menu and opens TownMenu_OPERATION_SETTING
    CMP_PARAMS = 0x16ef28

    def patch_params(self):
        """The "Settings" button of the Miscellaneous menu opens the viewer's Pause menu, not the game's original settings (volumes, speed, D-pad size, return to title): the comparison of the chosen button's number with 7
        is raised to 4095 (never equal: the engine treats the press as that of an unknown button, does not close the Miscellaneous menu and does not enter the "Settings" state). Without effect if already done or if the engine is another
        version. Called again after a savestate is loaded (savestates.apply puts back the library's original image)."""
        c = self.core; off, word, reg, button = self._params_spec(); ad = c.LIB_BASE + off; w = struct.unpack('<I', c.mem(ad, 4))[0]
        raised = (word & ~(0xfff << 10)) | (0xfff << 10)
        if w == word: c.mem_w(ad, struct.pack('<I', raised))       # "cmp w8, #7" -> "cmp w8, #4095"
        elif w != raised: self._record_missing("Settings button: unexpected comparison in the Misc menu, the button opens the game's original settings instead of the Pause menu")        # (raised = already patched)

    def _params_spec(self):
        """(library offset, original `cmp`, register, number of the "Settings" button) of the Miscellaneous menu's comparison."""
        return PARAMS_V2[GM.profile().key] if ENGINE_V2 else (self.CMP_PARAMS, 0x71001d1f, 8, 7)

    def _divert_params(self):
        """Patch of the "Settings" button (patch_params) and hook on the comparison: it raises `request_pause` when the chosen button is 7; the viewer then only has to open its Pause menu (Game.tick)."""
        c = self.core
        try:
            self.patch_params(); off, word, reg, button = self._params_spec(); ad = c.LIB_BASE + off
            def _p(uc, a, sz, u):
                if uc.reg_read(c.XREG[reg]) & 0xffffffff == button: self.request_pause = True
            c.uc.hook_add(unicorn.UC_HOOK_CODE, _p, None, ad, ad)
        except Exception as ex:
            self._record_missing('Settings button not redirected: %s' % (ex,))

    def _create_canvas(self):
        c = self.core
        # text canvas of the messages: Unity creates it through a port message (width, height); the handler is called directly
        self.obj_msg = _lay(self).obj_message
        shorts = [0]
        for v in (1, 0, CANVAS_L, CANVAS_H, 0): shorts += [v & 0xffff, (v >> 16) & 0xffff]
        mp = c.malloc(64); c.mem_w(mp, struct.pack('<%dH' % len(shorts), *shorts))
        c.call(self.E('_ZN9SDrawCTRL11CanvasSetupEPvPKs'), self.obj_msg, mp, label='CanvasSetup')

    def _global(self):
        return _lay(self).global_object

    def start_field(self):
        """(trial) forces the switch to the world map: Global::startField()."""
        self.core.call(self.E('_ZN6Global10startFieldEv'), self._global(), label='startField')

    def back_to_title(self):
        """Return to the title screen: the end of the "Return to title screen" button of the game's original Settings when "Yes" is answered (TownMenu_OPERATION_SETTING::menuUpdate, state 5): clears bank 4
        (autosave, killbank), closes the box, locks the player, then Global::startTitle() (30-frame fade to black started by UnitySetFade(0x101e), chapter 0, the next part is the title). To be
        invoked DURING a step (see _return_to_title_after): the fade packet must be read with those of the step, otherwise the fade is lost (the buffer is reset to zero at the next step) and the screen goes black at once."""
        c = self.core
        c.call(self.E('_ZN7profile8SaveLoad8killbankEib'), 4, 0, label='killbank')
        self._close_title_box()
        c.call(self.E('_ZN3cmn11GameManager12getSingletonEv'), label='getSingleton')
        c.call(self.E('_ZN3cmn13PlayerManager7setLockEb'), 1, label='setLock')
        c.call(self.E('_ZN6Global10startTitleEv'), self._global(), label='startTitle')

    def request_return_to_title(self, quit_app=False):
        """Pause menu > "Return to title screen": opens the game's box (its text, Yes / No) at the next step, like the button of the original Settings. -> false if a request is already in progress.
        "No" closes the box and gives control back to the player; "Yes" launches back_to_title, with the game's fade. Both continuations are played by `_return_to_title_after`, after each GameUpdate.
        Only in towns and on the world map, the two parts where the original Settings menu exists and where the box responds (trial: in battle, the box opens but receives neither keys nor
        clicks, the battle command menu keeps control; not verified in shops and the casino games). `quit_app`: the same box asks to quit the application (MSG_QUIT_APP, Esc menu >
        "Quit the game"); its "Yes" sets `quit_requested`, the window quits."""
        if self.back_to_title_state is not None or self.state.part not in (PART_TOWN, PART_FIELD): return False
        self.back_to_title_state = 'requested'; self.box_quits = bool(quit_app); return True

    def _title_box(self): return self.core.export('gTownMenu_MESSAGE') or self.core.export('_ZN4menu18gCommonMenuMessageE')          # (DQ VI names its town message box gCommonMenuMessage: the object its Settings menu opens for this question)

    def _close_title_box(self):
        c = self.core; m = self._title_box()
        if m and c.call(self.E('_ZN8MenuBase6isOpenEv'), m, label='isOpen') & 1: c.call(self.E('_ZN8MenuBase5closeEv'), m, label='close')

    step_calls = None          # (debug panel) () -> [calls to make in this step, right before GameUpdate]: the scene starts, as the scripts make them (startTown, forceBrew...)

    def _step_calls(self):
        """The step's queued calls (step_calls) -> their packets. Made before GameUpdate, their packets would go on from where the last step left the engine's
        cursors; GameUpdate rewinds the cursors to the start and writes over them (a fight's music, Global::startBattle's SoundManager::battlePlay, was lost):
        the cursors are rewound first, as GameUpdate does, and these packets read before it."""
        calls = self.step_calls() if self.step_calls is not None else None
        if not calls: return []
        c = self.core; c.mem_w(self.pk.ci, struct.pack('<Q', c.outp + 4)); c.mem_w(self.pk.cf, struct.pack('<Q', c.outf)); self.pk.begin()
        for fn in calls: fn()
        out = self.pk.packets(); self.pk.begin()
        return out

    def _return_to_title_before(self):
        """Before GameUpdate: opens the box. Same sequence of calls as state 5 of the original Settings menu (menuUpdate + 0x1ac): openMessageForMENU, addMessage(0xCBA2F), setYesNo() (cursor on "Yes"),
        setYesNoSuperCancel(false); the player is locked as in the game's menu (PlayerManager::setLock(true), given back by "No")."""
        if self.back_to_title_state != 'requested': return
        c = self.core; m = self._title_box(); self.back_to_title_state = 'dialog'
        c.call(self.E('_ZN3cmn11GameManager12getSingletonEv'), label='getSingleton')
        c.call(self.E('_ZN3cmn13PlayerManager7setLockEb'), 1, label='setLock')
        c.call(self.E('_ZN16TownMenu_MESSAGE18openMessageForMENUEv'), m, label='openMessageForMENU')
        c.call(self.E('_ZN16TownMenu_MESSAGE10addMessageEi'), m, MSG_QUIT_APP if self.box_quits else MSG_RETURN_TITLE, label='addMessage')
        c.call(self.E('_ZN16TownMenu_MESSAGE8setYesNoEv'), m, label='setYesNo')
        c.call(self.E('_ZN16TownMenu_MESSAGE19setYesNoSuperCancelEb'), m, 0, label='setYesNoSuperCancel')
        c.mem_w(m + MESSAGE_RESULT, bytes(4))                # the answer of a previous box does not count (MenuBase::menuBaseSetup resets it to 0 when the menu is added; here it may already be added: an NPC message in progress)

    def _return_to_title_after(self):
        """After GameUpdate, before the packets are read: the box's answer (TownMenu_MESSAGE + 0x10, as the original Settings menu's menuUpdate does). 2 = Non: the box is closed and control is
        given back (setLock(false), the inverse of the opening). 1 = Oui: back_to_title, in this step, therefore with its fade."""
        if self.back_to_title_state == 'fading':                                     # ("Yes": the fade, then the title; the player is free again for a step or two on the black screen, the screen buttons must not come back)
            self._title_steps += 1
            if self.state.game_map == TITLE_MAP or self.state.part not in (PART_TOWN, PART_FIELD) or self._title_steps > TITLE_RETURN_STEPS:
                self.back_to_title_state = None
                if self.state.game_map == TITLE_MAP and getattr(self, 'boot_cam_pos', None) is not None: self.state.cam_pos = self.boot_cam_pos          # (DQ V's title only gives the camera a target: its start menu, which stops the title's camera movement, showed the scene from wherever the last town's camera stood; as at launch instead. The titles of DQ IV and DQ VI place their camera themselves)
            return
        if self.back_to_title_state != 'dialog': return
        c = self.core; r = struct.unpack('<I', c.mem(self._title_box() + MESSAGE_RESULT, 4))[0]
        if r == 2:
            self.back_to_title_state = None; self._close_title_box()
            c.call(self.E('_ZN3cmn11GameManager12getSingletonEv'), label='getSingleton')
            c.call(self.E('_ZN3cmn13PlayerManager7setLockEb'), 0, label='setLock')
        elif r == 1 and self.box_quits:
            self.back_to_title_state = None; self._close_title_box(); self.quit_requested = True          # (the window quits after this step)
        elif r == 1:
            self.back_to_title_state = 'fading'; self._title_steps = 0; self.back_to_title()
        elif not c.call(self.E('_ZN8MenuBase6isOpenEv'), self._title_box(), label='isOpen') & 1:          # closed without an answer (the engine's scene took the box over: a message from more than one script, for example): the request is dropped, the player is released
            self.back_to_title_state = None
            c.call(self.E('_ZN3cmn11GameManager12getSingletonEv'), label='getSingleton')
            c.call(self.E('_ZN3cmn13PlayerManager7setLockEb'), 0, label='setLock')

    def start_the_end(self):
        """(test shortcut) The end of the game right away, in the order of the ending script of mlb1a: TownEndrollManager::setup (cmd_set_end_roll), the credits (the engine scrolls them until
        isStaffRollEnd), startTheEnd (cmd_the_end: the calligraphy "The End", meshes of packets 21 / 22), then back to the title screen (the script goes on with cmd_menu_save(2)). The steps are played by `_end_roll_after`,
        after each GameUpdate, so that the packets of startTheEnd are read with those of the step (called outside a step they were lost)."""
        c = self.core; tem = c.export('_ZN18TownEndrollManager11m_singletonE')
        c.call(c.export('_ZN18TownEndrollManager5setupEv'), tem, label='setup end roll')
        self.end_roll = 'credits'

    def story_chapter(self):
        """Current chapter of the story (StoryStatus +4): 1 to 6, 9 during the ending (set by `ev09`)."""
        c = self.core; return struct.unpack('<I', c.mem(c.export('_ZN6status7g_StoryE') + 4, 4))[0]

    def the_end_started(self):
        """TownEndrollManager::startTheEnd has been called (byte +0x43 of the manager): the credits are over, the calligraphy "The End" is being written. The viewer then stops showing the credits' last picture."""
        c = self.core; return c.mem(c.export('_ZN18TownEndrollManager11m_singletonE') + 0x43, 1)[0] == 1

    def _end_roll_after(self):
        """After GameUpdate: the end of the credits starts the end screen (what the ending script does after cmd_set_end_roll), the end of the end screen goes back to the title screen."""
        if self.end_roll is None: return
        c = self.core; tem = c.export('_ZN18TownEndrollManager11m_singletonE')
        if self.end_roll == 'credits' and c.call(c.export('_ZN18TownEndrollManager14isStaffRollEndEv'), tem, label='isStaffRollEnd') & 1:
            c.call(c.export('_ZN18TownEndrollManager11startTheEndEv'), tem, label='startTheEnd'); self.end_roll = 'the end'
        elif self.end_roll == 'the end' and c.call(c.export('_ZN18TownEndrollManager11isEndTheEndEv'), tem, label='isEndTheEnd') & 1:
            self.end_roll = None; self.back_to_title()

    def start_town(self, name_str):
        """(trial) loads a town or a dungeon: Global::startTown(name_str). Refuses a name without a map file (`assets/map/<name_str>.stg`): the original engine does not test it and crashes (null pointer in FldStage::getCameraLimitL),
        for example `za3f3`, an orphan map (`.map` and `.fld` in the graphics pack, no `.stg`)."""
        if self.core.fs is not None and self.core.fs.open('map/%s.stg' % name_str) is None: raise ValueError("unknown map: no file assets/map/%s.stg in the game data" % name_str)
        self.core.call(self.E('_ZN6Global9startTownEPKc'), self._global(), self.core.cstr(name_str.encode()), label='startTown')

    # walking speed of the hero (Fix32 per engine step, original values from libdq4core): towns (two variables), world map (Speed, plus the two local constants
    # 0x27520c and 0x275210 that FieldPlayer::move reads when moving diagonally). Running speeds up the engine clock (viewer, _loop), not these values;
    # they are put back to the original only when a savestate is loaded (see reset_speeds)
    ORIGIN_SPEEDS = (('_ZN16TownPlayerAction9walkSpeedE', 819), ('_ZN16TownPlayerAction10walkSpeed2E', 655), ('_ZN11FieldPlayer5SpeedE', 12288), (0x27520c, 8192), (0x275210, 8192))

    def reset_speeds(self):
        """Puts the hero's walking speeds back to their original values: a savestate recorded by an older version during a run contains them increased (running no longer touches them).
        The values are DQ IV's (its library's names and places; DQ V / VI have other names: nothing is written there). They are variables the engine sets when it starts (.bss: 0 in the
        library's file), so the library's image cannot give them: copied from it, they were 0 and the hero could no longer walk after a savestate was loaded."""
        c = self.core
        for n, base in self.ORIGIN_SPEEDS:
            if isinstance(n, int) and ENGINE_V2: continue
            ad = c.LIB_BASE + n if isinstance(n, int) else c.export(n)
            if ad: c.mem_w(ad, struct.pack('<i', base))

    def shops(self):
        """(current index, count) of the shops in the list of a town's map (TownImageMap::execute: two local statics, 0x2750c4 and 0x2750c8; the list is the type 24 window)."""
        c = self.core; b = c.LIB_BASE
        return struct.unpack('<hh', c.mem(b + 0x2750c4, 2) + c.mem(b + 0x2750c8, 2))

    def camera_town(self):
        """Camera of the original engine's towns (TownCamera, static object): (left limit, right limit, lock); limit = 0: free rotation, < 0: rotation forbidden, > 0: bounded angle (units of 1 / 4096 of a turn); lock: camera locked by an event."""
        c = self.core; ad = c.export('_ZN10TownCamera11m_singletonE')
        if ad is None: return 0, 0, False
        lg, ld = struct.unpack('<ii', c.mem(ad + 0x34, 8)); return lg, ld, bool(c.mem(ad + 0x3e, 1)[0])

    def camera_game(self):
        """State of the engine's TownCamera (static object), to know whether the game drives the camera: (lock +0x3e, target mode +0x48, viewpoint lock +0x40, movement in progress +0x43)."""
        c = self.core; ad = c.export('_ZN10TownCamera11m_singletonE')
        if ad is None: return 0, 0, 0, 0
        b = c.mem(ad + 0x3e, 6); return b[0], struct.unpack('<i', c.mem(ad + 0x48, 4))[0], b[2], b[5]

    CAM_ROT_TYPE = ('_ZN17TownPlayerManager16setCameraRotTypeE15CAMERA_ROT_TYPE', '_ZN3twn17TownPlayerManager16setCameraRotTypeENS_15CAMERA_ROT_TYPEE')       # DQ IV, DQ V / VI

    def _camera_rot(self):
        """(TownPlayerManager singleton, setCameraRotType, offset of the field it writes: `str w1, [x0, #imm]`, DQ IV + 0x698, DQ V + 0x1d8c), or None."""
        r = getattr(self, '_cam_rot', False)
        if r is not False: return r
        c = self.core; r = None
        ad, fn = c.export('_ZN17TownPlayerManager11m_singletonE'), next((a for a in map(c.export, self.CAM_ROT_TYPE) if a), None)
        if ad and fn:
            for k in range(0, 0x40, 4):
                w, = struct.unpack('<I', c.mem(fn + k, 4))
                if (w & 0xffc003ff) == 0xb9000001: r = (ad, fn, ((w >> 10) & 0xfff) * 4); break
                if w == RET: break
        self._cam_rot = r
        return r

    def camera_wait(self):
        """Camera rotation that the game has just accepted (TownPlayerManager + 0x698, CAMERA_ROT_TYPE): ROT_CAM_RIGHT or ROT_CAM_LEFT as long as the button held at this step has not yet turned the camera (player lock, scene, location that forbids
        turning it: the game does not touch it and the value stays 0), ROT_CAM_NORD during the return to north."""
        r = self._camera_rot()
        return 0 if r is None else struct.unpack('<i', self.core.mem(r[0] + r[2], 4))[0]

    def camera_turn(self, direction, times):
        """`times` more rotations of the game's camera (direction: ROT_CAM_RIGHT or ROT_CAM_LEFT), each exactly that of one engine step (setCameraRotType then TownPlayerManager::setCameraRot: TownCamera::rotateL / rotateR, which clamps according to the limits
        of the location, and rotation of the party's direction so that the hero keeps his heading on screen). Used by the "Camera speed" setting (the game turns only one notch per step) and by the wheel click drag. Called after a step that took the button (camera_wait)
        : the rotations of this step ARE these `times` calls (the first consumes the one that the button put on hold)."""
        c = self.core; r = self._camera_rot(); b = self.E('_ZN17TownPlayerManager12setCameraRotEv')
        if r is None or not b: return
        for _ in range(times): c.call(r[1], r[0], direction, label='camera'); c.call(b, r[0], label='camera')

    SUGOROKU_SERVICE_FADES = (11, 12, 13, 14)           # DQ V, twn::TownSugorokuManager states (+0xb48): SugorokuSystemFadeInNextMenu, FadeOutNextMenu, FadeOutNextShop, FadeInNextShop

    def _sugoroku_service_fade(self):
        """True while DQ V's T'n'T board fades to black to open or close a shop or an inn (Global::fadeOutBlack(30) / fadeInBlack(30) of the four states above): the port opens and closes
        those windows with no transition (the shop picture and the menus appear at once: a choice of the port)."""
        if GM.profile().key != 'dq5' or self.state.part != PART_TOWN: return False
        where = getattr(self, '_sugoroku_manager', False)
        if where is False:                                  # getSingleton's function-local static, read from its code (calling it would build the manager before the game does): adrp/add of the guard, then of the object
            fn = self.core.export('_ZN3twn19TownSugorokuManager12getSingletonEv')
            w = struct.unpack('<8I', self.core.mem(fn, 32)) if fn else ()
            where = self._sugoroku_manager = (PM.adrp_add(fn + 8, w[2], w[3]), PM.adrp_add(fn + 0x18, w[6], w[7])) if w else None
        if not where or None in where or not self.core.mem(where[0], 1)[0] & 1: return False          # (manager not built yet)
        mgr = where[1]
        if int.from_bytes(self.core.mem(mgr + 0xb48, 4), 'little', signed=True) not in self.SUGOROKU_SERVICE_FADES: return False
        mode = self.core.export('_ZN3twn19TownSugorokuManager14isSugorokuModeEv')          # (the state stays at its last value once the board is left)
        return not mode or bool(self.core.call(mode, mgr, label='TownSugorokuManager::isSugorokuMode') & 0xff)

    def pad_input_mode(self):
        """Input mode of the touch pad that the engine last sent (MenuPortSendPadInputMode, see pad_modes.py): -1 before the first one; always -1 for DQ IV (no modes)."""
        a = getattr(self, '_pad_mode_addr', False)
        if a is False:
            fn = self.core.export('_Z24MenuPortSendPadInputModes')
            a = self._pad_mode_addr = PM.static_address(self.core.mem(fn, 8), fn) if fn else None
        return int.from_bytes(self.core.mem(a, 2), 'little', signed=True) if a else -1

    def dpad_size(self):
        """The game's "D-pad size" option (g_pad_size): 0 Grande, 1 Moyenne (default), 2 Petite."""
        ad = self.core.export('g_pad_size'); v = self.core.mem(ad if ad else self.core.LIB_BASE + 0x24a779, 1)[0]          # (exported in the three games: DQ IV 0x24a779, DQ V 0x316079, DQ VI 0x32d109)
        return v if v in (0, 1, 2) else 1

    def awaits_touch(self):
        """True when the experience gift menu (`MaterielMenu_EXTRA_PRESENT_EXP`, a mobile addition: the king of Burland in chapter 1) is open and in its last state: once the levels are shown, `menuUpdate` (state +0x30 = 2)
        waits for a trigger from the game's pad (`dss::g_Pad + 2`, filled by `Global::NativeExec` from the 2nd argument of `GameUpdate`: the screen tap, exactly 0x100, or the buttons 0x400 to 0x2000), before closing the menu and
        giving control back to the scenario. The A key of a message (0x10) never raises it: the window then sends the tap (see Game._entries). "Open": the menu is in the array of the `MenuManager` manager (its state stays at 2 after
        closing until the next creation)."""
        if ENGINE_V2: return False                                          # (DQ V / VI have no such menu)
        try:
            c = self.core; L = self.L
            ges = int.from_bytes(c.mem(_lay(self).menu_manager, 8), 'little')
            if not ges: return False
            menu = L + MENU_GIFT_EXP
            if not any(int.from_bytes(c.mem(ges + 8 + 8 * i, 8), 'little') == menu for i in range(OPENED_MENUS)): return False
            return c.mem(menu + MENU_GIFT_EXP_STATE, 1)[0] == MENU_GIFT_EXP_WAIT
        except Exception: return False

    def slot_paying(self):
        """Is the casino's slot machine paying out a win (MaterielMenu_SLOT in its state 3, showEffect: one token a frame, Casino_Slot::cashCoin)? A screen tap pays the rest at once
        (cashAllCoin when the game's pad has a trigger, g_Pad + 2: any in DQ V, 0x200 (the tap 0x100, GameUpdate) or 0x800 in DQ VI, any of the tap and the buttons in DQ IV)."""
        if self.state.part != PART_CASINO: return False
        a = getattr(self, '_slot_state', False)
        if a is False:
            sym, off = SLOT_MENU.get(GM.profile().key, (None, 0))
            base = self.core.export(sym) if sym else 0
            a = self._slot_state = (base + off) if base else None
        try: return bool(a) and struct.unpack('<i', self.core.mem(a, 4))[0] == SLOT_PAYING
        except Exception: return False

    def flags(self):
        """Global.Flag: first word of the engine's output (bit 3: touch gamepad present; 4 player lock, 0x20 map allowed, 0x40 interface locked...)."""
        return struct.unpack('<I', self.core.mem(self.core.outp, 4))[0]

    def _effect_flag(self, active):
        """Bit 1 of Global+0xd8: Unity (Capture.ScreenEffect) raises it at the start of a screen effect and lowers it at the end; the engine waits for it to drop."""
        ad = self.core.outp; v = struct.unpack('<I', self.core.mem(ad, 4))[0]
        self.core.mem_w(ad, struct.pack('<I', (v | 2) if active else (v & ~2)))

    def _start_battle_effect(self, before):
        """Packet 34 (value 4): the screen shatters into pieces (BATTLE_EFFECT_FRAMES frames) while the engine waits; then the screen stays black until the battle scene has loaded."""
        if self.state.effect is not None: return
        self.battle_intro = True                                                                   # (History takes the state the replay of this battle starts from)
        self.state.effect = {'n': 0, 'duration': PJ.fade_duration(BATTLE_EFFECT_FRAMES, State.fade_speed)}; self._effect_flag(True)      # the cascade follows "Transition speed" like the fades: 57 frames at x1, 57 / factor afterwards (the simulation advances 57 frames in total, see Game._draw_effect)
        if self.state.load_wait is None: self.state.load_wait = {'base': before, 'n': 0}

    def _battle_effect(self):
        """One game frame: advances the battle start effect, lowers the flag at the end (the engine resumes on the next frame); counts the loading wait. At its end, Capture.BattleEffect (the three games)
        calls Fade.SetFadeOutBlack(0): the screen is black at once and the fade in progress is over. DQ V / VI start a 125-frame fade to black with the cascade (FieldCommandEncount::exec, UnitySetFade(0x107d))
        and change to the battle part only once it is over: without this end, the screen stayed black 68 frames longer than on the phone.
        A fight started by a script (Encount::forceEventBrew: the bosses) in DQ V / VI: the battle part starts two frames after the cascade's start and gives its own fade-in during
        the cascade; that fade-in is the scene's last one, so the cascade then ends without the black (it left the battle on a black screen)."""
        ef = self.state.effect
        if ef is not None:
            ef['n'] += 1
            if ef['n'] >= ef.get('duration', BATTLE_EFFECT_FRAMES):
                self._effect_flag(False); self.state.effect = None
                if not ef.get('fade_in'): self.state.sharp_black_fade()
        ac = self.state.load_wait
        if ac is not None:
            ac['n'] += 1
            if ac['n'] > LOAD_WAIT_MAX + BATTLE_EFFECT_FRAMES: self.state.load_wait = None

    def _start_screen_effect(self, value):
        """Packet 34, values 3 (trip: ailes de Chimere), 5 (lamp: Ranaruta) and 6 (Riremito): Capture.ScreenEffect raises bit 1 of Global.Flag, which the engine waits for, and starts a coroutine that lasts
        EF.FRAMES[...] frames (63 to 64 at departure, 45 at the arrival of a trip); the window draws the effect (window/capture_effects.py), see _screen_effect. Bit 7 of the value (DQ V / VI: bit 15, EFFECT_FLAG): bReverse (trip: arrival) or bMorning
        (lamp: morning tint). Like the battle cascade, the effect follows "Transition speed" (fewer engine frames)."""
        kind = EF.KINDS.get(value & EFFECT_KIND)
        if kind is None or self.state.screen_effect is not None or (kind in EF.V2_ONLY and not ENGINE_V2): return
        bit7 = bool(value & EFFECT_FLAG); inverse = kind in ('tabi', 'ido', 'pegasus') and bit7; frames = EF.frames_of(kind, inverse)          # (DQ V / VI: bit 15)
        awaited = kind not in EF.NOT_AWAITED                               # (DQ VI Pegasus: the engine does not wait for it, its duration stays the game's)
        self.state.screen_effect = {'n': 0, 'duration': PJ.fade_duration(frames, State.fade_speed) if awaited else frames, 'frames': frames, 'kind': kind, 'inverse': inverse, 'morning': kind == 'lamp' and bit7}
        if awaited: self._effect_flag(True)

    def _spotlight(self, v, f):
        """DQ V / VI Capture.ScreenEffect, effect 7: bit 15 = Spotlight.Release; otherwise, reading the packet's floats in this order (NativeManager.GetFloatEx): bit 8 = Spotlight.Create(torch = bit 11,
        x, y, z) (nothing if one exists already: its floats are still read), bit 9 = SetSideColor(r, g, b, a), bit 10 = SetLightColor(r, g, b, a); then always SetSize(value >> 16) and SetVisible(bit 12).
        Spotlight's defaults: white colors, size 64. The window draws it over the scene (window/spotlight.py)."""
        e = self.state; v &= 0xffffffff; f = [float(x) for x in f]
        if v & 0x8000: e.spotlight = None; return                   # (DQ V / VI only: their flag bit)
        sp = e.spotlight
        if v & 0x100:
            if sp is None and len(f) >= 3: sp = {'pos': tuple(f[:3]), 'torch': bool(v & 0x800), 'size': 64, 'light': (1.0, 1.0, 1.0, 1.0), 'side': (1.0, 1.0, 1.0, 1.0), 'visible': False}
            f = f[3:]
        if sp is None: return
        if v & 0x200 and len(f) >= 4: sp['side'] = tuple(f[:4]); f = f[4:]
        if v & 0x400 and len(f) >= 4: sp['light'] = tuple(f[:4]); f = f[4:]
        sp['size'] = v >> 16; sp['visible'] = bool(v & 0x1000)
        e.spotlight = sp

    def _screen_effect(self):
        """One game frame: advances the Capture effect in progress. The immediate black (lamp, Riremito: Fade.SetFadeOutBlack(0)) and the fade-in of a trip's arrival (Fade.SetFadeIn(30)) are started at their call of
        MoveNext; at the end, the flag drops (the engine resumes on the next frame). End of a trip's departure: the screen stays black (State.black_screen) until the end of the arrival. The black screen is abandoned if it
        lasts too long (safeguard) or if the game returns to the title."""
        e = self.state
        if e.black_screen is not None:
            e.black_screen['n'] += 1
            if e.black_screen['n'] > BLACK_SCREEN_MAX or e.part == PART_INITIAL or e.game_map == TITLE_MAP: e.black_screen = None
        ef = e.screen_effect
        if ef is None: return
        ef['n'] += 1; kind = ef['kind']; calls = EF.calls_made(ef['n'], ef['duration'], ef['frames'])
        if kind in EF.BLACK_CALL and calls >= EF.BLACK_CALL[kind] and not ef.get('black'): ef['black'] = True; e.sharp_black_fade()
        if kind == 'ido':                                                    # DQ VI well: its own Fade commands (render/ido_effect.py)
            for call, cmd, n in EF.IDO_FADES[ef['inverse']]:
                if calls >= call and not ef.get(cmd):
                    ef[cmd] = True
                    if cmd == 'black': e.sharp_black_fade()
                    elif cmd == 'in': e.current_fade_in(PJ.fade_duration(n, State.fade_speed))
                    else: e.current_fade_out_black(PJ.fade_duration(n, State.fade_speed))
        elif ef['inverse'] and calls >= EF.FADE_IN_CALL and not ef.get('fade_in'): ef['fade_in'] = True; e.current_fade_in(EF.FADE_IN)
        if ef['inverse'] and calls >= ef['frames']: e.black_screen = None            # last call of the arrival (state 4): the game's camera is given back to the game, the scene reappears
        if ef['n'] >= ef['duration']:
            self._effect_flag(False); e.screen_effect = None
            if kind == 'pegasus':                                            # its last call: Fade.SetFadeOutBlack(0) (departure) or Fade.SetFadeIn(0) (arrival)
                if ef['inverse']: fd = e.fade; fd['end'] = 0.0; fd['a'] = 0.0; fd['n'] = 0; fd['step'] = 0.0
                else: e.sharp_black_fade()
            if kind == 'tabi' and not ef['inverse']: e.black_screen = {'n': 0}

    def _map_name(self):
        s = _lay(self).map_name(self.core)
        return s.split('/')[-1]

    def step(self, held=0, decl=None):
        """One game frame. held = keys held down (walking); decl = keys that have just been pressed (buttons, menus).
        Screen fades are replayed in State (tick_fade). -> list of packets"""
        if decl is None: decl = held
        if self.end_roll is not None: held = decl = 0                      # the end of the game started by start_the_end: the keys do nothing (the real ending script has the player locked)
        if self.input_log is not None:                                  # what the viewer gives the engine at this step: keys, menu messages sent since the last step, imposed message speed, menu that had the input
            self.input_log.append((held, decl, self._menus_this_step, self.scroll_mode, self._menu_active)); self._menus_this_step = []; self._menu_active = None
        c = self.core
        w = struct.unpack('<I', c.mem(c.outp, 4))[0]            # bit 0: "a fade is in progress" (Fade.FadePlane.CheckFade: remaining frames > 0). The engine raises it itself when it starts a fade (UnitySetFade) then waits for it to drop
        w2 = (w | 1) if self.state.fade_in_progress() else (w & ~1)      # (Global::IsFadeEnd: inn, chapter titles, loading screen, menu openings, whistle...)
        if w2 & BGM_PLAY_FLAG and not (self.bgm_alive is not None and self.bgm_alive()): w2 &= ~BGM_PLAY_FLAG          # DQ VI, Sound.SystemUpdate: the music track no longer exists (a jingle has reached its end, the music was stopped)
        if w2 != w: c.mem_w(c.outp, struct.pack('<I', w2))
        self._finish_anims(); self._send_messages(self._camera_message() + (self._finish_fbx_anims() or b'') + self._map_object_messages())
        self._battle_effect(); self._screen_effect()
        if os.environ.get('DQ_CALLHOP'):                                       # (test) "image:monstre:hauteur:degats,...": calls BattleMenu_setHoppingNumbers like the engine after a hit
            for spec in os.environ['DQ_CALLHOP'].split(','):
                im, ch, hh, nn = [int(x) for x in spec.split(':')]
                if self.frames == im: self.ui.image = self.frames; c.call(self.E('_Z27BattleMenu_setHoppingNumbersss'), ch, hh, nn, label='hop')
        self.pk.begin()
        if self.scroll_mode is not None or self.hold_messages or self._hold_saved is not None: self.write_scroll_mode()
        self._return_to_title_before()
        early = self._step_calls()
        r = c.call(self.E('GameUpdate'), held, decl, label='GameUpdate')
        if self._messages_sent: c.mem_w(c.inp, struct.pack('<i', -1)); self._messages_sent = False          # Unity rewrites the list every frame: a message is only valid for the frame in which it is sent
        self._return_to_title_after(); self._end_roll_after(); self._reward_after()
        if MSGDBG: self._log_message()
        if r is None: raise RuntimeError('the engine stopped:\n' + '\n'.join(self.log.lines[-12:]))
        pkt = early + self.pk.packets()
        if os.environ.get('DQ_PKT') and self.state.part == PART_BATTLE:       # (test) log of the battle packets: frame, [(type, ints...)]
            with open(os.environ['DQ_PKT'], 'a') as fj:
                fj.write('%d %s\n' % (self.frames, [(t, a[:2]) if t != 22 else (22, a[0], a[1], [round(x, 2) for x in f[:3]], [round(min(f[3 + 5 * i + 2] for i in range(a[1])), 2), round(max(f[3 + 5 * i + 2] for i in range(a[1])), 2), round(min(f[3 + 5 * i + 3] for i in range(a[1])), 2), round(max(f[3 + 5 * i + 3] for i in range(a[1])), 2)]) for t, a, f in pkt if t not in (12, 13, 14)]))
        if os.environ.get('DQ_PKTALL'):       # (temporary test) all packets except 22/28/29
            with open(os.environ['DQ_PKTALL'], 'a') as fj:
                for t, a, f in pkt:
                    if t not in (22, 28, 29): fj.write('%d p=%#x %d %s %s\n' % (self.frames, self.state.part, t, a[:6], [round(x, 3) for x in f[:6]]))
        self.ui.image = self.frames
        self._consume_messages()
        self._staffroll_after()
        self._endroll_after()
        self._slime_touch_after()
        for p in pkt:
            if p[0] == 5 and p[1]:
                d = self.anim_duration(p[1][0] & 0xffff, (p[1][0] >> 16) & 0xffff) if self.anim_duration else None
                self.anims.append((self.frames + (d or ANIM_DURATION), p[1][0] & 0xffff))
        before = self.state.loaded_maps
        self.state.image = self.frames
        self.state.no_fade = any(t == 9 for t, a, f in pkt) and self._sugoroku_service_fade()
        self.state.apply(pkt, self._map_name)
        if getattr(self, 'boot_cam_pos', None) is None and self.state.cam_pos is not None: self.boot_cam_pos = tuple(self.state.cam_pos)
        for t, a, f in pkt:
            if t == LY.PARTICLE and a: self._particle(a[0])
        if self.state.loaded_maps > before or any(p[0] == 27 for p in pkt): self._grid = None; self._world_last = None          # new map or world map loaded again (another world: its own grid): the world grid is read again (never kept from before the load)
        if self.end_roll == 'the end': self.state.cam_free = True          # the end screen's camera: the engine sends a position and angles (free camera, as for the title); in the ending's own scene that mode is already on, in the shortcut's throne room it is not
        self.fbx_playing += [p[1][0] & 0xff for p in pkt if p[0] == 32 and p[1] and p[1][0] & 0x8000 and (p[1][0] & 0xff) in self.state.fbx]      # cmd_fbx : FbxObject.PlayAnimation puis m_anim_list.Add(emplacement | 0x1000)
        self._exit_countdown()
        for t, a, f in pkt:
            if t == 34 and a and (a[0] & EFFECT_KIND) == BATTLE_EFFECT: self._start_battle_effect(before)
            elif t == 34 and a and ENGINE_V2 and (a[0] & EFFECT_KIND) == SCREEN_SEPIA: self.state.screen_sepia = not a[0] & EFFECT_FLAG
            elif t == 34 and a and ENGINE_V2 and (a[0] & EFFECT_KIND) == SCREEN_SPOTLIGHT: self._spotlight(a[0], f)
            elif t == 34 and a and ENGINE_V2 and (a[0] & EFFECT_KIND) == SCREEN_MASK: self.state.screen_mask = not a[0] & 0x8000
            elif t == 34 and a: self._start_screen_effect(a[0])
        ac = self.state.load_wait
        if ac is not None and self.state.effect is None and self.state.loaded_maps > ac['base']: self.state.load_wait = None      # the battle scene has arrived
        self.state.update_loading()
        self.state.tick_fade(); self.state.tick_transition()
        prev = self.state.part
        self.state.part = _lay(self).part(c)
        if prev == PART_BATTLE and self.state.part != PART_BATTLE:
            self.state.return_window = BATTLE_RETURN_WINDOW      # end of battle: the fade-in that follows on the map is the return one (the game's duration)
            if self.battle_heal: self._battle_end_heal()
        if self.state.part == PART_BATTLE and prev != PART_BATTLE and self.state.load_wait is None and self.state.loaded_maps <= before:
            self.state.load_wait = {'base': before, 'n': 0}      # battle without a screen effect (scenario...): the previous scene must not appear before the battle's scene has loaded
        if self.state.part != PART_FIELD: self.state.field = None; self.state.world = None; self.state.field_meshes = {}
        elif self.state.field_meshes: self._world_mesh()
        else: self._read_world()
        self.frames += 1
        return pkt

    def _battle_end_heal(self):
        """Option "HP and MP restored at the end of a battle" (`battle_heal`, false = like the game). When the battle part ends (battle won, fled or lost: the game has already given its experience and its
        level-ups), each member of the party still standing (HP > 0, the wagon included) gets all their HP and all their MP back, through the game's own HaveStatus::setHp / setMp (the same refill as
        HaveStatus::levelup with a true argument). A fallen member stays fallen and keeps their MP; poison and the other states are not touched (PartyStatus::recovery, the inn's, would also clear them
        and refill the MP of the fallen). A monster arena fight of the casino (Global + GLOBAL_STADIUM = 1) is not the party's battle: nothing happens."""
        c = self.core; X = c.export
        if ENGINE_V2: return self._battle_end_heal_v2()
        try:
            if struct.unpack('<I', c.mem(self._global() + GLOBAL_STADIUM, 4))[0] == 1: return
            party, orig = X('_ZN6status7g_PartyE'), X('_ZN6status9PartyData15originalPlayer_E')
            get_hp, get_hp_max, get_mp_max = X('_ZNK6status10HaveStatus5getHpEv'), X('_ZNK6status10HaveStatus8getHpMaxEv'), X('_ZNK6status10HaveStatus8getMpMaxEv')
            set_hp, set_mp = X('_ZN6status10HaveStatus5setHpEt'), X('_ZN6status10HaveStatus5setMpEt')
            for i in struct.unpack('<%dI' % PARTY_SLOTS, c.mem(party + PARTY_MEMBERS, 4 * PARTY_SLOTS)):
                if not i: continue
                hs = orig + PLAYER_STRIDE * i + PLAYER_HAVESTATUS
                if not c.call(get_hp, hs, label='getHp') & 0xffff: continue
                c.call(set_hp, hs, c.call(get_hp_max, hs, label='getHpMax') & 0xffff, label='setHp')
                c.call(set_mp, hs, c.call(get_mp_max, hs, label='getMpMax') & 0xffff, label='setMp')
        except Exception as ex: self._record_missing('HP/MP at the end of a battle: not applied: %s' % (ex,))

    def _battle_end_heal_v2(self):
        """DQ V / VI: the same refill, through the party's list (PartyStatusData: every member, the wagon included; getOrder -1 = empty) and HaveStatusInfo's own functions. A battle that
        ends in the casino part is not the party's; a monster arena fight is not told apart from the party's battles (the members standing get their HP and MP back after it too)."""
        c = self.core; X = c.export
        if self.state.part == PART_CASINO: return
        try:
            pd_off, hsi_off, set_hp_name, set_mp_name = HEAL_V2[GM.profile().key]
            pd = X('_ZN6status7g_PartyE') + pd_off
            count, order, player = X('_ZN6status15PartyStatusData8getCountEv'), X('_ZN6status15PartyStatusData8getOrderEi'), X('_ZN6status15PartyStatusData22getPlayerStatusForCtrlEi')
            get_hp, get_hp_max, get_mp_max = X('_ZNK6status14HaveStatusInfo5getHpEv'), X('_ZNK6status14HaveStatusInfo8getHpMaxEv'), X('_ZNK6status14HaveStatusInfo8getMpMaxEv')
            set_hp, set_mp = X(set_hp_name), X(set_mp_name)
            n = c.call(count, pd, label='getCount') & 0xffffffff
            for i in range(min(n, 16)):
                ctrl = c.call(order, pd, i, label='getOrder') & 0xffffffff
                if ctrl == 0xffffffff: continue
                hs = c.call(player, pd, ctrl, label='getPlayerStatusForCtrl') + hsi_off
                if not c.call(get_hp, hs, label='getHp') & 0xffff: continue
                c.call(set_hp, hs, c.call(get_hp_max, hs, label='getHpMax') & 0xffff, label='setHp')
                c.call(set_mp, hs, c.call(get_mp_max, hs, label='getMpMax') & 0xffff, label='setMp')
        except Exception as ex: self._record_missing('HP/MP at the end of a battle: not applied: %s' % (ex,))

    def _staffroll_after(self):
        """What Unity does for the credits' port (StaffrollPort): at the creation of the port two 512 x 512 RGBA textures are made and their identifiers sent to the engine (`512, id0, id1`, each integer in two shorts),
        then at each frame UnityRenderEvent(1) makes the engine upload the next rows of text into them (glTexSubImage2D, copied by the emulator into `ui.staff['tex']`)."""
        st = self.ui.staff
        if st is None or not st['live']: return
        c = self.core
        if not st['ready']:
            st['ready'] = True; st['tex'] = []
            for t in STAFF_TEX:
                img = np.zeros((STAFF_SIZE, STAFF_SIZE, 4), np.uint8); img[..., 3] = 255; st['tex'].append(img)
            self.send_menu(st['port'], STAFF_SIZE, 0, STAFF_TEX[0], 0, STAFF_TEX[1], 0)
        for t, img in zip(STAFF_TEX, st['tex']): c.gl_textures[t] = img                      # (also after a savestate has been loaded: the emulator's table is not in it)
        c.call(c.export('UnityRenderEvent'), 1, label='UnityRenderEvent')

    def _endroll_after(self):
        """DQ V / VI: what Endroll does at each frame for the credits' port (core/endroll.py): the column goes up and the messages for the engine are dropped into the port's queue.
        The layouts come from the game's TextAssets (`text_asset`, given by the window); without them the credits stay hidden and send nothing."""
        er = self.ui.endroll
        if er is None or not er['live']: return
        if er['state'] is None:
            if self.text_asset is None: return
            er['state'] = ER.setup(GM.profile().key, er['type'], self.text_asset)
            if er['state'] is None: er['live'] = False; self._record_missing('end credits: no layout for section %s' % er['type']); return
            for a in er.pop('pending', []): ER.notice(er['state'], a)
        for msg in ER.update(er['state']): self.send_menu(er['port'], *msg)

    def _slime_touch_after(self):
        """DQ V's Slime Touch: what CgController.Update does at each frame (core/slime_touch.py): the CGs drawn this frame advance, the engine hears which ones play or have ended.
        The clips' lengths come from the game's prefabs (`prefab_clips`, given by the window); without them the CGs stay hidden and nothing is sent."""
        sl = self.ui.slime_touch
        if sl is None or not sl['live']: return
        if sl['state'] is None:
            if self.prefab_clips is None: return
            sl['state'] = ST.new(sl['port'], lambda cg: self.prefab_clips(ST.prefab(cg)))
            for a in sl.pop('pending', []): ST.notice(sl['state'], a)
        for msg in ST.update(sl['state']): self.send_menu(sl['port'], *msg)

    _curling_slide = None          # (DQ VI's slime curling) address of curling::CurlingSlide, 0 if this engine has none

    def curling_target(self):
        """DQ VI's slime curling, for the keys: (is the slide's touch zone shown, its center (x, y) in LCD units). The zone (CurlingSlide: its CurlingSlideTarget at + 0, center x, y,
        display at + 0x1c) is where the player rubs the ice while the slime slides (CurlingSlide::execute: a finger within 64 x 32 units of the center, its side and its sideways moves)."""
        c = self.core
        if self._curling_slide is None:
            f = c.export('_ZN7curling12CurlingSlide12getSingletonEv')
            self._curling_slide = (c.call(f, label='CurlingSlide::getSingleton') or 0) if f else 0
        a = self._curling_slide
        if not a: return False, None
        return c.mem(a + 0x1c, 1)[0] == 1, struct.unpack('<ii', c.mem(a, 8))

    def slime_touch_press(self, x, y):
        """A touch (mouse, key) at (x, y) in units of the orthographic layer during DQ V's Slime Touch: the box hit is sent to the engine (SlimeTouchBehaviour.Update). -> True if a box was hit."""
        sl = self.ui.slime_touch
        if sl is None or not sl['live'] or sl['state'] is None: return False
        msg = ST.touch(sl['state'], x, y)
        if msg is None: return False
        self.send_menu(sl['port'], *msg); return True

    def _exit_countdown(self):
        """Map transition sound effect (door, exit, village entrance, stairs, world map <-> town): cause of its loss with the shortened fades ("Transition speed" x3 and above, marginal at x2).
        When the game leaves a town or the world map, it starts a 30-frame fade to black AND arms a 15-frame countdown (EXIT_COUNTDOWN); at 0, NativeExec calls playTownExitSE / playFieldExitSE, which play
        sound effect 305 only if the old scene is still alive. In the original game, the next map's loading happens only at the end of the fade (30 frames): the sound effect is requested at mid-fade. The viewer's shortened fade
        (State._fade) brings the loading before the 15 frames: the old scene is destroyed, the engine no longer requests anything (no packet 35). The countdown is therefore reset to the same proportion as the fade
        (played duration / original duration): the sound effect stays requested at mid-fade, the duration of the fade and that of the sound effect do not change. Without a shortened fade (ratio 1), nothing is touched."""
        if ENGINE_V2: return                                                  # (DQ V / VI: another engine loop, the countdown is elsewhere: their fades are not shortened that way)
        c = self.core; b = c.mem(self.L + EXIT_COUNTDOWN, 1)[0]
        if b > self._countdown: self._countdown_armed = self.frames; self._countdown_done = False       # armed during this step (the engine has already decremented it once)
        self._countdown = b
        if self._countdown_done or b == 0 or b > 15: return
        im, r = self.state.fade_ratio
        if self.frames - self._countdown_armed > 2: self._countdown_done = True; return
        if im < self._countdown_armed - 2 or im > self.frames: return           # the fade of this exit has not (yet) arrived
        self._countdown_done = True
        if r < 1.0:
            num = max(1, int(round(b * r))); c.mem_w(self.L + EXIT_COUNTDOWN, bytes([num])); self._countdown = num

    def _log_message(self):
        """(test, DQ_MSGDBG=1) one line at each change of the message state: engine frame, state, letters written / letters of the line, window type speed."""
        o = self.game_options()
        if o != getattr(self, '_opt_vu', None): self._opt_vu = o; print('MSGDBG frame %d game options: music volume %d, sound effects volume %d, battle message speed %d (forced: %s)' % ((self.frames,) + o + (self.scroll_mode,)), flush=True)
        e = self.message_state()
        if (e[0], e[1], e[4]) != getattr(self, '_msg_vu', None) or (e[1] == 2 and e[2] == e[3]):
            self._msg_vu = (e[0], e[1], e[4]); print('MSGDBG frame %d active %d state %d letters %d/%d speed %d battle=%d' % ((self.frames,) + e + (self.state.part == PART_BATTLE,)), flush=True)

    def _read_world(self):
        """Entire world map (FieldData: grid of 256 x 256 block numbers, block table of 6 bytes, water animation offsets: see engine_layout.WORLD_V1 / WORLD_V2).
        Same rule as FieldData::drawBlock (verified: 1300 tiles out of 1300).
        The engine sends only 25 columns around the hero; here the port can draw as far around him as needed.
        Safeguards (the grid is cached for 30 frames, and `fielddata` is the pointer of the last drawBlock): the cache is dropped at each map load, and the result is checked against the
        tiles the engine itself sent (packet 28) in the same step: a grid that does not match (old or half-filled data) is read again, and if it still does not match the window gets `world = None`
        and draws the engine's own tiles, instead of a map of water."""
        c, fd = self.core, self.fielddata
        anim = _lay(self).world_anim
        if not fd or not anim: self.state.world = None; return
        u64 = lambda a: struct.unpack('<Q', c.mem(a, 8))[0]
        try:
            fresh = self._grid is None or self.frames - self._grid_frame >= 30
            W = LY.WORLD_V2 if ENGINE_V2 else LY.WORLD_V1
            b0, b1, b2 = (struct.unpack('<h', c.mem(a, 2))[0] for a in anim)
            if ENGINE_V2: b1 += struct.unpack('<i', c.mem(fd + W['anim8'], 4))[0]; b2 += struct.unpack('<i', c.mem(fd + W['anim18'], 4))[0]
            now = (b0, b1, b2); before, self._anim_now = getattr(self, '_anim_now', None), now          # (the animation steps in memory at this step and at the one before)
            for attempt in (0, 1):
                if fresh:
                    if ENGINE_V2:
                        W = LY.WORLD_V2; cw = fd + W['map']; hd_ = c.mem(u64(cw), 4); gp = u64(cw + 0x10); l, h = hd_[0] * hd_[2], hd_[1] * hd_[3]
                    else:
                        W = LY.WORLD_V1; gp = u64(fd + W['grid']); l, h = struct.unpack('<ii', c.mem(fd + W['size'], 8))
                    if not gp or not (0 < l <= 1024 and 0 < h <= 1024): self._grid = None; self.state.world = None; return
                    self._grid = np.frombuffer(c.mem(gp, l * h * 2), '<u2').reshape(h, l).astype(np.int32); self._grid_frame = self.frames
                    self._orient = None
                    if ENGINE_V2:                              # CWorldMap::getUV: one nibble per block (high nibble first), index of an 8-byte entry whose first byte is the orientation
                        nib = np.frombuffer(c.mem(u64(cw + 0x30), (l * h + 1) // 2), np.uint8)
                        k = np.stack([nib >> 4, nib & 15], 1).ravel()[:l * h].astype(np.int32)
                        ent = np.frombuffer(c.mem(u64(cw + 0x28), 16 * 8), np.uint8).reshape(16, 8)[:, 0].astype(np.int32)
                        self._orient = (ent[k] << 12).reshape(h, l)
                ch = np.frombuffer(c.mem(u64(fd + W['blocks']), 1024 * 6), np.uint8).reshape(1024, 6)
                t = ch[:, 1]; s2 = np.ascontiguousarray(ch[:, 2:4]).view('<i2').ravel().astype(np.int32); s4 = np.ascontiguousarray(ch[:, 4:6]).view('<i2').ravel().astype(np.int32)
                h, l = self._grid.shape
                def world_ids(b0, b1, b2):
                    tile = np.where(t == 0, s4 + b0, np.where(t == 0x18, s2 + b2, np.where(t == 8, s2 + b1, s2)))
                    ids = tile[(self._grid - 1) & 0x3ff]
                    return ids | self._orient if self._orient is not None else ids
                ids = world_ids(b0, b1, b2)
                if self._world_matches(ids): self._world_found(ids, l, h, now); return
                # The animation steps advance in memory after the engine sent its tiles: the screen still shows the step of the frame before. DQ V advances its two water kinds every 16 frames,
                # one frame apart: the tiles then follow the memory of the previous frame (one kind already at its new step, the other not yet), which neither the current values nor the last
                # ones seen to match reproduce (the map fell back to the engine's 25 columns for a frame: the screen's edges flashed).
                tried = {now}
                for cand in (before, getattr(self, '_anim_seen', None)):
                    if cand is None or cand in tried: continue
                    tried.add(cand); ids = world_ids(*cand)
                    if self._world_matches(ids): self._world_found(ids, l, h, None); return
                if fresh: break                                       # read just now and still wrong
                fresh = True                                          # cached grid: read it again once
            self._grid = None; self._world_lost()
        except Exception:
            self._grid = None; self._world_lost()

    WORLD_KEPT = 3                             # steps the last matching world map stays drawn when the grid read from memory stops matching (a frame of animation in between), before the engine's own tiles

    def _world_found(self, ids, l, h, seen):
        self.state.world = {'ids': ids, 'l': l, 'h': h}; self._world_last = (self.state.world, 0)
        if seen is not None: self._anim_seen = seen

    def _world_lost(self):
        """No grid matches the engine's tiles at this step: the last matching one stays for WORLD_KEPT steps (same world: a new map forgets it), then the window draws the engine's 25 columns."""
        last = getattr(self, '_world_last', None)
        if last is not None and last[1] < self.WORLD_KEPT: self._world_last = (last[0], last[1] + 1); self.state.world = last[0]
        else: self._world_last = None; self.state.world = None

    WORLD_MATCH = 0.9                          # share of the engine's own tiles (packet 28) that the grid read from memory must reproduce
    MESH_COLS, MESH_SIDES = WM.COLS, 1      # DQ VI: a tile mesh is 25 columns (FieldData::setMeshInfo: 32 bytes per tile, 4 corners); neighbors on each side

    def _world_mesh(self):
        """DQ VI's world map: the Unity layer draws a mesh of 25 x 53 tiles whose texture coordinates the engine computes (Field.SetIndexList -> FieldSet -> FieldData::setMeshInfo(uv, opt):
        the tile, its animation step and its orientation, around the origin FieldData + 0x6f4 / + 0x6f8). The port computes them the same way (world_mesh.tile_uvs: setMeshInfo redone with numpy,
        about 0.3 ms a mesh instead of an emulated call of 4 to 7 ms), and also the 25 columns on each side: the 16:9 screen is wider than the phone's.
        -> state.field = {'mesh': [{'opt', 'pos', 'uv' (rows, 25, 4, 2)}], 'cam': (x, y) in pixels, 'serial'}. Every mesh is computed in the same frame as the tiles it shows
        (computing the sides one frame later left the edge of the screen behind while walking)."""
        c, fd, s = self.core, self.fielddata, self.state
        if not fd: s.field = None; return
        anim = getattr(self, '_mesh_anim', None)
        if anim is None:
            fn = c.export('_ZN9FieldData11setMeshInfoEPfi')
            if not fn: s.field = None; return
            anim = self._mesh_anim = tuple(c.LIB_BASE + a for a in LY.halfword_refs(c, fn, 0x400))       # (the animation steps setMeshInfo adds: library halfwords)
        key = (tuple(sorted(s.field_meshes.items())), bytes(c.mem(fd + 0x6d8, 0x28)), b''.join(c.mem(a, 2) for a in anim))
        if key == getattr(self, '_mesh_key', None) and s.field is not None and 'mesh' in s.field:
            s.field['cam'] = s.field_cam; return                                                 # (same tiles and animation step: the mesh of the previous frame)
        self._mesh_key = key
        out = []
        for opt, pos in sorted(s.field_meshes.items()):
            sides = range(-self.MESH_SIDES, self.MESH_SIDES + 1) if opt != 0x301 else (0,)          # (0x301: the engine takes the origin 0, no neighbors)
            for k, uv in sorted(WM.tile_uvs(c.mem, c.LIB_BASE, fd, opt, sides).items()):
                out.append({'opt': opt, 'pos': (pos[0] + 16.0 * self.MESH_COLS * k, pos[1], pos[2]), 'uv': uv})
        s.field = {'mesh': out, 'cam': s.field_cam, 'type': s.field_type, 'serial': self._mesh_serial()}

    def _mesh_serial(self):
        """A number that changes with the mesh (the window rebuilds its vertices only then, instead of hashing them every frame)."""
        self._mesh_serials = getattr(self, '_mesh_serials', 0) + 1
        return self._mesh_serials

    def _world_matches(self, ids):
        """Does the world map read from memory give, around the hero, the tiles the engine sent in packet 28 (same coordinates)? True when the engine sent none yet."""
        f = self.state.field
        if not f or not f.get('cols') or not f.get('tiles'): return True
        cols = f['cols']; t = np.asarray(f['tiles'], np.int32); rows = len(t) // cols
        if rows <= 0: return True
        h, l = ids.shape
        win = ids[(np.arange(f['y0'], f['y0'] + rows) % h)[:, None], (np.arange(f['x0'], f['x0'] + cols) % l)[None, :]]
        return float((win.ravel() == t[:rows * cols]).mean()) >= self.WORLD_MATCH

    def engine_bgm(self):
        """DQ V / VI: SoundManager::bgmIndex_, the track of the game's last music order (SoundManager::play stores it, a stop puts 0; the game never sends a track it believes already playing).
        None for DQ IV, after a stop, or when unreadable. A savestate recorded without its music (the window was silent) restarts this one."""
        if not ENGINE_V2: return None
        try:
            a = self.core.export('_ZN12SoundManager9bgmIndex_E')
            v = struct.unpack('<i', self.core.mem(a, 4))[0] if a else 0
        except Exception: return None
        return v if 0 < v < 0x100 else None

    def read_canvas(self):
        """Bitmap of the message text (height x width, 0-255 = opacity), or None if the canvas does not exist yet."""
        c = self.core
        buf = struct.unpack('<Q', c.mem(self.obj_msg + _lay(self).canvas['buffer'], 8))[0]
        if not buf: return None
        import numpy as np
        return np.frombuffer(c.mem(buf, CANVAS_L * CANVAS_H), np.uint8).reshape(CANVAS_H, CANVAS_L)

    def _consume_messages(self):
        """Does what the Unity interface would do: reads the messages that the engine has put for each port (menus, windows...)
        then empties the queue. Without this the queue grows endlessly and ends up overwriting the neighboring memory."""
        c = self.core
        b2 = self.buf2
        a = struct.unpack('<%dh' % SHORTS_STACK, c.mem(b2, 2 * SHORTS_STACK))
        out = []
        for port in range(32):
            idx = a[port] & 0xffff; guard = 0
            while 0 < idx < SHORTS_STACK - 2 and guard < 600:
                n = a[idx]
                if n < 0 or idx + n + 1 >= SHORTS_STACK: break
                out.append((port, a[idx + 1: idx + 1 + n]))
                idx = a[idx + 1 + n] & 0xffff; guard += 1
        self.msgs_ui = out
        self.ui.apply(out)
        c.mem_w(b2, b'\0' * 128 + struct.pack('<h', 65))              # heads/tails at 0, free pointer = 65

    def active_menu(self):
        """Port of the menu window that waits for keys (active and accepting input), or None."""
        c = self.core; L = self.L
        u = lambda a, n, sg=False: int.from_bytes(c.mem(a, n), 'little', signed=sg)
        base = u(_lay(self).menu_ports, 8); cnt = u(_lay(self).menu_ports_count, 2, True)
        if not base: return None
        cand = []
        table = c.mem(base, 0x20 * cnt) if cnt > 1 else b''          # (the whole table in one read: called several times per frame, one read per field cost 1.5 ms a frame)
        for i in range(1, cnt - 1):                  # (entries 2 to cnt - 1: what follows the table is not an entry, DQ VI keeps other data there)
            port, = struct.unpack_from('<h', table, 0x20 * i + 0x28); this, other = struct.unpack_from('<QQ', table, 0x20 * i + 0x30)
            if this and other and port > 1 and u(this + 8, 1) == 1 and u(this + 0x50, 2):
                f = self.ui.f.get(port)
                if f and f['visible'] and f['cells'] and f.get('type') not in self.ignored_types: cand.append((f['order'], port))
        if not cand:                                 # otherwise: a menu whose input the engine requests (command 6 on every frame), even under a message: the Yes / No of a question
            cand = [(self.ui.f[p]['order'], p) for p in self.input_menus()]
        return max(cand)[1] if cand else None

    def input_menus(self):
        """All the menu ports whose input the engine requests (touch acts on each of them): the most recent first."""
        return sorted((p for p, f in self.ui.f.items() if p > 1 and f.get('input') and f['visible'] and f['cells'] and f.get('type') not in self.ignored_types), key=lambda p: -self.ui.f[p]['order'])

    def send_menu(self, port, *args):
        """What Unity does: drops a message [n, args...] into the engine<-interface queue of the port (menu keys/pointer)."""
        if os.environ.get('DQ_MENUDBG'): print('SEND frame %d port %d %s' % (self.frames, port, list(args)), flush=True)         # (test) interface -> engine messages
        if self.input_log is not None:                                                                    # (battle being recorded for its replay)
            if not self._menus_this_step: self._menu_active = self.active_menu()                         # (which menu the player was answering: the replay sends the choice to that one)
            self._menus_this_step.append((port,) + tuple(args))
        c = self.core
        a = list(struct.unpack('<%dh' % SHORTS_STACK, c.mem(self.buf1, 2 * SHORTS_STACK)))
        n = len(args); idx = a[64] or 65
        a[idx] = n
        for i, v in enumerate(args): a[idx + 1 + i] = v
        a[idx + n + 1] = 0
        if a[port]: a[a[32 + port]] = idx
        else: a[port] = idx
        a[32 + port] = idx + n + 1
        a[64] = idx + n + 2
        c.mem_w(self.buf1, struct.pack('<%dh' % SHORTS_STACK, *a))

    def params_message(self):
        """(number of visible lines - 1, height of a line, scroll) of the message canvas, as the engine holds them."""
        c = self.core; i16 = lambda ad: struct.unpack('<h', c.mem(ad, 2))[0]
        L, f = _lay(self), _lay(self).canvas
        return i16(L.msg_state + L.msg['lines']), i16(self.obj_msg + f['line_height']), i16(self.obj_msg + f['scroll'])

    def read_message(self):
        """Part of the canvas currently visible in the message window: n lines from the current scroll.
        -> (height x width) array of 0-255, or None."""
        a = self.read_canvas()
        if a is None: return None
        c = self.core
        i16 = lambda ad: struct.unpack('<h', c.mem(ad, 2))[0]
        L, f = _lay(self), _lay(self).canvas
        lines = i16(L.msg_state + L.msg['lines']) + 1            # number of visible lines of the window (engine setting)
        lh = i16(self.obj_msg + f['line_height'])                  # height of a line (canvas pixels)
        scroll = i16(self.obj_msg + f['scroll'])               # current scroll (the engine animates it by 8 pixels per frame)
        if not (1 <= lines <= 8 and 8 <= lh <= 64): lines, lh, scroll = 4, 28, -3
        import numpy as np
        h = lines * lh; top = max(0, scroll + 3)
        return a[(top + np.arange(h)) % a.shape[0]]                   # the canvas is circular (512 lines, REPEAT) and the engine's scroll grows without bound: like `ui_menu.select_language` (y & 0x1ff)

    def _finish_anims(self):
        c = self.core
        if self.state.fld_paused and self.anims: self.anims = [(due + 1, idx) for due, idx in self.anims]          # (DQ VI, map paused: the common animations do not advance, their end waits with them)
        due = [a for a in self.anims if a[0] <= self.frames]
        if not due: return
        self.anims = [a for a in self.anims if a[0] > self.frames]
        table = struct.unpack('<Q', c.mem(self.fldobj + 8, 8))[0]
        for _, idx in due:
            ad = table + idx * ANIM_ENTRY[ENGINE_V2] + 0x14
            c.mem_w(ad, bytes([c.mem(ad, 1)[0] & ~1]))

    def _finish_fbx_anims(self):
        """NativeManager.Update: for each 3D model animation in m_anim_list, FbxObject.IsEndAnimation (= not Animation.isPlaying); finished -> message (1, 0x1000 | slot) for the engine, which lowers
        ModelObject::motionFlag (setMotionEnd); without it ModelObject::isEnd stays false and the engine never hides or frees the model (spell effects: the image stayed on screen). A model freed in the meantime
        keeps its entry (m_model[slot] null: the entry is skipped). The clips come from the window (fbx_clips); model not yet read: it waits."""
        if not self.fbx_playing: return
        ends = []; keep = []; players = {}
        for slot in self.fbx_playing:
            m = self.state.fbx.get(slot)
            if m is None: keep.append(slot); continue
            if slot not in players:
                clips = self.fbx_clips(m['id']) if self.fbx_clips is not None else {}
                if clips is None: keep.append(slot); continue
                a = players[slot] = AnimPlayer(clips)
                for img, seq, force in m['anim']: a.play(seq, force, img)
                a.do_update(self.frames)
            a = players[slot]
            if a.cur is not None and not a._finished(a.cur, self.frames): keep.append(slot)
            else: ends.append(slot)
        self.fbx_playing = keep
        return b''.join(struct.pack('<ii', 1, 0x1000 | s) for s in ends)          # message type 1 = end of animation (identifier >= 0x1000: 3D model, slot = identifier & 0xff -> ModelObject::setMotionEnd)

    def _camera_message(self):
        """NativeManager.Update, the frame after a camera packet (cmd_camera): message (0, x, y, -z) in 1/4096, Camera.main's position as SceneCamera.Update left it (scene_camera_position).
        The engine keeps it (g_camera_pos, which ar::Camera::send_unity first fills with its own position) to place what it draws between the camera and a point of the scene: the monsters shown
        in a town or in the monster book and the town effects (DSSAObjectWithCamera, ModelObjectWithCamera), the icons that rise above a character (TownRiseupBase::calcNearPos)."""
        st = self.state
        if not st.cam_sent: return b''
        st.cam_sent = False
        p = scene_camera_position(st)
        if p is None or os.environ.get('DQ_NO_CAM_MSG'): return b''                # (test) DQ_NO_CAM_MSG: no message, the engine keeps its own position
        v = []
        for x in (p[0] * 4096.0, p[1] * 4096.0, -p[2] * 4096.0):
            v.append(-0x80000000 if not math.isfinite(x) else max(-0x80000000, min(0x7fffffff, int(x))))
        return struct.pack('<4i', 0, *v)

    def _map_object_messages(self):
        """NativeManager.Update, m_anim_list entries >= 0x4000 (cmd_fld_etc case 1): message (2, identifier, x, y, -z) in 1/4096, the world position of map object identifier & 0x1fff
        (Transform.position; an object FldObject.GetMapObjPtr does not find: Vector3.zero), given by the window (map_obj_pos). An infinite coordinate is sent as 0x80000000."""
        reqs = self.state.obj_requests
        if not reqs: return b''
        out = []
        for ident in reqs:
            p = self.map_obj_pos(ident & 0x1fff) if self.map_obj_pos is not None else None
            if p is None: p = (0.0, 0.0, 0.0)
            v = []
            for x in (p[0] * 4096.0, p[1] * 4096.0, -p[2] * 4096.0):
                v.append(-0x80000000 if not math.isfinite(x) else max(-0x80000000, min(0x7fffffff, int(x))))
            out.append(struct.pack('<5i', 2, ident, *v))
        return b''.join(out)

    def _send_messages(self, data):
        """Writes this frame's Unity -> engine messages (GameUpdate's input list, terminated by -1); nothing to send: the list stays empty."""
        if not data: return
        c = self.core; ad = _lay(self).messages_to_engine(c); self._messages_sent = True
        c.mem_w(ad, data + struct.pack('<i', -1))

    def catalog_status(self):
        """Mask of `getCatalogStatus`: bits 0-4 = valid bank, 5-9 = unreadable, 10-14 = corrupted, 15 = a file is missing."""
        return int(self.core.call(self.E('_ZN7profile8SaveLoad16getCatalogStatusEv'), label='getCatalogStatus')) & 0xffff

    def reread_savefiles(self):
        """After an import of saves: the engine reads the data1..5 / data9 files only when the start menu is created; it is made to read the catalog again (names, chapters, durations of the lists). -> new status mask."""
        for name_str in ('getCatalogView', 'getCatalogRecent'):
            self.core.call(self.E('_ZN7profile8SaveLoad%d%sEv' % (len(name_str), name_str)), label=name_str)
        return self.catalog_status()

    @staticmethod
    def initial_buttons_state(status):
        """What the engine sets when the start menu is created (`updateActiveDiary`): (a bank 0-2 contains data, bank 3 is valid, bank 4 is valid) -> grayed-out or absent buttons."""
        return (bool(status & 0x0007 or status & 0x00e0 or status & 0x1c00), bool(status & 0x0008), bool(status & 0x0010))

    def load_save(self, number):
        """number 1..5 -> reads data<number>.dat; the engine restarts the saved town"""
        r = self.core.call(self.E('_ZN7profile8SaveLoad8loadbankEi'), number - 1, label='loadbank')
        if r and 1 <= number <= 3: self.set_journal(number - 1)            # (as the start menu does: the church shows this log in cyan)
        elif r and number - 1 == QUICK_SAVE_BANK and self.quick_journal is not None: self.set_journal(self.quick_journal)
        return bool(r)
