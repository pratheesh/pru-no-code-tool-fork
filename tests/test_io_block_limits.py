"""Option limits and cross-instance checks for the I/O blocks.

- i2c addr7 must be a 7-bit address.
- stepper numSteps and quadrature sampleCycles are loaded into 16-bit registers.
- encoder_speed, capture and sdfm expose only options their macros implement.
- the AM261x palette does not list the ICSSG-only xfr2psi block.
- validateXfr2psiGlobal reports a value live across a transfer only when it was allocated in R1:R5.
- validateXfr2spinGlobal does not accept try_acquire as protection for a cross-core write.

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




# ---------------------------------------------------------------- options the macros implement

def test_encoder_speed_offers_only_the_iep_velocity_method():
    assert config_options("encoder_speed_block", "velocityMethod") == ["iep"]


def test_capture_offers_only_trigger_sources_the_macro_implements():
    assert config_options("capture_block", "triggerSource") == ["iep", "gpio"]


def test_sdfm_has_no_oversampling_option_the_macro_ignores():
    assert config_options("sdfm_block", "osr") is None


# ---------------------------------------------------------------- AM261x palette

def palette(soc, core):
    return {m["module"] for m in node("scripts/dump_soc_palette.js",
            f".metadata/sysconfig/.meta/soc/pru_blocks_{soc}.syscfg.js", core)}


@pytest.mark.parametrize("core", ["pru0", "pru1", "rtu_pru0"])
def test_am261x_palette_omits_icssg_only_xfr2psi(core):
    modules = palette("am261x", f"icss_m0_{core}")
    assert "/pru_blocks/pru_io_blocks/sdfm_block" in modules
    assert "/pru_blocks/pru_io_blocks/xfr2psi_block" not in modules


def test_am64x_palette_keeps_xfr2psi():
    assert "/pru_blocks/pru_io_blocks/xfr2psi_block" in palette("am64x", "icss_g0_pru0")


# ---------------------------------------------------------------- xfr2psi live-across

PSI = "/pru_blocks/pru_io_blocks/xfr2psi_block"


def psi_spec(alloc):
    return {
        "validator": "validateXfr2psiGlobal",
        "modules": {
            "/pru_blocks/data_handling/arithmetic_block": [
                {"$name": "PROD", "output1": [{"inst": "CONS"}]}, {"$name": "CONS"}],
            PSI: [{"$name": "XFR", "checkReady": True}],
        },
        "order": ["PROD", "XFR", "CONS"],
        "allocations": {"PROD": alloc},
    }


def run_global(spec):
    return node("scripts/static_global_runner.js", json.dumps(spec))


@pytest.mark.parametrize("alloc", ["R1", "R3", "R5", "R4.w2"])
def test_value_allocated_in_psi_window_across_xfr2psi_is_refused(alloc):
    errs = run_global(psi_spec(alloc))
    assert len(errs) == 1 and alloc.split(".")[0] in errs[0], errs


@pytest.mark.parametrize("alloc", ["R6", "R10", "R0", "-", "R7 (loop)"])
def test_value_allocated_outside_psi_window_across_xfr2psi_is_accepted(alloc):
    assert run_global(psi_spec(alloc)) == []


# ---------------------------------------------------------------- xfr2spin cross-core guard

SPIN = "/pru_blocks/pru_io_blocks/xfr2spin_block"
WRITER = {"module": "/test/writer_block", "label": "Writer", "crossCoreField": "otherCoreWrites",
          "variableField": "variable"}


def spin_spec(first_op):
    return {
        "validator": "validateXfr2spinGlobal",
        "writers": [WRITER],
        "modules": {
            "/test/writer_block": [{"$name": "W", "otherCoreWrites": True, "variable": "shared"}],
            SPIN: [
                {"$name": "L1", "operation": first_op, "lockId": 3, "protectsVariable": "shared"},
                {"$name": "L2", "operation": "release", "lockId": 3, "protectsVariable": "shared"},
            ],
        },
    }


def test_acquire_release_guards_a_cross_core_write():
    assert run_global(spin_spec("acquire")) == []


def test_try_acquire_does_not_guard_a_cross_core_write():
    errs = run_global(spin_spec("try_acquire"))
    assert len(errs) == 1 and "try_acquire" in errs[0] and "'W'" in errs[0], errs
