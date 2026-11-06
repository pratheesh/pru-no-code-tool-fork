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


# ------------------------------------------------------------ iep_block

IEP = "utils/iep_block"
IEP_BASE = {"func": "oneshot", "clkHz": 200000000, "periodNs": 5000, "cmpIndex": 0}


def iep_body(func, cmp_index, auto_reset=1):
    return code(expand_macro("iep_block", {"func": func, "counts": 1000, "cmpIndex": cmp_index, "autoReset": auto_reset}))


def test_iep_capture_is_refused():
    assert validate(IEP, IEP_BASE)["errors"] == []
    red = validate(IEP, {**IEP_BASE, "func": "capture"})
    assert [e["field"] for e in red["errors"]] == ["func"]


def test_iep_nonzero_compare_uses_its_own_register_enable_and_status_bit():
    for n in (1, 3, 15):
        body = iep_body(1, n)
        assert f"sbco  &R29, CONST_IEP, IEP_CMP0 + (4 * {n}), 4" in body, body
        assert f"ldi32 R28, (0x02 << {n})" in body, body
        waits = [l for l in body if l.startswith("qbbc")]
        assert len(waits) == 1 and waits[0].endswith(f"R29, {n}"), waits
        clears = [l for l in body if l == f"ldi32 R28, (1 << {n})"]
        assert len(clears) == 2, body  # status clear before the wait and again after it


def test_iep_compare_zero_keeps_periodic_and_oneshot_enables():
    assert "ldi32 R28, 0x03" in iep_body(0, 0, 1)
    assert "ldi32 R28, (0x02 << 0)" in iep_body(0, 0, 0)
    assert "ldi32 R28, (0x02 << 0)" in iep_body(1, 0, 1)


def test_iep_macro_uses_only_the_temporary_registers():
    for func, n in ((0, 0), (1, 5)):
        body = iep_body(func, n)
        used = {m for l in body for m in re.findall(r"\bR\d+\b", l)}
        assert used <= {"R28", "R29"}, used


# ------------------------------------------------------------ scheduler_block

SCHED = "program_control/scheduler_block"
SCHED_BASE = {"timerSource": "iep", "contextSwitchOverhead": 10,
              "task1_id": 0, "task2_id": 1, "task3_id": 255, "task4_id": 255}


def test_scheduler_refuses_external_timer_source():
    assert validate(SCHED, SCHED_BASE)["errors"] == []
    red = validate(SCHED, {**SCHED_BASE, "timerSource": "external"})
    assert [e["field"] for e in red["errors"]] == ["timerSource"]


def test_scheduler_validates_every_task_id():
    for field in ("task1_id", "task2_id", "task3_id", "task4_id"):
        for bad in (32, -1):
            red = validate(SCHED, {**SCHED_BASE, field: bad})
            assert [e["field"] for e in red["errors"]] == [field], (field, bad, red)
        assert validate(SCHED, {**SCHED_BASE, field: 31})["errors"] == []


# ------------------------------------------------------------ trigger_router_block

def test_trigger_router_delay_uses_a_temporary_register():
    for routes in ((255, 255, 255, 255), (5, 6, 255, 255)):
        args = dict(zip(("route1", "route2", "route3", "route4"), routes))
        body = code(expand_macro("trigger_router_block", args))
        used = {m for l in body for m in re.findall(r"\bR\d+\b", l)}
        assert used <= {"R28", "R29", "R31"}, (routes, used)
