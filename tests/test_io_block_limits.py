"""Option limits and cross-instance checks for the I/O blocks.

- i2c addr7 must be a 7-bit address.
- stepper numSteps and quadrature sampleCycles are loaded into 16-bit registers.

Validators run through scripts/block_validate_runner.js and scripts/static_global_runner.js,
which load the shipped sources.
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IO = ".metadata/sysconfig/.meta/pru_blocks/pru_io_blocks"


def node(*args):
    proc = subprocess.run(["node", *args], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout.strip())


def validate(block, inst):
    return node("scripts/block_validate_runner.js", block, json.dumps(inst))["errors"]


def config_options(block, name):
    code = (
        "const fs=require('fs'),vm=require('vm');"
        f"const src=fs.readFileSync('{IO}/{block}.syscfg.js','utf8');"
        "const sb={exports:{},console,system:{getScript:()=>({getSelfSysCfgCoreName:()=>'icss_g0_pru0'})}};"
        "vm.runInNewContext(src,sb,{filename:'blk'});"
        f"const c=sb.exports.config.find(x=>x.name==='{name}');"
        "console.log(JSON.stringify(c?(c.options||[]).map(o=>o.name):null));"
    )
    return node("-e", code)


# ---------------------------------------------------------------- i2c addr7

@pytest.mark.parametrize("addr", ["0x80", "0xFF", "0x100", "zz", ""])
def test_i2c_addr7_outside_seven_bits_is_refused(addr):
    errs = validate("pru_io_blocks/i2c_block", {"sclBit": "0", "sdaBit": "1", "addr7": addr, "rw": "write"})
    assert [e["field"] for e in errs] == ["addr7"], errs


@pytest.mark.parametrize("addr", ["0x00", "0x50", "50", "0x7F"])
def test_i2c_addr7_seven_bit_is_accepted(addr):
    assert validate("pru_io_blocks/i2c_block", {"sclBit": "0", "sdaBit": "1", "addr7": addr, "rw": "write"}) == []


# ---------------------------------------------------------------- 16-bit counters

STEPPER = {"stepPin": "5", "dirPin": "6", "numSteps": 1000, "maxSpeedSps": 1000, "clkHz": 200000000}
QUAD = {"aBit": "0", "bBit": "1", "zBit": "2", "sampleCycles": 10}


@pytest.mark.parametrize("steps", [0, 65536, 100000, 1.5])
def test_stepper_num_steps_outside_16_bits_is_refused(steps):
    errs = validate("pru_io_blocks/stepper_block", dict(STEPPER, numSteps=steps))
    assert [e["field"] for e in errs] == ["numSteps"], errs


@pytest.mark.parametrize("steps", [1, 1000, 65535])
def test_stepper_num_steps_within_16_bits_is_accepted(steps):
    assert validate("pru_io_blocks/stepper_block", dict(STEPPER, numSteps=steps)) == []


@pytest.mark.parametrize("cycles", [0, 65536, 70000, 2.5])
def test_quadrature_sample_cycles_outside_16_bits_is_refused(cycles):
    errs = validate("pru_io_blocks/quadrature_block", dict(QUAD, sampleCycles=cycles))
    assert [e["field"] for e in errs] == ["sampleCycles"], errs


@pytest.mark.parametrize("cycles", [1, 10, 65535])
def test_quadrature_sample_cycles_within_16_bits_is_accepted(cycles):
    assert validate("pru_io_blocks/quadrature_block", dict(QUAD, sampleCycles=cycles)) == []
