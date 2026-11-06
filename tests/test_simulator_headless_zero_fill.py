"""ZERO and FILL in the headless simulator change exactly the addressed bytes."""
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
const st = sim.runUntil({ maxCycles: 20, watchpoints: [] });
process.stdout.write(JSON.stringify(Array.from(st.state.registers)));
"""


def regs(program):
    out = subprocess.run(["node", "-e", JS, str(ROOT / "scripts" / "simulator.js"), json.dumps(program)],
                         text=True, capture_output=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize("program,expected", [
    (["LDI32 R0, 0xAABBCCDD", "ZERO &R0, 1"], 0xAABBCC00),
    (["LDI32 R0, 0xAABBCCDD", "ZERO &R0.b2, 1"], 0xAA00CCDD),
    (["LDI32 R0, 0xAABBCCDD", "FILL &R0.b1, 1"], 0xAABBFFDD),
    (["LDI32 R0, 0xAABBCCDD", "ZERO &R0, 4"], 0),
])
def test_zero_fill_bytes(program, expected):
    assert regs(program)[0] == expected
