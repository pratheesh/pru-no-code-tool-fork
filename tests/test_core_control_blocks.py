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


# ------------------------------------------------------------ expansion helpers

def expand_macro(block_id, args):
    return node_json(f"""
const h = require('./scripts/dual_oracle_harness');
const e = require('./scripts/emit_all_blocks');
const m = e.loadBlock(e.allBlocks().find(b => b.id === '{block_id}').file);
console.log(JSON.stringify(h.expandMacro(m.getMacro('', m.config.find(c => c.name === 'opCode').default), {json.dumps(args)})));
""")


def code(lines):
    return [l.split(";", 1)[0].strip() for l in lines if l.split(";", 1)[0].strip()]


# ------------------------------------------------------------ event_block

EVENT = "program_control/event_block"
EVENT_BASE = {"mode": "wait", "inputEventBit": 30, "outputEvent": 16, "clearAfterWait": False}


def test_event_clear_after_wait_is_refused_and_off_by_default():
    assert block_config("event_block")["clearAfterWait"] is False
    assert validate(EVENT, EVENT_BASE)["errors"] == []
    red = validate(EVENT, {**EVENT_BASE, "clearAfterWait": True})
    assert [e["field"] for e in red["errors"]] == ["clearAfterWait"]


def test_event_macro_leaves_r0_alone():
    for args in ({"mode": 2, "inputEventBit": 30, "outputEvent": 16, "clearAfterWait": 0},
                 {"mode": 0, "inputEventBit": 30, "outputEvent": 16, "clearAfterWait": 1}):
        body = code(expand_macro("event_block", {"RES": "RES", **args}))
        assert not [l for l in body if re.search(r"\bR0\b", l)], body


def test_event_signal_value_is_valid_strobe_plus_channel():
    # R31.b0 = 0x20 (strobe) | (system event - 16); the macro loads event + 16
    for event in (16, 31):
        body = code(expand_macro("event_block", {"mode": 1, "inputEventBit": 30, "outputEvent": event, "clearAfterWait": 0}))
        ldi = [l for l in body if l.startswith("ldi   R31.b0")]
        assert ldi == [f"ldi   R31.b0, {event} + 16"], body
        assert (event + 16) == 0x20 | (event - 16)
