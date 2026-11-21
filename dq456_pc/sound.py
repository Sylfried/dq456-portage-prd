"""Game sound: original music and sound effects (assets/sound/*.akb of the data package), played by pyglet.

AKB format (Square Enix container, read in libsdlib_android.so: akbMaterial*):
  0x00 'AKB '; 0x04 u16 version; 0x06 u16 header size; 0x08 u32 file size; 0x0c u8 codec (2 = MS-ADPCM, 5 = Ogg Vorbis);
  0x0d u8 channels; 0x0e u16 frequency; 0x10 u32 number of samples; 0x14 u32 loop start; 0x18 u32 loop end (0 = no loop);
  0x20 float volume; data at 0xcc: complete Ogg (codec 5), or 4 bytes then MS-ADPCM blocks (codec 2, block size = u16 at 0xc2).
The engine packets: 35 = sound effect (SE%04d), 36 = sound effect stop, 37 = music (BGM%02d; value >= 0x4000 = stop; DQ V / VI: see MUSIC_V2).
Nothing is written outside the cache folder; no game file is modified.
"""
import os, io, time, struct, zipfile, threading, collections
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from dq456_pc.language import tr
from dq456_pc.core.legacy_state import carry_over_file
from dq456_pc import games as GM

DATA_START = 0xcc
ADAPT = (230, 230, 230, 230, 307, 409, 512, 614, 768, 614, 512, 409, 307, 230, 230, 230)
COEF = ((256, 0), (512, -256), (0, 0), (192, 64), (240, 0), (460, -208), (392, -232))


def read_header(d):
    """-> dict (codec, canaux, frequence, echantillons, debut, fin, volume, bloc) or None if it is not an AKB."""
    if len(d) < DATA_START or d[:4] != b'AKB ': return None
    codec, channels, freq, n, start_idx, end = struct.unpack_from('<BBHIII', d, 0x0c)
    volume = struct.unpack_from('<f', d, 0x20)[0]
    block = struct.unpack_from('<H', d, 0xc2)[0]              # extra block at 0xc0 (u16 at 0xc0: 0, block size at 0xc2, samples u32 at 0xc4, loop start u32 at 0xc8)
    return dict(codec=codec, channels=channels, frequency=freq, samples=n, start=start_idx, end=end, volume=volume if 0.0 < volume <= 2.0 else 1.0, block=block)


def decode_msadpcm(d, channels, block):
    """Block-based MS-ADPCM (four bits per sample) -> int16 (n, channels). The blocks are independent (their header gives the state): all the blocks are decoded at once."""
    num = len(d) // block
    if num == 0: return np.zeros((0, channels), np.int16)
    b = np.frombuffer(d[:num * block], np.uint8).reshape(num, block)
    ent = 7 * channels
    pred = [b[:, c].astype(np.int64) for c in range(channels)]
    # header: prediction indices (1 byte per channel), deltas (s16), sample 1 (s16), sample 2 (s16), each grouped by channel
    def s16(o): return (b[:, o].astype(np.int64) | (b[:, o + 1].astype(np.int64) << 8)).astype(np.int16).astype(np.int64)
    delta = [s16(channels + 2 * c) for c in range(channels)]
    s1 = [s16(channels + 2 * channels + 2 * c) for c in range(channels)]
    s2 = [s16(channels + 4 * channels + 2 * c) for c in range(channels)]
    k1 = np.array([k[0] for k in COEF], np.int64); k2 = np.array([k[1] for k in COEF], np.int64)
    c1 = [k1[np.minimum(pc, 6)] for pc in pred]; c2 = [k2[np.minimum(pc, 6)] for pc in pred]
    raw = b[:, ent:]
    nibs = np.empty((num, raw.shape[1] * 2), np.int64)
    nibs[:, 0::2] = raw >> 4; nibs[:, 1::2] = raw & 15
    per_channel = nibs.shape[1] // channels
    output = np.zeros((num, 2 + per_channel, channels), np.int64)
    adapt = np.array(ADAPT, np.int64)
    for c in range(channels):
        output[:, 0, c] = s2[c]; output[:, 1, c] = s1[c]
        a, z = s1[c].copy(), s2[c].copy(); dl = delta[c].copy()
        nc = nibs[:, c::channels]                       # stereo: the nibbles alternate left (high-order) / right
        for i in range(per_channel):
            n = nc[:, i]; sn = n - ((n & 8) << 1)
            t = a * c1[c] + z * c2[c]; p = np.where(t < 0, -((-t) >> 8), t >> 8)         # integer division toward zero (Microsoft reference code)
            p = np.clip(p + sn * dl, -32768, 32767)
            z = a; a = p
            dl = np.maximum(16, (adapt[n] * dl) >> 8)
            output[:, 2 + i, c] = p
    return output.reshape(-1, channels).astype(np.int16)


SOUND_CACHE_VERSION = 2             # version of the sound effects kept in the disk cache (sound_SE*.npz): 2 (loops of SE 329, 331, 1118); a cache of another version is rebuilt
LOOP_END_TOLERANCE = 2048     # samples: beyond that, the header is judged inconsistent and the sound is not looped (original safeguard of the port)


def decode_ogg(raw):
    """Ogg Vorbis -> int16 (n, channels). Read as floats then CLIPPED to full scale: Vorbis sometimes exceeds +-1.0 after decoding, and reading directly as int16 made these samples overflow
    (a peak became a peak of opposite sign: BGM 14, 3 samples around 91 s; BGM 34, 7 samples)."""
    import soundfile as sf
    x, _ = sf.read(io.BytesIO(raw), dtype='float32', always_2d=True)
    return np.clip(np.rint(x * 32767.0), -32768, 32767).astype(np.int16)


