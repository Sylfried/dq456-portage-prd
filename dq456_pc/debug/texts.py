"""Text viewer of the debug mode (the web panel's "Texts" tab): any message of the game shown in the game's own message window, where the hero is (a town or the
world map), to check a translation in place: its line breaks, its pages, its names and macros as the game fills them.

The messages are the game's files `assets/msg/en/b<NNNNNNN>.mpt` (the French patch writes its texts in the English slot): a header (size, first number, last,
count, table offset, table size), a table of (key, length, offset / 4), the texts. A message's number is the file's first number + its key; the panel names it
`b<NNNNNNN>.mpt#<key>`, as the translation's workbooks do.

A message is shown as the scripts show one: their "message" command (DQ IV `cmd_message1`, DQ V `CmdMessage::initialize`, DQ VI `script::CmdMessage1::initialize`;
a block of (first message, count)), made during the next engine step. It opens the town's or the world map's message window (the speaker's voice, the pages),
which the player turns and closes as any other; several messages in a row: `count`.
Any text (`show_custom`): a message of the game carries it. `MsgData::msg_find(number)` (the three games) loads the number's file block, then
`msg_find_sub` puts the message's address at MsgData + 8 and its length at + 0x80 (its number at + 0x7c: the cache); right after it (msg_find + 0x7c),
for the carrier's number, the text's own address and length are put there instead (`msg_set_meta` then reads its codes as the game's). The cache is
emptied before (the carrier's own text was perhaps there) and once the window has closed (its own text again). Written as the game's messages are:
"@a<speaker>@b<text>\n@c0@"; DQ IV's French patch writes n and r as и and я (its font), so does the text when the game's own messages do."""
import re, struct

from dq456_pc import games as GM
from dq456_pc.core import engine as ENGINE
from dq456_pc.debug.catalog import mpt_entries
from dq456_pc.language import N_, tr

FOLDER = 'assets/msg/en/'
MESSAGE_CMD = {'dq4': ('_Z12cmd_message1P16CommandParameter', 4), 'dq5': ('_ZN10CmdMessage10initializeEPc', 0),
               'dq6': ('_ZN6script11CmdMessage110initializeEPc', 0)}          # (function, offset of (first message, count) in its block; DQ IV: the block alone)
PREVIEW = 90                                                                  # characters of a message shown in the list
COUNT_MAX = 50
MSG_FIND = {'dq4': '_ZN7MsgData8msg_findEi', 'dq5': '_ZN4ardq7MsgData8msg_findEi', 'dq6': '_ZN4ardq7MsgData8msg_findEi'}
AFTER_SUB = 0x7c                                                              # msg_find + 0x7c: just after msg_find_sub (cbz w0), the same in the three games
TEXT_AT, NUMBER_AT, LENGTH_AT = 0x8, 0x7c, 0x80                               # MsgData: the message's address, its number, its length
CUSTOM_MAX = 400                                                              # characters of a text typed in the panel
DQ4_GLYPHS = str.maketrans({'n': 'и', 'r': 'я'})                              # DQ IV's French patch: n and r drawn by these characters of its font


def file_first(data):
    """The first message number of an MPT0 file (its header), None if it is not one."""
    return struct.unpack_from('<I', data, 8)[0] if data[:4] == b'MPT0' and len(data) >= 12 else None


def preview(text):
    """A message as a line of the list: "speaker: text" ("@a<speaker>@b<text>@c0@"), the line breaks as spaces, the game's other codes left out."""
    m = re.match(r'@a(.*?)@b(.*?)(@c.*)?$', text, re.S)
    if m: speaker, body = m.group(1), m.group(2)
    else: speaker, body = '', text
    body = re.sub(r'@[0-9a-z@]', '', body).replace('\n', ' ').strip()
    return ('%s : %s' % (speaker, body) if speaker else body)[:PREVIEW]


