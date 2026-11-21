"""Field: maps, scenery, characters (sprites), shadows, world map, place icons."""
import math, os, threading, time
import numpy as np
import pyglet
from pyglet import gl
from dq456_pc.world import scenery_outlines as CD
from dq456_pc.world import flat_sceneries as DP
from dq456_pc.world import data as DO
from dq456_pc.world import plans as PL
from dq456_pc.world import fbx as FX
from dq456_pc.world import hd as HD
from dq456_pc.menus import sheets as SH
from dq456_pc.world import map_objects as OC
from dq456_pc.world import map_tint as TC
from dq456_pc.world import volcano as VO
from dq456_pc import games as GM
from dq456_pc.core import engine as ENGINE
from dq456_pc.core import load_tracking as SV
from dq456_pc.render import title_sky as CT
from dq456_pc.render import postfx as PF
from dq456_pc.window.helpers import screen_column, whole_pixels
from dq456_pc.window.constants import (CH_ATLAS, CH_CELL, CH_ORIENT_V2, EXTENDED_SKIES, CH_GUTTER, CH_ICONS, CH_ICON_TEXTURE, CH_PATTERN_END, CH_PATTERN_LIMIT, CH_PATTERN_NUM, CH_PATTERN_STEP,
    CH_PATTERN_PERIOD, CH_PX, SCENERY_BIAS, ORIGINAL_GUTTER, HD_GUTTER, HD_LEVELS, MODE_PP, CAM_JUMP_TOWN, TITLE_MAP)


def overlay_rects(win, W, H, area=(0.0, 0.0, 1.0, 1.0)):
    """Layout of an interface-layer picture: `win` = (u0, v0, u1, v1), the texture coordinates at the window's corners (beyond 0..1 where the window shows more than the plane);
    `area`: the part of the texture that is drawn (the effect's area, with its feathered margin: may go past 0..1). -> ((x0, y0, x1, y1) of that part in pixels, whole (it may go past the
    window), [((x0, y0, x1, y1), side), ...] of the window's parts beyond it: 'l', 'r', 'b', 't')."""
    u0, v0, u1, v1 = win
    X = lambda u: (u - u0) / (u1 - u0) * W; Y = lambda v: (v - v0) / (v1 - v0) * H
    img = (X(area[0]), Y(area[1]), X(area[2]), Y(area[3]))
    x0, y0, x1, y1 = (min(max(z, 0.0), lim) for z, lim in ((img[0], W), (img[1], H), (img[2], W), (img[3], H)))
    fills = [(r, side) for r, side in (((0, 0, x0, H), 'l'), ((x1, 0, W, H), 'r'), ((x0, 0, x1, y0), 'b'), ((x0, y1, x1, H), 't')) if r[2] - r[0] >= 0.5 and r[3] - r[1] >= 0.5]
    return img, fills


FEATHER = 0.25                       # width of the margin added around the dream's picture, as a share of its smaller side: there its alpha goes from the edge's own to opaque


def gaussian_1d(v, sigma):
    """Profile `v` blurred (Gaussian of `sigma` samples, its ends carried on)."""
    r = max(1, int(3 * sigma)); x = np.arange(-r, r + 1, dtype=np.float32); g = np.exp(-x * x / (2.0 * sigma * sigma)); g /= g.sum()
    return np.convolve(np.pad(np.asarray(v, np.float32), r, mode='edge'), g, mode='valid')


def feathered(tex, area_uv, share=FEATHER):
    """the effect's area of an interface picture (rows top first, RGBA), with a margin on each side in which the picture's white rises smoothly (smoothstep) from the area's edge to opaque.
    The margin carries the edge's alpha outwards, blurred along the edge more and more as it goes (texel noise first, then the edge's shapes): the clouds close up softly instead of stopping on a
    straight line where the scene still showed through, and without streaks (an edge carried straight out repeats its every irregularity). Corners: the two edges mixed by direction, Euclidean
    distance (round corners). -> (array, its uv rectangle, past the area)."""
    h, w = tex.shape[:2]; u0, v0, u1, v1 = area_uv
    r0, r1, c0, c1 = int(round((1 - v1) * h)), int(round((1 - v0) * h)), int(round(u0 * w)), int(round(u1 * w))
    crop = tex[r0:r1, c0:c1]; al = crop[..., 3]
    ch, cw = crop.shape[:2]; k = max(1, int(round(share * min(ch, cw))))
    yy, xx = np.mgrid[0:ch + 2 * k, 0:cw + 2 * k]
    xc, yc = np.clip(xx - k, 0, cw - 1), np.clip(yy - k, 0, ch - 1)
    dx, dy = np.abs(xx - k - xc).astype(np.float32), np.abs(yy - k - yc).astype(np.float32)
    t = np.minimum(np.hypot(dx, dy) / k, 1.0); smooth = lambda z: z * z * (3 - 2 * z)
    wx = np.where(dx + dy > 0, dx / np.maximum(dx + dy, 1e-6), 0.0)
    def edges(sigma):
        side = np.where(xx < k, gaussian_1d(al[:, 0], sigma)[yc], gaussian_1d(al[:, -1], sigma)[yc])
        top = np.where(yy < k, gaussian_1d(al[0, :], sigma)[xc], gaussian_1d(al[-1, :], sigma)[xc])
        return wx * side + (1.0 - wx) * top
    near, far = edges(2.0), edges(k / 3.0)
    v = near + (far - near) * smooth(np.minimum(3 * t, 1.0))                                           # (the edge's alpha, blurred more and more)
    a = np.where(t > 0, v + (255.0 - v) * smooth(t), np.pad(al, k, mode='edge'))                     # (then up to opaque; the area itself unchanged)
    out = np.full((ch + 2 * k, cw + 2 * k, 4), 255, np.uint8); out[k:k + ch, k:k + cw, :3] = crop[..., :3]; out[..., 3] = np.clip(np.round(a), 0, 255).astype(np.uint8)
    return out, (u0 - k / w, v0 - k / h, u1 + k / w, v1 + k / h)


_GRIDS = {}


def _quad_grid(rows, cols):
    """Vertices (rows * cols * 6, 3) of a grid of tile quads, two triangles each (NW NE SE, NW SE SW: the order of the game's quads), one unit a tile; kept per size."""
    g = _GRIDS.get((rows, cols))
    if g is None:
        X, Z = np.meshgrid(np.arange(cols, dtype=np.float32), np.arange(rows, dtype=np.float32)); O = np.zeros_like(X)
        q = np.stack([np.stack([X, O, Z], -1), np.stack([X + 1, O, Z], -1), np.stack([X + 1, O, Z + 1], -1), np.stack([X, O, Z + 1], -1)], 2)
        g = _GRIDS[(rows, cols)] = q[:, :, [0, 1, 2, 0, 2, 3], :].reshape(-1, 3)
    return g