def decode_akb(d):
    """AKB file -> dict (pcm int16 (n, channels), frequence, debut, fin, volume) or None."""
    h = read_header(d)
    if h is None: return None
    if h['codec'] == 2:
        pcm = decode_msadpcm(d[DATA_START + 4:], h['channels'], h['block'] or 512)
    elif h['codec'] == 5:
        pcm = decode_ogg(d[DATA_START:])
    else:
        return None
    n = len(pcm)
    start_idx, end = h['start'], h['end']
    if n < end <= n + LOOP_END_TOLERANCE: end = n          # loop end that exceeds the end of the data by a few samples (remainder of an ADPCM block: SE 329, 331, 1118): the loop goes up to the end of the data
    looping = 0 < end <= n and start_idx < end
    return dict(pcm=np.ascontiguousarray(pcm), frequency=h['frequency'], start=start_idx if looping else 0, end=end if looping else 0, volume=h['volume'], loop=looping)


BGM_RESTART = 0x1000        # cmd_bgm: value >= 0x1000 = Sound.PlayBGM(value - 0x1000) with resume: the current music is remembered and resumed (from the beginning) when this track is finished (jingle)
BGM_STOP = 0x4000           # value >= 0x4000 = Sound.StopBGM (SoundManager::stopBgm sends 0x4000 | duration, battleStop 0x4008); the duration is ignored by Unity (immediate stop)
# DQ V / VI (Sound.PlayBGM of their Unity layer): the track is value & 0xff; bits 8-11 = fade-out time of the current track in tenths of a second (played here as an immediate change);
# bit 13 = layer: the current track is paused (not stopped), the new one plays over it; the paused track resumes where it stopped when it is requested again without the layer bit,
# or by itself at the end of a layer track that also has bit 12 (BGM_STATE_RESTART); any other track drops it
MUSIC_V2 = GM.profile().key != 'dq4'
BGM_LAYER = 0x2000
BGM_LEVEL = 0x1000          # with BGM_STOP: Sound.StopBGM bit 12 = level of the current track, not a stop (DQ VI's Murdaw castle: the music fades out, the next one starts at level 0 and comes back by steps)
SFX_GRACE = 0.4               # seconds: a sound effect that has just been started is never released because its player does not yet report itself as "playing" (accelerated walking: the engine makes several steps, hence several tick() calls, in a row within the same displayed frame, just after the door or village exit sound effect order)
# Looping sound effects have no time limit, as in the original (SoundControl.Play / StopSE: a loop lasts until the engine's stop order, packet 36); the 120 s cap of the port
# was removed. Loading a savestate stops them (restore_bgm): the restored engine no longer knows them and would never send their stop.
FAILURES_MAX = 3               # consecutive playback failures (sound effect or music) before cutting the sound for a while (device removed or changed, driver)
RETRY_DELAYS = (2.0, 10.0, 30.0)   # seconds before the new attempt, depending on the number of cutoffs already suffered (the last delay repeats)
NOTES_MAX = 40               # log lines at most per session (a lasting failure does not fill the file)
ENGINE_FRAMES_PER_S = 30.0   # engine frames per second at normal speed (one engine step = 1/30 s)
# Music decoded in advance (background task, a separate thread, one at a time, when nothing else needs decoding) and kept in memory (never evicted by the cache;
# 59 MB in total): decoding a long music track takes time (BGM 23, 113 s: 0.6 to 1.2 s here, more on a slow PC; the jingles: 0.01 to 0.14 s), whereas the engine
# stops a music track requested during an accelerated battle 2.7 to 3.9 s after starting it (x3): without this, the defeat music (BGM 23, requested when the whole party
# is defeated) was only decoded at that moment (it started late, and an engine stop arriving before the end of the decoding canceled it: the party died in silence).
BGM_JINGLES = (6, 8, 10, 13) + tuple(range(35, 51))      # the tracks without loop of 2.9 to 23.2 s (see CATEGORIES): jingles, level up (47) included
BGM_PRELOADED = BGM_JINGLES + (30, 23)

# ---- sound categories (Esc menu > "Sound"). Unity only knows PlayBGM / PlaySE (class Sound): no voice, no separate "ME" channel. The categories are those that the ENGINE
# distinguishes by the way a sound is played: music (PlayBGM, looping); jingles and fanfares (PlayBGM without loop: BGM 6, 8, 10, 13, 35 to 50 of 2.9 to 23.2 s,
# or requested with resume of the previous music, 0x1000 | n = SoundManager::playRestart); sound effects (PlaySE); interface sounds (sound effects 300 to 304:
# 300 = menu button played by Unity and message page played by ui_MsgLoop, 301 = EXTRA_PRESENT_EXP menu, 304 = opening of a Yes / No box;
# 302 and 303: same block of the bank, never called directly by the engine, hence assumed to belong to the interface).
CATEGORIES = ('music', 'jingles', 'effects', 'interface')
SFX_INTERFACE = frozenset(range(300, 305))
SFX_JINGLES = frozenset({409})   # victory fanfare (BattleEndTask: SoundManager::play(409), 2.9 s): played like a sound effect but it is a fanfare; adjusted with "Jingles and fanfares" (volume of the game's "bruitages" option kept)
JINGLE_DURATION_MAX = 30.0       # seconds: a non-looping track shorter than this is a jingle; BGM 1 (111 s) and 34 (655 s), without loop, are real music (except when requested with resume)


def category_sfx(ident):
    return 'interface' if ident in SFX_INTERFACE else 'jingles' if ident in SFX_JINGLES else 'effects'


