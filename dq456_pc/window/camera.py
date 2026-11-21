"""Camera: town camera (the game's, which the player rotates with the GAME's camera buttons), world map, battle and title cameras, sprite edge correction. The player only adjusts the rotation (left / right arrows, wheel click):
no more zoom, tilt, centering or height smoothing (removed), no more camera acceleration and no more rotation done by the viewer alongside the game: it is the game's, as on mobile."""
import math, os
import numpy as np
import pyglet
from dq456_pc.window import inputs as EN
from dq456_pc.menus import hud_mobile as HM
from dq456_pc.core import engine as ENGINE
from dq456_pc.window.constants import (ASPECT_PORTRAIT, ORTHO_HALF_WIDTH, LOOK_DOWN_DEFAULT, CAM_DRAG_MAX, CAM_DRAG_STEP, TITLE_MAP, CB_DEC_Z, CB_SCENERY_SCALE,
    CB_FAR, CB_FOV_NOMINAL, CB_NEAR, CB_YOFFSET, CH_SCALE, CH_PX, CH_PHONE_SHORT, EDGE_CORRECTION_DEFAULT, DIST_ADJ_TOWN, TOWN_FOV_DEFAULT, MODE_PP, TOWN_FAR, TOWN_NEAR, ENDING_CHAPTER, ENDING_FRAMING)
from dq456_pc.window.helpers import camera_free, matrix, portrait_base_height, rot_unity, whole_pixels


