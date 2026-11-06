"""Register allocator: wide secondary outputs.

  - A multi-output macro's output2 and up, and the consumers wired to them, get a lo/hi register pair when the port
    is wider than 4 bytes (output1 already did).

The node driver runs the shipped allocator on hand-built instances.
"""

import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOC = os.path.join(ROOT, ".metadata", "sysconfig", ".meta", "pru_blocks", "common",
                     "register_allocation", "pru_register_allocator.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

DRIVER = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const ALLOC = process.argv[2], CASE = process.argv[3];
const modules = {};
const sb = { system: { modules }, exports: {}, console };
vm.createContext(sb);
vm.runInContext(fs.readFileSync(ALLOC, "utf8"), sb, { filename: "pru_register_allocator.js" });
const alloc = sb.exports;
const run = (mods) => { Object.keys(modules).forEach(k => delete modules[k]); Object.assign(modules, mods); alloc.allocatePruRegisters();
  const instrs = alloc.getPruRegisterAllocationSummary().pruInstructions.filter(x => typeof x === "string");
  return { failed: alloc.getAllocationFailures().length, instrs: instrs, used: instrs.join(" ") }; };

if (CASE === "wide-extra-output") {
  const W = { $name: "W", opCode: "WIDE", multiOutputMacroArgs: true, numOfOutputPorts: 2, numOfInputPorts: 0, numOfConstants: 0,
              noOfCycles: 1, output1Size: "4", output2Size: "8" };
  const C = { $name: "C", opCode: "ADD", numOfInputPorts: 1, numOfConstants: 0, noOfCycles: 1, output1Size: "4",
              input1: [{ inst: W, port: "output2" }] };
  W.output1 = []; W.output2 = [{ inst: C, port: "input1" }];
  const r = run({ "/f": { $instances: [W, C] } });
  assert.strictEqual(r.failed, 0);
  // W: output1 R0 (no consumer, released), output2 = R1/R2; C reads that pair
  assert.strictEqual(r.instrs[0], "WIDE R0, R1, R2", r.instrs[0]);
  assert.strictEqual(r.instrs[1], "ADD R0, R1 , R2", r.instrs[1]);
} else { throw new Error("unknown case " + CASE); }
console.log("DRIVER OK " + CASE);
"""


def _case(tmp_path, case):
    driver = tmp_path / ("driver_%s.js" % case)
    driver.write_text(DRIVER, encoding="utf-8")
    res = subprocess.run(["node", str(driver), ALLOC, case], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0 and ("DRIVER OK " + case) in res.stdout, \
        "node driver case %r failed:\n%s\n%s" % (case, res.stdout, res.stderr)


def test_wide_secondary_output_gets_a_register_pair(tmp_path):
    _case(tmp_path, "wide-extra-output")
