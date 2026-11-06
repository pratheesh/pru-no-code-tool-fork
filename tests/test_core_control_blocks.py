"""Core data-handling and program-control block checks.

Each test drives the committed block sources through scripts/emit_all_blocks.js
(macro expansion) or scripts/block_validate_runner.js (validate()).
"""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCKS = ROOT / ".metadata" / "sysconfig" / ".meta" / "pru_blocks"
VALIDATE = ROOT / "scripts" / "block_validate_runner.js"


def node_json(script):
    proc = subprocess.run(["node", "-e", script], cwd=str(ROOT), capture_output=True,
                          text=True, check=True, timeout=60)
    return json.loads(proc.stdout)


def block_config(block_id):
    return node_json(f"""
const e = require('./scripts/emit_all_blocks');
const m = e.loadBlock(e.allBlocks().find(b => b.id === '{block_id}').file);
const o = {{}};
for (const c of m.config) o[c.name] = c.default;
console.log(JSON.stringify(o));
""")


def expand(block_id, overrides=None):
    return node_json(f"""
const e = require('./scripts/emit_all_blocks');
const r = e.expandBlock('{block_id}', {{ ...e.LITERAL, ...{json.dumps(overrides or {})} }});
console.log(JSON.stringify({{ operands: r.operands, instructions: r.instructions }}));
""")


def validate(block, inst):
    proc = subprocess.run(["node", str(VALIDATE), block, json.dumps(inst)], cwd=str(ROOT),
                          capture_output=True, text=True, check=True, timeout=30)
    return json.loads(proc.stdout.strip())


# ------------------------------------------------------------ iqmath_park_clarke

def test_park_clarke_second_output_has_its_own_macro_destination():
    cfg = block_config("iqmath_park_clarke_block")
    assert cfg["numOfOutputPorts"] == 2
    assert cfg["multiOutputMacroArgs"] is True
    assert cfg["output1Size"] == 4 and cfg["output2Size"] == 4


def test_park_clarke_writes_both_outputs_in_both_directions():
    for is_forward in (1, 0):
        res = expand("iqmath_park_clarke_block", {"IS_FORWARD": is_forward})
        assert res["operands"][:2] == ["OUT1", "OUT2"]
        # the shared operand table binds OUT1 and OUT2 to R10 and R11
        loads = [l for l in res["instructions"] if re.match(r"lbbo\s+&R1[01]", l)]
        assert any(re.match(r"lbbo\s+&R10,.*,\s*0,\s*4$", l) for l in loads), loads
        assert any(re.match(r"lbbo\s+&R11,.*,\s*4,\s*4$", l) for l in loads), loads