def category_bgm(restart, looping, duration):
    """'jingles' for a non-looping track requested with resume of the previous music, or short; 'musique' otherwise (a looping track never returns to the previous music: it is music)."""
    return 'jingles' if (not looping and (restart or duration <= JINGLE_DURATION_MAX)) else 'music'


def game_option_volume(idx):
    """The game's "volume" option of the "Parametres" window (OptionStatus, 0 to 4, stored in the save) -> factor: Sound.SetVolumeBGM / SetVolumeSE set s_bgmVolume = s_seVolume = (idx + 1) x 0.2."""
    return (min(4, max(0, int(idx))) + 1) * 0.2


class Mixer:
    """Volumes (0 to 1) and switches per category. gain(category, the file's own volume) = general volume x category volume x own volume x the game's "volume" option
    (music and jingles: that of the music; sound effects and interface: that of the sound effects), 0 if the category is turned off. Pure computation: no audio player."""
    def __init__(self, general=1.0, music=1.0, jingles=1.0, effects=1.0, interface=1.0):
        self.general = float(general)
        self.vol = dict(music=float(music), jingles=float(jingles), effects=float(effects), interface=float(interface))
        self.active = {c: True for c in CATEGORIES}
        self.game = dict(music=1.0, effects=1.0)              # the game's "volume" options ("Parametres" window): factors 0.2 to 1
        self.muted = False                                      # window without focus: everything is at zero, the audio players keep running

    def gain(self, category, own=1.0, option=None):
        """option: "music" or "effects" = which of the game's "volume" options applies; by default that of the music for music and jingles, that of the sound effects for the rest (a sound effect stored under "jingles" passes option='effets')."""
        if self.muted or not self.active.get(category, True): return 0.0
        g = self.general * self.vol.get(category, 1.0) * own * self.game[option or ('music' if category in ('music', 'jingles') else 'effects')]
        return max(0.0, min(1.0, g))


class _SFX:
    """A sound effect in progress: identifier, player, end time (infinite for a loop), category, file's own volume, looping or not."""
    __slots__ = ('ident', 'p', 'end', 'category', 'own', 'begin', 'looping')
    def __init__(self, ident, p, end, category, own, begin=None, looping=False): self.ident, self.p, self.end, self.category, self.own, self.looping = ident, p, end, category, own, looping; self.begin = time.time() if begin is None else begin


