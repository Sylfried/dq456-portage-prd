"""Debug mode, the "Texts" tab (dq456_pc/debug/texts.py): any message of the game shown in the game's own window, as the scripts' "message" command shows one.
Stand-in engine and message file: the CI has no game.

    python3 tests/test_debug_texts.py

(Real-engine check: DQ IV save 4 on the world map, DQ V save 2 in a town and save 5 on the world map, DQ VI save 1 in a church: the message opens in the game's
window with its speaker, turns and closes with A, the player is free again.)
"""
import os, struct, sys, types, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, '..'))
from dq456_pc.core import engine as ENGINE
from dq456_pc.debug import texts as TX


def mpt(first, texts):
    """An MPT0 file: header (size, first number, last, count, table offset, table size), table (key, length, offset / 4), texts."""
    enc = {k: t.encode() + b'\0' * (-len(t.encode()) % 4) for k, t in texts.items()}; table = b''; body = b''
    for k, t in sorted(texts.items()):
        table += struct.pack('<3H', k, len(t.encode()), len(body) // 4); body += enc[k]
    table += b'\0' * (-len(table) % 4)
    return b'MPT0' + struct.pack('<6I', 0, first, first + max(texts), len(texts), 28, len(table)) + table + body


FILE = mpt(834003, {0: '@a@bSauvegarder ?\n@c0@', 3: '@aSancho@bS-Señor !\nOun bébé ; oui.\n@c0@', 75: 'Retour à l\'écran titre.'})


class Core:
    def __init__(self):
        self.calls = []; self.ram = bytearray(0x200)
        reader = type('R', (), {'read': lambda s, n: FILE})()
        self.fs = types.SimpleNamespace(names={'assets/msg/en/b0834000.mpt': reader, 'assets/msg/en/notes.txt': reader, 'assets/msg/ja/b0834000.mpt': reader})
    def export(self, n): return 0x900 if n in [v[0] for v in TX.MESSAGE_CMD.values()] else None
    def scratch(self, name, n): return {'text_block': 0x40, 'text_command': 0x100}[name]
    def mem_w(self, a, b): self.ram[a:a + len(b)] = b
    def call(self, f, *args, label=None): self.calls.append((label, args, bytes(self.ram[0x40:0x50])))


class TextsTab(unittest.TestCase):
    def texts(self, key='dq5', part=ENGINE.PART_TOWN, msg=False):
        gm = TX.GM; TX.GM = types.SimpleNamespace(profile=lambda: types.SimpleNamespace(key=key)); self.addCleanup(setattr, TX, 'GM', gm)
        core = Core(); eng = types.SimpleNamespace(core=core, state=types.SimpleNamespace(part=part), msg_visible=msg)
        s = types.SimpleNamespace(g=types.SimpleNamespace(engine=eng), panel_pages={}, panel_commands={}, notes=[]); s.note = s.notes.append
        cat = types.SimpleNamespace(in_step=[])
        return TX.Texts(s, cat), core, cat, s

    def test_files_and_entries(self):
        t, core, cat, s = self.texts()
        self.assertEqual(t.info()['files'], [('b0834000.mpt', 834003, 3)])                           # (the English slot's message files only)
        e = t.info({'file': 'b0834000.mpt'})
        self.assertEqual(e['first'], 834003)
        self.assertEqual(e['entries'], [(0, 834003, 'Sauvegarder ?'), (3, 834006, 'Sancho : S-Señor ! Oun bébé ; oui.'), (75, 834078, 'Retour à l\'écran titre.')])
        self.assertIn('error', t.info({'file': 'b9999000.mpt'}))
        self.assertIn('/api/texts', s.panel_pages); self.assertIn('show_text', s.panel_commands)

    def test_number_or_key(self):
        t, *_ = self.texts()
        self.assertEqual(t.number({'number': 834078}), 834078); self.assertEqual(t.number({'number': 'b0834000.mpt#75'}), 834078)
        self.assertEqual(t.number({'file': 'b0834000.mpt', 'key': 3}), 834006)

    def test_shown_as_a_script_shows_one(self):
        for key, off in (('dq4', 4), ('dq5', 0), ('dq6', 0)):
            t, core, cat, s = self.texts(key)
            self.assertEqual(t.show({'number': 'b0834000.mpt#3', 'count': 2}), {'ok': True, 'number': 834006})
            self.assertEqual(core.calls, []); self.assertIn('error', t.show({'number': 834003}))          # (during the next step; one at a time)
            cat.in_step.pop()()
            (label, args, block), = core.calls
            self.assertEqual(label, TX.MESSAGE_CMD[key][0]); self.assertEqual(struct.unpack_from('<2i', block, off), (834006, 2))
            self.assertEqual(args, (0x40,) if key == 'dq4' else (0x100, 0x40))                                  # (DQ IV: the block alone; DQ V / VI: the command object, the block)
            self.assertFalse(t.queued)

    def test_refused(self):
        self.assertIn('error', self.texts(part=ENGINE.PART_BATTLE)[0].show({'number': 834003}))
        self.assertIn('error', self.texts(msg=True)[0].show({'number': 834003}))
        t = self.texts()[0]
        for c in ({'number': 'x'}, {'number': 834003, 'count': 999}, {'number': 'b1234000.mpt#2'}): self.assertIn('error', t.show(c), c)


if __name__ == '__main__':
    unittest.main()
