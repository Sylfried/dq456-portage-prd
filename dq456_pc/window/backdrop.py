"""Blurred scenery behind the casino, the bestiary and the mini-games: these engine parts (slot machine, poker, monster book; DQ V's Slime Touch) draw a picture made for the
portrait screen of the phone on a black background; the port shows the blurred scenery behind it instead of black. The bestiary has its own picture in front of it, as in the game: a
parchment across the phone's column (`_paint_parchment`); whatever background the town left (its sky, its BackColor gradient) is not the book's, so the bestiary always has the blurred scenery. DQ V's slime race and DQ VI's slime curling draw a scene made for the
portrait screen: beside the phone's column (the edges of their world, often black) the window shows that column stretched, blurred and darkened the same way.

How: after each frame of a town or of the world map, the scene (without the interface) is copied at a quarter of the window size (a blit, cheap); the last copy is the place the player has just
left. On the first frame of a mini-game it is blurred once (Gaussian, on the CPU) and drawn over the whole window in place of the black, under the mini-game's picture, the windows and the fades.
Only when the background of the screen is black (clear color black, no `BackColor` gradient). No copy yet (a savestate loaded straight into the casino): black, as before."""
import ctypes
import numpy as np
import pyglet
from pyglet import gl
from dq456_pc.render import gl_errors as GE
from PIL import Image, ImageFilter
from dq456_pc.core import engine as ENGINE
from dq456_pc.window.constants import TITLE_MAP, ASPECT_PORTRAIT
from dq456_pc.render.screen_blur import ScreenBlur
from dq456_pc.core import phone_scenes as PS

BACKDROP_REDUCTION = 4          # the copy: a quarter of the window in width and in height
BACKDROP_BLUR = 6.0             # Gaussian radius, in pixels of the copy (24 pixels at 1080p)
BACKDROP_LUM = 0.75             # the blurred scenery slightly darkened: the mini-game's picture and its windows stay in front
SIDES_FEATHER = 0.08            # the curling: width of the soft border between the sharp column and the blurred sides (fraction of the half width)
# The bestiary's background (MenuPortItemTable.Make_MonsterEncyclopedia, the same in the three games): a quad of the texture "Menu/map/encyclopedia" (a parchment, 256 x 256) written as is in
# clip space (shader Custom/BackOrtho: gl_Position = in_POSITION0, opaque, queue Background+100), x from -1 to 1 and y from -0.89 x Camera.aspect to + 0.89 x Camera.aspect: the phone's whole
# width, 0.89 of that width in height, centered; uv (0, 1) and (1, 1) at the bottom, (1, 0) and (0, 0) at the top (the picture upside down, as the game shows it).
PARCHMENT_HEIGHT = 0.89
# Screens laid over the game (the bestiary, the casino, the mini-games): the game opens and closes them with a fade to black (UnitySetFade, then the part's fade-in); the port
# shows that fade as the place blurring and darkening to the blurred backdrop behind the screen, then the screen coming out of the blur (and back when it closes): no black.
OVERLAY_STARTS = ('_ZN6Global9startBookEv', '_ZN6Global11startCasinoEv', '_ZN6Global12startCurlingEv', '_ZN6Global14startSlimeRaceEv', '_ZN6Global15startSlimeTouchEv')
OVERLAY_PARTS = ENGINE.MINIGAME_PARTS + ENGINE.PHONE_SCENE_PARTS
TRANSITION_STEPS = 240          # engine frames: a transition not over by then is forgotten (its fades are black again)


def fade_black(fd):
    """Is a fade black (to or from black: the screen changes)?"""
    return max(fd.get('col') or (0.0, 0.0, 0.0)) < 0.05


def _gen(f):
    n = gl.GLuint(); f(1, ctypes.byref(n)); return n.value


def black_background(background, gradient):
    """The screen's background is black: clear color (r, g, b) black and no gradient."""
    return not gradient and max(float(c) for c in background[:3]) < 0.02


