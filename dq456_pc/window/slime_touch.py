"""Dragon Quest V's Slime Touch (the casino's mini-game): drawing and input of SlimeTouchPort (the logic is in core/slime_touch.py).

While the port exists the scene camera is off and the orthographic camera clears its view to black: the window shows that layer alone (on the blurred scenery of the casino instead
of the black, like the casino's other games: backdrop.py), the game's screen across the window's whole height (its 384 units; the phone's black margins above and below it left out),
with the engine's meshes of that layer (the board, the slimes, the hammer) and the CG prefabs that the engine draws this frame (countdown, "GAME OVER",
sweat drops, target boards), sorted like Unity's transparent queue: render queue, then farthest first.
Input: a click in one of the eight boxes (a touch on the phone); with the keys or a gamepad, a direction (diagonals included) and A hit the box on that side of the board's middle.
"""
import numpy as np
from pyglet import gl
from dq456_pc.core import slime_touch as ST
from dq456_pc.core import engine as ENGINE
from dq456_pc.world import fbx as FX

QUEUE = 3850                     # render queue of Custom/DssaCharaTransparent (the CG prefabs' shader)
SCREEN_HALF = (128.0, 192.0)     # half the width and height of the game's screen in the layer (the board's mesh: 256 x 384 units, centered; the phone's height, 2 x ORTHO_HALF_WIDTH
                                 # / ASPECT_PORTRAIT = 457 units, adds black margins above and below it, which left the game small in the middle of the window)


def half_height(aspect=None):
    """Half the height of the window in units of the orthographic layer: the game's screen across the window's whole height; a window narrower than the screen
    (`aspect` = width / height) shows its whole width."""
    hh = SCREEN_HALF[1]
    return max(hh, SCREEN_HALF[0] / aspect) if aspect else hh


def model_vertices(m, clip, t, shift):
    """World vertices of a prefab at time t of its clip: the root keeps its own place in the prefab (animated if the clip moves it), moved by `shift` in x (CgController.Setup)."""
    poses = [(clip, t, 1.0)] if clip in m.clips else []
    p, q, s = m.local(poses)
    L = np.eye(4); L[:3, :3] = FX.quat_mat(q[0:1])[0] * s[0][None, :]; L[:3, 3] = p[0]; L[0, 3] += shift
    return m.vertices(L, poses), m.materials(poses)


