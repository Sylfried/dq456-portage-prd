"""Reading the game's graphics data (datapack.unity3d, in the user's data APK).
Nothing is supplied with this program: everything is read from the player's files then cached on their disk."""
import os, re, math, pickle, zipfile, json, time, threading, gc
import numpy as np
from dq456_pc import language as LG
from dq456_pc import games as GM
from dq456_pc.world import fld
from dq456_pc.core.legacy_state import carry_over_file

WORLD_VEIL = {'dq4': 'dsdq4wmap01m', 'dq5': 'dq5ds_wmap1m'}           # TextAsset of the small world map's veil (ICityMap code 5)

DATAPACK = 'assets/bin/Data/datapack.unity3d'
# DQ V, FldObject.FixProblems(map number) (NativeManager.cmd_fld_etc, bit 29; twn::TownStageManager::initialize sends it at every load of three maps: 91 cfout, 339 dlf1g, 714 ev14):
# 'transz': the transparent materials (Custom/fld_trans) of these objects take Custom/fld_transz (the same blending, ZWrite on); 'color': (objects, material slot, gray): that material's
# _OrgColor and color become (gray, gray, gray, 1) (0.9725... = 248 / 255). Applied when the map is read (fix_problems): the groups concerned belong to these objects only.
# (Maps 707 to 709, the slot machines: the scenery is moved sideways by (aspect - 0.5625) x -10.24, nothing on the phone's 9:16 screen the port shows them in.)
FIX_PROBLEMS = {'dq5': {'cfout': dict(color=((6, 7), 1, 248.0 / 255.0)), 'dlf1g': dict(transz=(2,)), 'ev14': dict(transz=(23, 24, 36, 39, 40, 53, 54, 55))}}


def fix_problems(game, name_str, c):
    """Applies FIX_PROBLEMS to the map dict `c` just read: c['transz'] = keys of the transparent groups drawn with depth writing (window: _paint_scenery); the gray of 'color' multiplies
    the vertex colors of the material's range (its alpha is 1 already). A group shared with an object that is not listed is left alone."""
    fix = FIX_PROBLEMS.get(game, {}).get(name_str)
    if not fix or c is None or 'spans' not in c or os.environ.get('DQ_NO_FIX_PROBLEMS'): return c          # (test) DQ_NO_FIX_PROBLEMS: the map as its file gives it
    is_tr = lambda k: isinstance(k, tuple) and k and k[0] == 'tr'
    users = {}
    for i, pl in c['spans'].items():
        for key, d, n in pl: users.setdefault(key, set()).add(i)
    listed = set(fix.get('transz', ()))
    c['transz'] = {key for i in listed for key, d, n in c['spans'].get(i, ()) if is_tr(key) and len(key) == 3 and users[key] <= listed}
    if 'color' in fix:
        objs, slot, gray = fix['color']
        for i in objs:
            pl = c['spans'].get(i, ())
            if slot >= len(pl): continue
            key, d, n = pl[slot]
            if key not in c['groups']: continue
            pos, uv, col = c['groups'][key]
            col = np.array(col, np.float32); col[d:d + n, :3] *= gray; c['groups'][key] = (pos, uv, col)
    return c


MAP_PREFIX = 'map15_'               # map cache prefix; it goes up with each format change. The real-map tests read it HERE: hard-coded, they were silently skipped after the switch to v11
FBX_PREFIX = 'fbx4_'           # 3D model cache (fbx4_: one group per material with its color and texture placement, material curves of the clips, clip length over all its curves)
def material_target(class_id, attribute):
    """A float curve of a legacy clip that animates a material -> (material slot, 'col' or 'st', channel), None otherwise. Unity writes them on the renderer (class 23 MeshRenderer, 137 SkinnedMeshRenderer:
    "material._Color.a", a property of the renderer that reaches all its materials: slot -1) or on one material (class 21: "_Color.a" = slot 0, "[2]._Color.a" = slot 2); channels: _Color r g b a = 0 to 3,
    _MainTex scale.x scale.y offset.x offset.y = 0 to 3."""
    import re
    if class_id in (23, 137):
        if not attribute.startswith('material.'): return None
        slot, rest = -1, attribute[len('material.'):]
    elif class_id == 21:
        m = re.match(r'\[(\d+)\]\.(.*)$', attribute); slot, rest = (int(m.group(1)), m.group(2)) if m else (0, attribute)
    else: return None
    if rest.startswith('_Color.') and rest[7:] in ('r', 'g', 'b', 'a'): return slot, 'col', 'rgba'.index(rest[7:])
    st = {'_MainTex.scale.x': 0, '_MainTex.scale.y': 1, '_MainTex.offset.x': 2, '_MainTex.offset.y': 3}
    if rest in st: return slot, 'st', st[rest]
    return None


FBX_COMBAT = (3, 10, 11, 12, 13, 14, 15, 16, 20, 39, 40, 42, 43, 103, 217, 609)          # fbx requested in battle by the engine's effects (packet 31): Glace c10 to c12, Gigaglace c13, Tornade c14 to c16, Mégaflamme c3, Gigalame c20, Explosion magique c42...
FBX_SCREEN = (2000,)          # fbx on the interface camera's layer (Unity layer 8, see Data._screen_overlay): c2000 = the dream filter; read during the preparation, not at the dream (no stall then)
SCREEN_LAYER = 8                    # GameObject.layer of the fade planes and of c2000: drawn by the interface camera (orthographic, top view of the XZ plane, 1 unit = 1 pixel of the 450 x 800 column)
BUILTIN_QUAD = 10210                # path id of Unity's built-in "Quad" mesh (1 x 1 in XY, centered, facing -Z): DQ V's Slime Touch "gameover_p"
QUAD_V = np.array([(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (-0.5, 0.5, 0.0), (0.5, 0.5, 0.0)], np.float32)
QUAD_UV = np.array([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)], np.float32)
QUAD_TRI = np.array([(0, 3, 1), (3, 0, 2)], np.int64)
FBX_SHADERS = ('Custom/ImdChara', 'Custom/Effect0')            # the shaders of the "fbx/c<number>" models
PREFAB_SHADERS = FBX_SHADERS + ('Custom/DssaCharaTransparent',)          # prefabs of the orthographic layer (DQ V's Slime Touch)
PREFAB_PREFIX = 'prefab1_'          # cache of the other prefabs (Data.prefab_model)
BUILTIN_PLANE = 10209               # path id of Unity's built-in "Plane" mesh in "unity default resources" (10 x 10 units in XZ, 11 x 11 vertices): not in the game's data, c2000 uses it
WHITE_TEX = 'white'                 # key of the 4 x 4 white texture for untextured materials (see Data._texture)


def _quat_mat(x, y, z, w):
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]], dtype=np.float64)