class Sound:
    """Playback: one music track at a time (looping), sound effects in parallel. Decoding is done in a background task."""
    def __init__(self, apk, cache, log=print, music_volume=1.0, sfx_volume=1.0, active=True, logfile=None, alert=None):
        self.apk, self.cache, self.log = apk, cache, log
        self.logfile, self.alert = logfile, alert                        # logfile(text): line added to the error log; alert(text): small on-screen message (sound cutoff and return)
        self.failures = 0; self.outages = 0; self.retry = None; self.bgm_lost = None      # consecutive playback failures; cutoffs suffered; time of the new attempt (None = no cutoff in progress); music to restart when the sound returns
        self._notes = 0; self._seen = set(); self._in_outage = False
        self.mix = Mixer(music=music_volume, effects=sfx_volume)
        self.active = active; self.ok = False
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.background_pool = ThreadPoolExecutor(max_workers=1)                 # music preloading: a separate thread, so that it never delays a sound requested by the engine
        self.dec = collections.OrderedDict(); self.jobs = {}
        self.background = []; self.pinned = set()                              # music to decode in advance (names), names kept in memory
        self.bgm_queue = collections.deque(); self.e_req = None          # music orders held back during a jingle (value, engine frame); engine frame of the order that started the current music
        self.fanfare = None                                                 # victory fanfare (SFX_JINGLES, a sound effect) in progress: [start time, end time, engine frame of its order]; it holds back the music orders like a jingle
        self.bgm = None; self.bgm_id = None; self.bgm_request = None
        self.resume = None; self.bgm_end = None                           # jingle: music to resume, end time of the jingle
        self.bgm_layer = None                                              # DQ V / VI: track paused by a layer track {'id', 'p' (its player), 'own', 'category', 'pos'} (see MUSIC_V2)
        self.bgm_t0 = None; self.bgm_pos0 = 0.0; self.bgm_offset = 0.0     # time and position (seconds) of the playback start; requested position for the next music (loading a savestate)
        self.sfx = []                                                       # [_SFX]
        self.bgm_own_volume = 1.0; self.bgm_category = 'music'; self.t_pause = None   # own volume of the current music and its category; time of the start of the pause (Esc menu)
        self.suspended = False; self.listening = False                         # Esc menu open; "Sound" page of the menu (sounds play while the engine is stopped)
        self.out_of_focus = False                                            # the window no longer has focus (Alt + Tab, minimized): the sound is cut (volume at zero, even while listening on the Son page) but the game keeps running
        self.lock = threading.Lock(); self.zip = None; self.ogg = False; self._table_vol = None           # (snd_vol.dat: read on first use, see vol_snd)
        try:
            import soundfile  # noqa: F401                                 # (availability probe) reading of the music tracks (Ogg Vorbis); the sound effects do not need it
            self.ogg = True
        except Exception:
            self.log('Music unavailable: "soundfile" module missing (pip install soundfile). Sound effects stay active.')
        try:
            import pyglet
            from pyglet.media.codecs.base import AudioFormat, AudioData
            self.pyglet = pyglet; self.media = pyglet.media; self.AudioFormat = AudioFormat; self.AudioData = AudioData
            self.ok = True
        except Exception as e:
            self.log('Sound unavailable (%r)' % (e,))

    # ---- playback failures
    def _note(self, text, alert=None):
        """A line in the console and in the error log (at most NOTES_MAX per session); `alert`: the same, briefly, on screen."""
        self.log('Sound: %s' % text)
        if self._notes < NOTES_MAX:
            self._notes += 1
            if self.logfile is not None:
                try: self.logfile(text)
                except Exception: pass
        if alert and self.alert is not None:
            try: self.alert(alert)
            except Exception: pass

    def _note_once(self, lookup_key, e):
        """Error without immediate consequence (volume, resuming after pause): noted only once per session."""
        if lookup_key not in self._seen: self._seen.add(lookup_key); self._note('%s failed (%r), logged only once' % (lookup_key, e))

    def _failure(self, what, e):
        """A sound effect or music track could not be started: FAILURES_MAX consecutive failures cut the sound for a while (new attempt on its own, see tick) instead of cutting it until the game is closed."""
        self.failures += 1; self._note('%s failed (%r), failure %d' % (what, e, self.failures))
        if self.failures >= FAILURES_MAX:
            d = RETRY_DELAYS[min(self.outages, len(RETRY_DELAYS) - 1)]; self.outages += 1
            self.ok = False; self.retry = time.time() + d; self._in_outage = True
            self._note('sound interrupted, retrying in %d s' % d, alert=tr('window.sound_interrupted_automatic_retry'))

    def _success(self):
        """A sound has just started: the series of failures is over (and, after a cutoff, the sound is restored)."""
        if self._in_outage: self._in_outage = False; self._note('sound restored', alert=tr('window.sound_restored'))
        self.failures = 0

    def _retry(self):
        """End of the cutoff: the sound is reopened; a single further failure cuts it again immediately (longer delay). The last music order of the cutoff is applied, a stop included, even if a music track
        was already playing (play_bgm keeps a track that is requested again)."""
        self.ok = True; self.retry = None; self.failures = FAILURES_MAX - 1
        v, self.bgm_lost = self.bgm_lost, None
        if v is not None: self.play_bgm(v)

    def _track_during_outage(self, evts):
        """During the cutoff the engine's orders are lost, except the background music: the last order (a track, or a stop: BGM_STOP and above) is applied when the sound returns; a jingle with resume is ignored."""
        for t, v in evts:
            if t == 37 and not BGM_RESTART <= v < BGM_STOP: self.bgm_lost = v

    # ---- own volume of each sound (snd_vol.dat)
    def vol_snd(self, ident):
        """Factor from `assets/sound/snd_vol.dat` for a sound: music `BGM%02d` = index of the file number, sound effect `SE%04d` = index of the sound number (1171 floats; 1.0 for almost all). The original plays header x snd_vol x option (`SoundControl.Play`; the header once, in libsdlib_android.so `SQEX::SoundImpl::UpdateVolume` x `akbSoundGetVolume`): 49 music tracks from 0.7 to 0.95 and sound effect 409 (fanfare) at 0.6. 1.0 if the file is missing or if the index is outside the table."""
        t = self._table_vol
        if t is None:
            with self.lock:
                t = self._table_vol
                if t is None:
                    try:
                        if self.zip is None: self.zip = zipfile.ZipFile(self.apk)
                        t = np.frombuffer(self.zip.read('assets/sound/snd_vol.dat'), '<f4').copy()
                    except Exception: t = np.ones(0, np.float32)
                    self._table_vol = t
        return float(t[ident]) if 0 <= ident < len(t) and 0.0 < float(t[ident]) <= 1.0 else 1.0

    # ---- reading the files
    def _bytes(self, name_str):
        with self.lock:
            if self.zip is None: self.zip = zipfile.ZipFile(self.apk)
            try: return self.zip.read('assets/sound/%s.akb' % name_str)
            except KeyError: return None

    def _decode(self, name_str):
        f = carry_over_file(self.cache, 'son_%s.npz' % name_str, 'sound_%s.npz' % name_str)
        try:
            if os.path.exists(f):
                z = np.load(f)
                if len(z['p']) > 4 and int(z['p'][4]) == SOUND_CACHE_VERSION: return dict(pcm=z['pcm'], frequency=int(z['p'][0]), start=int(z['p'][1]), end=int(z['p'][2]), volume=float(z['v'][0]), loop=bool(z['p'][3]))        # (cache of an old version: rebuilt)
        except Exception: pass
        d = self._bytes(name_str)
        if d is None: return None
        r = decode_akb(d)
        if r is not None and name_str.startswith('SE'):                        # sound effects are small: disk cache (a long music track, on the other hand, takes more than a second: see BGM_PRELOADED)
            try: np.savez(f, pcm=r['pcm'], p=np.array([r['frequency'], r['start'], r['end'], int(r['loop']), SOUND_CACHE_VERSION], np.int64), v=np.array([r['volume']], np.float32))
            except Exception: pass
        return r

    def _get(self, name_str, callback, background=False):
        """Calls callback(data): immediately if `name_str` is already decoded, otherwise from tick() when the background task has finished."""
        if name_str in self.dec: self.dec.move_to_end(name_str); callback(self.dec[name_str]); return
        if name_str in self.jobs: self.jobs[name_str][1].append(callback); return
        self.jobs[name_str] = ((self.background_pool if background else self.pool).submit(self._decode_safe, name_str), [callback])

    def _decode_safe(self, name_str):
        try: return self._decode(name_str)
        except Exception as e:
            self.log('Sound: %s unreadable (%r)' % (name_str, e)); return None

    def preload(self, ids):
        """Decodes the commonly used sound effects in a background task."""
        if not (self.ok and self.active): return
        for i in ids: self._get('SE%04d' % i, lambda r: None)

    def preload_music(self, ids=BGM_PRELOADED):
        """Music to decode in advance: one per game frame, only when no other decoding is waiting (the first music track of the game therefore goes first) and once a music track is playing. They stay in memory."""
        if not (self.ok and self.ogg): return                                # (even with the sound off at the start: preloading waits for a music track to play)
        for i in ids:
            name_str = 'BGM%02d' % i
            self.pinned.add(name_str)
            if name_str not in self.background: self.background.append(name_str)

    def _purge(self):
        """Decoding cache: 80 sounds (excluding pinned music) and 400 MB at most; the least recently used go first."""
        while True:
            free_keys = [k for k in self.dec if k not in self.pinned]
            if len(free_keys) <= 80 and sum(x['pcm'].nbytes for x in self.dec.values()) <= 400 * 2 ** 20: return
            if not free_keys: return
            del self.dec[free_keys[0]]

    # ---- called every frame
    def tick(self):
        if not self.ok:
            if self.retry is not None and time.time() >= self.retry: self._retry()
            return
        for name_str, (fut, callbacks) in list(self.jobs.items()):
            if not fut.done(): continue
            r = fut.result(); del self.jobs[name_str]
            if r is None: continue
            self.dec[name_str] = r; self._purge()
            for cb in callbacks: cb(r)
        if self.background and not self.jobs and self.bgm is not None:             # nothing else to decode and a music track is playing: preloading advances by one music track
            name_str = self.background.pop(0)
            if name_str not in self.dec: self._get(name_str, lambda r: None, background=True)
        now = time.time()
        if self.fanfare is not None and self.t_pause is None and now >= self.fanfare[1]: self.fanfare = None
        self._release_orders(now)
        if self.resume is not None and self.bgm_end is not None and now >= self.bgm_end:       # Sound.SystemUpdate: the jingle is finished, the previous music resumes
            n = self.resume; self.resume = None; self.bgm_end = None
            self.stop_bgm(); self.play_bgm(n)
        living = []
        for x in self.sfx:                                                  # a finished sound effect (or one past its duration) is released
            if now < x.end and (x.p.playing or now - x.begin < SFX_GRACE): living.append(x)
            else:
                try: x.p.delete()
                except Exception: pass
        self.sfx = living

    # ---- engine orders
    def events(self, evts, engine_frame=None):
        """evts: [(packet type, value)] of the frame: 35 sound effect, 36 sound effect stop, 37 music. engine_frame: number of the last engine frame (30 per second at normal speed):
        used so as not to cut a jingle when the game runs faster than normal (see _is_jingle_protected)."""
        if not (self.ok and self.active):
            if self.retry is not None and self.active: self._track_during_outage(evts)
            return
        if os.environ.get('DQ_SOUND'): self.log('Sound: frame %s orders %s' % (getattr(self, 'image', '?'), evts))       # (test) log of the engine orders
        for t, v in evts:
            if t == 35: self.play_sfx(v, engine_frame)
            elif t == 36: self.stop_sfx(v)
            elif t == 37: self._bgm_order(v, engine_frame)

    def _bgm_order(self, v, e):
        """Engine music order (frame e): applied immediately, or held back as long as a jingle would warrant it (and behind those already waiting: the order of the orders is preserved)."""
        if self.bgm_queue or self._is_jingle_protected(e): self.bgm_queue.append((v, e))
        else: self._apply_order(v, e)

    def _apply_order(self, v, e):
        self.play_bgm(v); self.e_req = e

    def _is_jingle_protected(self, e):
        """True if a jingle is still playing AND the engine order of frame e would have arrived after it at normal speed. An accelerated battle chains its orders (stop, resume of the music, ...) 2 to 3 times earlier: the fanfare was cut
        (level up, BGM 47 of 3.1 s: played 1.75 s at x3). The order therefore waits for the time at which it would have arrived at normal speed (frame e - frame of the order that started the jingle, at 30 per second) or the end of the jingle, whichever comes first.
        The victory fanfare (sound effect 409, 2.9 s) is protected the same way, from its own order: at x2 / x3 the map music no longer covers it longer than at x1 (0.65 s, as in the original)."""
        if e is None or not self.mix.active.get('jingles', True): return False          # no engine frame, or jingles turned off: nothing to protect
        now = time.time(); f = self.fanfare
        if f is not None and f[2] is not None and now < min(f[1], f[0] + (e - f[2]) / ENGINE_FRAMES_PER_S): return True
        if self.e_req is None or self.bgm is None or self.bgm_end is None or self.bgm_category != 'jingles' or self.bgm_t0 is None: return False
        return now < min(self.bgm_end, self.bgm_t0 + (e - self.e_req) / ENGINE_FRAMES_PER_S)

    def is_jingle(self, v):
        """Is the music order v a jingle (a track requested with resume, or a short track without loop: the level-up BGM 47...)? Read from the decoded track, otherwise from BGM_JINGLES. False for a stop."""
        if v >= BGM_STOP: return False
        if v & BGM_RESTART if MUSIC_V2 else v >= BGM_RESTART: return True
        if MUSIC_V2: v &= 0xff
        r = self.dec.get('BGM%02d' % v)
        if r is None: return v in BGM_JINGLES
        return category_bgm(False, r['loop'], len(r['pcm']) / r['frequency']) == 'jingles'

    def _release_orders(self, now):
        """Held-back music orders: applied in order as soon as the jingle no longer has a reason to hold them back. Jingle finished and next order = another jingle (with resume): the previous music resumes first, as it would without the delay (Sound.SystemUpdate)."""
        if self.t_pause is not None: return
        while self.bgm_queue:
            v, e = self.bgm_queue[0]
            if self._is_jingle_protected(e): return
            if BGM_RESTART <= v < BGM_STOP and self.resume is not None and self.bgm_end is not None and now >= self.bgm_end:
                n = self.resume; self.resume = None; self.bgm_end = None; self.stop_bgm(); self.play_bgm(n)
            self.bgm_queue.popleft(); self._apply_order(v, e)

    def play_sfx(self, ident, engine_frame=None):
        """engine_frame: engine frame of the order (engine orders only): the victory fanfare protects the music orders that follow it (see _is_jingle_protected)."""
        if os.environ.get('DQ_SOUND'): self.log('Sound: frame %s sound effect %d' % (getattr(self, 'image', '?'), ident))
        if ident <= 0: return                                                         # sound effect 0: "none" (the engine sends it with each critical hit, SE0000 does not exist in the data)
        name_str = 'SE%04d' % ident
        self._get(name_str, lambda r, i=ident, e=engine_frame: self._launch_sfx(i, r, e))

    def _launch_sfx(self, ident, r, e=None):
        if not self.ok: return                                                         # sound off (outage in progress, or no audio driver): nothing is attempted, failures do not accumulate
        try:
            fmt = self.AudioFormat(channels=r['pcm'].shape[1], sample_size=16, sample_rate=r['frequency'])
            looping = r['loop']                                                            # looping sound effects (17 of 634, measured): until the engine's stop order (packet 36)
            src = LoopSource(self.media, self.AudioData, fmt, r['pcm'], r['start'] if looping else 0, r['end'] if looping else 0)
            category = category_sfx(ident); own = r['volume'] * self.vol_snd(ident); g = self.mix.gain(category, own, 'effects')
            if g <= 0.0 and not looping: return                                 # category turned off (or volume at zero): a short sound effect is not played; a looping sound effect is played at zero volume: it can be heard if the category is reopened
            p = self.media.Player(); p.queue(src); p.volume = g; p.play()
            now = time.time(); duration = len(r['pcm']) / r['frequency']
            self.sfx.append(_SFX(ident, p, float('inf') if looping else now + duration + 0.5, category, own, looping=looping))
            if ident in SFX_JINGLES and not looping: self.fanfare = [now, now + duration, e]
            if len(self.sfx) > 24:
                try: self.sfx[0].p.delete()
                except Exception: pass
                del self.sfx[0]
            self._success()
        except Exception as e:
            self._failure('sound effect %d' % ident, e)

    def stop_sfx(self, ident):
        if ident in SFX_JINGLES: self.fanfare = None
        for x in list(self.sfx):
            if x.ident == ident:
                try: x.p.pause(); x.p.delete()
                except Exception: pass
        self.sfx = [x for x in self.sfx if x.ident != ident]

    bgm_level = 1.0            # DQ V / VI: level of the current track set by the engine (Sound.StopBGM with bit 12: fades by steps, 0 = silent while it keeps playing); a new track starts at 1

    def _bgm_gain(self, category, own):
        return self.mix.gain(category, own) * self.bgm_level

    def play_bgm(self, v, offset=0.0):
        if MUSIC_V2 and v >= BGM_STOP and v & BGM_LEVEL:            # Sound.StopBGM(v & 0x3fff) with bit 12: SoundControl.SetVolume of the current track = music volume x (v & 0xff) / 127 (above 0x107e: x 1); the track keeps playing
            self.bgm_level = 1.0 if (v & 0x3fff) > 0x107e else (v & 0xff) / 127.0
            self._apply_volumes(); return
        if v >= BGM_STOP:                                         # cmd_bgm: >= 0x4000 = Sound.StopBGM
            n, self.resume, self.e_req = self.resume, None, None; self.stop_bgm(); self._drop_layer()
            if n is not None: self.play_bgm(n)                  # stop during a jingle with resume: Unity's StopBGM does not reset its state, the previous music restarts
            return
        if MUSIC_V2: return self._play_bgm_v2(v, offset)
        self._play_track(v, offset)

    def _play_bgm_v2(self, v, offset=0.0):
        """DQ V / VI: track v & 0xff, layer bit 13, restart bit 12 (see MUSIC_V2)."""
        track = v & 0xff
        if v & BGM_LAYER:
            if self.bgm_layer is None and self.bgm is not None and self.bgm_id is not None and not self._bgm_finished():
                pos = self.position_bgm() or 0.0
                try: self.bgm.pause()
                except Exception: pass
                self.bgm_layer = {'id': self.bgm_id, 'p': self.bgm, 'own': self.bgm_own_volume, 'category': self.bgm_category, 'pos': pos, 'level': self.bgm_level}
                self.bgm = None; self.bgm_id = None; self.bgm_request = None; self.bgm_end = None
            back = self.bgm_layer['id'] if v & BGM_RESTART and self.bgm_layer is not None else None
            self._play_track(track, offset); self.resume = back          # (the end of a non-looping layer track then asks for the paused one: it resumes, see below)
            return
        layer = self.bgm_layer
        if layer is not None:
            self.bgm_layer = None
            if layer['id'] == track: self.stop_bgm(); self._resume_layer(layer); return
            self._delete_player(layer['p'])
        self._play_track(track, offset)

    def _resume_layer(self, layer):
        p = layer['p']; self.resume = None
        self.bgm = p; self.bgm_id = self.bgm_request = layer['id']; self.bgm_own_volume = layer['own']; self.bgm_category = layer['category']; self.bgm_level = layer.get('level', 1.0)
        self.bgm_t0 = self.t_pause if self.t_pause is not None else time.time(); self.bgm_pos0 = layer['pos']; self.bgm_end = None
        try:
            p.volume = self._bgm_gain(self.bgm_category, self.bgm_own_volume)
            if self.t_pause is None: p.play()
        except Exception as e:
            self._failure('music track %d' % layer['id'], e)

    @staticmethod
    def _delete_player(p):
        try: p.pause(); p.delete()
        except Exception: pass

    def _drop_layer(self):
        if self.bgm_layer is not None: self._delete_player(self.bgm_layer['p']); self.bgm_layer = None

    def _play_track(self, v, offset=0.0):
        if not self.ogg: return
        resume = None; restart = v >= BGM_RESTART
        if restart: v -= BGM_RESTART; resume = self.bgm_id                 # Sound.PlayBGM with resume: jingle, then return to the current music
        elif v == self.bgm_id and self.bgm is not None and not self._bgm_finished(): self.resume = None; return       # already in progress (a non-looping track that has reached its end is no longer "in progress": requesting it again restarts it)
        else: self.resume = None
        self.stop_bgm(); self.bgm_id = v; self.bgm_request = v; self.resume = resume; self.bgm_end = None; self.bgm_offset = offset; self.e_req = None; self.bgm_level = 1.0          # (SoundControl.Play: the music volume)
        self._get('BGM%02d' % v, lambda r, i=v, rl=restart: self._launch_bgm(i, r, rl))

    def bgm_alive(self):
        """Does the game's music track still exist (SdSoundSystem_SoundCtrl_IsExist on Sound's music control)? A looping track, a non-looping one before its end, a track requested and not
        started yet (decoding, or held back behind a jingle). No track, sound unavailable, or a non-looping track that has reached its end: False."""
        if self.bgm_queue: return True
        if self.bgm is None: return bool(self.bgm_request is not None and self.ogg and self.ok)
        return not self._bgm_finished()

    def _bgm_finished(self):
        """True if the current non-looping track has reached its end (the player stays in place, `bgm_id` too: only the end time says so)."""
        return self.bgm_end is not None and self.t_pause is None and time.time() >= self.bgm_end

    def _launch_bgm(self, ident, r, restart=False):
        if self.bgm_request != ident: return                        # another music track was requested in the meantime
        if not self.ok:
            if not restart: self.bgm_lost = ident                  # sound off: to restart on return
            return
        try:
            fmt = self.AudioFormat(channels=r['pcm'].shape[1], sample_size=16, sample_rate=r['frequency'])
            src = LoopSource(self.media, self.AudioData, fmt, r['pcm'], r['start'], r['end'] if r['loop'] else 0)
            dec, self.bgm_offset = self.bgm_offset, 0.0
            if dec > 0: src.pos = min(int(dec * r['frequency']), max(0, len(r['pcm']) - 1))              # resume at the recorded moment (loaded savestate)
            initial = src.pos / r['frequency']                                                                # (before play: the player immediately reads a few seconds ahead)
            category = category_bgm(restart, r['loop'], len(r['pcm']) / r['frequency'])
            own = r['volume'] * self.vol_snd(ident)                                                          # (file header x snd_vol.dat: the original's volume)
            p = self.media.Player(); p.queue(src); p.volume = self._bgm_gain(category, own); p.play()          # (the music keeps running even when turned off or at zero: reopening the category makes it audible at the right place)
            self.bgm = p; self.bgm_own_volume = own; self.bgm_category = category
            self.bgm_t0 = self.t_pause if self.t_pause is not None else time.time(); self.bgm_pos0 = initial
            if self.t_pause is not None: p.pause()
            self.bgm_end = None if r['loop'] else self.bgm_t0 + len(r['pcm']) / r['frequency'] - initial       # non-looping jingle: time of the resumption of the previous music
            self._success()
        except Exception as e:
            if not restart: self.bgm_lost = ident                          # to restart when the sound returns (a missed jingle no longer has a purpose)
            self._failure('music track %d' % ident, e)

    def position_bgm(self):
        """Playback position of the current music (seconds into the track, loop included), or None if nothing is playing."""
        r = self.dec.get('BGM%02d' % self.bgm_id) if self.bgm_id is not None else None
        if self.bgm is None or r is None or self.bgm_t0 is None: return None
        pos = self.bgm_pos0 + ((self.t_pause if self.t_pause is not None else time.time()) - self.bgm_t0)
        fr = r['frequency']; n = len(r['pcm']) / fr
        if r['loop'] and r['end'] > r['start']:
            end, start_idx = r['end'] / fr, r['start'] / fr
            if pos >= end: pos = start_idx + (pos - end) % (end - start_idx)
        return min(max(pos, 0.0), max(n - 0.01, 0.0))

    def bgm_state(self):
        """What is needed to resume the music at the same moment (savestates): {'id', 'pos', 'reprise'} or None."""
        pos = self.position_bgm()
        return None if pos is None else {'id': self.bgm_id, 'pos': pos, 'reprise': self.resume, 'level': self.bgm_level}

    def restore_bgm(self, info, default=None):
        """Loading a savestate: the music restarts from the recorded moment (without `info`, an old savestate: from the start of `default`, the last engine command).
        The looping sound effects in progress are stopped (the restored engine would never send their stop: they have no time limit) and the fanfare no longer holds anything back."""
        self.stop_bgm(); self._drop_layer(); self.bgm_queue.clear(); self.fanfare = None
        self.stop_loops()
        if info is None:
            if default is not None: self.play_bgm(default)
            return
        self.play_bgm(info['id'], float(info.get('pos') or 0.0)); self.resume = info.get('reprise'); self.bgm_level = float(info.get('level', 1.0)); self._apply_volumes()

    def stop_loops(self):
        """Stops the looping sound effects in progress (a clock's ticking, a machine's hum): after a jump the engine never sends their stop (savestate loaded,
        the debug panel's map or story step)."""
        for x in [x for x in self.sfx if x.looping]: self.stop_sfx(x.ident)

    def stop_bgm(self):
        self.bgm_request = None; self.bgm_id = None
        if self.bgm is not None:
            try: self.bgm.pause(); self.bgm.delete()
            except Exception: pass
            self.bgm = None

    # old names (music / sound effects volume): kept for the existing interface
    @property
    def vol_music(self): return self.mix.vol['music']

    @property
    def vol_sfx(self): return self.mix.vol['effects']

    def set_mix(self, general=None, volumes=None, active_flags=None, game=None):
        """Esc menu: new volumes / switches, applied IMMEDIATELY to the sounds in progress (music, jingle and sound effects already started).
        general: 0 to 1; volumes, active_flags: {category: value}; game: {'music': factor, 'effects': factor} (the game's "volume" options)."""
        m = self.mix
        if general is not None: m.general = float(general)
        for c, v in (volumes or {}).items():
            if c in m.vol: m.vol[c] = float(v)
        for c, v in (active_flags or {}).items():
            if c in m.active: m.active[c] = bool(v)
        for c, v in (game or {}).items():
            if c in m.game: m.game[c] = float(v)
        self._apply_volumes()

    def _apply_volumes(self):
        if self.bgm is not None:
            try: self.bgm.volume = self._bgm_gain(self.bgm_category, self.bgm_own_volume)
            except Exception as e: self._note_once('music volume', e)
        for x in self.sfx:
            try: x.p.volume = self.mix.gain(x.category, x.own, 'effects')
            except Exception as e: self._note_once('sound effects volume', e)

    def set_volumes(self, music=None, sound_effects=None):
        """Compatibility: music and sound effects volume (0 to 1)."""
        self.set_mix(volumes={k: v for k, v in (('music', music), ('effects', sound_effects)) if v is not None})

    def _must_pause(self):
        """Should the audio players be paused? Yes if the menu is suspended outside of listening on the Son page (losing focus no longer pauses: see lose_focus)."""
        return self.suspended and not self.listening

    def set_paused(self, yes):
        """Esc menu open (yes) or closed: music and sound effects paused, except while listening ("Sound" page of the menu: the settings are heard)."""
        self.suspended = bool(yes)
        if not yes: self.listening = False
        self._pause_audio_players(self._must_pause())

    def lose_focus(self, yes):
        """Window without focus (yes) or back (no): the sound is cut (volume at zero) but not paused, since the game keeps running; the music stays in place and is heard again on return."""
        self.out_of_focus = self.mix.muted = bool(yes)
        self._apply_volumes()

    def listen(self, yes):
        """"Sound" page of the Esc menu: sounds resume (the current music, the test sound effects) so that the volumes being adjusted can be heard; on leaving the page, they are paused again."""
        self.listening = bool(yes)
        if self.suspended: self._pause_audio_players(self._must_pause())

    def _pause_audio_players(self, yes):
        """Actual pause of the players; on resume, the deadlines (end of a jingle, of a sound effect) are shifted by the duration of the pause."""
        if yes == (self.t_pause is not None) or not self.ok: return
        if yes:
            self.t_pause = time.time()
            for p in [self.bgm] + [x.p for x in self.sfx]:
                try:
                    if p is not None: p.pause()
                except Exception: pass
        else:
            d = time.time() - self.t_pause; self.t_pause = None
            if self.bgm_t0 is not None: self.bgm_t0 += d
            if self.bgm_end is not None: self.bgm_end += d
            if self.fanfare is not None: self.fanfare[0] += d; self.fanfare[1] += d
            for x in self.sfx: x.end += d
            for p in [self.bgm] + [x.p for x in self.sfx]:
                try:
                    if p is not None: p.play()
                except Exception as e: self._note_once('resume after pause', e)

    def toggle(self):
        """Key: sound on / off."""
        self.active = not self.active
        if not self.active:
            self.stop_bgm(); self.bgm_queue.clear(); self.fanfare = None
            for x in self.sfx:
                try: x.p.pause(); x.p.delete()
                except Exception: pass
            self.sfx = []
        return self.active

    def shutdown(self):
        for pool in (self.pool, self.background_pool):
            try: pool.shutdown(wait=False, cancel_futures=True)
            except Exception: pass
        self.stop_bgm()


