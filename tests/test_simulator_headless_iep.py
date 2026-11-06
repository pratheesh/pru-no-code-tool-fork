"""The headless simulator's IEP timer uses the generated register map and the hardware control bits."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")

JS = r"""
const { Simulator } = require(process.argv[1]);
const sim = new Simulator();
sim.loadInstructions(JSON.parse(process.argv[2]), []);
const st = sim.runUntil({ maxCycles: 200, watchpoints: [] });
process.stdout.write(JSON.stringify(Array.from(st.state.registers)));
"""

NOPS = ["NOP"] * 20


def run(program):
    out = subprocess.run(["node", "-e", JS, str(ROOT / "scripts" / "simulator.js"), json.dumps(program)],
                         text=True, capture_output=True, check=True)
    return json.loads(out.stdout)


def configure(global_cfg, cmp0, cmp_cfg):
    # offsets as the generated timer code uses them: GLOBAL_CFG 0x00, CMP_CFG 0x70, CMP0 0x78
    return [
        f"LDI32 R1, {cmp0}", "SBCO &R1, c26, 0x78, 4",
        f"LDI32 R1, {cmp_cfg}", "SBCO &R1, c26, 0x70, 4",
        f"LDI32 R1, {global_cfg}", "SBCO &R1, c26, 0x00, 4",
    ]


def test_compare_at_generated_offsets_sets_status():
    prog = configure(0x11, 5, 0x3) + NOPS + ["LBCO &R3, c26, 0x74, 4"]
    assert run(prog)[3] & 1 == 1


def test_status_is_write_one_to_clear_at_generated_offset():
    prog = (configure(0x11, 5, 0x2) + NOPS + ["LDI32 R1, 1", "SBCO &R1, c26, 0x74, 4",
            "LBCO &R3, c26, 0x74, 4"])
    assert run(prog)[3] & 1 == 0


def test_counter_does_not_run_without_enable():
    prog = configure(0x10, 5, 0x3) + NOPS + ["LBCO &R3, c26, 0x74, 4", "LBCO &R4, c26, 0x10, 4"]
    regs = run(prog)
    assert regs[3] == 0 and regs[4] == 0


def test_compare_without_cmp_enable_does_not_fire():
    prog = configure(0x11, 5, 0x0) + NOPS + ["LBCO &R3, c26, 0x74, 4"]
    assert run(prog)[3] == 0


def test_count_is_not_reset_without_cmp0_reset_enable():
    prog = configure(0x11, 5, 0x2) + NOPS * 3 + ["LBCO &R4, c26, 0x10, 4"]
    assert run(prog)[4] > 5