def euler_mat(rx, ry, rz):
    """Rotation of a map object (degrees) like FldObject.rotateXZY (0xCD47F0, read from the disassembly): q = (AngleAxis(ry, up) * AngleAxis(-rz, forward)) * AngleAxis(-rx, right) -- the axes are the static
    Vector3.up / forward / right (offsets 0x18 / 0x48 / 0x3C), the X and Z angles are inverted, and the quaternion product applies X first (in the object's frame), then Z, then the Y heading
    last (in the world frame): matrix Ry . Rz . Rx. (This is not Unity's Quaternion.Euler, Z-X-Y, nor is it the X, Y, Z order wrongly noted.) Used by init_fld (initial rotation),
    SetCommonAnimation, pmv_func and c_pmv_func (movements): a chest's lid, key X = -60, lifts around ITS hinge whatever the chest's heading (a chest with heading 90, -90 or 180 used to open sideways
    or forward when the heading was applied before the tilt)."""
    a, b, c = math.radians(-rx), math.radians(ry), math.radians(-rz)
    Rx = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])
    Ry = np.array([[math.cos(b), 0, math.sin(b)], [0, 1, 0], [-math.sin(b), 0, math.cos(b)]])
    Rz = np.array([[math.cos(c), -math.sin(c), 0], [math.sin(c), math.cos(c), 0], [0, 0, 1]])
    return Ry @ Rz @ Rx


def with_time_of(chosen, engine_name):
    """A battle scenery chosen in the debug panel, at the time of day the engine asks (`engine_name`'s _d / _e / _n / _dk) when both have one; as chosen otherwise
    (a missing variant: fallback_sceneries)."""
    a = re.match(r'^(.*)_(dk|d|e|n)$', chosen or ''); b = re.match(r'^(.*)_(dk|d|e|n)$', engine_name or '')
    return '%s_%s' % (a.group(1), b.group(2)) if a and b else chosen


def fallback_sceneries(name_str):
    """Neighboring sceneries of a battle scenery missing from the data. The engine picks the final letter according to the world time (`StageStatus::getBtlMapName`: _d day, _e evening, _n night AND dawn: zone 1 -- 3264 to 127 --
    reuses `_n`, there is no dawn scenery); `param_map_battle.dat` lists each location's sceneries, but `btlcc1` only has _d and _e: without `_n` nothing would be displayed. Dark world with no time-of-day zone:
    the engine's table requests `_dk` (btl_pl_dk, btl_se_dk, btl_sd_dk, btl_mt_dk, btl_po_dk, btl_wd_dk), but the data pack only has pl, po, wd (and tc1) in `_dk`: se, sd, mt take the night
    (assumption: the table requests them, no resource carries them). In order: the variant closest in brightness, the others, then the name without a letter. A name that does not end in
    _d, _e, _n or _dk: none."""
    m = re.match(r'^(.*)_(dk|d|e|n)$', name_str or '')
    if not m: return []
    base, c = m.groups()
    order = {'n': ['e', 'd'], 'e': ['d', 'n'], 'd': ['e', 'n'], 'dk': ['n', 'd', 'e']}[c]
    return ['%s_%s' % (base, x) for x in order] + [base]


