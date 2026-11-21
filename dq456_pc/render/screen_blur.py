"""Live blur of the window behind the port's own screens: the "Settings" menu and its pages, the history, the saves and language screens of the title; and blur of the edges of
the game picture ("Edge blur" setting: the same shape as the vignette, the blurred copy laid over the scene with an opacity growing toward the edges).

The blur is drawn behind the port's menus and the history, never behind the game's own in-game menu (spells, formation, tactics...).

What has just been drawn in the window (the game, which keeps running behind these screens) is reduced to an eighth of the window by successive halvings (each one averages 2 x 2 pixels), blurred by two
Gaussian passes (horizontal then vertical) and drawn back over the whole window. Everything stays on the graphics card (no readback): a few tenths of a millisecond per frame."""
import ctypes
from pyglet import gl
from dq456_pc.render import gl_errors as GE
from pyglet.graphics.shader import Shader, ShaderProgram

LEVELS = 3                       # 3 halvings: an eighth of the window (240 x 135 at 1080p)
SIGMA = 3.0                      # Gaussian, in pixels of the reduction (24 pixels at 1080p, like the casino's background)
RADIUS = 7                       # taps on each side

VERTEX = '''#version 330 core
in vec2 position; out vec2 v_uv;
void main() { gl_Position = vec4(position, 0.0, 1.0); v_uv = position * 0.5 + 0.5; }'''

FRAG = '''#version 330 core
in vec2 v_uv; out vec4 fragColor;
uniform sampler2D tex; uniform vec2 step; uniform float lum; uniform float weights[%d];
void main() {
    vec3 c = texture(tex, v_uv).rgb * weights[0];
    for (int i = 1; i < %d; i++) c += (texture(tex, v_uv + step * float(i)).rgb + texture(tex, v_uv - step * float(i)).rgb) * weights[i];
    fragColor = vec4(c * lum, 1.0); }''' % (RADIUS + 1, RADIUS + 1)


FRAG_EDGES = '''#version 330 core
in vec2 v_uv; out vec4 fragColor;
uniform sampler2D tex; uniform float amount;
void main() {
    float r = length(v_uv * 2.0 - 1.0);                       // same ellipse as the vignette (menu_display._draw_vignette): untouched up to 45 % of the radius, full at 130 %
    fragColor = vec4(texture(tex, v_uv).rgb, amount * smoothstep(0.45, 1.3, r)); }'''


FRAG_MIX = '''#version 330 core
in vec2 v_uv; out vec4 fragColor;
uniform sampler2D tex; uniform float amount; uniform float lum;
void main() { fragColor = vec4(texture(tex, v_uv).rgb * lum, amount); }'''          # (the blurred copy over the whole picture, `amount` of it: a transition that blurs)


FRAG_SIDES = '''#version 330 core
in vec2 v_uv; out vec4 fragColor;
uniform sampler2D tex; uniform float inner; uniform float feather; uniform float lum; uniform float stretch;
void main() {
    float x = abs(v_uv.x * 2.0 - 1.0);                        // the phone's column stays sharp (|x| < inner), the sides are blurred, with a soft border `feather` wide
    vec2 uv = vec2(mix(v_uv.x, 0.5 + (v_uv.x - 0.5) * inner, stretch), v_uv.y);     // stretch 1: the sides show the blurred column stretched across the whole width (what lies beside it is often empty: black); 0: their own scenery, blurred
    fragColor = vec4(texture(tex, uv).rgb * lum, smoothstep(inner, inner + feather, x)); }'''


def side_source(x, inner):
    """Where the blurred copy is read for x (0..1 of the picture's width): the column stretched across the whole width (the shader's formula)."""
    return 0.5 + (x - 0.5) * inner