class SlimeTouchMixin:
    _st_models = None                    # prefab name -> {'m': FbxModel, 'groups': [...]} (OpenGL objects made in the drawing thread)
    _st_pictures = None                  # board picture name -> texture

    def _slime_touch_up(self):
        sl = getattr(self.engine.ui, 'slime_touch', None)
        return sl is not None and sl['live']

    def _slime_touch_clips(self, name_str):
        """{clip: length} of a prefab (Engine.prefab_clips), None if it cannot be read."""
        try: return {k: c['dur'] for k, c in self.data.prefab_model(name_str)['clips'].items()}
        except Exception as ex: print('Slime Touch: prefab %s not read: %s' % (name_str, ex), flush=True); return None

    def _st_model(self, name_str):
        if self._st_models is None: self._st_models = {}
        r = self._st_models.get(name_str)
        if r is None:
            try:
                m = FX.FbxModel(self.data.prefab_model(name_str)); groups = []
                for gi, (gtex, gtri) in enumerate(m.groups):
                    idx = gtri.ravel(); n = len(idx)
                    t = self._tex(gtex, False)
                    gl.glBindTexture(t.target, t.id); gl.glTexParameteri(t.target, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR); gl.glTexParameteri(t.target, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
                    vl = self.prog.vertex_list(n, gl.GL_TRIANGLES, position=('f', np.zeros(n * 3, np.float32)), tex_coords=('f', np.zeros(n * 2, np.float32)), colors=('f', np.ones(n * 4, np.float32)))
                    groups.append({'tex': t, 'raw': gtex, 'idx': idx, 'vl': vl, 'n': n, 'slot': m.materials0[gi][1]})
                r = {'m': m, 'groups': groups}
            except Exception as ex:
                print('Slime Touch: prefab %s not displayed: %s' % (name_str, ex), flush=True); r = {'m': None}
            self._st_models[name_str] = r
        return r if r['m'] is not None else None

    def _st_picture(self, name_str):
        """A board's picture (`board/slime`...: the Texture2D of that name)."""
        if self._st_pictures is None: self._st_pictures = {}
        if name_str not in self._st_pictures:
            arr = self.data.texture(name_str.split('/')[-1])
            t = None
            if arr is not None:
                t = self._tex(arr, False)
                gl.glBindTexture(t.target, t.id); gl.glTexParameteri(t.target, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR); gl.glTexParameteri(t.target, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
            self._st_pictures[name_str] = t
        return self._st_pictures[name_str]

    def _st_layer_matrix(self, aspect):
        hh = half_height(aspect); hw = hh * aspect
        M = np.zeros((4, 4)); M[0, 0] = 1.0 / hw; M[1, 1] = 1.0 / hh; M[3, 3] = 1.0
        return M

    def _draw_slime_touch(self, e):
        """The blurred scenery (black without it), then the orthographic layer: the engine's meshes and the CGs drawn this frame, in Unity's order."""
        gl.glClearColor(0.0, 0.0, 0.0, 1.0); self.clear()
        gl.glViewport(0, 0, *self.get_framebuffer_size())
        self._paint_backdrop()                                                # the blurred scenery of the casino instead of the black (backdrop.py)
        gl.glDisable(gl.GL_DEPTH_TEST); gl.glDisable(gl.GL_CULL_FACE); gl.glEnable(gl.GL_BLEND); gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        aspect = self.width / max(1, self.height)
        items = []
        if e.meshes and any(st.get('ortho') for st in e.mesh_state.values()):
            for key, tex, tw, th, vl, shader, hd in self._battle_meshes(e, None, True, ortho=True, keys=True):
                items.append((key, 'mesh', (tex, tw, th, vl, hd)))
        st = self.engine.ui.slime_touch.get('state')
        for cg, name_str, clip, t, picture, shift in (ST.shown(st) if st is not None else []):
            r = self._st_model(name_str)
            if r is None: continue
            V, mats = model_vertices(r['m'], clip, t, shift)
            for gi, g in enumerate(r['groups']):
                col, place = mats[min(gi, len(mats) - 1)]
                if col[3] <= 0.0: continue
                pos = V[g['idx']]
                items.append(((QUEUE, -float(pos[:, 2].mean())), 'cg', (r['m'], g, pos, col, place, picture)))
        items.sort(key=lambda it: it[0])
        self.prog.use()
        self.prog['mvp'] = tuple(float(x) for x in self._st_layer_matrix(aspect).T.ravel()); self.prog['cutoff'] = 0.0; self.prog['tsize'] = (1.0, 1.0); self.prog['amul'] = 1.0
        self._gl_tint = None; self._set_tint((1.0, 1.0, 1.0)); gl.glActiveTexture(gl.GL_TEXTURE0)
        self._linked_domain = None
        for key, kind, it in items:
            if kind == 'mesh':
                tex, tw, th, vl, hd = it
                self.prog['crisp'] = 0.0 if hd else (1.0 if self.smoothing else 2.0); self.prog['tsize'] = (float(tw), float(th)); self._paint(tex, vl)
            else:
                m, g, pos, col, place, picture = it
                tex = g['tex']
                if picture is not None and g['slot'] == 1: tex = self._st_picture(picture) or tex          # (a board's second material shows the picture the engine chose)
                vl = g['vl']; vl.position[:] = pos.ravel(); vl.colors[:] = np.tile(np.array(col, np.float32), g['n'])
                vl.tex_coords[:] = (m.uv0[g['idx']] * np.array(place[:2], np.float32) + np.array(place[2:], np.float32)).ravel()
                self.prog['crisp'] = 0.0; self.prog['tsize'] = (float(g['raw'].shape[1]), float(g['raw'].shape[0]))
                self._linked_domain = None
                self._paint(tex, vl)
        self.prog['crisp'] = 0.0; self.prog['tsize'] = (1.0, 1.0); self.prog['cutoff'] = 0.05
        self.prog.stop()

    def slime_touch_click(self, x, y):
        """A click (window pixel, pyglet) during the Slime Touch: the box under it, if the boxes are on. -> True if a box was hit."""
        if not self._slime_touch_up(): return False
        lx, ly = ST.window_to_layer(x, y, self.width, self.height, half_height(self.width / max(1, self.height)))
        return self.engine.slime_touch_press(lx, ly)

    def slime_touch_keys(self, p, decl):
        """Keys / gamepad during the Slime Touch: A pressed with a direction held hits the box on that side (that A is then not given to the engine). -> the new (p, decl)."""
        if not (decl & ENGINE.A) or not self._slime_touch_up(): return p, decl
        sl = self.engine.ui.slime_touch; st = sl.get('state')
        if st is None or not st['touch']: return p, decl
        b = ST.box_of_direction(p)
        if b is None: return p, decl
        cx, cy = ST.BOXES[b]
        self.engine.slime_touch_press(cx, cy)
        return p, decl & ~ENGINE.A                          # (A stays in the held keys: the next frames do not see a new press)