class Data:
    def __init__(self, apk_data, cache_dir, log=print):
        self.apk, self.cache, self.log = apk_data, cache_dir, log
        os.makedirs(cache_dir, exist_ok=True)
        self._env = None
        self._objs = None
        self._lock = threading.RLock()          # UnityPy shares a single file reader: only one extraction at a time (background preparation and game)
        self._tex_names = None
        self._mesh = {}
        self._go_by_name = None

    # ---- UnityPy (loaded on demand, only if the cache is not enough)
    def env(self):
        if self._env is None:
            with self._lock:
                if self._env is None:
                    import UnityPy
                    self.log('Reading the game data pack (once only, a few seconds)...')
                    raw = zipfile.ZipFile(self.apk).read(DATAPACK)
                    env = UnityPy.load(raw)
                    self._objs = {o.path_id: o for o in env.objects}
                    self._env = env
        return self._env

    def free_up(self):
        """Frees the data pack's memory (more than 800 MB); it will be read again on demand if an element is still missing."""
        with self._lock:
            self._env = None; self._objs = None; self._go_by_name = None; self._tex_names = None; self._mesh = {}
        gc.collect()

    def _gos(self):
        if self._go_by_name is None:
            self.env()
            self._go_by_name = {}
            for pid, o in self._objs.items():
                if o.type.name == 'GameObject':
                    self._go_by_name.setdefault(o.read().m_Name, pid)
        return self._go_by_name

    def _mesh_data(self, ref):
        from UnityPy.helpers.MeshHelper import MeshHandler
        if ref.path_id not in self._mesh:
            m = ref.deref_parse_as_object(); h = MeshHandler(m); h.process()
            v = np.array(h.m_Vertices, np.float32).reshape(-1, 3)
            uv = np.array(h.m_UV0, np.float32).reshape(len(v), -1)[:, :2] if h.m_UV0 is not None else np.zeros((len(v), 2), np.float32)
            col = np.array(h.m_Colors, np.float32).reshape(len(v), -1) if h.m_Colors is not None else np.ones((len(v), 4), np.float32)
            if col.shape[1] == 3: col = np.c_[col, np.full(len(col), 255.0, np.float32)]
            if col.size and col.max() > 1.0: col = col / 255.0
            tris = [np.array(t, np.int64).reshape(-1, 3) for t in h.get_triangles()]
            self._mesh[ref.path_id] = (v, uv, col, tris)
        return self._mesh[ref.path_id]

    def _texture(self, matref, tex_out, name_out=None, fmt_out=None):
        """-> (key, scale, offset, the material's _Color, kind); fills tex_out[key] = RGBA array (top first) and name_out[key] = texture name.
        kind = shader chosen by FldObject.set_shader (CD5414): 'trans' (Custom/fld_trans: SrcAlpha / OneMinusSrcAlpha blending, ZWrite off) if the texture name does not contain '.tim'
        or if _Color.a != 1; otherwise 'atest' (fld_atest) if the texture is RGBA32 (format 4), 'normal' (fld_normal) otherwise."""
        try:
            m = matref.deref_parse_as_object()
            col = (1.0, 1.0, 1.0, 1.0)
            for k, c in m.m_SavedProperties.m_Colors:
                if k == '_Color': col = (c.r, c.g, c.b, c.a)
            for k, v in m.m_SavedProperties.m_TexEnvs:
                if v.m_Texture.path_id:
                    key = v.m_Texture.path_id
                    if key not in tex_out or (fmt_out is not None and key not in fmt_out):
                        t = v.m_Texture.deref_parse_as_object()
                        tex_out[key] = np.array(t.image.convert('RGBA'))
                        if name_out is not None: name_out[key] = t.m_Name
                        if fmt_out is not None: fmt_out[key] = int(t.m_TextureFormat)
                    name_str = (name_out or {}).get(key, '.tim'); fmt = (fmt_out or {}).get(key, 3)
                    kind = 'trans' if ('.tim' not in name_str or col[3] != 1.0) else ('atest' if fmt == 4 else 'normal')
                    return key, (v.m_Scale.x, v.m_Scale.y), (v.m_Offset.x, v.m_Offset.y), col, kind
        except Exception as ex:
            self.log('  unreadable texture: %s' % ex)
            return None, (1, 1), (0, 0), (1.0, 1.0, 1.0, 1.0), 'normal'
        # material without a texture (no texture in its TexEnvs: 21 location maps, 10 battle backdrops): Unity samples the default white texture, so the surface is drawn in vertex color x _Color
        # (dark floor, water...). Before, the surface was missing from the viewer (untextured group skipped when drawing).
        tex_out[WHITE_TEX] = np.full((4, 4, 4), 255, np.uint8)
        return WHITE_TEX, (1.0, 1.0), (0.0, 0.0), col, 'trans' if col[3] != 1.0 else 'normal'

    def _mat_name(self, matref):
        """Name of the material asset (FldObject.get_material_list: Material.name, "<model>_<FSRA name>"; Renderer.materials instances add a suffix to it, never at the beginning)."""
        names = self.__dict__.setdefault('_mat_names', {}); pid = matref.path_id
        if pid not in names:
            try: names[pid] = matref.deref_parse_as_object().m_Name or ''
            except Exception: names[pid] = ''
        return names[pid]

    def _components(self, g):
        out = {}
        for c in g.m_Components:
            o = (c.component if hasattr(c, 'component') else c).deref(); out[o.type.name] = o
        return out

    def _map_text(self, name_str):
        self.env()
        for pid, o in self._objs.items():
            if o.type.name == 'TextAsset':
                t = o.read()
                if t.m_Name == name_str + '.map':
                    return t.m_Script.encode('utf-8', 'surrogateescape') if isinstance(t.m_Script, str) else bytes(t.m_Script)
        return None

    # ---- a map
    def _frame_children(self, cg):
        """Frame meshes of a model (children sNN_*, FldObject.seq_build_mesh): list of MeshFilter, in order; frame 0 is the model's own mesh."""
        t = self._components(cg)['Transform'].read(); out = []
        for ch in t.m_Children:
            cgo = self._objs[ch.deref_parse_as_object().m_GameObject.path_id].read()
            if re.match(r's\d+_', cgo.m_Name):
                c = self._components(cgo)
                if 'MeshFilter' in c: out.append((cgo.m_Name, c['MeshFilter'].read().m_Mesh))
        return [m for _, m in sorted(out, key=lambda x: x[0])]

    def game_map(self, name_str, silent=False):
        """-> dict: 'groups' {texture_key: (positions Nx3, uv Nx2, colors Nx4)}, 'textures' {key: RGBA}, 'objects' (FMAP: the map's objects, then the event objects), 'nobj' (number of map objects), 'ean' (FEAN: [(start, end)] of the event animations), 'spans' {object: [(key, start, count)]},
        'names' {object: model name}, 'var' {(object, model): {'spans', 'images'}}: model variants of the common animations (FCAN), baked at the object's original position
        and placed after the map objects in the groups; 'tabs' (FCAN), 'motions' (FPMV), 'cameras' (FEMV: camera movements of the event animations), 'background' (FETC color), 'sequence_entries' / 'sequences' (FSEQ / FSQD),
        'imgobj' {object: frames per submesh} (mesh sequences played continuously), 'pam' (FPAM: palette animations) and 'pamdef' {group key: {'name', 'pam', 'event', 'images': [RGBA]}}: surfaces whose texture changes from frame to frame (water, swamp, damaging floors...), separate groups"""
        return fix_problems(GM.profile().key, name_str, self._read_map(name_str, silent))

    def _read_map(self, name_str, silent):
        f = os.path.join(self.cache, MAP_PREFIX + '%s.pkl' % name_str)
        if os.path.exists(f):
            with open(f, 'rb') as fh: return pickle.load(fh)
        with self._lock:
            if os.path.exists(f):                                   # produced in the meantime by the background preparation
                with open(f, 'rb') as fh: return pickle.load(fh)
            return self._extract_map(name_str, f, silent)

    @staticmethod
    def _frames(ip, iuv, icol, idx, sc, off, mc=(1.0, 1.0, 1.0, 1.0)):
        """Frames of a sub-mesh: {'pos': (frames, n, 3), 'uv': (frames, n, 2) or None, 'col': (frames, n, 4) or None}; UVs and colors only if they change from one frame to the next (flowing water, flickering lamp)."""
        im = {'pos': np.stack([x[idx] for x in ip]), 'uv': None, 'col': None}
        u = np.stack([x[idx] * np.array(sc, np.float32) + np.array(off, np.float32) for x in iuv]).astype(np.float32)
        if np.abs(u - u[0]).max() > 1e-6: im['uv'] = u
        c = np.stack([x[idx] for x in icol]).astype(np.float32) * np.array(mc, np.float32)
        if np.abs(c - c[0]).max() > 1e-6: im['col'] = c
        return im

    def _pam_group(self, q, pam, tex, pamdef):
        """Key of the texture group of an animated palette (one per name, animation and event); its frames are the textures 'pam/<name>.NNN' (FldObject.init_fld). None if they are missing."""
        key = ('pam', q['name'], q['pam'], q['event'])
        if key not in pamdef:
            tx = self._textures(); imgs = []
            for k in range(len(pam['durations'])):
                o = tx.get('%s.%03d' % (q['name'], k))
                if o is None: break
                imgs.append(np.array(o.read().image.convert('RGBA')))
            if len(imgs) < len(pam['durations']) or not imgs:
                self.log('  animated palette %s: %d textures out of %d, ignored' % (q['name'], len(imgs), len(pam['durations']))); pamdef[key] = None
            else:
                pamdef[key] = {'name': q['name'], 'pam': q['pam'], 'event': q['event'], 'images': imgs}; tex[key] = imgs[0]
        return key if pamdef[key] else None

    def _extract_map(self, name_str, f, silent=False):
        if not silent: self.log('Extracting map %s ...' % name_str)
        gos = self._gos(); raw = self._map_text(name_str)
        root = gos.get(name_str + '.fld')
        if root is None or raw is None: raise KeyError('map not found in the data: ' + name_str)
        nobj, objs = fld.map_objs(raw)
        kids = {}
        rg = self._objs[root].read(); rt = self._components(rg)['Transform'].read()
        for ch in rt.m_Children:
            cg = self._objs[ch.deref_parse_as_object().m_GameObject.path_id].read()
            m = re.match(r'(\d+)_', cg.m_Name)
            if m: kids[int(m.group(1))] = cg
        if not kids and {'MeshFilter', 'MeshRenderer'} <= set(self._components(rg)): kids[0] = rg          # scenery made of a single mesh carried by the ROOT (18 battle sceneries: btl_mt_*, btl_po_*, btl_sd_*, btldc1, btldh1, btldp8_*, btlta2, btltd1_*): map object 0 is the root itself
        for o in objs:                                               # ChunkFile.readPosition (CCEB58) reads x, y, z then NEGATES y (fneg): a map object's position is (x, -y, z); without this, the trees and the ruin of the reliefs
            o['pos'] = (o['pos'][0], -o['pos'][1], o['pos'][2])      # sink in (file y = -relief height); this is also the reason for the "+1" of the chests (file -0.5 -> world +0.5)
        tex = {}; acc = {}; lens = {}; spans = {}; names = {}; tex_names = {}; tfmt = {}
        pams = fld.pam(raw); plp_obj = {}; pamdef = {}; sra = fld.sra(raw); sra_spans = {}
        for q in fld.plp(raw):                                       # FldObject.set_palette_data: textures of the object whose name contains the palette's
            if 0 <= q['pam'] < len(pams) and 0 <= q['obj'] < len(objs): plp_obj.setdefault(q['obj'], []).append(q)

        def bake(o, cg, hits=None):
            """Geometry of a model at the object's original position -> ([(key, start, count)], frames or None); added to the groups. `hits` (list): receives, for each submesh, (numbers of the
            FSRA color animations that target it, _Color alpha of the material)."""
            comps = self._components(cg)
            if 'MeshFilter' not in comps or 'MeshRenderer' not in comps: return [], None
            t = comps['Transform'].read(); p, q, s = t.m_LocalPosition, t.m_LocalRotation, t.m_LocalScale
            L = np.eye(4); L[:3, :3] = _quat_mat(q.x, q.y, q.z, q.w) @ np.diag([s.x, s.y, s.z]); L[:3, 3] = (p.x, p.y, p.z)
            M = np.eye(4); M[:3, :3] = euler_mat(*o['rot']) @ np.diag(o['scl']); M[:3, 3] = o['pos']
            W = M @ L
            mr = comps['MeshRenderer'].read()
            v, uv, col, tris = self._mesh_data(comps['MeshFilter'].read().m_Mesh)
            vw = (np.c_[v, np.ones(len(v))] @ W.T)[:, :3].astype(np.float32)
            ip, iuv, icol = [], [], []
            for mref in self._frame_children(cg):                      # sequence frames (FldObject.seq_build_mesh: Transform.GetChild(key value)), same number of vertices as the model
                vi, uvi, ci, _ = self._mesh_data(mref)
                if len(vi) == len(v): ip.append((np.c_[vi, np.ones(len(vi))] @ W.T)[:, :3].astype(np.float32)); iuv.append(uvi); icol.append(ci)
            out = []; imgs = []
            for i, tr in enumerate(tris):
                mat = mr.m_Materials[min(i, len(mr.m_Materials) - 1)]
                key, sc, off, mc, kind = self._texture(mat, tex, tex_names, tfmt)
                mn = self._mat_name(mat) if sra and hits is not None else ''
                hit = [k for k, x in enumerate(sra) if x['name'] and mn.find(x['name']) >= 1]
                if hit: kind = 'trans'                                # FldObject.get_material_list (init_fld, after set_shader): the materials of an FSRA animation switch to Custom/fld_trans (alpha blending)
                for q in plp_obj.get(o['i'], ()):
                    if q['name'] in tex_names.get(key, '').rsplit('.', 1)[0]:
                        key = self._pam_group(q, pams[q['pam']], tex, pamdef) or key; break
                # FldObject.save_org_color (init_fld, RVA 0xCD811C): _OrgColor = (1, 1, 1, alpha of _Color); FldObject.update_material_color (every frame, 0xCD79F4):
                # Material.color = _OrgColor x FldObject.m_color (packet 33 tint, white by default). The RGB color of _Color recorded in the asset is therefore NEVER used in game
                # (it made the cedars, undergrowth and trees of the night battle scenery black: _Color = 0, 0, 0); only its alpha counts. Shader fld_*.vert: vertex color x
                # _Color.
                mcol = np.array((1.0, 1.0, 1.0, mc[3]), np.float32)
                if kind == 'trans' and key is not None:               # transparent material: separate group, drawn after the opaque scenery in queue 3000 + priority (FldObject.set_priority)
                    ev02 = 'ev02' in name_str                         # FldObject.init_fld (only for maps whose name contains "ev02"): bit 0x8000000 on objects 1, 4 and 37 (get_material_list: their FSRA materials take Custom/fld_transz, ZWrite on); set_priority2(object 4, 2999): queue 2999 instead of 3000 + priority
                    gk = ('tr', -1 if ev02 and o['i'] == 4 else o['prio'], key) + (('z',) if ev02 and hit and o['i'] in (1, 4, 37) else ())
                    if gk not in tex and key in tex: tex[gk] = tex[key]
                    if key in pamdef and pamdef[key]: pamdef[gk] = pamdef[key]
                    key = gk
                idx = tr.ravel()
                a = acc.setdefault(key, ([], [], []))
                out.append((key, lens.get(key, 0), len(idx))); lens[key] = lens.get(key, 0) + len(idx)
                a[0].append(vw[idx]); a[1].append(uv[idx] * np.array(sc, np.float32) + np.array(off, np.float32)); a[2].append(col[idx] * mcol)
                imgs.append(self._frames(ip, iuv, icol, idx, sc, off, mcol) if ip else None)
                if hits is not None: hits.append((hit, mc[3]))
            return out, imgs

        sequence_entries, sequences = fld.sequences(raw); imgobj = {}
        for iobj, o in enumerate(objs):
            cg = kids.get(o['model'])
            if cg is None: continue
            names[iobj] = cg.m_Name
            hits = []; pl, imgs = bake(o, cg, hits)
            if pl: spans[iobj] = pl
            for (key, d, n), (sequence_clock, a) in zip(pl, hits):
                for k in sequence_clock: sra_spans.setdefault(k, []).append((key, d, n, float(a)))      # vertex ranges of each FSRA animation's material (+ _Color alpha, baked into the vertex color)
            if pl and imgs and any(imgs) and 0 <= o['sequence_object'] < len(sequence_entries) and 0 <= sequence_entries[o['sequence_object']][1] < len(sequences): imgobj[iobj] = imgs      # sequence played continuously (water, lamps...)
        tabs = fld.canim(raw); var = {}
        for iobj, o in enumerate(objs):                              # variants of the common animations (open door, broken jar or barrel, open chest...)
            if not 0 <= o['canim'] < len(tabs): continue
            for m, sq, mp in tabs[o['canim']]:
                if m == o['model'] or (iobj, m) in var or kids.get(m) is None: continue
                pl, imgs = bake(o, kids[m])
                if pl: var[(iobj, m)] = {'spans': pl, 'images': imgs}
        groups = {k: tuple(np.concatenate(x).astype(np.float32) for x in a) for k, a in acc.items()}
        res = {'groups': groups, 'textures': {k: tex[k] for k in groups if k in tex}, 'objects': objs, 'nobj': nobj, 'ean': fld.fean(raw), 'sra': sra, 'sra_plages': [sra_spans.get(k, []) for k in range(len(sra))], 'spans': spans, 'names': names,
               'var': var, 'tabs': tabs, 'motions': fld.pmv(raw), 'cameras': fld.emv(raw), 'background': fld.background(raw), 'sequence_entries': sequence_entries, 'sequences': sequences, 'imgobj': imgobj, 'pam': pams, 'pamdef': {k: v for k, v in pamdef.items() if k in groups}}
        with open(f + '.tmp', 'wb') as fh: pickle.dump(res, fh, protocol=4)
        os.replace(f + '.tmp', f)                                   # atomic write: never a half-written file
        return res

    def _textures(self):
        """{name: Texture2D object} (call with the lock held)."""
        if self._tex_names is None:
            self.env()
            self._tex_names = {}
            for o in self._objs.values():
                if o.type.name == 'Texture2D': self._tex_names.setdefault(o.read().m_Name, o)
        return self._tex_names

    def text_asset(self, name_str):
        """Bytes of the TextAsset `name_str` (e.g. the end credits' layouts, `Retro5_endroll_en_01.bin`), None if the game has none; cached."""
        f = os.path.join(self.cache, 'ta_%s' % name_str)
        if os.path.exists(f):
            with open(f, 'rb') as fh: return fh.read()
        with self._lock:
            if os.path.exists(f):
                with open(f, 'rb') as fh: return fh.read()
            self.env()
            for o in self._objs.values():
                if o.type.name != 'TextAsset': continue
                t = o.read()
                if t.m_Name != name_str: continue
                s = t.m_Script; b = bytes(s) if isinstance(s, (bytes, bytearray)) else s.encode('utf-8', 'surrogateescape')
                with open(f + '.tmp', 'wb') as fh: fh.write(b)
                os.replace(f + '.tmp', f); return b
        return None

    def texture(self, name_str):
        """Unity texture by its name (e.g. `field`: world map tiles), RGBA top to bottom; cached."""
        f = os.path.join(self.cache, 'tex_%s.npy' % name_str)
        if os.path.exists(f): return np.load(f)
        with self._lock:
            if os.path.exists(f): return np.load(f)
            o = self._textures().get(name_str)
            if o is None: return None
            a = np.array(o.read().image.convert('RGBA')); np.save(f, a); return a

    # ---- large world plan ("Map" window of the world map: MenuPortItemTable.Make_WorldLargeMap / LoadLargeWorldMap)
    # Background: a TextAsset of raw 1024 x 1024 RGB pixels (top first). DQ IV / V: `dsdq4lmap0_1024` / `dq5ds_lmap0`, pieces `P<bit>_<x>_<y>_<width>` (other TextAssets, placed when the bit of
    # the engine's flag is set). DQ VI: two backgrounds, `dq6ds_ulmap0` (flag bit 0 clear) and `dq6ds_dlmap0` (set), whose pieces follow the 1024 x 1024 pixels in the same TextAsset: the static
    # table of MenuPortItemTable (MAP_DIFF: offset, x, y, width, height), entry i placed when bit i + 1 of the flag is set (MAP_DIFF.Apply).
    WORLD_PLAN = {'dq4': ('dsdq4lmap0_1024',), 'dq5': ('dq5ds_lmap0',), 'dq6': ('dq6ds_ulmap0', 'dq6ds_dlmap0')}
    WORLD_DIFF_DQ6 = ((0x300000, 0x120, 0x80, 0x20, 0x20), (0x300c00, 0x260, 0x140, 0x40, 0x20), (0x302400, 0x360, 0x180, 0x40, 0x40), (0x305400, 0xa0, 0xa0, 0x60, 0x40),
                      (0x309c00, 0x60, 0x1c0, 0x40, 0x40), (0x30cc00, 0x160, 0x300, 0x40, 0x40), (0x30fc00, 0x300, 0x2e0, 0x40, 0x40), (0x300000, 0x200, 0x240, 0x20, 0x40))

    def world_plan(self, flag=0):
        """{'base': 1024 x 1024 RGB image (top first), 'pieces': [(bit, x, y, image h x w x 3)]} of the large world plan for the engine's `flag` (DQ VI: its bit 0 chooses the world);
        `pieces` are placed on the background when their bit of the flag is set (x and y counted from the top left corner). Cached in world_plan.npz (DQ VI: one file per world)."""
        g = GM.profile().key; names = self.WORLD_PLAN.get(g, self.WORLD_PLAN['dq4']); name_str = names[flag & 1 if len(names) > 1 else 0]
        f = carry_over_file(self.cache, 'plan_monde.npz', 'world_plan.npz') if len(names) == 1 else os.path.join(self.cache, 'world_plan_%s.npz' % name_str)
        def read(z):
            pieces = [(int(k[1:].split('_')[0]), int(k.split('_')[1]), int(k.split('_')[2]), z[k]) for k in sorted(z.files) if k != 'base']
            return {'base': z['base'], 'pieces': pieces}
        if os.path.exists(f):
            with np.load(f) as z: return read(z)
        with self._lock:
            if os.path.exists(f):
                with np.load(f) as z: return read(z)
            self.env(); out = {}
            for o in self._objs.values():
                if o.type.name != 'TextAsset': continue
                t = o.read(); nm = t.m_Name
                if nm == name_str or (len(names) == 1 and re.fullmatch(r'P\d+_\d+_\d+_\d+', nm)):
                    s = t.m_Script; out[nm] = bytes(s) if isinstance(s, (bytes, bytearray)) else s.encode('utf-8', 'surrogateescape')
            if name_str not in out: return None
            raw = out.pop(name_str); arrs = {'base': np.frombuffer(raw[:1024 * 1024 * 3], np.uint8).reshape(1024, 1024, 3)}
            for nm, b in out.items():
                w = int(nm.split('_')[3]); arrs[nm] = np.frombuffer(b, np.uint8).reshape(-1, w, 3)
            if g == 'dq6':
                for i, (off, x, y, w, h) in enumerate(self.WORLD_DIFF_DQ6):
                    if off + w * h * 3 <= len(raw): arrs['P%d_%d_%d_%d' % (i + 1, x, y, w)] = np.frombuffer(raw[off:off + w * h * 3], np.uint8).reshape(h, w, 3)
            tmp = f + '.tmp.npz'; np.savez(tmp, **arrs); os.replace(tmp, f)
            with np.load(f) as z: return read(z)

    # ---- world map veil ("Map" window, ICityMap: code 5)
    def world_veil(self):
        """192 x 256 x 4 (RGBA) image of the TextAsset WORLD_VEIL (DQ IV `dsdq4wmap01m`, DQ V `dq5ds_wmap1m`, the same blocks in Make_CityMap b__5): grayed silhouette of the lands that the map
        window places over the 10 x 10 pixel blocks (from x = 48, y = 16) not yet explored. Cached. DQ VI has no code 5 (None)."""
        if GM.profile().key not in WORLD_VEIL: return None
        f = carry_over_file(self.cache, 'voile_monde.npy', 'world_veil.npy')
        if os.path.exists(f): return np.load(f)
        with self._lock:
            if os.path.exists(f): return np.load(f)
            self.env()
            for o in self._objs.values():
                if o.type.name != 'TextAsset': continue
                t = o.read()
                if t.m_Name != WORLD_VEIL.get(GM.profile().key, 'dsdq4wmap01m'): continue
                s = t.m_Script; b = bytes(s) if isinstance(s, (bytes, bytearray)) else s.encode('utf-8', 'surrogateescape')
                if len(b) != 192 * 256 * 4: return None
                a = np.frombuffer(b, np.uint8).reshape(192, 256, 4).copy(); np.save(f, a); return a
        return None

    # ---- animated 3D model (FbxObject: Resources "fbx/c<number>", engine packets 31 and 32)
    def fbx_model(self, no):
        """Model "fbx/c%04d": bone hierarchy, skinned mesh (one bone per vertex, or up to 4 with weights), texture, animation clips (position / rotation / scale curves of each bone) -> dict
        (see fbx.FbxModel); cached on disk. The extraction exception (unsupported shader or component) is propagated: the caller then ignores the model."""
        f = os.path.join(self.cache, FBX_PREFIX + 'c%04d.pkl' % no)
        if os.path.exists(f):
            with open(f, 'rb') as fh: return pickle.load(fh)
        with self._lock:
            if os.path.exists(f):
                with open(f, 'rb') as fh: return pickle.load(fh)
            return self._extract_fbx(no, f)

    def prefab_model(self, name_str):
        """A Resources prefab other than "fbx/c<number>" with a legacy Animation component (DQ V's Slime Touch: "SlimeTouch/start_p"...): the same dict as `fbx_model`, read the same way, with the
        shader Custom/DssaCharaTransparent of the orthographic layer and Unity's built-in Quad mesh allowed; cached on disk."""
        f = os.path.join(self.cache, PREFAB_PREFIX + name_str.replace('/', '_') + '.pkl')
        if os.path.exists(f):
            with open(f, 'rb') as fh: return pickle.load(fh)
        with self._lock:
            if os.path.exists(f):
                with open(f, 'rb') as fh: return pickle.load(fh)
            return self._extract_fbx(name_str.split('/')[-1], f, PREFAB_SHADERS)

    def _extract_fbx(self, no, f, allowed=FBX_SHADERS):
        from UnityPy.helpers.MeshHelper import MeshHandler
        go_name = 'c%04d' % no if isinstance(no, int) else no
        pid = self._gos().get(go_name)
        if pid is None: raise KeyError('fbx model not found in the data: %s' % go_name)
        names, parent, pos, rot, scl, paths, t_idx, rendered = [], [], [], [], [], [], {}, []

        def visit(g, parent_idx, path):
            comps = self._components(g); to = comps['Transform']; t = to.read(); i = len(names)
            names.append(g.m_Name); parent.append(parent_idx); paths.append(path); t_idx[to.path_id] = i
            p, q, s = t.m_LocalPosition, t.m_LocalRotation, t.m_LocalScale
            pos.append((p.x, p.y, p.z)); rot.append((q.x, q.y, q.z, q.w)); scl.append((s.x, s.y, s.z))
            if 'SkinnedMeshRenderer' in comps: rendered.append((i, comps['SkinnedMeshRenderer'].read(), None))
            elif 'MeshRenderer' in comps and 'MeshFilter' in comps: rendered.append((i, comps['MeshRenderer'].read(), comps['MeshFilter'].read().m_Mesh))          # maillage non skinne : rigide, suit son noeud (c42, c609)
            elif 'MeshRenderer' in comps: raise ValueError('mesh without filter (%s)' % g.m_Name)
            for ch in t.m_Children:
                cg = self._objs[ch.deref_parse_as_object().m_GameObject.path_id].read()
                visit(cg, i, (path + '/' if path else '') + cg.m_Name)
        root = self._objs[pid].read(); comps = self._components(root)
        visit(root, -1, '')
        if not rendered: raise ValueError('no mesh')
        if root.m_Layer == SCREEN_LAYER:                                    # (c2000, the dream filter)
            res = self._screen_overlay(rendered, scl[0])
            with open(f + '.tmp', 'wb') as fh: pickle.dump(res, fh, protocol=4)
            os.replace(f + '.tmp', f)
            return res
        # materials: a single shader, either "Custom/ImdChara" (vertex color x _Color, alpha blending, ZWrite on, Cull Back, Geometry queue: the title dragon) or "Custom/Effect0" (same
        # color and same alpha blending, but ZWrite off, Cull Off, Transparent+300 queue: the rings and waves of battle attacks; states read from the shader in the data pack);
        # one texture, one scale and one offset per material (Glace c10 to c12 and Gigalame c20 have two textures, c3 two offsets) = one triangle GROUP per submesh;
        # several renderers in one model (Gigaglace c13 to c15, Tornade c16): their bones are laid end to end; a non-skinned mesh follows its node (rigid bone, bind pose = identity)
        tex = {}; tex_names = {}; tfmt = {}; shaders = set(); P, UVS, UV0, BIS, BWS, groups, binds, bones = [], [], [], [], [], [], [], []; nv = 0
        for node, rend, mflt in rendered:
            if mflt is not None and mflt.m_FileID != 0 and mflt.m_PathID == BUILTIN_QUAD:          # Unity's built-in Quad (DQ V's Slime Touch "gameover_p"): 1 x 1 in XY, centered
                v, uv0, tri_list = QUAD_V.copy(), QUAD_UV.copy(), [QUAD_TRI.ravel()]; n = len(v); mesh = None
            else:
                mesh = (rend.m_Mesh if mflt is None else mflt).deref_parse_as_object(); h = MeshHandler(mesh); h.process()
                v = np.array(h.m_Vertices, np.float32).reshape(-1, 3); n = len(v); uv0 = np.array(h.m_UV0, np.float32).reshape(n, -1)[:, :2]; tri_list = h.get_triangles()
            bi = np.array(h.m_BoneIndices, np.int64).reshape(n, -1) if mesh is not None and h.m_BoneIndices is not None else np.zeros((n, 1), np.int64)
            bw = np.array(h.m_BoneWeights, np.float32).reshape(n, -1) if mesh is not None and h.m_BoneWeights is not None else np.ones((n, 1), np.float32)       # a single bone per vertex: weight 1 ("rigid" mesh, the dragon's case)
            if mflt is None:
                bind_r = np.array([[[getattr(b, 'e%d%d' % (r, c)) for c in range(4)] for r in range(4)] for b in mesh.m_BindPose], float); bones_r = [t_idx[b.path_id] for b in rend.m_Bones]
                if len(bones_r) != len(bind_r): raise ValueError('bones (%d) and bind poses (%d) differ' % (len(bones_r), len(bind_r)))
            else: bind_r = np.eye(4)[None]; bones_r = [node]
            off = len(bones); bones += bones_r; binds.append(bind_r)
            for si, tri in enumerate(tri_list):
                mref = rend.m_Materials[min(si, len(rend.m_Materials) - 1)]
                sh = mref.deref_parse_as_object().m_Shader.deref_parse_as_object().m_ParsedForm.m_Name
                if sh not in allowed: raise ValueError('unsupported shader: %s' % sh)
                shaders.add(sh)
                key, scale, offset, col = self._texture(mref, tex, tex_names, tfmt)[:4]          # (Material.mainTextureScale / Offset: c103 shifts the light band by 0.16 in v)
                if key is None or key == WHITE_TEX: raise ValueError('textures: %s' % key)          # (3D model without texture: refused as before, the white texture is only used for map scenery)
                tri = np.array(tri, np.int64).reshape(-1, 3); used = np.unique(tri)          # (only the submesh's vertices: its UVs have their own scale / offset)
                P.append(v[used]); UV0.append(uv0[used]); UVS.append(uv0[used] * np.array(scale, np.float32) + np.array(offset, np.float32))          # (Unity UV: uv * scale + offset; the texture repeats)
                BIS.append(bi[used] + off); BWS.append(bw[used]); gt = np.searchsorted(used, tri) + nv; nv += len(used)
                groups.append({'tex': tex[key], 'tri': gt, 'node': node, 'mat': si, 'col': tuple(float(x) for x in col), 'st': (float(scale[0]), float(scale[1]), float(offset[0]), float(offset[1]))})          # one group per material slot: the clips animate each slot's color and texture placement
        if len(shaders) != 1: raise ValueError('several shaders: %s' % sorted(shaders))
        w = max(x.shape[1] for x in BIS)
        pad = lambda a: np.pad(a, ((0, 0), (0, w - a.shape[1])))          # (rendered with 1 or 4 bones per vertex: weight 0 beyond)
        v = np.concatenate(P); uv = np.concatenate(UVS); uv_raw = np.concatenate(UV0); bi = np.concatenate([pad(x) for x in BIS]); bw = np.concatenate([pad(x) for x in BWS])
        bind = np.concatenate(binds); tri_indices = np.concatenate([g['tri'] for g in groups])
        clips = {}
        for cref in comps['Animation'].read().m_Animations:
            if not getattr(cref, 'm_PathID', 0): continue                      # (an empty slot in the Animation component's list: c1003 has one)
            c = cref.deref_parse_as_object()
            if getattr(c, 'm_Compressed', False): raise ValueError('unsupported compressed animation (%s)' % c.m_Name)
            cb = []
            for kind, entries in (('pos', c.m_PositionCurves), ('rot', c.m_RotationCurves), ('scl', c.m_ScaleCurves)):
                for q in entries:
                    if q.path not in paths: continue
                    ks = q.curve.m_Curve; dim = 4 if kind == 'rot' else 3
                    def vec(x): return ([x.x, x.y, x.z] + ([x.w] if dim == 4 else [])) if hasattr(x, 'x') else [float(x)]
                    cb.append((paths.index(q.path), kind, [k.time for k in ks], [vec(k.value) for k in ks], [vec(k.inSlope) for k in ks], [vec(k.outSlope) for k in ks]))
            mc = []                                                       # material curves: color (_Color r g b a) and texture placement (_MainTex scale / offset x y) of one material slot of a renderer
            for q in getattr(c, 'm_FloatCurves', None) or []:
                target = material_target(q.classID, q.attribute)
                if target is None or q.path not in paths: continue
                ks = q.curve.m_Curve
                if ks: mc.append((paths.index(q.path),) + target + ([k.time for k in ks], [[float(k.value)] for k in ks], [[float(k.inSlope)] for k in ks], [[float(k.outSlope)] for k in ks]))
            ends = [k[2][-1] for k in cb if k[2]] + [k[4][-1] for k in mc]
            clips[c.m_Name] = {'dur': max(ends or [0.0]), 'wrap': int(c.m_WrapMode), 'curves': cb, 'mcurves': mc}          # (length = the last key of ALL its curves: Réflexion c103 has only material curves)
        res = {'names': names, 'parent': parent, 'pos': pos, 'rot': rot, 'scl': scl, 'v': v, 'uv': uv, 'uv0': uv_raw, 'bi': bi, 'bw': bw, 'tri': tri_indices, 'bind': bind, 'bones': bones,
               'tex': groups[0]['tex'], 'groups': groups, 'clips': clips, 'shader': next(iter(shaders))}
        with open(f + '.tmp', 'wb') as fh: pickle.dump(res, fh, protocol=4)
        os.replace(f + '.tmp', f)
        return res

    def _screen_overlay(self, rendered, root_scale):
        """A model of the interface camera's layer: FbxObject instantiates the prefab at the root of the scene, but its layer 8 is drawn by the interface camera, which looks down on the
        XZ plane: a plane lying there covers the screen. The only one in the game, c2000 (the dream filter of the interludes, `white_mask`): Unity's built-in Plane mesh, scale (90, 1, 100) kept
        by the engine (it only shows / hides it), shader Custom/FontAlpha8 (color = vertex color x _Color, alpha = texture alpha x color alpha, read in the shader's GLES program; ZWrite off,
        Transparent queue). -> {'overlay', 'size' (x, z units), 'tex' (RGBA: white, the texture's alpha), 'col', 'st'}; anything else is refused (ValueError) as before."""
        if len(rendered) != 1: raise ValueError('screen layer: %d renderers' % len(rendered))
        node, rend, mflt = rendered[0]
        if mflt is None or mflt.m_FileID == 0 or mflt.m_PathID != BUILTIN_PLANE: raise ValueError('screen layer: not the built-in plane')
        mref = rend.m_Materials[0]; sh = mref.deref_parse_as_object().m_Shader.deref_parse_as_object().m_ParsedForm.m_Name
        if sh != 'Custom/FontAlpha8': raise ValueError('screen layer: unsupported shader: %s' % sh)
        tex = {}; key, scale, offset, col = self._texture(mref, tex)[:4]
        if key is None or key == WHITE_TEX: raise ValueError('screen layer: texture %s' % key)
        a = tex[key].copy(); a[..., :3] = 255                                          # (FontAlpha8 takes only the texture's alpha; white_mask is Alpha8)
        return {'overlay': True, 'size': (10.0 * float(root_scale[0]), 10.0 * float(root_scale[2])), 'tex': a, 'col': tuple(float(x) for x in col),
                'st': (float(scale[0]), float(scale[1]), float(offset[0]), float(offset[1])), 'clips': {}}

    # ---- a character's sprite sheet
    def sheet(self, ident):
        f = os.path.join(self.cache, 'sprite_h%d.npy' % ident)
        if os.path.exists(f): return np.load(f)
        with self._lock:
            if os.path.exists(f): return np.load(f)
            tx = self._textures()
            for name_str in ('h%d' % ident, 'h%03d' % ident):
                o = tx.get(name_str)
                if o is not None:
                    a = np.array(o.read().image.convert('RGBA')); np.save(f, a); return a
        return None

    # ---- background cache preparation
    def _prep_file(self):
        return os.path.join(self.cache, 'preparation.json')

    def _elements(self, entries):
        """[(kind, name, file)]: everything the game may need to extract from the data pack."""
        el = [('texture', 'field', 'tex_field.npy')]
        el += [('map', n, MAP_PREFIX + '%s.pkl' % n) for n in entries['maps']]
        el += [('sheet', n, 'sprite_h%d.npy' % int(n)) for n in entries['sheets']]
        el += [('texture', n, 'tex_%s.npy' % n) for n in entries['monsters'] + entries.get('plans', []) + entries.get('effects', [])]
        names = self.WORLD_PLAN.get(GM.profile().key, self.WORLD_PLAN['dq4'])
        if len(names) == 1: el.append(('world_plan', '0', 'world_plan.npz'))
        else: el += [('world_plan', str(i), 'world_plan_%s.npz' % n) for i, n in enumerate(names)]
        if GM.profile().key in WORLD_VEIL: el.append(('world_veil', 'world_veil', 'world_veil.npy'))          # (the veil of the unexplored lands of the map window: read on its first opening otherwise)
        fbx = sorted(set(int(n) for n in entries.get('fbx', ())) | {1018} | set(FBX_COMBAT) | set(FBX_SCREEN))
        el += [('fbx', str(n), FBX_PREFIX + 'c%04d.pkl' % n) for n in fbx]          # every 3D model of the data (c0003 to c2000: battle attacks, the title dragon, the dream filter, the models of the maps' scenes): one read
        return el                                                                    # on demand loaded the whole data pack in the middle of a game (seconds of stalls when a scene with a model not yet cached loaded)

    def _purge_old(self):
        """Deletes the maps of an old cache version (carte2_ to carte11_: carte11_: without the event objects (FMAP beyond nobj) or FEAN; carte10_: transparent surfaces sorted by half of the flags and not by their priority; without the palette and sequence animations, sunken chests, lid upside down or open sideways: object rotations in the wrong order; carte9_: untextured surfaces and scenery carried by the root missing), which are now useless. Also the maps `carte14_` and the 3D models `fbx1_`, whose keys were in French (map15_ / fbx2_ since the switch to English), and `fbx2_` / `fbx3_` (without the material curves and per-material groups: fbx4_)."""
        try:
            for f in os.listdir(self.cache):
                if re.fullmatch(r'carte[0-9]+_.+\.pkl|map(?:[0-9]|1[0-4])_.+\.pkl|fbx[123]_.+\.pkl|fonds_decor\.json', f): os.remove(os.path.join(self.cache, f))
        except OSError as ex:
            self.log('Old maps in the cache not deleted: %s' % ex)

    def prepare(self, stop):
        """Extracts in the background, at a relaxed pace, all the maps, sprite sheets and monster textures not yet in the cache: the game never has to read
        the big data pack in the middle of a game again (the first unknown map, a well for example, froze the screen during the read). When everything is ready, the memory is
        freed, and subsequent launches no longer touch the pack. `stop`: Event to interrupt."""
        try:
            fp = self._prep_file(); entries = None
            if os.path.exists(fp):
                try: entries = json.load(open(fp, encoding='utf-8'))
                except Exception: entries = None
                if entries is not None and ('maps' not in entries or 'plans' not in entries or 'effects' not in entries or 'fbx' not in entries): entries = None            # old list (French keys, or without the map window plans, the effect textures or the list of 3D models): rebuilt
            if entries is not None:
                failures = set(entries.get('failures', []))
                if all(os.path.exists(os.path.join(self.cache, f)) or n in failures for _, n, f in self._elements(entries)): self._purge_old(); return       # everything already ready
            else:
                with self._lock:
                    go = self._gos(); tx = self._textures()
                entries = {'maps': sorted(n[:-4] for n in go if n.endswith('.fld')),
                         'sheets': sorted(str(int(n[1:])) for n in tx if re.fullmatch(r'h\d+', n)),
                         'monsters': sorted(n for n in tx if re.fullmatch(r'm\d{3}', n)), 'effects': sorted(n for n in tx if re.fullmatch(r'm\d{4}', n)), 'plans': sorted(n for n in tx if re.fullmatch(r'[a-z0-9]+_map\d|dsdq4wmap\d+|dq5ds_wmap\d+', n)),
                         'fbx': sorted((str(int(n[1:])) for n in go if re.fullmatch(r'c\d{4}', n)), key=int), 'failures': []}
            failures = set(entries.get('failures', []))
            t_start = time.time(); n_done = 0
            self.log('Preparing the cache in the background (once only, about a minute and a half; the game stays playable)...')
            for kind, n, f in self._elements(entries):
                if stop.is_set(): return
                if os.path.exists(os.path.join(self.cache, f)) or n in failures: continue
                t = time.perf_counter()
                try:
                    if kind == 'map': self.game_map(n, True)
                    elif kind == 'sheet': self.sheet(int(n))
                    elif kind == 'world_plan': self.world_plan(int(n) if n.isdigit() else 0)
                    elif kind == 'world_veil':
                        if self.world_veil() is None and GM.profile().key in WORLD_VEIL: raise ValueError('TextAsset %s missing or of another size' % WORLD_VEIL[GM.profile().key])          # (noted among the failures: not searched again at each launch)
                    elif kind == 'fbx': self.fbx_model(int(n))
                    else: self.texture(n)
                except Exception as ex:
                    failures.add(n); self.log('Preparation: %s %s skipped (%s)' % (kind, n, str(ex)[:60]))
                n_done += 1
                time.sleep(min(0.25, 1.0 * (time.perf_counter() - t)) + 0.01)          # relaxed pace
            entries['failures'] = sorted(failures)
            with open(fp + '.tmp', 'w', encoding='utf-8') as fh: json.dump(entries, fh)
            os.replace(fp + '.tmp', fp)
            if n_done: self.log('Cache preparation finished: %d items in %.0f s.' % (n_done, time.time() - t_start))
            self._purge_old()
            self.free_up()
        except Exception as ex:
            self.log('Cache preparation interrupted: %s' % ex)


if __name__ == '__main__':
    # Cache preparation in a separate process (launched by the viewer, low priority): in a game thread, the extractions (pure Python) took the
    # interpreter lock away from it and made the screen stutter. End of the process: when standard input closes (the viewer has stopped).
    import sys
    LG.select_language(os.environ.get('DQ_LANGUAGE') or 'system')          # (the tool's language, given by the game that launches this process; on its own, the computer's language)
    stop = threading.Event()
    threading.Thread(target=lambda: (sys.stdin.buffer.read(), stop.set()), daemon=True).start()
    try:
        Data(sys.argv[1], sys.argv[2], log=lambda *a: print(*a, flush=True)).prepare(stop)
    finally:
        # The "daemon" thread above is blocked in `sys.stdin.buffer.read()` and holds its lock: a normal interpreter shutdown ends in
        # `Fatal Python error: _enter_buffered_busy` and code 134 (SIGABRT) as soon as the preparation finishes before the game. The process exits without going through it.
        sys.stdout.flush(); sys.stderr.flush(); os._exit(0)
