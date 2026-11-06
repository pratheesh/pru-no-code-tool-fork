"""Register allocator: macro scratch registers TEMP_REG3 / TEMP_REG4.

  - TEMP_REG3 / TEMP_REG4 are R26 / R27. A design whose macros use them must not get those registers for
    values (a macro would overwrite a live value, or its own result); a design that uses neither keeps them.

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
const ldi = (n, i) => ({ $name: n + i, opCode: "LDI", numOfInputPorts: 0, numOfConstants: 1, constant1: i, noOfCycles: 1, output1Size: "4" });
const fill = (k) => { const a = []; for (let i = 0; i < k; i++) a.push(ldi("K", i)); return a; };
const scratchModule = (regs, insts) => ({ $instances: insts, getMacro: () => ["SCR .macro a", "    ldi " + regs.join(", ") + ", 1", "    .endm"].join("\n") });
const scratchBlock = () => ({ $name: "S0", opCode: "SCR", numOfInputPorts: 0, numOfConstants: 0, noOfCycles: 1, output1Size: "4" });
const run = (mods) => { Object.keys(modules).forEach(k => delete modules[k]); Object.assign(modules, mods); alloc.allocatePruRegisters();
  const instrs = alloc.getPruRegisterAllocationSummary().pruInstructions.filter(x => typeof x === "string");
  return { failed: alloc.getAllocationFailures().length, instrs: instrs, used: instrs.join(" ") }; };
const uses = (text, reg) => new RegExp(String.raw`\b${reg}\b`).test(text);

if (CASE === "pool-without-scratch") {
  const r = run({ "/f": { $instances: fill(28) } });
  assert.strictEqual(r.failed, 0, "28 values fit the full pool when no macro uses R26/R27");
  assert(uses(r.used, "R26") && uses(r.used, "R27"));
} else if (CASE === "scratch-both") {
  const r = run({ "/f": { $instances: fill(27) }, "/s": scratchModule(["TEMP_REG3", "TEMP_REG4"], [scratchBlock()]) });
  assert.strictEqual(r.failed, 2, "28 values do not fit once R26 and R27 are held back: " + JSON.stringify(r));
  assert(!uses(r.used, "R26") && !uses(r.used, "R27"), "R26/R27 handed out: " + r.used);
  assert.deepStrictEqual(Array.from(alloc.getScratchRegisters()), ["TEMP_REG3", "TEMP_REG4"]);
} else if (CASE === "scratch-reg3-only") {
  const r = run({ "/f": { $instances: fill(27) }, "/s": scratchModule(["TEMP_REG3"], [scratchBlock()]) });
  assert.strictEqual(r.failed, 1, "only R26 is held back: " + JSON.stringify(r));
  assert(!uses(r.used, "R26") && uses(r.used, "R27"), r.used);
} else { throw new Error("unknown case " + CASE); }
console.log("DRIVER OK " + CASE);
"""


def _case(tmp_path, case):
    driver = tmp_path / ("driver_%s.js" % case)
    driver.write_text(DRIVER, encoding="utf-8")
    res = subprocess.run(["node", str(driver), ALLOC, case], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0 and ("DRIVER OK " + case) in res.stdout, \
        "node driver case %r failed:\n%s\n%s" % (case, res.stdout, res.stderr)


def test_pool_keeps_r26_r27_when_no_macro_uses_them(tmp_path):
    _case(tmp_path, "pool-without-scratch")


def test_scratch_registers_are_not_allocated_to_values(tmp_path):
    _case(tmp_path, "scratch-both")


def test_only_the_scratch_register_in_use_is_held_back(tmp_path):
    _case(tmp_path, "scratch-reg3-only")
