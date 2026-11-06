"""Register and opcode semantics of the GUI simulator (common/simulation/pru_core.js).

PRU word registers overlap byte-wise: Rn.w0 is bits 15:0, Rn.w1 bits 23:8 and Rn.w2 bits 31:16, so the shift of
Rn.wK is 8*K. A shift of 16*K puts .w1 at bits 31:16 (the .w2 field) and makes every .w1 read or write wrong.
A sub-register write must leave the register as an unsigned 32-bit value (JS bitwise operators return signed ones).

Base instructions the simulator recognises but does not execute (MAC, MACU, WBC) must stop the run with an error
instead of being skipped, which would leave registers silently stale.

Needs only node.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SIM_DIR = ROOT / ".metadata" / "sysconfig" / ".meta" / "pru_blocks" / "common" / "simulation"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")

RUN = r"""
const fs = require('fs'), path = require('path'), vm = require('vm');
const SIM_DIR = process.argv[1];
const cache = {};
function load(p) {
    const sandbox = { console, Math, JSON, Number, Array, Object, Set, Error, Uint8Array, BigInt, parseInt, isNaN,
        system: { getScript: (s) => { const b = path.basename(s); return cache[b] || (cache[b] = load(path.join(SIM_DIR, b))); } },
        exports: {} };
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(p, 'utf8'), sandbox);
    return sandbox.exports;
}
const core = load(path.join(SIM_DIR, 'pru_core.js'));
const prog = JSON.parse(process.argv[2]);
try {
    const r = core.simulatePruInstructions(prog, {}, 50);
    process.stdout.write(JSON.stringify({ regs: Array.from(r.pruState.registers) }));
} catch (e) {
    process.stdout.write(JSON.stringify({ error: String(e.message || e) }));
}
"""


def run(program):
    out = subprocess.run(["node", "-e", RUN, str(SIM_DIR), json.dumps(program)],
                         cwd=ROOT, text=True, capture_output=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize("dest,expected", [
    ("R1.w0", 0xAABB1234),
    ("R1.w1", 0xAA1234DD),
    ("R1.w2", 0x1234CCDD),
])
def test_word_register_write_touches_only_its_16_bits(dest, expected):
    regs = run(["LDI32 R1, 0xAABBCCDD", "LDI %s, 0x1234" % dest])["regs"]
    assert regs[1] == expected, "%s write gave 0x%08X, want 0x%08X" % (dest, regs[1], expected)


@pytest.mark.parametrize("src,expected", [("R2.w0", 0x3344), ("R2.w1", 0x2233), ("R2.w2", 0x1122)])
def test_word_register_read_returns_its_16_bits(src, expected):
    regs = run(["LDI32 R2, 0x11223344", "MOV R3, %s" % src])["regs"]
    assert regs[3] == expected, "MOV R3, %s gave 0x%08X, want 0x%04X" % (src, regs[3], expected)


@pytest.mark.parametrize("opcode", ["MAC", "MACU", "WBC"])
def test_unimplemented_base_instruction_stops_the_run(opcode):
    result = run(["LDI R1, 1", "%s R1, R2" % opcode])
    assert "not yet implemented" in result.get("error", ""), result


@pytest.mark.parametrize("program,reg,expected", [
    (["LDI32 R0, 0xAABBCCDD", "ZERO &R0, 1"], 0, 0xAABBCC00),
    (["LDI32 R0, 0xAABBCCDD", "ZERO &R0.b2, 1"], 0, 0xAA00CCDD),
    (["LDI32 R0, 0xAABBCCDD", "ZERO &R0.w1, 2"], 0, 0xAA0000DD),
    (["LDI32 R0, 0xAABBCCDD", "FILL &R0.b1, 1"], 0, 0xAABBFFDD),
    (["LDI32 R0, 0xAABBCCDD", "ZERO &R0, 4"], 0, 0),
])
def test_zero_and_fill_touch_only_the_named_bytes(program, reg, expected):
    regs = run(program)["regs"]
    assert regs[reg] == expected, "R%d is 0x%08X, want 0x%08X" % (reg, regs[reg], expected)


def test_zero_spans_into_the_next_register_by_bytes():
    regs = run(["LDI32 R0, 0xAABBCCDD", "LDI32 R1, 0x11223344", "ZERO &R0.b3, 3"])["regs"]
    assert regs[0] == 0x00BBCCDD
    assert regs[1] == 0x11220000
