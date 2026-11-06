"""The GPO/GPI shared-pad check says so when the device carries no pad data.

getDevicePadSharing returned an empty set for a device without usable pin data, which the validator
read as "no pad is shared": a design with a GPO and a GPI claim then got neither a collision warning nor
the "could not be checked" warning. Runs the real validateBidiPinCollisionsGlobal.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / ".metadata" / "sysconfig" / ".meta" / "pru_blocks" / "common" / "pru_blocks_static_module.syscfg.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

DRIVER = r"""
const fs = require("fs"), vm = require("vm");
const [STATIC, DEVICE] = [process.argv[2], JSON.parse(process.argv[3])];
function stub() { return new Proxy(function () { return stub(); }, {
  get(_t, p) { if (p === Symbol.toPrimitive || p === "toString" || p === "valueOf") return () => ""; if (p === Symbol.iterator || p === "then") return undefined; return stub(); },
  apply() { return stub(); } }); }
const claimsOf = (dir) => ({ $instances: [{ $name: dir + "_0" }],
  moduleStatic: { getPinUsage: () => [{ pin: 0, dir }] } });
const system = {
  deviceData: DEVICE,
  modules: { "/pru_blocks/pru_io_blocks/pru_gpo_block": claimsOf("GPO"), "/pru_blocks/pru_io_blocks/pru_gpi_block": claimsOf("GPI") },
  getScript: (p) => (p === "/common" ? { getSelfSysCfgCoreName: () => "icss_g0_pru0" }
    : new Proxy(stub(), { get: (t, k) => (k === "findDeviceInputConflicts" ? () => [] : t[k]) })),
};
const sb = { system, exports: {}, module: { exports: {} }, console: { log() {}, error() {}, warn() {} }, require: () => stub() };
sb.global = sb;
vm.createContext(sb);
vm.runInContext(fs.readFileSync(STATIC, "utf8"), sb, { filename: STATIC });
const warnings = [];
sb.exports.validateBidiPinCollisionsGlobal({ $name: "design" }, { logWarning: (m) => warnings.push(m), logError: () => {}, logInfo: () => {} });
console.log(JSON.stringify(warnings));
"""


def _warnings(tmp_path, device):
    drv = tmp_path / "driver.js"
    drv.write_text(DRIVER, encoding="utf-8")
    proc = subprocess.run(["node", str(drv), str(STATIC), json.dumps(device)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _pad(sig_list):
    return {"pinModeInfo": [{"signalName": s} for s in sig_list]}


def test_device_without_pad_data_warns_that_sharing_was_not_checked(tmp_path):
    for device in ({}, {"pinCommonInfos": {}}):
        warns = _warnings(tmp_path, device)
        assert [w for w in warns if "could not be checked" in w], (device, warns)


def test_shared_pad_still_reports_the_collision(tmp_path):
    device = {"pinCommonInfos": {"A": _pad(["PRG0_PRU0_GPO0", "PRG0_PRU0_GPI0"])}}
    warns = _warnings(tmp_path, device)
    assert [w for w in warns if "will contend on this pad" in w], warns
    assert not [w for w in warns if "could not be checked" in w], warns


def test_available_data_without_a_shared_pad_stays_silent(tmp_path):
    device = {"pinCommonInfos": {"A": _pad(["PRG0_PRU0_GPO1"]), "B": _pad(["PRG0_PRU0_GPI2"])}}
    assert _warnings(tmp_path, device) == []