class FieldMixin:
    """Field: maps, scenery, characters (sprites), shadows, world map, place icons."""

    # ---- textures
    def _anisotropy(self):
        """Maximum usable anisotropy level (1.0 if the extension is missing), capped at 8."""
        try:
            v = (gl.GLfloat * 1)(); gl.glGetFloatv(0x84FF, v); return max(1.0, min(8.0, float(v[0])))
        except Exception:
            return 1.0

    def _tex(self, arr, repeat, mip=0):
        """RGBA texture; mip > 0: mipmaps (limited to `mip` levels, 99 = all) + anisotropic filtering."""
        h, w = arr.shape[:2]
        img = pyglet.image.ImageData(w, h, 'RGBA', np.ascontiguousarray(arr[::-1]).tobytes(), pitch=w * 4)
        t = img.get_texture()
        gl.glBindTexture(t.target, t.id)
        m = gl.GL_REPEAT if repeat else gl.GL_CLAMP_TO_EDGE
        gl.glTexParameteri(t.target, gl.GL_TEXTURE_WRAP_S, m); gl.glTexParameteri(t.target, gl.GL_TEXTURE_WRAP_T, m)
        if mip:
            gl.glGenerateMipmap(t.target)
            gl.glTexParameteri(t.target, gl.GL_TEXTURE_MAX_LEVEL, min(mip, 20))
            self._filter_texture(t, True)
        return t

    def _filter_texture(self, t, smooth):
        """Filtering of a mipmapped scenery texture (F3): from afar, always trilinear mipmaps + anisotropic (stable); up close, smooth = bilinear, otherwise nearest neighbor (sharp texels)."""
        gl.glBindTexture(t.target, t.id)
        gl.glTexParameteri(t.target, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR_MIPMAP_LINEAR)
        gl.glTexParameteri(t.target, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR if smooth else gl.GL_NEAREST)
        if self.aniso > 1.0: gl.glTexParameterf(t.target, 0x84FE, self.aniso)

    def _original_sheet(self, ident):
        """Original sheet `h<ident>` (sharp pixels); False if absent. 194 = alias of 193 (Chara.SetTexture)."""
        ident = HD.SHEET_ALIASES.get(ident, ident)
        lookup_key = (ident, 0)
        if lookup_key in self.sprite_tex: return self.sprite_tex[lookup_key]
        arr = self.data.sheet(ident & 0xfff if ident >= HD.V2_KEY else ident); r = False
        if arr is not None:
            if ident >= HD.V2_KEY: arr, geo = HD.pack_sheet_v2(arr, ident)          # DQ V / VI: texture, rows and mesh in the identifier (Chara.SetTexture)
            else: geo = HD.sheet_geometry(ident, arr)
            arr = HD.clean_sheet(arr, cell=(geo['width'], geo['height']))[0]          # hero: body opaque, halo removed, pixels enclosed by the body opaque (the soft halo is dropped)                  # character: 24 x 32 cells on 3 columns; boat (193): 40 x 40 cells on 12 columns and larger panel (see hd.SPECIAL_SHEETS)
            atlas, _, _ = HD.upscale_sheet(arr, geo['width'], geo['height'], geo['cols'], geo['nl'], None, ORIGINAL_GUTTER, 1, lambda cells: HD._dilate(cells, 2))         # transparent pixels = color of the neighboring edge: no more dark fringe when filtering
            r = dict(t=self._tex(atlas, False), tw=atlas.shape[1], th=atlas.shape[0], nl=geo['nl'], cols=geo['cols'], pw=geo['pw'], ph=geo['ph'], hd=False, cl=geo['width'], ch=geo['height'], g=ORIGINAL_GUTTER, arr=arr, geo=geo)
        self.sprite_tex[lookup_key] = r
        return r

    def _calc_hd(self, arr, geo, ident, cid, algo):
        """(background task) HD atlas of a sheet, read from the disk cache if it exists (a sheet with a special slicing has a file of its own: the boat has nothing in common with the old 24 x 32 slicing)."""
        suffix = '' if geo['standard'] else '_g%dx%dx%dx%d' % (geo['width'], geo['height'], geo['cols'], geo['nl'])
        f = os.path.join(self.args.cache, 'hd_%s_v%d_h%d%s.npy' % (cid, HD.VERSION, ident, suffix))
        try:
            if os.path.exists(f): return np.load(f), geo['width'] * HD.FACTOR, geo['height'] * HD.FACTOR
        except Exception: pass
        atlas, cl, ch = HD.upscale_sheet(arr, geo['width'], geo['height'], geo['cols'], geo['nl'], algo, HD_GUTTER)
        try: np.save(f, atlas)
        except Exception: pass
        return atlas, cl, ch

    def _sheet(self, ident, mode):
        """Sprite sheet for the selected mode. The HD versions are computed in a background task: in the meantime, the original is used."""
        ident = HD.SHEET_ALIASES.get(ident, ident)
        orig = self._original_sheet(ident)
        if not orig or mode in (0, MODE_PP): return orig
        lookup_key = (ident, mode)
        if lookup_key in self.sprite_tex: return self.sprite_tex[lookup_key]
        fut = self.hd_jobs.get(lookup_key)
        if fut is None:
            _name, cid, algo = HD.MODES[mode]
            self.hd_jobs[lookup_key] = self.pool.submit(self._calc_hd, orig['arr'], orig['geo'], ident, cid, algo)
            return orig
        if not fut.done(): return orig
        del self.hd_jobs[lookup_key]
        try: atlas, cl, ch = fut.result()
        except Exception as ex:
            print('HD smoothing failed (%s, h%d): %s' % (HD.MODES[mode][0], ident, ex), flush=True); self.sprite_tex[lookup_key] = orig; return orig
        r = self.sprite_tex[lookup_key] = dict(t=self._tex(atlas, False, HD_LEVELS), tw=atlas.shape[1], th=atlas.shape[0], nl=orig['nl'], cols=orig['cols'], pw=orig['pw'], ph=orig['ph'], cl=cl, ch=ch, g=HD_GUTTER, hd=True)
        if not self.smoothing: self._filter_texture(r['t'], False)
        return r

    def _field_atlas(self, name):
        """The tile atlas `name` copied with a gutter of CH_GUTTER pixels around each 16 x 16 cell (texture, cells per row, cells per column); the original atlas for the place icons."""
        arr = self.data.texture(name)
        if arr is None: return False
        nr, nc = arr.shape[0] // 16, arr.shape[1] // 16
        t4 = arr[:nr * 16, :nc * 16].reshape(nr, 16, nc, 16, 4).transpose(0, 2, 1, 3, 4)       # (row, column, y, x, channel): one 16 x 16 tile per cell
        t4 = np.pad(t4, ((0, 0), (0, 0), (CH_GUTTER, CH_GUTTER), (CH_GUTTER, CH_GUTTER), (0, 0)), mode='edge')
        self.field_tex = self._tex(np.ascontiguousarray(t4.transpose(0, 2, 1, 3, 4).reshape(nr * CH_CELL, nc * CH_CELL, 4)), False)
        icons = self.data.texture(CH_ICON_TEXTURE[0]) if CH_ICON_TEXTURE[0] else None           # DQ VI: the place icons have their own texture (Field.Init: Field/symbol)
        self.field_tex_icon = self._tex(arr if icons is None else icons, False); self.field_nearest = None
        self.field_tex_name = name; self.field_cells = (nc, nr)
        return True

    def _update_field_mesh(self, ch):
        """DQ VI's world map: meshes of 25-column tile rows whose texture coordinates the engine computed (Engine._world_mesh), placed where the engine puts them (Field.SetIndexList:
        tile (column, row) at pixel (x + 16 column, -z + 16 row)); the atlas is the Unity texture field<type> (Field.Init)."""
        key = ('mesh', ch.get('type'), ch['serial'] if 'serial' in ch else hash(b''.join(m['uv'].tobytes() for m in ch['mesh'])), self.mode == MODE_PP)          # (the engine numbers each new mesh: no hashing of the vertices every frame)
        if key == self.field_key: return
        name = 'field%d' % (int(ch.get('type') or 0) & 0xf)              # (Field.Init: the low nibble names the texture; bit 4 = a second tile mesh, bits 8-15 / 16-23 = their render queues)
        if self.field_tex is None or getattr(self, 'field_tex_name', None) != name:
            if not self._field_atlas(name): return
        self.field_key = key; self.field_pattern = None
        nc, nr = self.field_cells; aw, ah = float(nc * CH_CELL), float(nr * CH_CELL)
        P, U = [], []
        for m in ch['mesh']:
            uv = m['uv']; rows = uv.shape[0]
            if not rows: continue
            grid = _quad_grid(rows, uv.shape[1])
            q = grid + np.array([m['pos'][0] / CH_PX, 0.0, -m['pos'][2] / CH_PX], np.float32)
            u, v = uv[..., 0] * nc, uv[..., 1] * nr                                          # in cells; the cell of a quad: its smallest corner
            cu, cv = np.floor(u.min(-1, keepdims=True) + 1e-3), np.floor(v.min(-1, keepdims=True) + 1e-3)
            t = np.stack([(cu * CH_CELL + CH_GUTTER + np.clip(u - cu, 0, 1) * 16) / aw, (cv * CH_CELL + CH_GUTTER + np.clip(v - cv, 0, 1) * 16) / ah], -1)
            P.append(q); U.append(t[:, :, [0, 1, 2, 0, 2, 3], :].reshape(-1, 2))
        if not P: return
        pos = np.concatenate(P).astype(np.float32).ravel(); uv = np.concatenate(U).astype(np.float32).ravel(); n = len(pos) // 3
        if self.field_vl is not None and self.field_vl.count == n:
            self.field_vl.position[:] = pos; self.field_vl.tex_coords[:] = uv
        else:
            if self.field_vl is not None: self.field_vl.delete()
            self.field_vl = self.prog.vertex_list(n, gl.GL_TRIANGLES, position=('f', pos), tex_coords=('f', uv), colors=('f', np.ones(n * 4, np.float32)))

    def _update_field(self, e):
        """(Re)builds the tile grid when the engine sends another one; the atlas is the Unity `field` texture."""
        if e.field and 'mesh' in e.field: return self._update_field_mesh(e.field)
        ch = e.field; eng = e.world
        if eng is not None and self._field_view is not None:      # whole map read from the engine: the whole visible area is drawn (the engine only sends 25 columns of it)
            cx, cz, dl, dh = self._field_view
            x0 = 4 * int(math.floor((cx - dl - 1) / 4)); x1 = x0 + 4 * int(math.ceil((2 * dl + 7) / 4))                  # bounds on multiples of 4 and constant width: the mesh keeps the same size
            y0 = 4 * int(math.floor((cz - dh - 1) / 4)); y1 = y0 + 4 * int(math.ceil((2 * dh + 7) / 4))                  # while walking, and its vertices are replaced in place (no reallocation: no stutter)
            x0, x1 = max(x0, int(cx) - 120), min(x1, int(cx) + 120); y0, y1 = max(y0, int(cz) - 80), min(y1, int(cz) + 80)
            cols = x1 - x0
            t = eng['ids'][(np.arange(y0, y1) % eng['h'])[:, None], (np.arange(x0, x1) % eng['l'])[None, :]].astype(np.int32).ravel()
            ch = {'x0': x0, 'y0': y0, 'cols': cols}
        else:
            t = np.array(ch['tiles'], np.int32)
        lookup_key = (ch['x0'], ch['y0'], ch['cols'], hash(t.tobytes()), self.mode == MODE_PP)
        if lookup_key == self.field_key: self._refresh_patterns(); return
        self.field_key = lookup_key
        if self.field_tex is None:
            arr = self.data.texture('field')
            if arr is None: return
            t4 = arr[:CH_ATLAS * 16, :CH_ATLAS * 16].reshape(CH_ATLAS, 16, CH_ATLAS, 16, 4).transpose(0, 2, 1, 3, 4)       # (row, column, y, x, channel): one 16 x 16 tile per cell
            t4 = np.pad(t4, ((0, 0), (0, 0), (CH_GUTTER, CH_GUTTER), (CH_GUTTER, CH_GUTTER), (0, 0)), mode='edge')
            self.field_tex = self._tex(np.ascontiguousarray(t4.transpose(0, 2, 1, 3, 4).reshape(CH_ATLAS * CH_CELL, CH_ATLAS * CH_CELL, 4)), False)      # tile atlas, with gutters
            self.field_tex_icon = self._tex(arr, False)                                                  # original atlas: the place icons are arbitrary rectangles there
            self.field_nearest = None
        cols = ch['cols']; lines = len(t) // cols
        g = t[:lines * cols].reshape(lines, cols)
        self.field_g = g; self.field_acc_seen = tuple(self.acc_pattern)
        self.field_pattern = np.zeros_like(g) if ENGINE.ENGINE_V2 else CH_PATTERN_NUM[np.searchsorted(CH_PATTERN_END, g, side='right')]
        if self.field_base is None or self.field_base[0] != (cols, lines):        # grid vertices at the origin (NW, NE, SE, SW, two triangles per tile), then offset by (x0, y0)
            X, Z = np.meshgrid(np.arange(cols), np.arange(lines)); O = np.zeros_like(X)
            P = np.stack([np.stack([X, O, Z], -1), np.stack([X + 1, O, Z], -1), np.stack([X + 1, O, Z + 1], -1), np.stack([X, O, Z + 1], -1)], 2).astype(np.float32)   # (l, c, 4, 3)
            self.field_base = ((cols, lines), P[:, :, [0, 1, 2, 0, 2, 3], :].reshape(-1, 3))
        pos = (self.field_base[1] + np.array([ch['x0'], 0, ch['y0']], np.float32)).ravel(); uv = self._tile_uv(g + self.acc_pattern[self.field_pattern])
        n = len(pos) // 3
        if self.field_vl is not None and self.field_vl.count == n:                  # same size: in-place replacement
            self.field_vl.position[:] = pos; self.field_vl.tex_coords[:] = uv
        else:
            if self.field_vl is not None: self.field_vl.delete()
            self.field_vl = self.prog.vertex_list(n, gl.GL_TRIANGLES, position=('f', pos), tex_coords=('f', uv.ravel()), colors=('f', np.ones(n * 4, np.float32)))

    def _tile_uv(self, g):
        """Texture coordinates (6 vertices per tile, flat) of the tile number grid `g`; the tiles are stored upside down in the atlas."""
        aw = float(CH_ATLAS * CH_CELL)                                                  # atlas copied with a gutter of CH_GUTTER pixels around each tile
        if ENGINE.ENGINE_V2:                                                            # DQ V / VI: cell | orientation << 12 (CH_ORIENT_V2)
            o = CH_ORIENT_V2[(g >> 12) & 0xf]; g = g & 0xfff
            u0 = ((g % CH_ATLAS) * CH_CELL + CH_GUTTER) / aw; v0 = ((g // CH_ATLAS) * CH_CELL + CH_GUTTER) / aw; d = 16.0 / aw
            U = np.stack([u0[..., None] + o[..., 0] * d, v0[..., None] + o[..., 1] * d], -1).astype(np.float32)          # (l, c, 4, 2)
            return U[:, :, [0, 1, 2, 0, 2, 3], :].reshape(-1, 2).ravel()
        u0 = ((g % CH_ATLAS) * CH_CELL + CH_GUTTER) / aw; v0 = ((g // CH_ATLAS) * CH_CELL + CH_GUTTER) / aw; d = 16.0 / aw
        U = np.stack([np.stack([u0, v0], -1), np.stack([u0 + d, v0], -1), np.stack([u0 + d, v0 + d], -1), np.stack([u0, v0 + d], -1)], 2).astype(np.float32)      # (l, c, 4, 2)
        return U[:, :, [0, 1, 2, 0, 2, 3], :].reshape(-1, 2).ravel()

    def _advance_patterns(self):
        """One game frame on the world map (NativeManager.Update -> Field.UpdatePattern): every 17 frames, the offset of each pattern advances by one step, then falls back to 0 at its limit."""
        self.pattern_n += 1
        if self.pattern_n < CH_PATTERN_PERIOD: return
        self.pattern_n = 0
        for i in range(1, len(CH_PATTERN_STEP)):
            v = int(self.acc_pattern[i]) + CH_PATTERN_STEP[i]
            self.acc_pattern[i] = v if v < CH_PATTERN_LIMIT[i] else 0

    def _refresh_patterns(self):
        """Updates in place the texture coordinates of the animated tiles when a pattern has changed step."""
        acc = tuple(self.acc_pattern)
        if acc == self.field_acc_seen or self.field_vl is None or self.field_pattern is None: return
        self.field_acc_seen = acc
        if self.field_pattern.any(): self.field_vl.tex_coords[:] = self._tile_uv(self.field_g + self.acc_pattern[self.field_pattern])

    def _paint_field(self, e):
        if self.field_vl is None or self.field_tex is None: return
        nearest = self.mode == MODE_PP or not self.smoothing          # pixel perfect (integer scale) or smoothing off (F3): nearest neighbor; otherwise sharp pixels with softened boundaries (like the original sprites)
        if self.field_nearest != nearest:
            self.field_nearest = nearest
            for t in (self.field_tex, self.field_tex_icon):
                gl.glBindTexture(t.target, t.id)
                gl.glTexParameteri(t.target, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST if nearest else gl.GL_LINEAR); gl.glTexParameteri(t.target, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST if nearest else gl.GL_LINEAR)
        sharp = 0.0 if nearest else 1.0
        tint = self._scenery_tint()                       # packet 33 on the world map: Field.SetColor = color of the tile material (Custom/Field: texture x _Color) and of each symbol (own alpha kept)
        if tint is not None: self._set_tint(tint)
        nc, nr = getattr(self, 'field_cells', (CH_ATLAS, CH_ATLAS))
        self.prog['cutoff'] = 0.05; self.prog['crisp'] = sharp; self.prog['tsize'] = (float(nc * CH_CELL), float(nr * CH_CELL))
        self._paint(self.field_tex, self.field_vl)
        ts = float(CH_ICON_TEXTURE[1]); self.prog['tsize'] = (ts, ts)
        q, uv, col = [], [], []
        for s in sorted(e.symbols.values(), key=lambda s: s.get('prio', ENGINE.SYMBOL_PRIO)):          # place icons: laid flat (the hero stands at the middle of the bottom edge); the highest (closest to the camera) last
            r = CH_ICONS.get(s['kind'])
            if r is None or not s['visible']: continue
            ox, bottom, w, h = r
            sc = s.get('scale', 1.0)                       # (Field.SetSymbolScale: the rings of the barrier of Nadiria grow from their top-left corner)
            x0, z0 = (whole_pixels(s['x']) if self.mode == MODE_PP else s['x']) / CH_PX, (whole_pixels(s['y']) if self.mode == MODE_PP else s['y']) / CH_PX; x1, z1 = x0 + w * sc / CH_PX, z0 + h * sc / CH_PX       # flat on the map, like the tiles (drawn as seen from above)
            q += [(x0, 0.02, z0), (x1, 0.02, z0), (x1, 0.02, z1), (x0, 0.02, z0), (x1, 0.02, z1), (x0, 0.02, z1)]
            u0, u1, v1, v0 = ox / ts, (ox + w) / ts, 1 - (bottom - h) / ts, 1 - bottom / ts
            uv += [(u0, v0), (u1, v0), (u1, v1), (u0, v0), (u1, v1), (u0, v1)]       # upside down in the atlas, like the tiles: the top of the map = the bottom of the image
            col += [(1.0, 1.0, 1.0, float(s['alpha']))] * 6
        if GM.profile().key == 'dq5':                                       # DQ V: the volcano's crater and smoke over the mountain's icon (Volcano, Custom/FieldVolcano: after the icons)
            for s in e.symbols.values():
                if s['kind'] != VO.MOUNTAIN or not s['visible']: continue
                x0, z0 = (whole_pixels(s['x']) if self.mode == MODE_PP else s['x']) / CH_PX, (whole_pixels(s['y']) if self.mode == MODE_PP else s['y']) / CH_PX
                sc = s.get('scale', 1.0)
                for lx, lz, w, h, alpha, kind in VO.quads(self.engine.frames, CH_ICONS[VO.CRATER][2:], CH_ICONS[VO.SMOKE][2:]):
                    ox, bottom, rw, rh = CH_ICONS[kind]
                    xa, za = x0 + lx * sc / CH_PX, z0 - lz * sc / CH_PX; xb, zb = xa + w * sc / CH_PX, za + h * sc / CH_PX
                    q += [(xa, 0.02, za), (xb, 0.02, za), (xb, 0.02, zb), (xa, 0.02, za), (xb, 0.02, zb), (xa, 0.02, zb)]
                    u0, u1, v1, v0 = ox / ts, (ox + rw) / ts, 1 - (bottom - rh) / ts, 1 - bottom / ts
                    uv += [(u0, v0), (u1, v0), (u1, v1), (u0, v0), (u1, v1), (u0, v1)]
                    col += [(1.0, 1.0, 1.0, float(s['alpha']) * alpha)] * 6
        if q:
            n = len(q); pos = np.array(q, np.float32).ravel(); tc = np.array(uv, np.float32).ravel(); cc = np.array(col, np.float32).ravel()
            if self.field_icon is None or self.field_icon[0] != n:
                if self.field_icon is not None: self.field_icon[1].delete()
                self.field_icon = (n, self.prog.vertex_list(n, gl.GL_TRIANGLES, position=('f', pos), tex_coords=('f', tc), colors=('f', cc)))
            else:
                v = self.field_icon[1]; v.position[:] = pos; v.tex_coords[:] = tc; v.colors[:] = cc
            self._linked_domain = None                                  # vertices written after the 1st draw of the frame: without rebind + commit() they go out with the previous frame (icons one frame late, absent for one frame when their number changes)
            gl.glDepthMask(gl.GL_FALSE); gl.glDepthFunc(gl.GL_LEQUAL)          # Custom/FieldSymbol: Blend SrcAlpha OneMinusSrcAlpha, ZWrite Off, ZTest LEqual, Transparent queue (sorted back to front: by priority, above);
            self._paint(self.field_tex_icon, self.field_icon[1])                # with the depth written, an icon drawn later vanished under an earlier one at the same height (the barrier of Nadiria under the castle, its rings under each other)
            gl.glDepthMask(gl.GL_TRUE); gl.glDepthFunc(gl.GL_LESS)
        if tint is not None: self._set_tint((1.0, 1.0, 1.0))
        self.prog['crisp'] = 0.0

    def _paint(self, tex, vl, first=0, count=None):
        """Draws a vertex_list (or `count` of its vertices from `first`), binding the VAO and the buffers only once per frame (much faster than vl.draw)."""
        gl.glBindTexture(tex.target, tex.id)
        try:
            dom = vl.domain
            if dom is not self._linked_domain:
                dom.vao.bind()
                for buf, _ in dom.buffer_attributes: buf.commit()
                self._linked_domain = dom
            gl.glDrawArrays(gl.GL_TRIANGLES, vl.start + first, vl.count - first if count is None else count)
        except AttributeError:
            vl.draw(gl.GL_TRIANGLES)

    def _purge_outlines(self, src, guard=CD.KEPT_TEXTURES):
        """The battle scenery textures enlarged x4 (scenery_outlines.py, 5.6 MB with their mipmaps) are only kept for the last sceneries seen: the oldest ones give their space back on the graphics card."""
        while len(self._tex_outlines) > guard:
            for i, lookup_key in enumerate(self._tex_outlines):
                if lookup_key[0] != src: break
            else: return
            lookup_key = self._tex_outlines.pop(i); t = self.tex_gl.pop(lookup_key, None)
            if t is not None: t.delete()

    def _take_outlines(self):
        """Battle sceneries softened in the background (_load_map): each one ready replaces its original texture (in the scenery being drawn too) and joins the kept enlarged textures."""
        jobs = self._outline_jobs
        for lookup_key in [k for k, f in jobs.items() if f.done()]:
            try: arr = jobs.pop(lookup_key).result()
            except Exception as ex: print('scenery outlines %s: %s' % (lookup_key, ex), flush=True); continue
            t = self._tex(arr, True, 99); self._filter_texture(t, self.smoothing)
            old = self.tex_gl.get(lookup_key); self.tex_gl[lookup_key] = t
            self.map_vl = [(t if tex is old and k == lookup_key[1] and self.map_src == lookup_key[0] else tex, vl, k) for tex, vl, k in self.map_vl]
            if old is not None and old is not t: old.delete()
            self._tex_outlines.append(lookup_key); self._purge_outlines(self.map_src)

    def _load_map(self, name_str):
        t_load = time.perf_counter()
        for vl in self.map_vl: vl[1].delete()
        for x in self.ext_map: x[1].delete()
        if self.ext_sky and self.ext_sky['vl'] is not None: self.ext_sky['vl'].delete()
        if self.flat_sky and self.flat_sky.get('vl') is not None: self.flat_sky['vl'].delete()
        if self.ext_sky:                                                  # (the variants' vertex lists and textures)
            for vl, _ in (self.ext_sky.get('var_vl') or {}).values(): vl.delete()
            for t in (self.ext_sky.get('var') or ()): t.delete()
        self.ext_sky = None; self.zoom_sky = None; self.flat_sky = None; self.ext_map = []; self.map_vl = []; self.map_pos = {}; self.spans = {}; self.names = {}; self.objects = None; self.palettes = None; self.map_background = None; self.pam_gl = {}; self.map_transz = frozenset()
        src = (os.environ.get('DQ_SCENERY') or name_str) if name_str.startswith('btl_') else name_str       # DQ_SCENERY: (test) forces another battle scenery
        chosen = (getattr(self, 'fight_scenery', None) or getattr(self, 'battle_scenery', None)) if name_str.startswith('btl') else None
        if chosen: src = DO.with_time_of(chosen, name_str)          # (debug panel: the backdrop chosen for a fight or for all of them, drawing only: the engine's battle is unchanged; the engine's time of day)
        c = error = None
        for cand in [src] + (DO.fallback_sceneries(src) if name_str.startswith('btl') else []):         # battle scenery without the requested variant (time of day): the closest one, rather than an empty scenery
            try: c = self.data.game_map(cand)
            except KeyError as ex: error = ex; continue
            except Exception as ex: error = ex
            if c is not None and cand != src: print('scenery %s missing from the data: %s used instead' % (src, cand), flush=True); src = cand
            break
        if c is None:
            print('map not loaded:', error, flush=True); self.map_name = name_str; return
        if not os.environ.get('DQ_NO_OFFSET'):
            try: DP.shift(c, os.path.join(self.args.cache, 'flat1_%s%s.npy' % (DO.MAP_PREFIX, src)) if getattr(self.args, 'cache', None) else None)          # flower beds & co: superimposed flat triangles raised by 3 mm (no more z-fighting, see flat_sceneries.py); the triangles found are kept in the cache folder
            except Exception as ex: print('flat_sceneries:', ex, flush=True)
        self.map_src = src; self.scenery_arr = {k: v for k, v in c['textures'].items() if k in c['groups']}       # scenery textures (enlarged according to the F2 mode, see _decor_tex)
        fut = self.hd_jobs.get(('d', src, self.mode)) if name_str == TITLE_MAP else None
        if fut is not None:                                               # the title's enlarged scenery, prepared during the credits (_prepare_title_scenery): waited for if it is not finished yet
            try: fut.result(timeout=10)
            except Exception: pass
        self.objects = OC.MapObjects(c)                                   # doors, jars, barrels...: displayed positions (variants hidden at the start); water, lamps... animated continuously
        self.map_transz = frozenset(c.get('transz') or ())                # (DQ V, FldObject.FixProblems: transparent groups drawn with depth writing, see data.FIX_PROBLEMS)
        self.map_background = c.get('background')                         # (FETC color: background of the DQ V / VI maps until a packet 8, see _clear_color)
        self.palettes = OC.MapPalettes(c)                               # surfaces whose texture changes from frame to frame (sparkling water, swamps...)
        outlines = name_str.startswith('btl') and not os.environ.get('DQ_NO_OUTLINES')        # battle sceneries: softened silhouettes of trees and mountains (scenery_outlines.py); DQ_NO_OUTLINES=1: (test) original texture
        for key, (pos, uv, col) in c['groups'].items():
            if key not in c['textures']: continue
            if (src, key) not in self.tex_gl:
                arr = raw = c['textures'][key]
                if outlines and CD.is_sliced(raw, key):
                    if getattr(self, 'fixed_test', False): arr = CD.soften(raw)                    # (test screenshots: at once, reproducible)
                    elif (src, key) not in self._outline_jobs: self._outline_jobs[(src, key)] = self.pool.submit(CD.soften, raw)          # in a background task (70 to 95 ms each): the original texture until it is ready (_take_outlines), behind the battle's opening effect
                self.tex_gl[(src, key)] = t = self._tex(arr, True, 99); self._filter_texture(t, self.smoothing)
                if arr is not raw: self._tex_outlines.append((src, key)); self._purge_outlines(src)
            vl = self.prog.vertex_list(len(pos), gl.GL_TRIANGLES, position=('f', self.objects.cur[key].ravel()), tex_coords=('f', self.objects.uv(key).ravel()), colors=('f', self.objects.col(key).ravel()))
            self.map_vl.append((self.tex_gl[(src, key)], vl, key)); self.map_pos[key] = pos.copy()
        self.obj_shown = {k: v.copy() for k, v in self.objects.cur.items()}; self.obj_moving = {}          # (vertices at the end of the last step, and those moving between two steps)
        for key, dd in self.palettes.defs.items():                         # frames of each palette animation
            if key not in c['groups']: continue
            self.pam_gl[key] = []
            for k, a in enumerate(dd['images']):
                if (src, key, k) not in self.tex_gl:
                    self.tex_gl[(src, key, k)] = t = self._tex(a, True, 99); self._filter_texture(t, self.smoothing)
                self.pam_gl[key].append(self.tex_gl[(src, key, k)])
        self.objects.dirty = set(); self.objects.dirty_uv = set(); self.objects.dirty_col = set()
        self.spans = c.get('spans', {}); self.names = c.get('names', {})
        if name_str == TITLE_MAP: self._title_sky(c, src)         # title screen: sky extended on both sides of the portrait area
        if name_str in EXTENDED_SKIES: self._extended_sky(c, src, name_str)         # dpout, ev09, DQ V's title: sky plane extended by symmetry
        allp = np.concatenate([g[0] for g in c['groups'].values()]) if c['groups'] else np.zeros((1, 3), np.float32)
        self.bounds_x = (float(np.percentile(allp[:, 0], 0.5)), float(np.percentile(allp[:, 0], 99.5)))
        self.map_name = name_str
        self.scenery_background = self._battle_background(c) if name_str.startswith('btl_') else None
        FieldMixin._deliver_orders(self, True)           # the orders the engine gave for this map before it was loaded
        print('map %s: %d groups' % (name_str, len(self.map_vl)), flush=True)
        SV.note('map loaded', name_str=name_str, groups=len(self.map_vl), ms=round(1000 * (time.perf_counter() - t_load)))

    def _calc_hd_scenery(self, arrs, algo):
        """(background task) scenery textures enlarged by the HD algorithm of the F2 mode (x8, capped at 4096 px per side). The edge is repeated in a loop before the enlargement: the textures join up seamlessly."""
        out = {}
        for key, arr in arrs.items():
            h, w = arr.shape[:2]; passes = 3
            while passes > 0 and max(h, w) * 2 ** passes > 4096: passes -= 1
            if passes == 0: out[key] = arr; continue
            a = np.pad(arr, ((2, 2), (2, 2), (0, 0)), mode='wrap')
            r = algo(a) if passes == 3 else HD.upscale_epx(a, passes)
            f = r.shape[0] // a.shape[0]
            out[key] = np.ascontiguousarray(r[2 * f:r.shape[0] - 2 * f, 2 * f:r.shape[1] - 2 * f])
        return out

    def _prepare_title_scenery(self):
        """F2 modes: the title's enlarged scenery computed in the background from the launch (during the opening credits), under the key `_hd_scenery` looks for. Computed only once
        the title was on screen, it replaced the original scenery in the middle of the title (DQ V's clouds changed under the eyes)."""
        algo = HD.MODES[self.mode][2]
        def job():
            c = self.data.game_map(TITLE_MAP, silent=True)
            return self._calc_hd_scenery({k: v for k, v in c['textures'].items() if k in c['groups']}, algo)
        self.hd_jobs[('d', TITLE_MAP, self.mode)] = self.pool.submit(job)

    def _hd_scenery(self):
        """Enlarged GL textures of the current scenery for the F2 mode; None until the background computation is finished (the original scenery is used in the meantime)."""
        lookup_key = (self.map_src, self.mode); r = self.scenery_hd.get(lookup_key)
        if r is not None: return r
        jc = ('d',) + lookup_key; fut = self.hd_jobs.get(jc)
        if fut is None:
            self.hd_jobs[jc] = self.pool.submit(self._calc_hd_scenery, dict(self.scenery_arr), HD.MODES[self.mode][2]); return None
        if not fut.done(): return None
        del self.hd_jobs[jc]
        try: arrs = fut.result()
        except Exception as ex:
            print('HD smoothing of the scenery failed (%s): %s' % (self.map_src, ex), flush=True); arrs = {}
        r = self.scenery_hd[lookup_key] = {}
        for k, a in arrs.items():
            r[k] = t = self._tex(a, True, 99); self._filter_texture(t, self.smoothing)
        while len(self.scenery_hd) > 8:                                  # only the latest enlarged sceneries are kept (graphics card memory)
            for t in self.scenery_hd.pop(next(iter(self.scenery_hd))).values(): t.delete()
        return r

    def _scenery_tint(self):
        """Tint of packet 33 to apply to the scenery (r, g, b), or None if it is neutral (nothing changes: the drawing stays identical). DQ_NO_TINT=1: (debugging) never any tint, for comparison."""
        if os.environ.get('DQ_NO_TINT'): return None
        t = self.engine.state.tint
        return None if TC.neutral_tint(t) else t

    def _set_tint(self, t):
        """Writes the `tint` uniform if it has changed (the program is in use)."""
        t = (float(t[0]), float(t[1]), float(t[2]))
        if t != self._gl_tint: self._gl_tint = t; self.prog['tint'] = t

    @staticmethod
    def _scenery_culling(active):
        """Hidden faces of the scenery: the game's map scenery shaders (Custom/fld_normal, fld_atest, fld_trans, fld_transz and their sepia variants) are "Cull Back" (read in the data packet:
        ShaderLab culling = 2): a wall, a roof, a ceiling only has a texture on its front face; seen from behind (rotated camera, from outside a room) it is invisible, hence the view through the walls of an interior.
        Unity: front face = clockwise triangle on screen (left hand), like the 3D models (DQ_FBX_CULL); DQ_SCENERY_CULL: 0 = no culling (the former rendering), 1 = default, 2 = reverse direction (trial)."""
        mode = os.environ.get('DQ_SCENERY_CULL', '1')
        if active and mode != '0':
            gl.glEnable(gl.GL_CULL_FACE); gl.glCullFace(gl.GL_BACK); gl.glFrontFace(gl.GL_CCW if mode == '2' else gl.GL_CW)
        else: gl.glDisable(gl.GL_CULL_FACE); gl.glFrontFace(gl.GL_CCW)

    def _same_queue_objects(self, queue, eye):
        """The transparent scenery in the characters' own render queue (DQ V: priority == Chara.SetPriorityShader's number, 97 maps): -> [(distance, key, first, count)], one entry per map object
        (a Unity renderer: all its submeshes share its distance) plus what no object covers. Unity sorts the renderers of one transparent queue from the farthest to the nearest, by the distance
        from the camera to the center of their bounds: a light shaft closer than a character is drawn after it (over it), a farther one before (the character hides it).
        Some maps have hundreds of them (dlf1a: 252 glows): the centers are kept and only those of the objects whose groups changed since the last call are computed again (`_queue_dirty`)."""
        cur = self.objects.cur; ident = (self.map_name, queue, id(self.objects)); cache = getattr(self, '_same_queue_cache', None)
        if cache is None or cache['id'] != ident:
            keys = [k for k in cur if isinstance(k, tuple) and k and k[0] == 'tr' and k[1] == queue]
            per_object = []; covered = {k: [] for k in keys}
            for i, pl in self.spans.items():
                mine = [(k, d, n) for k, d, n in pl if k in covered and n]
                if not mine: continue
                per_object.append(([(k, d, n) for k, d, n in pl if k in cur and n], mine))
                for k, d, n in mine: covered[k].append((d, d + n))
            for k in keys:                                               # (model variants and anything else outside the objects' spans: one more entry each)
                at = 0
                for lo, hi in sorted(covered[k]) + [(len(cur[k]), len(cur[k]))]:
                    if lo > at: per_object.append(([(k, at, lo - at)], [(k, at, lo - at)]))
                    at = max(at, hi)
            cache = self._same_queue_cache = {'id': ident, 'objects': per_object, 'keys': [frozenset(k for k, d, n in whole) for whole, mine in per_object],
                                              'centers': np.zeros((len(per_object), 3)), 'shown': [() for _ in per_object]}
            self._queue_dirty = None                                     # (everything is to be computed)
        dirty = getattr(self, '_queue_dirty', None); self._queue_dirty = set()
        for i, (whole, mine) in enumerate(cache['objects']):
            if dirty is not None and not (cache['keys'][i] & dirty): continue
            pts = [cur[k][d:d + n] for k, d, n in whole]
            cache['centers'][i] = (np.min([p.min(0) for p in pts], 0) + np.max([p.max(0) for p in pts], 0)) / 2.0
            cache['shown'][i] = tuple(bool(cur[k][d:d + n].any()) for k, d, n in mine)          # (a hidden object's vertices are all at zero)
        if not cache['objects']: return []
        dist = np.linalg.norm(cache['centers'] - np.asarray(eye, float), axis=1)
        return [(float(dist[i]), k, d, n) for i, (whole, mine) in enumerate(cache['objects']) for (k, d, n), on in zip(mine, cache['shown'][i]) if on]

    def _paint_scenery(self, sharp=False, part='all', queue=0, only=None):
        """Town or battle scenery. The scenery textures are paintings of 8 to 256 px stretched in 3D: in "Originaux" and in pixel perfect (settings for the SPRITES) it keeps the original rendering,
        smoothed (mipmaps + anisotropic, F3 = linear enlargement or nearest neighbor); the enlarged mode (EPX) applies the chosen HD algorithm to it.
        `part`: 'all'; 'early' = all but the transparent groups of priority > `queue`; 'late' = only those; 'before' = like 'early' without the groups of priority == `queue`, which 'same' draws
        one object at a time between the characters (`only` = [(key, first, count)] in drawing order, see _same_queue_objects). FldObject.set_priority gives a transparent material (Custom/fld_trans*, queue 3000)
        the render queue 3000 + priority; the characters (Custom/Chara, ZWrite on) are in queue 3000 + `queue` (0, except DQ V: Chara.SetPriorityShader), so the scenery of a higher priority is drawn
        after them: in front of a character it covers it (mist, light shafts), behind it the character's depth hides it. Same priority: left before the characters."""
        e = self.engine.state
        if e.part == ENGINE.PART_TOWN and not e.game_map: return          # (no map in the engine: DQ V's opening credits on black, the title's map loaded in advance is not drawn under them with no camera of its own)
        r = self._hd_scenery() if self.mode not in (0, MODE_PP) else None
        sharp_o = 1.0 if (sharp and r is None and self.smoothing and not os.environ.get('DQ_SCENERY_BLUR')) else 0.0           # towns and dungeons in "Originaux" / pixel perfect: sharp texels, only the boundary between two texels is softened (like the sprites); in battle, stretched paintings: normal smoothing
        is_tr = lambda k: isinstance(k, tuple) and k[0] == 'tr'
        opaque_ones = [x for x in self.map_vl if not is_tr(x[2])] if part not in ('late', 'same') else []
        transp = sorted((x for x in self.map_vl if is_tr(x[2])), key=lambda x: x[2][1])         # render queue 3000 + priority (FldObject.set_priority)
        if part == 'early': transp = [x for x in transp if x[2][1] <= queue]
        elif part == 'before': transp = [x for x in transp if x[2][1] < queue]
        elif part == 'late': transp = [x for x in transp if x[2][1] > queue]
        elif part == 'same': transp = [x for x in transp if x[2][1] == queue]
        if part in ('late', 'same') and not transp: return
        cutoff0 = self.prog['cutoff']; a2c = self.post.a2c; transz = getattr(self, 'map_transz', ())
        sepia = bool(getattr(self.engine.state, 'sepia', False)) and not os.environ.get('DQ_NO_SEPIA')          # packet 1: flashback, scenery in sepia (characters, bubbles and menus keep their colors)
        if sepia: self.prog['sepia'] = 1.0
        tint = self._scenery_tint(); ids_tz = self.tz.ids() if tint is not None else None; neutral = (1.0, 1.0, 1.0)      # packet 33: tint of the whole scenery, except groups whose texture has "tz_" in its name (lamps, candles, waves)
        if a2c: self.prog['a2c'] = 1.0; gl.glEnable(PF.SAMPLE_ALPHA_TO_COVERAGE); gl.glDisable(gl.GL_BLEND)      # cut-out opaque scenery: alpha -> MSAA coverage, no blending (blending would double the alpha)
        for step, entries in ((0, opaque_ones), (1, transp)):
            if step == 1 and entries:
                if a2c: self.prog['a2c'] = 0.0; gl.glDisable(PF.SAMPLE_ALPHA_TO_COVERAGE); gl.glEnable(gl.GL_BLEND)
                gl.glDepthMask(gl.GL_FALSE); gl.glDepthFunc(gl.GL_LEQUAL); self.prog['cutoff'] = 0.0      # Custom/fld_trans : Blend SrcAlpha OneMinusSrcAlpha, ZWrite Off (fld_transz: On, see below), ZTest LEqual, no alpha test
            gl.glEnable(gl.GL_POLYGON_OFFSET_FILL)
            sky_key = self.flat_sky['key'] if self.flat_sky is not None else (self.zoom_sky['key'] if self.zoom_sky is not None else None)          # (the title sky has its own vertices: never any culling)
            ranges = None
            if only is not None:                                             # (a run of objects of the characters' queue: each group's state is set when the run changes group, then one draw per object)
                place = {key: (i, tex, vl) for i, (tex, vl, key) in enumerate(entries)}; ranges = [x for x in only if x[0] in place]; last = None
                entries_drawn = [place[k] + (k, d, n) for k, d, n in ranges]
            else: entries_drawn = [(i, tex, vl, key, 0, None) for i, (tex, vl, key) in enumerate(entries)]
            culled = None; sent = {}                                         # (state already given to the graphics card in this pass: a map has 50 to 150 groups, nearly all with the same culling, sharpness and texel shift)
            def uniform(name, value):
                if sent.get(name) != value: sent[name] = value; self.prog[name] = value
            for i, tex, vl, key, first, count in entries_drawn:
                if ranges is not None:
                    if key == last: self._paint(t, vl, first, count); continue
                    last = key
                if step: gl.glDepthMask(gl.GL_TRUE if len(key) == 4 or key in transz else gl.GL_FALSE)           # ('tr', priority, key, 'z'): Custom/fld_transz, ZWrite on (ev02, objects 1, 4, 37; DQ V: the map's FixProblems groups)
                if (key != sky_key) is not culled: culled = key != sky_key; self._scenery_culling(culled)
                gl.glPolygonOffset(0.0, -SCENERY_BIAS * (i + (len(opaque_ones) if step else 0)))         # flat sceneries superimposed at the same height (two flower beds, path and lawn...): without this their depth is equal up to rounding and one or the other wins pixel by pixel, which changes with every camera movement (flicker); the group drawn last always wins
                pg = self.pam_gl.get(key)
                t = pg[self.palettes.image(key, self.objects.event if self.objects is not None else 0)] if pg else (r.get(key, tex) if r else tex)          # animated palette: current frame (without HD enlargement)
                uniform('crisp', sharp_o if (r is None or key not in r) else 0.0)
                uniform('tsize', (float(t.width), float(t.height))); uniform('tshift', self._texel_center_shift(key, t) if (r is None or key not in r) else (0.0, 0.0))
                if tint is not None: self._set_tint(neutral if TC.group_tz(key, ids_tz) else tint)
                zoom = CT.screen_zoom(self._town_aspect) if (self.zoom_sky is not None and key == self.zoom_sky['key'] and self._M_town is not None) else 1.0
                if zoom > 1.0 + 1e-6: self.prog['mvp'] = tuple(float(x) for x in CT.zoom_matrix(self._M_town, zoom).T.ravel())          # title sky enlarged around the center of the screen
                if ranges is not None: self._paint(t, vl, first, count); continue
                if self.flat_sky is not None and key == self.flat_sky['key']:
                    sent.clear(); culled = None                                 # (the flat sky's own drawing sets its state)
                    if not self._paint_flat_sky(t): self._paint(t, vl)
                else: self._paint(t, vl)
                if step and any(x[2] == key for x in self.ext_map):            # the mirrored copies of a transparent group (EXTENDED_SKIES): blended like it, right after it, in front of the title's sky bands
                    gl.glPolygonOffset(0.0, -SCENERY_BIAS * (len(self.map_vl) + 3)); self._scenery_culling(False); culled = False          # (DQ VI's title: the haze at the foot of the sky, cut at the plane's right edge; a mirrored copy turns its back: no culling)
                    for etex, evl, ekey, start_idx, n, dx in self.ext_map:
                        if ekey == key: self._paint(etex, evl)
                if zoom > 1.0 + 1e-6: self.prog['mvp'] = tuple(float(x) for x in self._M_town.T.ravel())
            if step == 1 and entries: gl.glDepthMask(gl.GL_TRUE); gl.glDepthFunc(gl.GL_LESS); self.prog['cutoff'] = cutoff0
            gl.glDisable(gl.GL_POLYGON_OFFSET_FILL); self._scenery_culling(False)
            if step == 0 and part not in ('late', 'same'):
                if tint is not None: self._set_tint(tint)
                for tex, vl, key, start_idx, n, dx in self.ext_map:
                    if is_tr(key): continue                                  # (drawn with their group, in the transparent pass)
                    self.prog['crisp'] = sharp_o; self.prog['tsize'] = (float(tex.width), float(tex.height)); self.prog['tshift'] = self._texel_center_shift(key, tex); self._paint(tex, vl)
                if self.ext_sky is not None: self._paint_sky(r, sharp_o)
        if a2c: self.prog['a2c'] = 0.0; gl.glDisable(PF.SAMPLE_ALPHA_TO_COVERAGE); gl.glEnable(gl.GL_BLEND)
        if tint is not None: self._set_tint(neutral)
        if sepia: self.prog['sepia'] = 0.0
        self.prog['crisp'] = 0.0; self.prog['tsize'] = (1.0, 1.0); self.prog['tshift'] = (0.0, 0.0)

    def _texel_center_shift(self, key, t):
        """Half an ORIGINAL texel, in texels of the texture drawn (`t` may be the softened battle texture, enlarged): the uv of the scenery point at texel centers (see FRAG, tshift)."""
        a = self.scenery_arr.get(key)
        if a is None: return (0.5, 0.5)
        h, w = a.shape[:2]
        return (0.5 * t.width / max(w, 1), 0.5 * t.height / max(h, 1))

    def _fbx_clips(self, no):
        """Clips of 3D model `no` for the engine (Engine.fbx_clips: end of the animations): {name: {'dur', 'wrap'}}, None while the model is being read, {} if it is unreadable."""
        r = self._fbx_read(no)
        if r['m'] is not None: return {k: {'dur': c['dur'], 'wrap': c['wrap']} for k, c in r['m'].clips.items()}
        return None if r['status'] == 'loading' else {}

    def _fbx_read(self, no):
        r = self.fbx_cache.get(no)
        if r is None:
            r = self.fbx_cache[no] = {'status': 'loading', 'm': None}
            def task():
                try: d = self.data.fbx_model(no); r['m'] = FX.ScreenOverlay(d) if d.get('overlay') else FX.FbxModel(d); r['status'] = 'loaded'
                except Exception as ex: r['status'] = 'error'; print('3D model %d not displayed: %s' % (no, ex), flush=True)
            threading.Thread(target=task, daemon=True).start()
        return r

    def _fbx_ready(self, no):
        """3D model number `no` ready to be drawn ({'m': fbx.FbxModel, 'vl', 'tex'...}), None while it is being read (background task: the disk cache, or the data packet the first time) or if it is not handled."""
        r = self._fbx_read(no)
        if r['status'] == 'loaded' and getattr(r['m'], 'overlay', False): r['status'] = 'ready'          # (screen picture: its texture is made by _draw_screen_models)
        if r['status'] == 'loaded':                                       # OpenGL objects: in the drawing thread
            try:
                m = r['m']; r['groups'] = []                                  # one group per texture (Glace has two): its texture, its vertices (triangles flattened) and its OpenGL vertex list
                for gi, (gtex, gtri) in enumerate(m.groups):
                    idx = gtri.ravel(); n = len(idx)
                    self.tex_gl[('fbx', no, gi)] = t = self._tex(gtex, True, 99); self._filter_texture(t, self.smoothing)
                    vl = self.prog.vertex_list(n, gl.GL_TRIANGLES, position=('f', np.zeros(n * 3, np.float32)), tex_coords=('f', m.uv[idx].ravel().astype(np.float32)), colors=('f', np.ones(n * 4, np.float32)))
                    r['groups'].append({'tex': t, 'raw': gtex, 'idx': idx, 'vl': vl, 'n': n, 'st': None})
                r['status'] = 'ready'
            except Exception as ex: r['status'] = 'error'; print('3D model %d not displayed: %s' % (no, ex), flush=True)
        return r if r['status'] == 'ready' else None

    def _paint_fbx(self, e, effects=False):
        """`effects` False: the opaque models (Custom/ImdChara); True: those with the Custom/Effect0 shader (rings and waves of battle attacks: queue Transparent+300, ZWrite off, ZTest LEqual, Cull Off, same alpha blending;
        the caller turns off depth writing, as for monster meshes). 3D models of the scene (e.fbx): pose = engine position / rotation / scale (packet 32), animation played since its launch (engine frames: 30 Hz, fractional step included), color = Material.color.
        Custom/ImdChara shader: vertex color x _Color x texture, alpha blending, ZWrite, ZTest LEqual, Cull Back, Geometry queue (with the opaque scenery, before the characters and the mesh effects)."""
        tf = max(0.0, self.engine.frames - 2.0 + self.alpha)           # engine frame displayed: that of the previous step + alpha (see _interpolate)
        for k in [k for k in self.fbx_player if k not in e.fbx]: del self.fbx_player[k]
        for slot, mod in sorted(e.fbx.items()):
            if not mod['visible']: continue
            r = self._fbx_ready(mod['id'])
            if r is None: continue
            m = r['m']
            if getattr(m, 'overlay', False) or m.effect != effects: continue          # (the interface camera's pictures: _draw_screen_models)
            lc = self.fbx_player.get(slot)
            if lc is None or lc['mod'] is not mod or lc['n'] > len(mod['anim']):                  # (the engine creates a new dictionary on each load (packet 31): a recast spell reloads its model in the same slot, and its animation player must restart from the cast)
                lc = self.fbx_player[slot] = {'mod': mod, 'n': 0, 'a': FX.AnimPlayer({k: {'dur': c['dur'], 'wrap': c['wrap']} for k, c in m.clips.items()})}
            for img, seq, force in mod['anim'][lc['n']:]: lc['a'].play(seq, force, img)
            lc['n'] = len(mod['anim'])
            ui_scale = 0.4 if mod['id'] == 10 else 1.0                      # FbxObject.m_defScale (.ctor: 0.4 for model 10, 1 otherwise)
            sc = tuple(float(x) * ui_scale for x in mod['scl'])
            poses = lc['a'].poses(tf); V = m.vertices(FX.object_matrix(mod['pos'], mod['rot'], sc), poses)
            mats = m.materials(poses, mod['col'] if mod.get('col_set') else None)          # each material's color and texture placement: the material's own, the engine's color, then what the clip animates
            self.prog['cutoff'] = 0.0; self.prog['crisp'] = 1.0 if self.smoothing else 0.0
            cull = os.environ.get('DQ_FBX_CULL', '1')                   # Custom/ImdChara: Cull Back (read in the shader); DQ_FBX_CULL=0: no culling, 2: reverse direction (the former one, wrong: the inside of the dragon showed) for comparison
            if cull != '0' and not effects:                                      # Custom/Effect0 : Cull Off
                gl.glEnable(gl.GL_CULL_FACE); gl.glCullFace(gl.GL_BACK)
                gl.glFrontFace(gl.GL_CCW if cull == '2' else gl.GL_CW)         # Unity: front face = clockwise triangle on screen (left hand), the opposite of OpenGL's default; without this only the back faces were drawn (the inside of the dragon: face seen from the inside, body seen from behind)
            for gi, g in enumerate(r['groups']):                                 # one pass per material slot (group order = submesh order)
                col, st = mats[min(gi, len(mats) - 1)]
                if col[3] <= 0.0: continue                                     # (a slot faded out by the clip: nothing to draw)
                vl = g['vl']; vl.position[:] = V[g['idx']].ravel(); vl.colors[:] = np.tile(np.array(col, np.float32), g['n'])
                if st != g.get('st'): vl.tex_coords[:] = (m.uv0[g['idx']] * np.array(st[:2], np.float32) + np.array(st[2:], np.float32)).ravel(); g['st'] = st          # (texture placement: Unity uv x scale + offset, scrolled by some clips)
                self.prog['tsize'] = (float(g['raw'].shape[1]), float(g['raw'].shape[0]))
                self._linked_domain = None                                     # vertices written AFTER the previous draw (the previous group, or a monster mesh): without a rebind + commit they go out with those of the previous frame (the 2nd Glace group, on the 1st draw of a recast spell: its last pose from the previous cast)
                self._paint(g['tex'], vl)
            gl.glDisable(gl.GL_CULL_FACE); gl.glFrontFace(gl.GL_CCW)
        self.prog['crisp'] = 0.0; self.prog['tsize'] = (1.0, 1.0); self.prog['cutoff'] = 0.05

    def _draw_screen_models(self, e):
        """The 3D models of the interface camera's layer (c2000, the clouds around the screen during the dreams). Not stretched: one interface unit has the same size across and up and
        the effect's area (the part of the picture that is not opaque: the dream's clear middle and its soft edge) is enlarged until its top and bottom edges touch the window's (landscape;
        portrait: its left and right edges); the rest of the window is the picture's opaque edge, then its edge color (white) beyond the plane. Drawn over the scene and the background fade plane, behind the windows and messages (Unity: interface camera after the scene camera, Transparent queue after the
        Background one). Color: the engine's (packet 32) if it gave one, otherwise the material's; alpha = texture alpha x color alpha."""
        for slot, mod in sorted(e.fbx.items()):
            if not mod['visible']: continue
            r = self._fbx_ready(mod['id'])
            if r is None or not getattr(r['m'], 'overlay', False): continue
            m = r['m']; scale = mod['scl'] if mod.get('scl_set') else None
            col = mod['col'] if mod.get('col_set') else m.col
            size, pos = m.area(scale, mod['pos'])
            self._draw_overlay(r, m.window(scale, pos, column=screen_column(self.width, self.height, size)), col)

    def _draw_overlay(self, r, win, col):
        """Draws overlay `r` over the whole window, `win` = its texture coordinates at the window's corners: the effect's area of the texture (its part that is not opaque) with a feathered
        margin (see feathered), and beyond it the color of the picture (white, opaque; the texture's own opaque margin, which the phone's screen never shows
        across, is not drawn)."""
        m = r['m']; W, H = self.width, self.height
        if r.get('img') is None or r.get('feather_of') is not m:
            part, r['area'] = feathered(m.tex, m.area_uv); part = np.ascontiguousarray(part[::-1])          # (rows top first -> pyglet: bottom first)
            r['img'] = pyglet.image.ImageData(part.shape[1], part.shape[0], 'RGBA', part.tobytes()); r['feather_of'] = m
        (x0, y0, x1, y1), fills = overlay_rects(win, W, H, r['area'])
        rgb = tuple(int(round(255 * min(max(c, 0.0), 1.0))) for c in col[:3]); alpha = min(max(col[3], 0.0), 1.0)
        gl.glDisable(gl.GL_DEPTH_TEST); gl.glEnable(gl.GL_BLEND); gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        spr = pyglet.sprite.Sprite(r['img'], x0, y0); spr.scale_x = (x1 - x0) / r['img'].width; spr.scale_y = (y1 - y0) / r['img'].height
        spr.color = rgb; spr.opacity = int(round(255 * alpha)); spr.draw()
        for (a, b, c, d), side in fills:                                                                 # beyond the picture and its margin: the picture's color
            q = pyglet.shapes.Rectangle(a, b, c - a, d - b, color=rgb); q.opacity = int(round(255 * alpha)); q.draw()

    def _icon_tex(self, ident):
        """Texture of icon `ident` (32 x 32 cell of the MenuTexture sheet, numbered left to right and top to bottom; Riseup.SetTexture -> UV 1/32); None if the sheet is absent."""
        if ident in self.icon_tex: return self.icon_tex[ident]
        if self._menu_tex is None:
            try:
                self._menu_tex = SH.rgba(self.args.cache, 'ui_MenuTexture.png')          # (the sheet the menus have already decoded)
            except Exception: self._menu_tex = False
        t = None
        if self._menu_tex is not False:
            r, c = divmod(ident, 32); h = self._menu_tex.shape[0]
            if (r + 1) * 32 <= h: t = self._tex(HD._dilate(self._menu_tex[r * 32:(r + 1) * 32, c * 32:(c + 1) * 32], 3), False)          # (edge color under the transparent pixels: no light fringe when filtering)
        self.icon_tex[ident] = t
        return t

    def _icons(self, e, field, is_battle, dh, cam3d=None):
        """Icon bubbles above the characters (packets 19/20, Riseup: 0.96 square facing the camera, advanced by 0.48 toward it): [(texture, vertex_list)]."""
        if field or is_battle or not e.icons or e.cam_pos is None or e.cam_target is None: return []
        fwd = np.array(e.cam_target, float) - np.array(e.cam_pos, float); fwd /= np.linalg.norm(fwd) or 1
        out = []; corr = self._edge_correction(); C_view = self._view_border[0] if self._view_border is not None else None
        for slot, ic in sorted(e.icons.items()):
            if ic['pos'] is None or ic['alpha'] < 0.02: continue
            tex = self._icon_tex(ic['id'])
            if not tex: continue
            if cam3d is not None:
                # facing the screen, like the characters (camera rotation). Advanced by 0.48 toward the VIEWER's camera (and not the game's: rotated or tilted by the
                # player, it shifted the bubble on screen), along the view ray (see _shift_depth); the bubble stays stuck to the character carrying it, whatever the
                # camera tilt (see _anchor_icon)
                P = self._anchor_icon(e, np.array(ic['pos'], float), cam3d); r_, u_, f_ = cam3d; D = self._shift_depth
                A0, B0 = P - r_ * 0.48, P + r_ * 0.48
                a, b, c, d = D(A0, f_, 0.48, corr, C_view), D(B0, f_, 0.48, corr, C_view), D(B0 + u_ * 0.96, f_, 0.48, corr, C_view), D(A0 + u_ * 0.96, f_, 0.48, corr, C_view)
                q = np.array([a, b, c, a, c, d], np.float32).ravel().tolist()
            else:
                p = np.array(ic['pos'], float) - fwd * 0.48
                a, b, up = p - dh * 0.48, p + dh * 0.48, np.array([0.0, 0.96, 0.0])
                q = np.array([a, b, b + up, a, b + up, a + up], np.float32).ravel().tolist()
            uv = [0, 0, 1, 0, 1, 1, 0, 0, 1, 1, 0, 1]; cl = [1.0, 1.0, 1.0, float(min(max(ic['alpha'], 0.0), 1.0))] * 6
            vl = self.icon_vl.get(slot)
            if vl is None: vl = self.icon_vl[slot] = self.prog.vertex_list(6, gl.GL_TRIANGLES, position=('f', q), tex_coords=('f', uv), colors=('f', cl))
            else: vl.position[:] = q; vl.tex_coords[:] = uv; vl.colors[:] = cl
            out.append((tex, vl))
        return out

    def _anchor_icon(self, e, P, cam3d):
        """Position of an icon bubble (packet 20) for the viewer's camera. The engine places it above the target (1.294 above its feet, measured on three villagers) in WORLD coordinates, whereas
        the characters are panels facing the camera: if the player's camera is tilted or rotated, the height of the bubble relative to the head changes (1.294 x cos of the on-screen tilt, against a fixed panel
        height) and it seems too high or too low. When a character is under the bubble (same x, z), the offset is therefore kept as the GAME's camera sees it (right / up / forward components) and
        expressed in the frame of the viewer's camera: the bubble stays at the same place relative to the head (identical to P when the two cameras are the same). With no character below (barrel, jar...): P, anchored in the world."""
        S = None; best = 0.35
        for sp in e.sprites.values():
            pos = sp.get('pos')
            if pos is None or len(pos) < 3 or not sp.get('visible', True): continue
            dx, dz, dy = P[0] - pos[0], P[2] - pos[2], P[1] - pos[1]
            if 0.3 < dy < 3.0 and math.hypot(dx, dz) < best: best = math.hypot(dx, dz); S = np.array(pos, float)
        if S is None: return P
        try: _, Dr, H, F = self._game_border(e)
        except Exception: return P
        d = P - S
        return S + cam3d[0] * float(d @ Dr) + cam3d[1] * float(d @ H) + cam3d[2] * float(d @ F)

    def _map_ports(self):
        """Windows of the open map (FieldImageMap): {25: fixed plan, 26: large map that fills in, 24: list of the town's shops, 22: back button} -> port; empty if no map is open."""
        r = {}; hidden_windows = {}
        for p, f in self.engine.ui.f.items():
            if p >= 12 and f.get('type') in (22, 23, 24, 25, 26):
                t = 22 if f['type'] == 23 else f['type']
                if f.get('visible'): r[t] = p
                elif f.get('act'): hidden_windows[t] = p                       # window created but not shown (the 2nd view of the map, before the switch to it)
        if r:
            for t, p in hidden_windows.items(): r.setdefault(t, p)
        return r                                                      # (map windows left created after closing do not count: without a visible window, the map is closed)

    PLAN_RATIO = 0.75

    def _plan_window(self, wnd, f):
        """Engine map window (type 25): plan texture named by the engine and position marker (coordinates in pixels of the 256 x 192 texture)."""
        name_str = f['cells'].get((0, 0))
        if name_str and wnd.map_name != name_str:
            wnd.map_name = name_str
        lic = f.get('lists') or {}                                 # discovered places (code 3: x + 256 y, one per number) and veil of unexplored blocks (code 5) sent by the engine
        wnd.symbols = [(v & 0xff, v >> 8 & 0xff) for v in lic.get(3, ())]
        if wnd.symbols and getattr(wnd, 't_blink', None) is None: wnd.t_blink = time.monotonic()
        v4 = f['indicators'].get(4); wnd.special = None if v4 is None else (v4 & 0xff, v4 >> 8 & 0xff)        # code 4: special place (red cross), same encoding as the places: x + 256 y
        lookup_key = tuple(lic[5][:16]) if lic.get(5) else None
        blocks = f.get('ints', {}).get((0, 0)) if ENGINE.ENGINE_V2 and GM.profile().key == 'dq6' else None
        if blocks is not None: lookup_key = (blocks,) + tuple(lic.get('veil', ()))                 # DQ VI's small world map: composed from its TextAsset (see _dq6_world_picture)
        if wnd.map_name and (wnd.game_map is None or getattr(wnd, '_key_tex', None) != (wnd.map_name, lookup_key)):
            wnd._key_tex = (wnd.map_name, lookup_key)
            hd_a = self.plans.get(wnd.map_name) if blocks is None else None          # enlarged plan (1024 x 768): Lanczos, with rounding of the outlines (EPX) for the world plan whose lands are pixel art
            if blocks is not None:
                a = PL.dq6_world_picture(self.engine.text_asset(wnd.map_name) if getattr(self.engine, 'text_asset', None) else None, blocks, lic.get('veil', ()))
                if a is not None: hd_a = HD.upscale_plan(a, epx=True)
            elif hd_a is None:
                try:
                    a = self.data.texture(wnd.map_name)
                    if a is not None and ENGINE.ENGINE_V2: a = a[:int(round(a.shape[0] * self.PLAN_RATIO))]          # DQ V / VI, MenuParts.CreateFromTexture(path, true, 0.75): the plan shows the top 3 / 4 of its texture (UV from 1 - 0.75 to 1; DQ IV's: the whole texture)
                    if a is not None: hd_a = self.plans[wnd.map_name] = HD.upscale_plan(a, epx=wnd.map_name.startswith('dsdq4w') or wnd.map_name.startswith('dq5ds_wmap'))
                except Exception as ex: print('Map image %s unreadable: %r' % (wnd.map_name, ex), flush=True)
            if hd_a is not None:
                if lookup_key and blocks is None:                         # world map: veil (grayed silhouette) over the unexplored 10 x 10 pixel blocks (16 x 16 blocks from x = 48, y = 16 of the 256 x 192 plan)
                    src = self.data.world_veil()
                    if src is not None: hd_a = HD.veil_plan(hd_a, src, lookup_key)
                t = pyglet.image.ImageData(hd_a.shape[1], hd_a.shape[0], 'RGBA', np.ascontiguousarray(hd_a[::-1]).tobytes(), pitch=hd_a.shape[1] * 4).get_texture()
                gl.glBindTexture(t.target, t.id)
                gl.glTexParameteri(t.target, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR); gl.glTexParameteri(t.target, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
                gl.glTexParameteri(t.target, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE); gl.glTexParameteri(t.target, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
                if wnd.game_map is not None: wnd.game_map.delete()
                wnd.game_map = t; wnd.map_mode = 0
        node = next((o for o in wnd.N.values() if o['mats'] and o['mats'][0].get('part') == 3), None)      # "you are here" marker (24 x 24 icon, part 3; its number changes from one generation of the layout to another) centered on the point (x, y) of the plan
        mx, my = f['indicators'].get(1), f['indicators'].get(2)
        if node is not None and mx is not None and my is not None:
            node['pos'][0] = -223.0 + mx * 1.740234; node['pos'][2] = 398.0 - my * 1.740234; node['act'] = True        # AddIcon(Vector3.zero, 31, bInBounds): the bottom left corner of the icon (the tip of the quill) is on the position, in the 450 x 337.5 plan placed at (2, -2) at scale 0.99 (0.99 x 450 / 256)

    def _world_plan_window(self, wnd, f):
        """Large world map (type 26, Make_WorldLargeMap): the engine sends the flag of the plan's pieces (indicator 0), then the player's position x, y (indicators 1 and 2) in pixels of a
        1024 x 1024 plan. The game composes the plan (LoadLargeWorldMap: background + pieces whose bit is raised in the flag) and shows only a 256 x 192 pixel window of it centered on the player
        (texture offset x / 1024 - 0.125; the marker stays at the center of the window)."""
        flag = f['indicators'].get(0, 0) & 0xffff; x, y = f['indicators'].get(1), f['indicators'].get(2)
        if getattr(wnd, '_plane_flag', None) != flag or wnd.game_map is None:
            try: pl = self.data.world_plan(flag)
            except Exception as ex: pl = None; print('World map image unreadable: %r' % (ex,), flush=True)
            if pl is None: return
            im = pl['base'].copy()
            for bit, px, py, a in pl['pieces']:
                if flag >> bit & 1: im[py:py + a.shape[0], px:px + a.shape[1]] = a
            rgba = np.dstack([im, np.full(im.shape[:2], 255, np.uint8)])
            if wnd.game_map is not None: wnd.game_map.delete()
            t = pyglet.image.ImageData(1024, 1024, 'RGBA', np.ascontiguousarray(rgba[::-1]).tobytes(), pitch=4096).get_texture()
            gl.glBindTexture(t.target, t.id)
            gl.glTexParameteri(t.target, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR); gl.glTexParameteri(t.target, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
            gl.glTexParameteri(t.target, gl.GL_TEXTURE_WRAP_S, gl.GL_REPEAT); gl.glTexParameteri(t.target, gl.GL_TEXTURE_WRAP_T, gl.GL_REPEAT)
            wnd.game_map = t; wnd._plane_flag = flag; wnd.map_mode = 3
        if x is not None and y is not None:
            wnd.map_uv = ((x - 128) / 1024.0, 1.0 - (y + 96) / 1024.0, (x + 128) / 1024.0, 1.0 - (y - 96) / 1024.0)

    def _map_objects(self):
        """Engine orders on the map objects (doors, jars...), then one animation frame; the modified vertices are sent back to the graphics card."""
        e = self.engine.state; cmds, e.object_cmds = e.object_cmds, []
        if cmds or getattr(self, '_orders', None): FieldMixin._take_orders(self, e, cmds)
        if self.objects is None or getattr(e, 'part', None) == ENGINE.PART_FIELD: return          # (world map: the objects are those of the last town, not drawn; animating them cost 1.5 ms a step)
        if os.environ.get('DQ_ANIM_TEST') and self.map_name == os.environ.get('DQ_MAP_TEST', ';').split(';')[0]:   # (test) DQ_ANIM_TEST="object:number:frame": starts a common animation at this loop frame
            o_t, no_t, n_t = (int(v) for v in os.environ['DQ_ANIM_TEST'].split(':'))
            if self.n == n_t: self.objects.common_anim(o_t, no_t)
        budget = getattr(self, 'scenery_ticks', None)
        if budget is not None:                                          # (battle replay between two turns: only the steps the real clock allows)
            if budget <= 0: return
            self.scenery_ticks = budget - 1
        if getattr(e, 'fld_paused', False): dirty_keys = self.objects.dirty; self.objects.dirty = set()          # DQ VI, FldObject.Pause: nothing advances (the engine's orders are still applied)
        else:
            dirty_keys = self.objects.tick()
            if self.palettes is not None: self.palettes.tick()
        rider = getattr(e, 'rider_object', None)
        if rider is not None and self.map_name == e.game_map and e.sprites.get(0) is not None:           # DQ V's mine cart ride: the first character stands on the cart, wherever the engine put it
            p = self.objects.rider_position(rider)
            if p is not None: e.sprites[0]['pos'] = p
        dirty_uv_keys, dirty_col_keys = self.objects.dirty_uv, self.objects.dirty_col; self.objects.dirty_uv = set(); self.objects.dirty_col = set()
        stopped = set(getattr(self, 'obj_moving', {})) - dirty_keys
        if getattr(self, '_queue_dirty', None) is not None: self._queue_dirty |= dirty_keys          # (see _same_queue_objects)
        FieldMixin._note_movements(self, dirty_keys)          # (called on the class: the tests' stand-ins of the window have only _map_objects)
        for tex, vl, key, start_idx, n, dx in self.ext_map:              # (copies of the sky: they follow the original's scrolling texture and its colors too, DQ V's title clouds turning blue)
            if key in dirty_keys or key in stopped: vl.position[:] = CT.cloned_positions(self.objects.cur[key][start_idx:start_idx + n], dx).ravel()
            if key in dirty_uv_keys: vl.tex_coords[:] = self.objects.uv(key)[start_idx:start_idx + n].ravel()
            if key in dirty_col_keys: vl.colors[:] = self.objects.col(key)[start_idx:start_idx + n].ravel()
        for tex, vl, key in self.map_vl:
            if key in dirty_keys or key in stopped: vl.position[:] = self.objects.cur[key].ravel()
            if key in dirty_uv_keys: vl.tex_coords[:] = self.objects.uv(key).ravel()
            if key in dirty_col_keys: vl.colors[:] = self.objects.col(key).ravel()

    ORDERS_MAX = 4096                          # orders kept at most for a map that is not loaded yet (never reached in practice)

    def _take_orders(self, e, cmds):
        """The orders on the map objects are kept for the map they were given for (packet 0 opens each list with its 'map' mark) and applied to that map's objects only, once it is loaded: never to the objects
        of the previous map (the world map keeps those of the last town visited: a town reached by teleportation would get the orders meant for it on the old one and show its own variants all at once, the
        volcano of Azimut, mixed tiles) and never lost while the map is not loaded yet (the former code dropped them when the window had no objects)."""
        target, queue = getattr(self, '_orders', None) or (e.game_map, [])
        for c in cmds:
            if c[0] == 'map': target, queue = e.game_map, [c]
            else: queue.append(c)
        if target != e.game_map: target, queue = e.game_map, []         # (a list left over from another map: void)
        del queue[:-FieldMixin.ORDERS_MAX]
        self._orders = (target, queue)
        if queue and target and self.map_name != target and e.part != ENGINE.PART_FIELD: self._load_map(target)       # the map is loaded before its orders (it delivers them itself)
        FieldMixin._deliver_orders(self)

    def _deliver_orders(self, fresh=False):
        """Applies the orders kept for the loaded map. `fresh`: the map has just been loaded, its objects are in their initial state (the 'map' mark has nothing to reinitialize)."""
        target, queue = getattr(self, '_orders', None) or (None, [])
        if not queue or self.map_name != target: return
        self._orders = (target, [])
        if self.objects is None: return                                       # (map that could not be loaded)
        for c in queue:
            if c[0] == 'map':
                if not fresh: self.objects.reinit()
            elif c[0] == 'anim': self.objects.common_anim(c[1], c[2])
            elif c[0] == 'onoff': self.objects.on_off(c[1], c[2])
            elif c[0] == 'alpha': self.objects.alpha(c[1], c[2])
            elif c[0] == 'pos': self.objects.position(c[1], c[2])
            elif c[0] == 'rot': self.objects.rotation(c[1], c[2])
            elif c[0] == 'event':
                self.objects.event_anim(c[1])
                if self.palettes is not None: self.palettes.start_event(c[1])
            elif c[0] == 'camera': self.objects.event_camera(c[1])
            elif c[0] == 'camera_key':                                        # (the camera as the last picture placed it: Camera.main when the packet arrives)
                b = getattr(self, '_view_border', None)
                self.objects.camera_first_key(c[1], c[2], None if b is None else b[0], None if b is None else b[3])
            elif c[0] == 'camera_engine': self.objects.camera_engine()

    def _note_movements(self, dirty_keys):
        """the map objects move once per engine step (30 Hz), the screen draws more often and the characters and the camera are interpolated between two steps (_interpolate): an object that
        moves (the boat of a town, a door that slides...) would advance by jerks next to them. For each group whose vertices this step moved, the positions at the end of the previous step and now are kept;
        _smooth_objects draws the instant in between. Not interpolated: a vertex that appears or disappears (hidden = zeros) or jumps (more than CAM_JUMP_TOWN units in one step)."""
        shown = getattr(self, 'obj_shown', None)
        if shown is None: return
        moving = {}
        for key in dirty_keys:
            new = self.objects.cur.get(key)
            if new is None: continue
            old = shown.get(key)
            if old is not None and old.shape == new.shape:
                d = np.abs(new - old).max(axis=1) if len(new) else np.zeros(0)
                mask = (d > 0) & (d <= CAM_JUMP_TOWN) & np.abs(old).any(axis=1) & np.abs(new).any(axis=1)
                if mask.any(): moving[key] = (old, new.copy(), mask)
            shown[key] = new.copy()
        self.obj_moving = moving

    def _smooth_objects(self, alpha):
        """Draws the moving map objects at fraction `alpha` of the way between the previous step and the current one (alpha 1 = the step's positions)."""
        moving = getattr(self, 'obj_moving', None)
        if not moving: return
        alpha = min(1.0, max(0.0, alpha)); done = {}
        for tex, vl, key in self.map_vl:
            m = moving.get(key)
            if m is None: continue
            old, new, mask = m; v = new.copy(); v[mask] = old[mask] + (new[mask] - old[mask]) * alpha; done[key] = v
            vl.position[:] = v.ravel()
        for tex, vl, key, start_idx, n, dx in self.ext_map:
            if key in done: vl.position[:] = CT.cloned_positions(done[key][start_idx:start_idx + n], dx).ravel()

    def _orient_scenery(self, cam3d):
        """Scenery panels turned toward the camera; the modified vertices are sent back to the graphics card only if the orientation has changed."""
        fw = cam3d[2]
        if not self.objects.orient(math.atan2(fw[0], fw[2]), cam3d, 0.0): return
        dirty_keys, self.objects.dirty = self.objects.dirty, set()
        if getattr(self, '_queue_dirty', None) is not None: self._queue_dirty |= dirty_keys          # (see _same_queue_objects)
        moving = getattr(self, 'obj_moving', {}); shown = getattr(self, 'obj_shown', None)
        for key in dirty_keys & set(moving):                                    # (a panel turned while it moves: its interpolation ends on the new orientation)
            old, new, mask = moving[key]; moving[key] = (old, self.objects.cur[key].copy(), mask)
        if shown is not None:                                                   # the panels as they stand now are what the next step compares with: left as they stood before the camera turned,
            for key in dirty_keys - set(moving):                                # the first object of their group to change (a barrel broken) made every other panel of the group swing from its old
                if key in shown and key in self.objects.cur: shown[key] = self.objects.cur[key].copy()          # orientation to the new one for a step (the other barrels blinked)
        for tex, vl, key in self.map_vl:
            if key in dirty_keys: vl.position[:] = self.objects.cur[key].ravel()

    def _anim_duration(self, idx, no):
        return self.objects.duration(idx, no) if self.objects is not None else None

    def _map_obj_pos(self, idx):
        """Displayed world position of map object idx (Engine.map_obj_pos), None while the map shown is not the engine's."""
        if self.objects is None or self.map_name != self.engine.state.game_map: return None
        return self.objects.display_position(idx)

    # ---- loop
    def _held(self, action):
        """Is one of the action keys (Touches menu) held down? (A key swallowed by a shortcut, Enter of Alt + Enter, does not count until it is released.)"""
        k = self.key_list; av = self.swallowed
        return any(k[c] and c not in av for c in self.reg.codes(action))

    def _terrain_free(self):
        """Field with no window: no Esc menu, no message, no game menu or map, no battle, no screen effect. The camera keys (left / right arrows by default) then rotate the game's camera; otherwise they are left / right in the windows (no up / down arrow action)."""
        eng = self.engine; e = eng.state
        return (not self.pause.opened and self.title_screen is None and e.part in (ENGINE.PART_TOWN, ENGINE.PART_FIELD) and not eng.msg_visible and eng.active_menu() is None and not self.map_open and e.effect is None and getattr(e, 'screen_effect', None) is None)

    # shadow of the characters: Chara.s_shadowMesh (square +-1 in XZ, Y = 0.00625), scale s_shadowDefScale (0.347 ; 10 ; 0.206), texture CharaTex/common (32
    # x 32: black ellipse of alpha 96 / 255, point filter), shader Custom/CharaShadow (SrcAlpha / OneMinusSrcAlpha, ZWrite Off, ZTest LEqual, queue 2999 =
    # before the characters), position Chara.SetShadowPosition = pos + panel rotation x (0 ; 0 ; 0.25) (hence shifted by 0.25 along the camera ray: same
    # point on screen, but in front of the ground), orientation Euler(0, camera yaw, 0) = flat.
    SHADOW_HALF_SIZE = (0.347, 0.206); SHADOW_HEIGHT = 0.0625; SHADOW_OFFSET = 0.25

    def _draw_character_shadows(self, shadows, cam3d):
        if getattr(self, '_shadow_tex', None) is None: self._create_shadow_tex()
        if not self._shadow_tex: return
        r_, u_, f_ = cam3d
        rh = np.array([r_[0], 0.0, r_[2]]); rh /= np.linalg.norm(rh) or 1                 # yaw of the camera alone
        fh = np.array([f_[0], 0.0, f_[2]]); fh /= np.linalg.norm(fh) or 1
        ex, ez = self.SHADOW_HALF_SIZE
        gl.glDepthMask(gl.GL_FALSE); gl.glDepthFunc(gl.GL_LEQUAL)
        self.prog['cutoff'] = 0.0; self.prog['crisp'] = 0.0; self.prog['tsize'] = (64.0, 64.0); drawn = []
        for slot, p, alpha in shadows:
            c = p - f_ * self.SHADOW_OFFSET + np.array([0.0, self.SHADOW_HEIGHT, 0.0])
            P = [c - rh * ex - fh * ez, c + rh * ex - fh * ez, c + rh * ex + fh * ez, c - rh * ex + fh * ez]
            q = np.array([P[0], P[1], P[2], P[0], P[2], P[3]], np.float32).ravel().tolist()
            uv = [0, 0, 1, 0, 1, 1, 0, 0, 1, 1, 0, 1]; cl = [1.0, 1.0, 1.0, min(1.0, alpha)] * 6          # _Color.a = SetShadowAlpha (1 by default), x alpha of the character (SetColorWithShadow)
            vl = self._shadow_vl.get(slot)
            if vl is None: vl = self._shadow_vl[slot] = self.prog.vertex_list(6, gl.GL_TRIANGLES, position=('f', q), tex_coords=('f', uv), colors=('f', cl))
            else: vl.position[:] = q; vl.tex_coords[:] = uv; vl.colors[:] = cl
            drawn.append(vl)
        self._linked_domain = None                                      # (written after the scenery's draws: rebind + commit, otherwise a frame late)
        for vl in drawn: self._paint(self._shadow_tex, vl)
        gl.glDepthMask(gl.GL_TRUE); gl.glDepthFunc(gl.GL_LESS); self.prog['cutoff'] = 0.05        # (the following characters discard transparent pixels: otherwise their whole panel writes depth and hides the companion behind)

    # shadow of the characters on the world map: Chara.SetFieldMode resizes the shadow mesh to s_shadowDefScale x 28.8 (ellipse with 10 x 5.9 map-pixel
    # semi-axes) and tilts it by 36 degrees like the character (projected: 5.9 x cos 36 = 4.8); Chara.SetFieldPosition places it at the same point as the
    # character (its feet). The engine hides that of boats (packet 15).
    SHADOW_FIELD = (9.99, 5.93 * math.cos(math.radians(36.0)))

    def _draw_field_shadows(self, shadows, ui_scale):
        if getattr(self, '_shadow_tex', None) is None: self._create_shadow_tex()
        gl.glDepthMask(gl.GL_FALSE); gl.glDepthFunc(gl.GL_LEQUAL)
        self.prog['cutoff'] = 0.0; self.prog['crisp'] = 0.0; self.prog['tsize'] = (64.0, 64.0)
        rx, rz = self.SHADOW_FIELD[0] * ui_scale / CH_PX, self.SHADOW_FIELD[1] * ui_scale / CH_PX
        drawn = []
        for slot, p, alpha in shadows:
            x, y, z = float(p[0]), 0.03, float(p[2])
            q = [x - rx, y, z - rz, x + rx, y, z - rz, x + rx, y, z + rz, x - rx, y, z - rz, x + rx, y, z + rz, x - rx, y, z + rz]
            uv = [0, 0, 1, 0, 1, 1, 0, 0, 1, 1, 0, 1]; cl = [1.0, 1.0, 1.0, min(1.0, alpha)] * 6
            vl = self._shadow_vl.get(slot)
            if vl is None: vl = self._shadow_vl[slot] = self.prog.vertex_list(6, gl.GL_TRIANGLES, position=('f', q), tex_coords=('f', uv), colors=('f', cl))
            else: vl.position[:] = q; vl.tex_coords[:] = uv; vl.colors[:] = cl
            drawn.append(vl)
        self._linked_domain = None                                      # (vertices written after the 1st draw of the frame, the map's: without rebind + commit the shadows went out with the previous frame's, a frame late)
        for vl in drawn: self._paint(self._shadow_tex, vl)
        gl.glDepthMask(gl.GL_TRUE); gl.glDepthFunc(gl.GL_LESS); self.prog['cutoff'] = 0.05

    def _create_shadow_tex(self):
        # CharaTex/common texture: ellipse of alpha 96 / 255 with sharp edges (point filter); here the same maximum and the same size, but the edge is softened
        # (shadows "too harsh")
        n = 64; yy, xx = np.mgrid[0:n, 0:n]; r = np.hypot((xx + 0.5 - n / 2) / (n / 2), (yy + 0.5 - n / 2) / (n / 2)); k = np.clip((r - 0.45) / 0.55, 0.0, 1.0)
        a = np.zeros((n, n, 4), np.uint8); a[..., 3] = np.round(96 * (1.0 - k * k * (3 - 2 * k))).astype(np.uint8)
        t = self._tex(a, False)
        gl.glBindTexture(t.target, t.id); gl.glTexParameteri(t.target, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR); gl.glTexParameteri(t.target, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        self._shadow_tex = t; self._shadow_vl = {}