def side_weight(x, inner, feather):
    """Opacity of the blurred copy at x (0..1 of the picture's width) beside a sharp column of half width `inner` (0..1 of the half width): the shader's formula."""
    t = min(max((abs(x * 2.0 - 1.0) - inner) / max(feather, 1e-6), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def edge_weight(x, y, amount=1.0):
    """Opacity of the blurred copy at (x, y) in 0..1 of the picture (the shader's formula): 0 in the middle, `amount` in the corners."""
    import math
    r = math.hypot(x * 2 - 1, y * 2 - 1); k = min(max((r - 0.45) / (1.3 - 0.45), 0.0), 1.0)
    return amount * k * k * (3 - 2 * k)


def gaussian_weights(sigma=SIGMA, radius=RADIUS):
    """Weights of taps 0..radius (tap 0 counted once, the others twice): their total is 1."""
    import math
    w = [math.exp(-0.5 * (i / sigma) ** 2) for i in range(radius + 1)]
    total = w[0] + 2 * sum(w[1:])
    return [v / total for v in w]


def _gen(f):
    n = gl.GLuint(); f(1, ctypes.byref(n)); return n.value


class ScreenBlur:
    def __init__(self):
        self.size = None; self.levels = []; self.spare = None; self.prog = None; self.vl = None; self.ok = True; self.prog_edges = None; self.vl_edges = None; self.prog_sides = None; self.vl_sides = None; self.prog_mix = None; self.vl_mix = None

    def _texture(self, w, h):
        t = gl.GLuint(); gl.glGenTextures(1, ctypes.byref(t)); gl.glBindTexture(gl.GL_TEXTURE_2D, t.value)
        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA8, w, h, 0, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, None)
        for p, v in ((gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR), (gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR), (gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE), (gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)):
            gl.glTexParameteri(gl.GL_TEXTURE_2D, p, v)
        fbo = _gen(gl.glGenFramebuffers); gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, fbo)
        gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0, gl.GL_TEXTURE_2D, t.value, 0)
        return (t.value, fbo, w, h)

    def _free(self):
        for t, fbo, w, h in self.levels + ([self.spare] if self.spare else []):
            gl.glDeleteTextures(1, ctypes.byref(gl.GLuint(t))); gl.glDeleteFramebuffers(1, ctypes.byref(gl.GLuint(fbo)))
        self.levels = []; self.spare = None

    def _create(self, W, H):
        self._free(); w, h = W, H
        for _ in range(LEVELS):
            w, h = max(1, w // 2), max(1, h // 2); self.levels.append(self._texture(w, h))
        self.spare = self._texture(w, h)
        self.size = (W, H)
        if self.prog is None:
            self.prog = ShaderProgram(Shader(VERTEX, 'vertex'), Shader(FRAG, 'fragment'))
            self.vl = self.prog.vertex_list(6, gl.GL_TRIANGLES, position=('f', [-1, -1, 1, -1, 1, 1, -1, -1, 1, 1, -1, 1]))
            self.prog.use(); self.prog['weights'] = gaussian_weights(); self.prog.stop()

    def _pass(self, src, dst, step, lum=1.0):
        t, _, w, h = src
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, dst[1] if dst is not None else 0)
        gl.glViewport(0, 0, *(dst[2:] if dst is not None else self.size))
        self.prog['step'] = step; self.prog['lum'] = lum
        gl.glActiveTexture(gl.GL_TEXTURE0); gl.glBindTexture(gl.GL_TEXTURE_2D, t); self.vl.draw(gl.GL_TRIANGLES)

    def apply(self, W, H, lum=1.0, edges=None, sides=None, stretch=True, mix=None):
        """Replaces the content of the window (W x H pixels, framebuffer 0) with its blurred copy; with `edges` (0..1), only the edges of the picture, by that much (the middle stays sharp);
        with `sides` = (inner, feather), only the sides of a sharp middle column (see FRAG_SIDES), darkened by `lum`; `stretch`: the sides show the column stretched (otherwise their own scenery);
        with `mix` (0..1), the blurred copy darkened by `lum` laid over the whole picture, that much of it (the transitions of the screens laid over the game, backdrop.py).
        False if it could not be done (the screen stays as it is)."""
        if not self.ok or W < 16 or H < 16: return False
        try:
            fresh = self.size != (W, H)
            if fresh: GE.clear(); self._create(W, H)
            src = (0, W, H)
            for t, fbo, w, h in self.levels:                                    # halvings: framebuffer 0 -> 1/2 -> 1/4 -> 1/8
                gl.glBindFramebuffer(gl.GL_READ_FRAMEBUFFER, src[0]); gl.glBindFramebuffer(gl.GL_DRAW_FRAMEBUFFER, fbo)
                gl.glBlitFramebuffer(0, 0, src[1], src[2], 0, 0, w, h, gl.GL_COLOR_BUFFER_BIT, gl.GL_LINEAR)
                src = (fbo, w, h)
            low = self.levels[-1]; w, h = low[2], low[3]
            gl.glDisable(gl.GL_DEPTH_TEST); gl.glDisable(gl.GL_BLEND)
            self.prog.use()
            self._pass(low, self.spare, (1.0 / w, 0.0))                        # horizontal
            self._pass(self.spare, low, (0.0, 1.0 / h))                        # vertical
            if edges is None and sides is None and mix is None: self._pass(low, None, (0.0, 0.0), lum)           # back into the window, stretched (bilinear)
            self.prog.stop()
            if edges is not None:                                              # edges: the blurred copy over the scene, its opacity growing toward the edges
                if self.prog_edges is None:
                    self.prog_edges = ShaderProgram(Shader(VERTEX, 'vertex'), Shader(FRAG_EDGES, 'fragment'))
                    self.vl_edges = self.prog_edges.vertex_list(6, gl.GL_TRIANGLES, position=('f', [-1, -1, 1, -1, 1, 1, -1, -1, 1, 1, -1, 1]))
                gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0); gl.glViewport(0, 0, W, H)
                gl.glEnable(gl.GL_BLEND); gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
                self.prog_edges.use(); self.prog_edges['amount'] = float(min(max(edges, 0.0), 1.0))
                gl.glActiveTexture(gl.GL_TEXTURE0); gl.glBindTexture(gl.GL_TEXTURE_2D, low[0]); self.vl_edges.draw(gl.GL_TRIANGLES)
                self.prog_edges.stop()
            if mix is not None:                                                # mix: the blurred copy over the whole picture, `mix` of it
                if self.prog_mix is None:
                    self.prog_mix = ShaderProgram(Shader(VERTEX, 'vertex'), Shader(FRAG_MIX, 'fragment'))
                    self.vl_mix = self.prog_mix.vertex_list(6, gl.GL_TRIANGLES, position=('f', [-1, -1, 1, -1, 1, 1, -1, -1, 1, 1, -1, 1]))
                gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0); gl.glViewport(0, 0, W, H)
                gl.glEnable(gl.GL_BLEND); gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
                self.prog_mix.use(); self.prog_mix['amount'] = float(min(max(mix, 0.0), 1.0)); self.prog_mix['lum'] = float(lum)
                gl.glActiveTexture(gl.GL_TEXTURE0); gl.glBindTexture(gl.GL_TEXTURE_2D, low[0]); self.vl_mix.draw(gl.GL_TRIANGLES)
                self.prog_mix.stop()
            if sides is not None:                                              # sides: the blurred copy beside the sharp column
                if self.prog_sides is None:
                    self.prog_sides = ShaderProgram(Shader(VERTEX, 'vertex'), Shader(FRAG_SIDES, 'fragment'))
                    self.vl_sides = self.prog_sides.vertex_list(6, gl.GL_TRIANGLES, position=('f', [-1, -1, 1, -1, 1, 1, -1, -1, 1, 1, -1, 1]))
                gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0); gl.glViewport(0, 0, W, H)
                gl.glEnable(gl.GL_BLEND); gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
                self.prog_sides.use(); self.prog_sides['inner'] = float(sides[0]); self.prog_sides['feather'] = float(sides[1]); self.prog_sides['lum'] = float(lum); self.prog_sides['stretch'] = 1.0 if stretch else 0.0
                gl.glActiveTexture(gl.GL_TEXTURE0); gl.glBindTexture(gl.GL_TEXTURE_2D, low[0]); self.vl_sides.draw(gl.GL_TRIANGLES)
                self.prog_sides.stop()
            if fresh: GE.check('blur')                                             # (the first use of new buffers: OpenGL errors there make the blur unavailable)
            return True
        except Exception as ex:
            self.ok = False; print('Blur unavailable (%r): the game stays sharp behind the menus and on its edges' % (ex,), flush=True); return False
        finally:
            gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0); gl.glViewport(0, 0, W, H)
            gl.glEnable(gl.GL_BLEND); gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