class CameraMixin:
    """Camera: town camera (the game's: the player rotates it with the game's camera buttons), world map, battle and title cameras, sprite edge correction."""

    def _field_camera(self, e, aspect):
        """World map, like the game's Field.Init / Field.SetPosition: orthographic top-down camera (Rotate(90, 0, 0)), centered on the point given by the engine
        (in pixels, 16 per tile). The top of the screen is north (-Z), east is on the right. Visible area = the phone's (16 tiles across, 28.4 up in portrait), its two sides swapped in landscape (16:9: 28.4 tiles across,
        16 up; see helpers.portrait_base_height) x game scale (balloon, packet 30). As on the phone, the world map's zoom is not the towns' (the hero 13 % of the height here, 14.6 % in a
        town: cancels). The camera is the game's: no player setting."""
        cx, cz = (e.field['cam'] if e.field and e.field['cam'] else (0.0, 0.0))
        dh = CH_PHONE_SHORT * portrait_base_height(aspect) / 2 * float(getattr(e, 'field_scale', 1.0)); dl = dh * aspect          # same field for all shaders; game scale (balloon: up to 1.25, Field.SetScale: orthographicSize x scale)
        if self.mode == MODE_PP: cx, cz = whole_pixels(cx) / CH_PX, whole_pixels(cz) / CH_PX; ui_scale = 1.0       # pixel perfect: camera on a map pixel, hero at its exact size (24 x 32 map pixels)
        else: cx, cz = cx / CH_PX, cz / CH_PX; ui_scale = CH_SCALE
        self._field_view = (cx, cz, dl, dh)                                      # (the world map camera is not adjustable: neither angle nor rotation)
        M = np.zeros((4, 4)); M[0, 0] = 1 / dl; M[0, 3] = -cx / dl
        M[1, 2] = -1 / dh; M[1, 3] = cz / dh                                            # screen y = -(z - cz) / dh
        M[2, 1] = -1 / 100.0; M[3, 3] = 1.0                                             # depth: the height (top view)
        return M, np.array([1.0, 0.0, 0.0]), 24 / CH_PX * ui_scale, 32 / CH_PX * ui_scale

    def _drivable_camera(self):
        """Are the game's camera buttons (left / right arrows, wheel click) sent to the engine? Free field of a town, dungeon or tower: not the world map, not the title screen, not a message, a menu, a map or an effect.
        The rest (player locked by an event, camera held by a scene, place that forbids rotating it, rotation limits) is decided by the game itself, as on mobile: it then refuses the button (Engine.camera_wait)."""
        e = self.engine.state
        return (self._terrain_free() and e.part == ENGINE.PART_TOWN and getattr(self, 'map_name', None) != TITLE_MAP and self.license_panel is None and self.port_screen is None and not self._name_entry_in_progress())

    def recenter_camera(self):
        """Wheel click, or both arrows together: both of the game's camera buttons together at the next step = the game returns the camera to north (TownPlayerManager::setCameraRotToNorth: it animates the return and holds the player during that time)."""
        self.cam_recenter = True

    def _axis_state(self, minus, plus):
        """The two camera arrows: -1 (left) / 0 / +1 (right) = direction requested by hand. Both together request the return to north, once; as long as one of them is still held after this double press, nothing happens
        (the release is never exactly simultaneous)."""
        if minus and plus:
            if not self.cam_two: self.cam_recenter = True
            self.cam_two = True; return 0
        if not (minus or plus): self.cam_two = False; return 0
        if self.cam_two: return 0
        return 1 if plus else -1

    def _bits_camera(self):
        """Game camera buttons to send at this engine step (ENGINE.CAM_G / ENGINE.CAM_D; both together: return to north), based on the arrows, the wheel click and the wheel click drag (rotation still to be done: `cam_request`).
        Outside the free field nothing is sent and pending requests are forgotten. The direction sent is kept for Game._camera_after_step."""
        self.cam_direction = (0, False)
        e = self.bar_end if self.bar_gesture.in_progress else 0                    # an end of the rotation bar held down counts as the matching arrow
        g, d = self._held('cam_left') or e < 0, self._held('cam_right') or e > 0
        if not self._drivable_camera(): self._camera_forget(); return 0
        direction = self._axis_state(g, d); dragging = False
        if self.cam_recenter: self.cam_recenter = False; self.cam_request = 0.0; return ENGINE.CAM_G | ENGINE.CAM_D
        if not direction and not (g or d) and abs(self.cam_request) >= ENGINE.CAM_STEP / 2: direction = 1 if self.cam_request > 0 else -1; dragging = True
        if not direction: return 0
        self.cam_direction = (direction, dragging)
        return ENGINE.CAM_D if direction > 0 else ENGINE.CAM_G

    def _camera_forget(self):
        """Nothing is sent to the game's camera at this step (menu, message, title, world map...): pending requests (return to north, mouse rotation) are forgotten, and two arrows already held do not count as a double press
        on return to the field."""
        self.cam_recenter = False; self.cam_request = 0.0; self.cam_direction = (0, False); self.cam_two = bool(self._held('cam_left') and self._held('cam_right'))

    def _camera_after_step(self, eng):
        """After an engine step sent with a camera button: did the game take it (Engine.camera_wait)? It then rotates by one notch (ENGINE.CAM_STEP degrees) at the next step; for the wheel-click drag, it is made to do the extra notches right away
        (Engine.camera_turn), up to CAM_DRAG_STEP notches, so that it follows the pointer; for the arrows: one notch per step, like the game.
        If it refuses (player lock, scene, place that forbids rotating it, limit reached), the rotation requested with the mouse is abandoned: the camera stays where the game puts it."""
        (direction, dragging), self.cam_direction = self.cam_direction, (0, False)
        if not direction: return
        kind = ENGINE.ROT_CAM_RIGHT if direction > 0 else ENGINE.ROT_CAM_LEFT
        if eng.camera_wait() != kind:
            if dragging: self.cam_request = 0.0
            return
        if dragging:
            n = max(1, min(CAM_DRAG_STEP, int(round(abs(self.cam_request) / ENGINE.CAM_STEP))))
            self.cam_request -= direction * n * ENGINE.CAM_STEP
        else:
            n = 1                                                                                          # (one notch per step, like the game: no more camera speed setting)
        if n > 1: eng.camera_turn(kind, n)

    def _engine_yaw(self):
        """Heading of the game's camera in degrees (0 = the original heading of towns, toward +Z; positive toward +X, like the right arrow), for the F1 display."""
        e = self.engine.state
        if e.cam_pos is None or e.cam_target is None: return 0.0
        return math.degrees(math.atan2(e.cam_target[0] - e.cam_pos[0], e.cam_target[2] - e.cam_pos[2]))

    def on_mouse_scroll(self, x, y, sx, sy):
        """Wheel: scrolls the Esc menu or the license; on the field it no longer does anything (no more zoom)."""
        if not self._ready: return
        if self.pause.opened: self.pause.wheel(sy); return
        if getattr(self, 'port_screen', None) is not None and hasattr(self.port_screen, 'wheel'): self.port_screen.wheel(sy); return          # (the language screen's cards and flags)
        if self.license_panel is not None: self.license_panel.scroll_by(-sy * 60.0)

    def _wheel_press(self, x, y):
        """Middle button pressed: a click (the camera returns to north) or the start of a drag (camera rotation). Outside the field (menus, messages, battle, title...) it does nothing."""
        if self.hud.dpad_on or self.press_state or not self._drivable_camera(): self.wheel.cancel(); return
        self.wheel.press_state(x, y)

    def _wheel_drag(self, x, y):
        """Middle button held and pointer moved: the camera rotates (left / right only; vertical movement does nothing), same default directions as the arrows (inputs.rotate);
        the "Invert left / right" setting reverses the direction of the drag (never that of the keyboard arrows). The game rotates in notches (see _camera_after_step)."""
        self._rotate_by(self.wheel.move_to(x, y))

    def _rotate_by(self, dx):
        """Horizontal movement of dx pixels of a drag (middle button or rotation bar) -> rotation requested from the game."""
        if not dx or not self._drivable_camera(): return
        self.cam_recenter = False                                             # a drag cancels the requested return to north that has not been sent yet
        self.cam_request = EN.rotate(self.cam_request, dx, self.height, bool(self.reg['camera_invert_lr']), CAM_DRAG_MAX)

    def _wheel_release(self):
        """Middle button released without having dragged: click = the camera returns to north (on a town's free field only)."""
        if self.wheel.let_go() and self._drivable_camera(): self.recenter_camera()

    def _bar_press(self, x, y):
        """Left button pressed on the rotation bar (at the very bottom of the screen, see hud_mobile.BAR): on one of its two ends the camera turns that way as long as the button is held (like the arrow), a tap on the central
        pattern returns the camera to north, a horizontal drag elsewhere rotates it like the middle button (the threshold is c_RotThreshold). Outside the free field (message, event...) the bar does nothing."""
        if not self._drivable_camera(): self.bar_gesture.cancel(); return
        self.bar_gesture.threshold = HM.BAR_THRESHOLD * self.height / 800.0; self.bar_gesture.press_state(x, y)
        self.bar_end = self.hud.bar_end_at(x); self.bar_center = self.hud.bar_reset_zone(x)

    def _bar_drag(self, x, y):
        if not self.bar_end: self._rotate_by(self.bar_gesture.move_to(x, y))

    def _bar_release(self):
        """Left button released: a tap on the central zone, without dragging, returns the camera to north."""
        if self.bar_gesture.let_go() and self.bar_center and self._drivable_camera(): self.recenter_camera()

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        """Esc menu: a held cursor follows the mouse; on the field, the on-screen cross held with the mouse; middle button: camera rotation."""
        if not self._ready: return
        self.mouse = (x, y)                                          # (during a press, pyglet no longer sends on_mouse_motion: the position is used to know whether the finger is still on the button)
        M = pyglet.window.mouse
        if buttons & M.MIDDLE and not buttons & M.LEFT: self._wheel_drag(x, y); return
        if buttons & M.LEFT and self.bar_gesture.in_progress: self._bar_drag(x, y); return
        if self.pause.opened: self.pause.drag_to(x, y)
        elif self.license_panel is not None: self.license_panel.dragging and self.license_panel.drag_to(y)
        elif self.hud.dpad_on: self.hud.dpad_drag(x, y)


    # ---- battle: scenery + monsters (engine dynamic meshes)
    def _rot_camera(self, e):
        """Unity rotation of the battle camera (NativeManager.cmd_camera + SceneCamera.Update, targetless mode): euler (-x, -(y + 180), z); DQ VI: euler (-x, -y, z) (ENGINE.BATTLE_YAW)."""
        return rot_unity(*ENGINE.battle_camera_euler(e.cam_rot))

    def _ending_framing(self, e, aspect):
        """(extra pitch in degrees, nominal fov) of an ending scene whose subject lies above what a 16:9 window shows of the phone's tall screen (ENDING_FRAMING: the balloon of `mqout`), else (0, None).
        Only during the ending (chapter 9), with the game's own camera (no fov sent by the engine); the full correction at 16:9 and wider, none from the shape where the window already shows the whole phone height."""
        fix = ENDING_FRAMING.get(getattr(self, 'map_name', None))
        if fix is None or e.cam_fov is not None or self.engine.story_chapter() != ENDING_CHAPTER: return 0.0, None
        k = min(1.0, max(0.0, (16.0 / 9.0 - portrait_base_height(aspect)) / (16.0 / 9.0 - 1.0)))
        return fix[0] * k, TOWN_FOV_DEFAULT + (fix[1] - TOWN_FOV_DEFAULT) * k

    def _town_camera(self, e, aspect):
        """Town camera as in Unity (SceneCamera.Update, mode with target): LookAt(position, target), pitch - lookDownAdjust, then advance by distanceAdjust along the axis. The game's: position, target,
        field, vertical offset and rotation sent by the engine, without the viewer's zoom, tilt, centering on the hero or height smoothing (TownCamera::execute follows the player and clamps its target to the map edges).
        The rotation the player gives it is also the game's (camera buttons sent to the engine): position and target already have it, nothing is added here, and the game picks it up in its scenes as on mobile.
        Projection: the original fixes the HORIZONTAL field (fov x 4/3) of its portrait screen (0.5625), i.e. the short side of what the phone shows. in landscape 16:9 the window shows the phone's
        two sides swapped (its short side up, its long side across); other shapes keep that base whole (helpers.portrait_base_height); a portrait window shows exactly the phone's image. The title screen keeps
        the phone's whole height in every shape (its picture is composed for the portrait screen: logo above, island below; the sky is widened by title_sky), and so do DQ V's slime race and
        DQ VI's slime curling (their scenes are composed for the portrait screen: the whole track; the slime waits at the bottom of the screen, the target is at its top)."""
        event = CameraMixin._event_view(self, e)
        pos = np.array(e.cam_pos if e.cam_pos is not None else (0.0, 0.0, 0.0), float); target_pos = np.array(e.cam_target if e.cam_target is not None else pos + (0.0, 0.0, 1.0), float)      # (no camera yet: only a camera movement draws)
        free = camera_free(e)
        raise_deg, fov_end = CameraMixin._ending_framing(self, e, aspect)          # (called on the class: the tests' stand-ins of the window only have _town_camera)
        fov_h = math.radians((e.cam_fov if e.cam_fov is not None else (fov_end or TOWN_FOV_DEFAULT)) * 4.0 / 3.0)
        whole = CameraMixin._whole_phone_height(self, e)
        th_v = math.tan(fov_h / 2) * (1.0 / ASPECT_PORTRAIT if whole else portrait_base_height(aspect))          # tangent of the vertical half field of view of the window
        if event is not None: pos, fw, topmost = event                  # camera movement of the map (FldObject.update_emv): it replaces the engine's camera
        elif free and e.cam_rot is not None:                         # free camera (packet 11, bit 7): position + angles, as in battle
            R = self._rot_camera(e); fw = R @ np.array([0.0, 0.0, 1.0]); topmost = R @ np.array([0.0, 1.0, 0.0])
        else:
            fw = target_pos - pos; fw /= np.linalg.norm(fw)
            yaw = math.atan2(fw[0], fw[2]); tang = math.asin(-fw[1]) - math.radians((e.cam_down if e.cam_down is not None else LOOK_DOWN_DEFAULT) + raise_deg)
            fw = np.array([math.sin(yaw) * math.cos(tang), -math.sin(tang), math.cos(yaw) * math.cos(tang)]); topmost = np.array([0.0, 1.0, 0.0])
            e.tall_screen = aspect < ASPECT_PORTRAIT - 1e-3
            pos = pos + fw * ENGINE.scene_distance_adjust(getattr(e, 'game_map', None), e.tall_screen)          # (DIST_ADJ_TOWN; DQ VI's e13din* in a tall portrait window: 7)
        M, right = matrix(pos, pos + fw * 10, math.degrees(2 * math.atan(th_v)), aspect, n=e.cam_near or TOWN_NEAR, f=(e.cam_far or TOWN_FAR) + (0.05 * self.n_loop if os.environ.get('DQ_ZDEBUG') else 0.0), topmost=topmost)      # (test) DQ_ZDEBUG: the far plane changes every frame: only the surfaces in z-fighting change from one frame to the next
        yoff = e.cam_yoff or 0.0
        if yoff:
            Ty = np.eye(4); Ty[1, 3] = yoff; M = Ty @ M              # SceneCamera.s_yOffset
        ref = getattr(self, 'book_frame', None) if e.part == ENGINE.PART_BESTIARY else None
        if ref is not None:                                          # the bestiary: its picture follows the book's window (its size and its place: menu_display.book_frame)
            cx, cy, s = ref(); H = max(1.0, float(self.height)); k = s / (H / 800.0); dx = cx / max(1.0, float(self.width)) * 2.0 - 1.0; dy = cy / H * 2.0 - 1.0
            if k != 1.0 or dx or dy:
                T = np.eye(4); T[0, 0] = T[1, 1] = k; T[0, 3] = dx; T[1, 3] = dy; M = T @ M
        self._view_border = (np.array(pos, float), right, np.cross(fw, right), fw)
        # Sprite reference frame: a character's panel takes the rotation of the GAME's camera (Chara.normalBillboard): that of the view
        return M, (right, np.cross(fw, right), fw)


    def _event_view(self, e):
        """(position, forward, up) of the loaded map's camera movement in progress (DQ V / VI: packet 6 >= 0x1000, FldObject.SetCameraNo; see map_objects.event_camera), None without one.
        Like Transform.LookAt(target, up): forward toward the target, up = the movement's up vector made perpendicular to it."""
        objects = getattr(self, 'objects', None)
        if objects is None or getattr(self, 'map_name', None) != e.game_map or e.part not in (ENGINE.PART_TOWN, ENGINE.PART_INITIAL) + ENGINE.MINIGAME_PARTS: return None          # (the start menu keeps the title's map and its camera movement behind its windows: DQ V's castle above the clouds; without it the engine's own camera showed an empty sky, or the last town's angle after a return to the title)
        view = objects.camera_view(getattr(self, 'alpha', 1.0) if not getattr(self, 'fixed_test', False) else 1.0)
        if view is None: return None
        pos, target, up = (np.array(v, float) for v in view)
        fw = target - pos; n = np.linalg.norm(fw)
        if n < 1e-9: return None
        fw /= n; right = np.cross(up, fw)
        if np.linalg.norm(right) < 1e-9: right = np.cross(np.array([0.0, 1.0, 0.0]) if abs(fw[1]) < 0.99 else np.array([0.0, 0.0, 1.0]), fw)
        right /= np.linalg.norm(right)
        return pos, fw, np.cross(fw, right)

    def _whole_phone_height(self, e):
        """Does the window show the phone's whole height (its long side up), whatever its shape? The title screen (the map loaded by the window), DQ V's slime race and DQ VI's slime curling,
        and the bestiary: its monster is placed by the engine's camera for the phone's portrait screen, beside the sheet of the interface column (which shows the phone's whole height too)
        (`e`: the engine's state, None: unknown)."""
        return getattr(self, 'map_name', None) == TITLE_MAP or (e is not None and (e.part in ENGINE.PHONE_SCENE_PARTS or e.part == ENGINE.PART_BESTIARY))

    def _ortho_matrix(self, aspect, e=None):
        """Projection of the orthographic layer (DQ V / VI: SceneCamera.s_DSSAOrthoCamera, meshes of packet MESH_ORTHO): no rotation, x and y as the engine gives them. The phone shows x from
        -ORTHO_HALF_WIDTH to +ORTHO_HALF_WIDTH across its short side; the window keeps the same base as its 3D scene: the phone's whole height on the title screen, otherwise
        `portrait_base_height` (16:9: the phone's short side up)."""
        whole = CameraMixin._whole_phone_height(self, e)
        hh = ORTHO_HALF_WIDTH * (1.0 / ASPECT_PORTRAIT if whole else portrait_base_height(aspect)); hw = hh * aspect
        M = np.zeros((4, 4)); M[0, 0] = 1.0 / hw; M[1, 1] = 1.0 / hh; M[3, 3] = 1.0
        return M

    def _game_border(self, e):
        """(position, right, up, forward) of the town camera as the original game places it (same computation as _town_camera, without the player's rotation)."""
        event = CameraMixin._event_view(self, e)
        if event is not None:
            pos, fw, topmost = event; right = np.cross(topmost, fw); right /= np.linalg.norm(right)
            return pos, right, np.cross(fw, right), fw
        pos, target_pos = np.array(e.cam_pos, float), np.array(e.cam_target, float)
        if camera_free(e) and e.cam_rot is not None:
            R = self._rot_camera(e); fw = R @ np.array([0.0, 0.0, 1.0]); topmost = R @ np.array([0.0, 1.0, 0.0])
        else:
            fw = target_pos - pos; fw /= np.linalg.norm(fw)
            yaw = math.atan2(fw[0], fw[2]); tang = math.asin(-fw[1]) - math.radians(e.cam_down if e.cam_down is not None else LOOK_DOWN_DEFAULT)
            fw = np.array([math.sin(yaw) * math.cos(tang), -math.sin(tang), math.cos(yaw) * math.cos(tang)]); topmost = np.array([0.0, 1.0, 0.0])
            pos = pos + fw * ENGINE.scene_distance_adjust(getattr(e, 'game_map', None), getattr(e, 'tall_screen', False))
        right = np.cross(topmost, fw); right /= np.linalg.norm(right)
        return pos, right, np.cross(fw, right), fw

    def _battle_camera(self, e, aspect):
        R = self._rot_camera(e)
        cam = np.array(e.cam_pos, float); fw = R @ np.array([0.0, 0.0, 1.0]); topmost = R @ np.array([0.0, 1.0, 0.0])
        fov_h = math.radians((e.cam_fov if e.cam_fov is not None else CB_FOV_NOMINAL) * 4.0 / 3.0)         # SceneCamera.Update: fov x 4/3, applied to the horizontal (transposed projection matrix)
        fov_v = math.degrees(2 * math.atan(math.tan(fov_h / 2) / (4.0 / 3.0)))                         # original 4/3 view; in 16/9 the field height stays the same and more scenery shows on the sides
        M, _ = matrix(cam, cam + fw * 10, fov_v, aspect, n=CB_NEAR, f=CB_FAR, topmost=topmost)
        Ty = np.eye(4); Ty[1, 3] = CB_YOFFSET; M = Ty @ M                                              # SceneCamera.s_yOffset: vertical offset of the image
        if self.cb_mx: M = M @ np.diag([-1.0, 1.0, 1.0, 1.0])
        T = np.diag([CB_SCENERY_SCALE] * 3 + [1.0]); T[2, 3] = CB_DEC_Z                              # NativeManager.cmd_fld_load in battle mode: FldObject.SetDispScale(6, 6, 6), applied to the root of the scenery (FldObject.Update)
        return M, M @ T

    def _dbg_proj(self, M, e, cam3d, to_draw, icons):
        """(debugging, DQ_PROJ=n) pixels (x, y from the top) of the feet / top of the sprite and bubble panels, every n frames."""
        def px(P):
            v = M @ np.array([P[0], P[1], P[2], 1.0]); return None if v[3] <= 1e-6 else (round(float((v[0] / v[3] * 0.5 + 0.5) * self.width), 1), round(float((1 - (v[1] / v[3] * 0.5 + 0.5)) * self.height), 1))
        out = {}
        for slot, f, vl in to_draw:
            q = np.array(vl.position[:], float).reshape(-1, 3); out['s%d' % slot] = (px(q[0]), px(q[1]), px(q[2]), px(q[5]))       # corners: bottom-left, bottom-right, top-right, top-left
        for slot, ic in sorted(e.icons.items()):
            if ic['pos'] is not None: out['i%d' % slot] = (px(ic['pos']),)
        for tex, vl in icons:
            q = np.array(vl.position[:], float).reshape(-1, 3); out['bubble'] = (px(q[0]), px(q[1]), px(q[2]), px(q[5]))
        print('PROJ n=%d' % self.n_loop, out, flush=True)

    def _edge_correction(self):
        """Strength (0 to 1) of the sprite edge correction (towns, dungeons, towers; see _town_panel): 0 by default (the raw position of the NPCs, exactly as in the game: original panel,
        without on-screen reconstruction); the former full correction (vertical edges, constant proportion) is obtained with the --edge-correction option or the DQ_EDGE_CORRECTION environment variable,
        from 0 (none, default) to 100 (full). It is not a player setting (there is no "sprite edge correction" option any more)."""
        v = getattr(self.args, 'edge_correction', None)
        if v is None and os.environ.get('DQ_EDGE_CORRECTION'):
            try: v = float(os.environ['DQ_EDGE_CORRECTION'])
            except ValueError: v = None
        return EDGE_CORRECTION_DEFAULT if v is None else min(1.0, max(0.0, float(v) / 100.0))

    def _sprite_reference(self, cam3d, corr):
        """(right, up, forward) that orients the panel of a town character: the frame of reference of the GAME's camera (Chara.normalBillboard takes the rotation of the game's camera, including the one the player gives it: same tilt as on mobile,
        and a character does not "align" with the view when the camera goes up or down), which is that of the view since the viewer no longer rotates the camera alongside the game. `corr`: strength of the edge correction (see _edge_correction)."""
        return cam3d

    SPRITE_TILT_DEFAULT = 0.0                                       # share of the tilt of the world's verticals that the sprite takes: 0 = exactly vertical edges (default: the trial at 0.5 distorted sprites near the edges), 1 = parallel to the walls

    def _sprite_tilt(self):
        """(Trial, REMOVED by default: "put the NPC display back as on the mobile version, ... they stretch when they get close to the edge") Share (0 to 1) of the tilt of the WORLD'S VERTICALS (wall edges, doors...) that a character's panel takes on screen, 0 by default (trial: 0.5). Since the edge correction (_town_panel) the sprite is
        exactly vertical on screen everywhere, but the neighboring walls lean (11 to 19 degrees at 9 units from the center in 16/9, camera at 47 degrees, versus 4 to 6.5 on the mobile portrait) and this angle changes when the hero goes up or
        down: the sprite then seems to lean relative to the scenery (optical illusion reported). Leaning it halfway toward the world vertical reduces this gap by half without going back to the original panel
        (which moved away from the center of the image by its top alone and got distorted). Comparison with the trial: environment variable DQ_SPRITE_TILT, from 0 (vertical edges, default) to 100 (parallel to the scenery)."""
        v = os.environ.get('DQ_SPRITE_TILT')
        if v is None or not v.strip(): return self.SPRITE_TILT_DEFAULT
        try: return min(1.0, max(0.0, float(v) / 100.0))
        except ValueError: return self.SPRITE_TILT_DEFAULT

    @staticmethod
    def _shift_depth(X, F, delta, t, C):
        """Point X (of a panel parallel to the screen) brought closer to the camera by `delta` depth units. t = 0: along the camera axis (F), like the vertices of the Riseup bubble panel in Unity (Riseup.s_vtxDepth);
        t = 1: along the view ray (camera -> X): same point on screen, same depth (otherwise the shifted edge moves away from the center of the image by delta x offset / depth: the bubble slides sideways when the camera is rotated). In between: linear blend."""
        if t <= 0.0 or C is None: return X - F * delta
        d = X - C; z = float(d @ F)
        if z <= delta * 1.5: return X - F * delta                          # (right up against the camera: no division by a zero depth)
        return X - ((1.0 - t) * delta) * F - (t * delta / z) * d

    def _town_panel(self, p, r_, u_, f_, w, h, zb, zt, t, C, inc=0.0, lr=False):
        """The 4 vertices (bottom left, bottom right, top right, top left) of the panel of a town character whose feet are at p: a w x h rectangle parallel to the screen, whose bottom is set back by `zb` and whose top by `zt`
        toward the camera (depth of the original panel, which leans by 36 degrees). t = 0: the original panel (offsets along the camera axis). A flaw of the original, made visible by the horizontal field of 16/9
        (the mobile portrait saw much less of it): the top, being closer, moves away from the center of the image (by zt x offset / depth) and the sprite "leans" outward near the left / right edges (up to 15 to
        20 degrees); moreover the height on screen is compressed toward the bottom of the image, hence a sprite that is too wide (up to 0.9 instead of 0.75). t = 1: the panel is rebuilt ON SCREEN: same position, same height
        and same depths as before (size, overlaps and z-buffer unchanged), but vertical edges and width = 0.72 x the height on screen (the proportion of the panel at the center of the image: the width there is the
        average of those of the bottom and the top), so the sprite keeps its proportions everywhere. At the center of the image, nothing changes. In between: linear blend of the vertices. Unknown camera or panel against the camera: the original.
        `inc` (0 to 1, trial, see _sprite_tilt): the rebuilt panel is then SHEARED toward the world vertical, feet fixed: the top slides by inc x (on-screen tilt of a world vertical at the character's foot) x height,
        at the same depth. Size, depth and bottom unchanged; 0: vertical edges; 1: edges parallel to the walls. At the center of the image the world vertical is straight: nothing changes.
        `lr` (always; left and right only): the original panel, slid back sideways by what its depths make it slide away from the center of the image: on screen (focal length 1)
        the foot is at d.r / z, the panel's bottom center at d.r / (z - zb) and its top center at d.r / (z - zt); the bottom's two vertices move along r by -d.r x zb / z and the top's by
        -d.r x zt / z (world units at their depths), so the panel stands straight above its foot, on its shadow, wherever it is on the screen: the character no longer slides over the ground
        when the camera turns (the wide field of 16/9 made the original's drift reach a third of a tile at the left and right edges). Heights, depths, the top and bottom widths and
        everything up and down are those of the game."""
        a, b = p - r_ * (w / 2), p + r_ * (w / 2)
        orig = (a - f_ * zb, b - f_ * zb, b + u_ * h - f_ * zt, a + u_ * h - f_ * zt)
        if lr and t <= 0.0 and C is not None:
            d = p - C; z = float(d @ f_)
            if z > zt * 1.5:
                x = float(d @ r_); kb, kt = r_ * (-x * zb / z), r_ * (-x * zt / z)
                orig = (orig[0] + kb, orig[1] + kb, orig[2] + kt, orig[3] + kt)
        if t <= 0.0 or C is None: return orig
        d = p - C; z = float(d @ f_)
        if z <= zt * 1.5: return orig
        xs, yb, yt = float(d @ r_) / (z - zb), float(d @ u_) / (z - zb), (float(d @ u_) + h) / (z - zt)      # on screen (focal length 1): x of the bottom center, y of the bottom and of the top of the original panel
        hw = 0.5 * (w / h) * (z - zt) / (z - 0.5 * (zb + zt)) * (yt - yb)                                    # half-width on screen: proportion (average width / height) of the original panel at the center of the image
        gl_ = 0.0                                                                                                   # top slide (screen units, focal length 1): inc x tan(tilt of a world vertical at the foot) x height on screen
        if inc > 0.0:
            Y = np.array([0.0, 1.0, 0.0]); db = d - f_ * zb; zz = z - zb                                             # foot of the panel: its direction on screen (a world line stays a line) = derivative of the projection when moving up by dy
            vx = float(Y @ r_) / zz - float(db @ r_) * float(Y @ f_) / zz ** 2; vy = float(Y @ u_) / zz - float(db @ u_) * float(Y @ f_) / zz ** 2
            if abs(vy) > 1e-3: gl_ = min(1.0, max(-1.0, vx / vy)) * min(1.0, max(0.0, inc)) * (yt - yb)
        new = [C + r_ * ((xs + sx * hw + gh) * (z - dz)) + u_ * (ys * (z - dz)) + f_ * (z - dz) for sx, ys, dz, gh in ((-1, yb, zb, 0.0), (1, yb, zb, 0.0), (1, yt, zt, gl_), (-1, yt, zt, gl_))]
        return tuple(o + t * (n - o) for o, n in zip(orig, new))

    def _camv(self, eng):
        """(debugging, DQ_CAMV=n) state of the game's camera every n frames."""
        e = eng.state; sp = e.sprites.get(0); hp = sp.get('pos') if sp else None
        r = lambda v: None if v is None else tuple(round(float(x), 1) for x in v)
        print('CAMV n=%d map=%s part=%#x cam=%s game=%s flag=%#x msg=%d hero=%s target=%s pos=%s free=%s' % (self.n, e.game_map, e.part, eng.camera_town(), eng.camera_game(), eng.flags(), eng.msg_visible, r(hp), r(e.cam_target), r(e.cam_pos), e.cam_free), flush=True)
        if os.environ.get('DQ_CAMV_SPR'):                                  # (debugging) + all the sprites and the icon bubbles

            print('   sprites', {k: (r(s_.get('pos')), s_.get('id'), s_.get('visible', True)) for k, s_ in e.sprites.items()}, 'icons', {k: (i['id'], r(i['pos']), round(i['alpha'], 2)) for k, i in e.icons.items()}, flush=True)