def _source_class(media):
    class LoopSource(media.Source):
        """Music with sample-accurate loop (start, end); end == 0: played only once."""
        def __init__(self, media_, audio_data, fmt, pcm, begin, end):
            super().__init__()
            self.audio_format = fmt; self.pcm = pcm; self.begin = begin; self.end = end; self.pos = 0; self._audio_data = audio_data

        def get_audio_data(self, num_bytes, compensation_time=0.0):
            oc = self.audio_format.bytes_per_frame
            n = max(1, num_bytes // oc); chunks = []; remaining = n; t0 = self.pos / self.audio_format.sample_rate
            while remaining > 0:
                limit = self.end if self.end and self.pos < self.end else len(self.pcm)
                k = min(remaining, limit - self.pos)
                if k <= 0:
                    if self.end: self.pos = self.begin; continue
                    break
                chunks.append(self.pcm[self.pos:self.pos + k]); self.pos += k; remaining -= k
                if self.end and self.pos >= self.end: self.pos = self.begin
            if not chunks: return None
            data = np.concatenate(chunks).tobytes()
            return self._audio_data(data, len(data), t0, len(data) / oc / self.audio_format.sample_rate)

        def seek(self, timestamp):
            self.pos = int(max(0.0, timestamp) * self.audio_format.sample_rate) % max(1, len(self.pcm))
    return LoopSource


class _Factory:
    def __call__(self, media, *a):
        return _source_class(media)(media, *a)


LoopSource = _Factory()
