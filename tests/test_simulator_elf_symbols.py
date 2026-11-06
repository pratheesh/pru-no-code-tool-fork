"""The headless simulator's ELF loader reads symbols from the .symtab section, not the file start."""
import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _elf_with_symbols(symbols):
    text = struct.pack("<I", 0x2A000000)
    strtab = b"\0" + b"".join(n.encode() + b"\0" for n, _, _ in symbols)
    symtab = b"\0" * 16
    off = 1
    for name, value, size in symbols:
        symtab += struct.pack("<IIIBBH", off, value, size, 0x11, 0, 1)
        off += len(name) + 1
    shstr = b"\0.text\0.symtab\0.strtab\0.shstrtab\0"
    names = {n: shstr.index(n.encode()) for n in (".text", ".symtab", ".strtab", ".shstrtab")}
    body = text + symtab + strtab + shstr
    base = 52
    off_text, off_sym = base, base + len(text)
    off_str = off_sym + len(symtab)
    off_shstr = off_str + len(strtab)
    shoff = base + len(body)
    shdr = lambda *f: struct.pack("<10I", *f)
    sections = [
        shdr(0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        shdr(names[".text"], 1, 6, 0, off_text, len(text), 0, 0, 4, 0),
        shdr(names[".symtab"], 2, 0, 0, off_sym, len(symtab), 3, 1, 4, 16),
        shdr(names[".strtab"], 3, 0, 0, off_str, len(strtab), 0, 0, 1, 0),
        shdr(names[".shstrtab"], 3, 0, 0, off_shstr, len(shstr), 0, 0, 1, 0),
    ]
    ehdr = b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\0" * 8 + struct.pack(
        "<HHIIIIIHHHHHH", 2, 0xCB, 1, 0, 0, shoff, 0, 52, 0, 0, 40, len(sections), 4)
    return ehdr + body + b"".join(sections)


def test_symbols_come_from_the_symbol_table(tmp_path):
    elf = tmp_path / "fw.out"
    elf.write_bytes(_elf_with_symbols([("result", 0x120, 4), ("counter", 0x124, 8)]))
    js = ("const s=require(process.argv[1]);const fs=require('fs');"
          "const r=s.loadPruFirmware(fs.readFileSync(process.argv[2]));"
          "process.stdout.write(JSON.stringify(r.symbols));")
    out = subprocess.run(["node", "-e", js, str(ROOT / "scripts" / "simulator.js"), str(elf)],
                         text=True, capture_output=True, check=True)
    syms = {s["name"]: (s["value"], s["size"]) for s in json.loads(out.stdout) if s["name"]}
    assert syms == {"result": (0x120, 4), "counter": (0x124, 8)}