class Texts:
    """Registered on the debug service: the page `/api/texts` and the command `show_text`."""

    def __init__(self, service, catalog):
        self.d = service; self.g = service.g; self.key = GM.profile().key; self.catalog = catalog
        self._files = None; self.queued = False          # (a message waits for the next engine step)
        self.custom = None                                # a text typed in the panel: {'number' (its carrier), 'data', 'state': new / shown / done}
        self._hooked = False
        service.panel_pages['/api/texts'] = self.info
        service.panel_commands.update({'show_text': self.show, 'show_custom': self.show_custom})
        service.ticks.append(self.tick)

    def _fs(self): return self.g.engine.core.fs

    def _read(self, name):
        fs = self._fs(); p = FOLDER + name
        return fs.names[p].read(p) if fs is not None and p in fs.names else None

    def files(self):
        """[(file name, first number, messages)] of the game's message files."""
        if self._files is None:
            out = []; fs = self._fs()
            for p in sorted(fs.names if fs is not None else ()):
                if not (p.startswith(FOLDER) and p.endswith('.mpt')): continue
                d = fs.names[p].read(p); first = file_first(d)
                if first is not None: out.append((p[len(FOLDER):], first, len(mpt_entries(d))))
            self._files = out
        return self._files

    def info(self, q=None):
        name = (q or {}).get('file')
        if not name: return {'game': self.key, 'files': self.files()}
        d = self._read(name)
        if d is None or file_first(d) is None: return {'error': tr(N_('debug.texts_file_bad')) % name}
        first = file_first(d)
        return {'file': name, 'first': first, 'entries': [(k, first + k, preview(t)) for k, t in sorted(mpt_entries(d).items())]}

    def number(self, c):
        """The message number of a command: `number`, or `file` and `key` ("b0834000.mpt", 75), or "b0834000.mpt#75" in `number`."""
        v = c.get('number'); name = c.get('file'); k = c.get('key')
        if isinstance(v, str) and '#' in v: name, k = v.split('#', 1); v = None
        if v not in (None, ''): return int(v)
        d = self._read(str(name).strip()) if name else None
        if d is None or file_first(d) is None: raise ValueError(name)
        return file_first(d) + int(k)

    def show_custom(self, c):
        """Any text, in the game's message window as `show` shows a message: a message of the game carries it (the first of the first file)."""
        text = str(c.get('text') or '').replace('\r', '').strip('\n')
        if not text.strip() or len(text) > CUSTOM_MAX: return {'error': tr(N_('debug.texts_custom_bad')) % CUSTOM_MAX}
        files = self.files(); d = self._read(files[0][0]) if files else None
        own = mpt_entries(d) if d else {}
        if not own: return {'error': tr(N_('debug.texts_file_bad')) % (files[0][0] if files else FOLDER)}
        if self.key == 'dq4' and any('и' in t or 'я' in t for t in own.values()): text = text.translate(DQ4_GLYPHS)
        if not self._hook(): return {'error': tr(N_('debug.catalog_missing')) % MSG_FIND[self.key]}
        number = file_first(d) + min(own)
        r = self.show({'number': number, 'count': 1})
        if r.get('error'): return r
        self.custom = {'number': number, 'data': ('@a@b%s\n@c0@' % text).encode('utf-8'), 'state': 'new'}
        return {'ok': True, 'number': number}

    def _hook(self):
        """The two code hooks of msg_find (once): the cache emptied for the carrier's number, its text replaced right after msg_find_sub."""
        if self._hooked: return True
        core = self.g.engine.core; f = core.export(MSG_FIND[self.key])
        if not f: return False
        import unicorn
        X0, X1, X19 = unicorn.arm64_const.UC_ARM64_REG_X0, unicorn.arm64_const.UC_ARM64_REG_X1, unicorn.arm64_const.UC_ARM64_REG_X19
        def entry(uc, addr, size, user):
            c = self.custom
            if c is None or c['state'] == 'shown' or uc.reg_read(X1) & 0xffffffff != c['number']: return
            uc.mem_write(uc.reg_read(X0) + NUMBER_AT, struct.pack('<i', -1))          # (the cache: the carrier's own text, or ours)
            if c['state'] == 'done': self.custom = None
        def after(uc, addr, size, user):
            c = self.custom; me = uc.reg_read(X19)
            if c is None or c['state'] != 'new' or not uc.reg_read(X0) & 1 or struct.unpack('<i', uc.mem_read(me + NUMBER_AT, 4))[0] != c['number']: return
            a = core.scratch('custom_text', len(c['data']) + 1); uc.mem_write(a, c['data'] + b'\0')
            uc.mem_write(me + TEXT_AT, struct.pack('<Q', a)); uc.mem_write(me + LENGTH_AT, struct.pack('<I', len(c['data']))); c['state'] = 'shown'
        core.uc.hook_add(unicorn.UC_HOOK_CODE, entry, None, f, f); core.uc.hook_add(unicorn.UC_HOOK_CODE, after, None, f + AFTER_SUB, f + AFTER_SUB)
        self.g.engine._flush_tb('custom text'); self._hooked = True
        return True

    def tick(self):
        """Each loop frame: a text shown, its window closed: the carrier's own text comes back (the cache emptied at its next msg_find)."""
        c = self.custom
        if c is None or c['state'] != 'shown': return
        if self.g.engine.msg_visible: c['seen'] = True
        elif c.get('seen'): c['state'] = 'done'

    def show(self, c):
        """The message (and the `count` - 1 that follow it) in the game's message window, as a script shows one, during the next engine step."""
        try: num = self.number(c); count = int(c.get('count') or 1)
        except (TypeError, ValueError): return {'error': tr(N_('debug.texts_bad')) % (c.get('number') or c.get('file'))}
        if not 1 <= count <= COUNT_MAX: return {'error': tr(N_('debug.texts_bad')) % count}
        eng = self.g.engine
        if eng.state.part not in (ENGINE.PART_TOWN, ENGINE.PART_FIELD): return {'error': tr(N_('debug.texts_not_here'))}
        if eng.msg_visible or self.queued: return {'error': tr(N_('debug.texts_busy'))}
        core = eng.core; fn, off = MESSAGE_CMD[self.key]; f = core.export(fn)
        if not f: return {'error': tr(N_('debug.catalog_missing')) % fn}
        def go():
            self.queued = False; block = bytearray(32); struct.pack_into('<2i', block, off, num, count)
            b = core.scratch('text_block', 32); core.mem_w(b, bytes(block))
            if self.key == 'dq4': core.call(f, b, label=fn)
            else: o = core.scratch('text_command', 128); core.mem_w(o, bytes(128)); core.call(f, o, b, label=fn)          # (the command object, unused by the message)
        self.queued = True; self.catalog.in_step.append(go); self.d.note(tr(N_('debug.texts_shown')) % num)
        return {'ok': True, 'number': num}