def blurred(rgb, radius=BACKDROP_BLUR):
    """Copy (h x w x 3, uint8) -> blurred copy, same size."""
    return np.asarray(Image.fromarray(np.ascontiguousarray(rgb)).filter(ImageFilter.GaussianBlur(radius)))


class BackdropMixin:
    backdrop_fbo = None; backdrop_copy = None; backdrop_size = None; backdrop_serial = 0; backdrop_tex = None; backdrop_built = 0; backdrop_vl = None; screen_blur = None; backdrop_off = False
    parchment_tex = None; parchment_vl = None; parchment_missing = False

    def _blur_window(self):
        """What has just been drawn in the window, blurred in place (the port's own screens are drawn on top: "Settings" menu, history, Sauvegardes and language screens)."""
        if self.screen_blur is None: self.screen_blur = ScreenBlur()
        self.screen_blur.apply(*self.get_framebuffer_size())

    def _warm_up_gl(self):
        """The shader programs made on first use (screen blur and its edges and sides variants, the town gradient, the wide window sides) compiled and drawn once on the first frame,
        behind the loading screen (the frame is drawn over right after): the first opening of the Esc menu, of the casino or of a mini-game, the first town with a gradient no longer stall
        on their compilation by the graphics driver."""
        try:
            W, H = self.get_framebuffer_size()
            if W < 16 or H < 16: return
            if self.screen_blur is None: self.screen_blur = ScreenBlur()
            self.screen_blur.apply(W, H); self.screen_blur.apply(W, H, edges=0.5); self.screen_blur.apply(W, H, lum=BACKDROP_LUM, sides=(0.5, SIDES_FEATHER))
            self._gradient_program(); self._sides_program()
            self._paint_background_gradient((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        except Exception as ex: print('warming up the shaders:', ex, flush=True)
        finally:
            gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0); gl.glViewport(0, 0, *self.get_framebuffer_size())

    def _overlay_hooks(self):
        """Once per engine: code hooks on the Global::start* of the screens laid over the game (OVERLAY_STARTS), the start of their transition."""
        core = getattr(self.engine, 'core', None)
        if core is None or getattr(self, '_overlay_hooked', None) is core: return
        self._overlay_hooked = core
        import unicorn
        def started(uc, addr, size, user): self.overlay_opening = True
        found = [a for a in (core.export(n) for n in OVERLAY_STARTS) if a]
        for a in found: core.uc.hook_add(unicorn.UC_HOOK_CODE, started, None, a, a)
        if found: self.engine._flush_tb('overlay screens')

    def _overlay_update(self, e):
        """The transition of a screen laid over the game, updated once a frame: {'phase': 'open' / 'close', 'seen', 'f0'} or None. Open: from the Global::start* that
        opens it until its own fade-in is over; close: from the black fade-out that starts in it until the place's fade-in is over."""
        try: self._overlay_hooks()
        except Exception as ex: self._overlay_hooked = getattr(self.engine, 'core', None); print('overlay screens: %s' % ex, flush=True)
        over = e.part in OVERLAY_PARTS or bool(self._slime_touch_up()); fd = e.fade; st = getattr(self, 'overlay_tr', None); frames = self.engine.frames
        if getattr(self, 'overlay_opening', False): self.overlay_opening = False; st = {'phase': 'open', 'seen': over, 'f0': frames}
        elif st is None and over and fade_black(fd) and fd['end'] >= 0.99 and fd['a'] < 0.99: st = {'phase': 'close', 'seen': False, 'f0': frames}
        if st is not None:
            if (st['phase'] == 'open') == over: st['seen'] = True
            if (st['seen'] and fd['a'] <= 0.004 and fd['end'] <= 0.01) or frames - st['f0'] > TRANSITION_STEPS: st = None
        self.overlay_tr = st
        return st

    def _overlay_fade(self, e, fd, background):
        """A black fade of such a transition drawn as a blur instead: what is drawn blurred `a` much, darkened to the backdrop's light on the place's side.
        True when it replaced the fade (nothing else to draw)."""
        st = self._overlay_update(e) if not background else getattr(self, 'overlay_tr', None)
        if st is None or not fade_black(fd): return False
        if background or fd['a'] <= 0.004: return True
        over = e.part in OVERLAY_PARTS or bool(self._slime_touch_up()); a = min(float(fd['a']), 1.0)
        if self.screen_blur is None: self.screen_blur = ScreenBlur()
        self.screen_blur.apply(*self.get_framebuffer_size(), lum=1.0 if over else 1.0 - (1.0 - BACKDROP_LUM) * a, mix=a)
        return True

    def _backdrop_keep(self, e):
        """The scene of a town or of the world map has just been drawn (interface not yet): reduced copy, kept for the mini-games (not the title screen, not a mini-game itself)."""
        if self.backdrop_off or e.part not in (ENGINE.PART_TOWN, ENGINE.PART_FIELD) or (e.part == ENGINE.PART_TOWN and e.game_map == TITLE_MAP): return
        W, H = self.get_framebuffer_size(); w, h = max(1, W // BACKDROP_REDUCTION), max(1, H // BACKDROP_REDUCTION)
        if W < 2 or H < 2: return
        try:
            fresh = self.backdrop_size != (w, h)
            if fresh:
                GE.clear()
                if self.backdrop_copy is not None: self.backdrop_copy.delete()
                if self.backdrop_fbo is not None: gl.glDeleteFramebuffers(1, ctypes.byref(gl.GLuint(self.backdrop_fbo)))
                t = pyglet.image.Texture.create(w, h, internalformat=gl.GL_RGBA8)
                fbo = _gen(gl.glGenFramebuffers); gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, fbo)
                gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0, t.target, t.id, 0)
                self.backdrop_copy, self.backdrop_fbo, self.backdrop_size = t, fbo, (w, h)
            gl.glBindFramebuffer(gl.GL_READ_FRAMEBUFFER, 0); gl.glBindFramebuffer(gl.GL_DRAW_FRAMEBUFFER, self.backdrop_fbo)
            gl.glBlitFramebuffer(0, 0, W, H, 0, 0, w, h, gl.GL_COLOR_BUFFER_BIT, gl.GL_LINEAR)
            if fresh: GE.check('backdrop copy')                                    # (the first copy into new buffers)
            self.backdrop_serial += 1
        except Exception: self.backdrop_serial = 0; self.backdrop_off = True          # (no blit on this machine: black behind the mini-games, as before; not tried again)
        finally:
            gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0); gl.glViewport(0, 0, W, H)

    def _blur_edges(self):
        """"Edge blur" setting: the edges of the game picture blurred, like the vignette darkens them (the scene has just been drawn, interface not yet)."""
        v = float(self.reg['edge_blur'])
        if v <= 0.005: return
        if self.screen_blur is None: self.screen_blur = ScreenBlur()
        self.screen_blur.apply(*self.get_framebuffer_size(), edges=v)

    def _blur_sides(self, e):
        """DQ V's slime race and DQ VI's slime curling (their scenes are made for the phone's portrait screen, the window shows its whole height): what lies beside the phone's column (the edges
        of their world, often nothing: black) shows the column itself, stretched across the window, blurred and darkened like the casino's background; the column stays sharp.
        The maps of core/phone_scenes (DQ VI's prologue cliff), while a camera movement of the map shows the scene (the dragon's arrival and flight; not while the hero walks): beyond the
        phone's width, their own scenery blurred (a camouflage of the ends of the cliff)."""
        ob = getattr(self, 'objects', None)
        if (e.part == ENGINE.PART_TOWN and PS.concerned(ENGINE.GM.profile().key, e.game_map) and e.game_map == getattr(self, 'map_name', None)
                and ob is not None and (ob.camera_no or ob.key_view is not None)):
            W, H = self.get_framebuffer_size(); inner = PS.column(W / max(1.0, float(H)))
            if inner >= 1.0: return
            if self.screen_blur is None: self.screen_blur = ScreenBlur()
            self.screen_blur.apply(W, H, lum=PS.SIDES_LUM, sides=(inner, PS.SIDES_FEATHER), stretch=False); return
        if e.part not in ENGINE.PHONE_SCENE_PARTS: return
        W, H = self.get_framebuffer_size()
        inner = ASPECT_PORTRAIT * H / max(1.0, float(W))
        if inner >= 1.0: return
        if self.screen_blur is None: self.screen_blur = ScreenBlur()
        self.screen_blur.apply(W, H, lum=BACKDROP_LUM, sides=(inner, SIDES_FEATHER))

    def _backdrop_forget(self):
        """Savestate loaded: the copy belonged to the game before the load."""
        self.backdrop_serial = 0

    def _backdrop_texture(self):
        """The blurred scenery (texture), built once per copy; None if there is no copy."""
        if not self.backdrop_serial or self.backdrop_fbo is None: return None
        if self.backdrop_built != self.backdrop_serial or self.backdrop_tex is None:
            w, h = self.backdrop_size; buf = (ctypes.c_ubyte * (w * h * 4))()
            drawn, read = gl.GLint(), gl.GLint()                       # (called while the scene is being drawn: the off-screen target stays bound afterwards)
            gl.glGetIntegerv(gl.GL_DRAW_FRAMEBUFFER_BINDING, ctypes.byref(drawn)); gl.glGetIntegerv(gl.GL_READ_FRAMEBUFFER_BINDING, ctypes.byref(read))
            gl.glBindFramebuffer(gl.GL_READ_FRAMEBUFFER, self.backdrop_fbo); gl.glReadPixels(0, 0, w, h, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, buf)
            gl.glBindFramebuffer(gl.GL_READ_FRAMEBUFFER, read.value); gl.glBindFramebuffer(gl.GL_DRAW_FRAMEBUFFER, drawn.value)
            rgb = blurred(np.frombuffer(buf, np.uint8).reshape(h, w, 4)[:, :, :3])
            if self.backdrop_tex is not None: self.backdrop_tex.delete()
            t = pyglet.image.ImageData(w, h, 'RGB', rgb.tobytes()).get_texture()
            gl.glBindTexture(t.target, t.id)
            gl.glTexParameteri(t.target, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR); gl.glTexParameteri(t.target, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
            gl.glTexParameteri(t.target, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE); gl.glTexParameteri(t.target, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
            self.backdrop_tex, self.backdrop_built = t, self.backdrop_serial
        return self.backdrop_tex

    def _draw_backdrop(self, e, background, gradient):
        """Mini-game on a black background: the blurred scenery over the whole drawing area, before the mini-game's picture (depth neither read nor written). The bestiary: always, then its
        parchment."""
        if e.part not in ENGINE.MINIGAME_PARTS: return
        book = e.part == ENGINE.PART_BESTIARY
        if not book and not black_background(background, gradient): return
        self._paint_backdrop()
        if book: self._paint_parchment()

    def _parchment_texture(self):
        """The bestiary's parchment (cache file of the "Menu/map/encyclopedia" texture, extracted with the title's images), or None without it."""
        if self.parchment_tex is None and not self.parchment_missing:
            path = (getattr(self, 'pre_game_files', None) or {}).get('encyclopedia')
            try:
                t = pyglet.image.load(path).get_texture() if path else None
            except Exception: t = None
            if t is None: self.parchment_missing = True; return None
            gl.glBindTexture(t.target, t.id)
            gl.glTexParameteri(t.target, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR); gl.glTexParameteri(t.target, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
            gl.glTexParameteri(t.target, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE); gl.glTexParameteri(t.target, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
            self.parchment_tex = t
        return self.parchment_tex

    def parchment_quad(self):
        """The parchment's corners in clip space: (x0, y0, x1, y1), over the interface column (450 units wide: the phone's width), 0.89 of its width in height, centered on it."""
        cx, cy, s = self.book_frame(); W, H = max(1.0, float(self.width)), max(1.0, float(self.height))          # (the book's window frame: enlarged with it, menu_display.book_frame)
        hw = 225.0 * s; hh = PARCHMENT_HEIGHT * hw
        return (cx - hw) / W * 2 - 1, (cy - hh) / H * 2 - 1, (cx + hw) / W * 2 - 1, (cy + hh) / H * 2 - 1

    def _paint_parchment(self):
        """The bestiary's parchment, opaque, behind the list, the sheet and the monster (depth neither read nor written)."""
        t = self._parchment_texture()
        if t is None: return
        x0, y0, x1, y1 = self.parchment_quad(); u, v = t.tex_coords[3], t.tex_coords[7]
        prog = self.program_effect
        if self.parchment_vl is None:
            self.parchment_vl = prog.vertex_list(6, gl.GL_TRIANGLES, position=('f', [0.0] * 12), tex_coords=('f', [0.0] * 12), colors=('f', [1.0] * 24))
        self.parchment_vl.position[:] = [x0, y0, x1, y0, x1, y1, x0, y0, x1, y1, x0, y1]
        self.parchment_vl.tex_coords[:] = [0, v, u, v, u, 0, 0, v, u, 0, 0, 0]
        gl.glDisable(gl.GL_DEPTH_TEST); gl.glDepthMask(gl.GL_FALSE); gl.glDisable(gl.GL_BLEND)
        prog.use(); prog['lum'] = 1.0; prog['blur'] = 0.0
        gl.glActiveTexture(gl.GL_TEXTURE0); gl.glBindTexture(t.target, t.id); self.parchment_vl.draw(gl.GL_TRIANGLES)
        prog.stop()
        gl.glDepthMask(gl.GL_TRUE); gl.glEnable(gl.GL_DEPTH_TEST); gl.glEnable(gl.GL_BLEND); gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)

    def _paint_backdrop(self):
        """The blurred scenery over the whole drawing area (the mini-games that clear their own view to black: DQ V's Slime Touch, DQ VI's curling, call it directly)."""
        t = self._backdrop_texture()
        if t is None: return
        u, v = t.tex_coords[3], t.tex_coords[7]                      # (texture rounded up to a power of 2 on some drivers: the picture's part only)
        prog = self.program_effect
        if self.backdrop_vl is None:
            self.backdrop_vl = prog.vertex_list(6, gl.GL_TRIANGLES, position=('f', [-1, -1, 1, -1, 1, 1, -1, -1, 1, 1, -1, 1]), tex_coords=('f', [0] * 12), colors=('f', [1.0] * 24))
        self.backdrop_vl.tex_coords[:] = [0, 0, u, 0, u, v, 0, 0, u, v, 0, v]
        gl.glDisable(gl.GL_DEPTH_TEST); gl.glDepthMask(gl.GL_FALSE); gl.glDisable(gl.GL_BLEND)
        prog.use(); prog['lum'] = 1.0; prog['blur'] = 0.0
        self.backdrop_vl.colors[:] = [BACKDROP_LUM, BACKDROP_LUM, BACKDROP_LUM, 1.0] * 6
        gl.glActiveTexture(gl.GL_TEXTURE0); gl.glBindTexture(t.target, t.id); self.backdrop_vl.draw(gl.GL_TRIANGLES)
        prog.stop()
        gl.glDepthMask(gl.GL_TRUE); gl.glEnable(gl.GL_DEPTH_TEST); gl.glEnable(gl.GL_BLEND); gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
