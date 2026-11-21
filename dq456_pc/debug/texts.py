"""Text viewer of the debug mode (the web panel's "Texts" tab): any message of the game shown in the game's own message window, where the hero is (a town or the
world map), to check a translation in place: its line breaks, its pages, its names and macros as the game fills them.

The messages are the game's files `assets/msg/en/b<NNNNNNN>.mpt` (the French patch writes its texts in the English slot): a header (size, first number, last,
count, table offset, table size), a table of (key, length, offset / 4), the texts. A message's number is the file's first number + its key; the panel names it
`b<NNNNNNN>.mpt#<key>`, as the translation's workbooks do.

A message is shown as the scripts show one: their "message" command (DQ IV `cmd_message1`, DQ V `CmdMessage::initialize`, DQ VI `script::CmdMessage1::initialize`;
a block of (first message, count)), made during the next engine step. It opens the town's or the world map's message window (the speaker's voice, the pages),
which the player turns and closes as any other; several messages in a row: `count`."""
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
        service.panel_pages['/api/texts'] = self.info
        service.panel_commands['show_text'] = self.show

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
