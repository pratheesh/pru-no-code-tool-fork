"""IO-Link master macro on the TI pru-simulator's ENDAT peripheral model.

The macro is expanded with scripts/sim/emit_block_asm.js, assembled with clpru and run on the
pru-simulator core it was generated for (pru0 or pru1), against the simulator's peripheral
interface registers (GPCFG, TXCFG/RXCFG, CHn_CFG0, R30 FIFO push, R31 status).  The simulator
checkout comes from PRU_SIMULATOR_PATH; the tests skip without it or without clpru.

What is checked is what the peripheral model defines: which core's registers the macro
programs, the bits that leave the transmit channel, and the bytes the receive channel hands
back.  Bus levels on a physical C/Q line, transceiver delays and the ENDAT receiver's behaviour
on silicon are not covered.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "sim"))

try:
    import elf_cosim
    elf_cosim.find_clpru()
    SIM_DIR = elf_cosim.find_pru_simulator()
    HAVE_TOOLS = True
except (RuntimeError, ImportError):
    HAVE_TOOLS = False

pytestmark = pytest.mark.skipif(not HAVE_TOOLS, reason="clpru or pru-simulator (PRU_SIMULATOR_PATH) missing")

BLOCK, SUBDIR = "iolink_master_block", "application_specific"

# Only the register definitions: the text of getMacro("") before the .macro line.
DEFS_JS = r"""const {loadBlockModule}=require('./scripts/dual_oracle_harness');
const m=loadBlockModule('iolink_master_block','application_specific');
const t=m.getMacro('','m_iolink_master_pru'+process.argv[1]);
process.stdout.write(t.split(/^m_iolink\S+ \.macro.*$/m)[0]);"""

DEFAULT_ARGS = dict(RES="R10", STATUS="R11", DATA_IN_LO="R2", DATA_IN_HI="R3", DIR_PIN="2", WAKE_PIN="3",
                    CHANNEL="0", CLK_DIV="832", TX_BYTES="1", RX_BYTES="0", TURN_BITS="5",
                    WAKE_LPS="6400", PREAMBLE="0", RX_CLK_DIV="103")


class Run:
    """Result of one simulator run of the expanded macro."""

    def __init__(self, sim, core, steps, halted):
        self.sim, self.core, self.steps, self.halted = sim, core, steps, halted
        self.perif = {name: sim._perif[name] for name in ("pru0", "pru1")}
        self.regs = list(sim.registers(core))

    def channel(self, core=None, index=0):
        return self.perif[core or self.core].channels[index]


def run_macro(core, lo=0xA5, hi=0, max_steps=400000, device=None, **args):
    """Expand and build the macro for ``core``, run it, return a Run."""
    n = core[-1]
    call = dict(DEFAULT_ARGS, **args)
    spec = dict(block=BLOCK, subdir=SUBDIR, macroCall="m_iolink_master_pru" + n, macroArgs=call,
                prelude=["ldi32 R2, %d" % lo, "ldi32 R3, %d" % hi], postlude=["halt"])
    with tempfile.TemporaryDirectory(prefix="iolink_") as wd:
        asm = elf_cosim.expand_block_asm(spec, wd)
        defs = subprocess.check_output(["node", "-e", DEFS_JS, n], text=True, cwd=ROOT)
        elf = elf_cosim.compile_asm(defs + "\n" + asm, wd, "iolink")
        with elf_cosim._sim_namespace(SIM_DIR):
            from simulator import Simulator
            sim = Simulator(os.path.join(SIM_DIR, "config", "memory_am243x.cfg"))
            errors = sim.load_elf(core, Path(elf).read_bytes())
            assert not errors, errors
            pru = sim.cores[core]
            if device is not None:
                device(sim, core)
            steps = 0
            while steps < max_steps and not pru.halted:
                sim.step(core, 1)
                steps += 1
            return Run(sim, core, steps, pru.halted)


def drive_windows(channel):
    """(start_ns, end_ns) of every interval in which the transmit channel drove its output."""
    edges = channel.tx_out_en_transitions
    wins, start = [], None
    for t, v in edges:
        if v and start is None:
            start = t
        elif not v and start is not None:
            wins.append((start, t))
            start = None
    return wins


@pytest.mark.parametrize("core", ["pru0", "pru1"])
def test_transmit_uses_the_registers_of_the_core_it_runs_on(core):
    """A PRU1 instance must configure and drive PRU1's peripheral, not PRU0's."""
    other = "pru1" if core == "pru0" else "pru0"
    r = run_macro(core)
    assert r.halted
    assert drive_windows(r.channel(core)), "no frame left the transmit channel of " + core
    assert not drive_windows(r.channel(other)), other + "'s channel was driven by a macro built for " + core


def expected_char(byte):
    """Bits of one IO-Link UART character in transmit order: start, data LSB first, even parity, stop."""
    data = [(byte >> i) & 1 for i in range(8)]
    return [0] + data + [sum(data) & 1] + [1]


def tx_characters(channel, bits=11):
    """Decode every transmit window into ``bits`` bits, sampled in the middle of each bit cell."""
    period = channel.tx_clock_period_ns()
    chars = []
    for start, _end in drive_windows(channel):
        chars.append([channel.tx_line_at(start + (k + 0.5) * period) for k in range(bits)])
    return chars


@pytest.mark.parametrize("core", ["pru0", "pru1"])
def test_every_payload_byte_is_sent_as_an_11_bit_even_parity_character(core):
    """Start, 8 data bits LSB first, even parity, stop: one character per byte, none dropped."""
    payload = [0xA5, 0x01, 0xFF, 0x80, 0x7F, 0x00, 0x3C, 0xC3]
    lo = int.from_bytes(bytes(payload[:4]), "little")
    hi = int.from_bytes(bytes(payload[4:]), "little")
    r = run_macro(core, lo=lo, hi=hi, TX_BYTES="8")
    assert r.halted
    got = tx_characters(r.channel())
    assert got == [expected_char(b) for b in payload], got


@pytest.mark.parametrize("count", [1, 2, 5])
def test_tx_bytes_selects_how_many_characters_are_sent(count):
    payload = [0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0]
    r = run_macro("pru0", lo=int.from_bytes(bytes(payload[:4]), "little"),
                  hi=int.from_bytes(bytes(payload[4:]), "little"), TX_BYTES=str(count))
    assert tx_characters(r.channel()) == [expected_char(b) for b in payload[:count]]


@pytest.mark.parametrize("channel", [1, 2])
def test_characters_leave_the_selected_endat_channel(channel):
    r = run_macro("pru0", lo=0x5A, CHANNEL=str(channel))
    assert tx_characters(r.channel(index=channel)) == [expected_char(0x5A)]
    assert not drive_windows(r.channel(index=0))


class Device:
    """Drives a channel's RX input like an IO-Link device at COM3 (230.4 kbit/s).

    The line idles high.  ``gap_bits`` bit times after the master's last character has been
    sent, the device answers with one 11-bit character per response byte, back to back.
    ``bad_parity`` / ``bad_stop`` list the response indexes sent with a wrong parity / stop bit.
    """

    def __init__(self, response, gap_bits=6, baud=230400, bad_parity=(), bad_stop=(), channel=0):
        bits = []
        for i, byte in enumerate(response):
            ch = expected_char(byte)
            if i in bad_parity:
                ch[9] ^= 1
            if i in bad_stop:
                ch[10] = 0
            bits += ch
        self.bits, self.gap_bits, self.bit_ns, self.index = bits, gap_bits, 1e9 / baud, channel

    def __call__(self, sim, core):
        channel = sim._perif[core].channels[self.index]
        channel.rx_line_source = lambda t: self.level(channel, t)

    def level(self, channel, t):
        edges = channel.tx_out_en_transitions
        if not edges or edges[-1][1]:       # the master has not finished sending
            return 1
        start = edges[-1][0] + self.gap_bits * self.bit_ns
        k = int((t - start) // self.bit_ns) if t >= start else -1
        return self.bits[k] if 0 <= k < len(self.bits) else 1


def receive(core="pru0", response=(0xA5,), device_kw=None, **args):
    kw = dict(device_kw or {})
    r = run_macro(core, RX_BYTES=str(len(response)), device=Device(response, **kw), **args)
    assert r.halted, "the macro did not finish (receive loop waiting for samples)"
    return r.regs[10], r.regs[11]


@pytest.mark.parametrize("core", ["pru0", "pru1"])
@pytest.mark.parametrize("response", [(0xA5,), (0x00,), (0xFF,), (0x81, 0x7E), (0x12, 0x34, 0x56, 0x78)])
def test_response_bytes_are_returned_with_the_first_byte_most_significant(core, response):
    res, status = receive(core, response)
    assert res == int.from_bytes(bytes(response), "big")
    assert status == 0


@pytest.mark.parametrize("channel", [1, 2])
def test_response_is_read_from_the_selected_channel(channel):
    res, status = receive(response=(0xC3, 0x3C), CHANNEL=str(channel), device_kw=dict(channel=channel))
    assert (res, status) == (0xC33C, 0)


def test_wrong_parity_is_reported_in_status_bit_0():
    res, status = receive(response=(0x11, 0x22), device_kw=dict(bad_parity=(1,)))
    assert res == 0x1122 and status == 1


def test_wrong_stop_bit_is_reported_in_status_bit_1():
    res, status = receive(response=(0x11, 0x22), device_kw=dict(bad_stop=(0,)))
    assert res == 0x1122 and status == 2


def test_response_with_two_gap_bits_between_the_master_and_the_device_is_still_read():
    res, status = receive(response=(0x3C,), device_kw=dict(gap_bits=5))
    assert (res, status) == (0x3C, 0)


# --- SysConfig generation: the generated call matches the macro and the firmware assembles ---
TI_TOOLS = Path(os.environ.get("TOOLS_PATH", (os.environ.get("SystemDrive", "C:") + "/ti") if os.name == "nt" else Path.home() / "ti"))
SYSCONFIG_NODE = Path(os.environ.get("SYSCONFIG_NODE", str(TI_TOOLS / "sysconfig_1.28.0" / "nodejs" / "node.exe")))
SYSCONFIG_CLI = Path(os.environ.get("SYSCONFIG_CLI", str(TI_TOOLS / "sysconfig_1.28.0" / "dist" / "cli.js")))
PRODUCT = ROOT / ".metadata" / "product.json"

SYSCFG = """\
// @cliArgs --device AM243x_ALX_beta --package ALX --part ALX --context icss_g0_{core} --product PRU_NO_CODE_TOOL@01.00.01
const common = scripting.addModule("/pru_blocks/common/pru_blocks_static_module", {{}}, false);
const lc = scripting.addModule("/pru_blocks/data_handling/load_constant_block", {{}}, false);
const m = scripting.addModule("/pru_blocks/application_specific/iolink_master_block", {{}}, false);
const lo = lc.addInstance(); lo.$name = "LO"; lo.constant1 = 0x11223344;
const hi = lc.addInstance(); hi.$name = "HI"; hi.constant1 = 0x55667788;
const inst = m.addInstance();
inst.$name = "BLOCK_0";
scripting.connect(lo, "output1", inst, "input1");
scripting.connect(hi, "output1", inst, "input2");
scripting.connect(lo, "next", hi, "prev");
scripting.connect(hi, "next", inst, "prev");
"""

MAIN_ASM = """\
    .retain
    .retainrefs
    .global     main
    .ref        sysconfig_generated_start
    .sect       ".text:main"
main:
    jmp     sysconfig_generated_start
    .global sysconfig_generated_end
sysconfig_generated_end:
    halt
"""


@pytest.mark.skipif(not (SYSCONFIG_NODE.exists() and SYSCONFIG_CLI.exists()), reason="SysConfig CLI missing")
@pytest.mark.parametrize("core", ["pru0", "pru1"])
def test_generated_design_calls_the_macro_of_its_core_and_assembles(core, tmp_path):
    script = tmp_path / "iolink.syscfg"
    script.write_text(SYSCFG.format(core=core), encoding="utf-8")
    out = tmp_path / "out"
    r = subprocess.run(
        [str(SYSCONFIG_NODE), str(SYSCONFIG_CLI), "--product", str(PRODUCT), "--device", "AM243x_ALX_beta",
         "--package", "ALX", "--part", "ALX", "--script", str(script), "--context", "icss_g0_" + core,
         "--output", str(out)], cwd=ROOT, text=True, capture_output=True)
    assert r.returncode == 0 and "Generating Code" in r.stdout, r.stdout + r.stderr
    assert "Macro arity mismatch" not in r.stdout + r.stderr
    asm = (out / "pru_syscfg.asm").read_text(encoding="utf-8", errors="replace")
    inc = (out / "pru_syscfg.inc").read_text(encoding="utf-8", errors="replace")
    name = "m_iolink_master_" + core
    calls = [ln.split(";")[0].split() for ln in asm.splitlines() if ln.split(";")[0].split()[:1] == [name]]
    assert len(calls) == 1, calls
    args = [a.strip() for a in " ".join(calls[0][1:]).split(",")]
    header = next(ln for ln in inc.splitlines() if ln.startswith(name + " .macro"))
    assert len(args) == len(header.split(".macro")[1].split(",")) == 14, (args, header)
    (out / "main.asm").write_text(MAIN_ASM, encoding="utf-8")
    (out / "link.cmd").write_text(elf_cosim.LINKER_CMD, encoding="utf-8")
    for cmd in (["--silicon_version=4", "-c", "-I.", "pru_syscfg.asm", "main.asm"],
                ["--silicon_version=4", "-z", "pru_syscfg.obj", "main.obj", "link.cmd", "--entry_point=main",
                 "-o", "main.out"]):
        c = subprocess.run([elf_cosim.find_clpru()] + cmd, cwd=out, text=True, capture_output=True)
        assert c.returncode == 0 and "warning" not in (c.stdout + c.stderr).lower(), c.stdout + c.stderr


# --- turnaround: TURN_BITS bit periods of the selected COM rate between the last TX bit and RX enable ---
COM_CLK_DIV = {"COM1": (4800, 39999, 4999), "COM2": (38400, 4999, 624), "COM3": (230400, 832, 103)}


@pytest.mark.parametrize("com", sorted(COM_CLK_DIV))
def test_turnaround_lasts_the_configured_number_of_bit_periods(com):
    baud, tx_div, rx_div = COM_CLK_DIV[com]
    arms = []

    def watch(sim, core):
        perif = sim._perif[core]
        channel = perif.channels[0]
        orig = channel.arm_rx

        def arm(enabled):
            if enabled and not channel.rx_en:
                arms.append(perif._now_ns)
            orig(enabled)
        channel.arm_rx = arm

    turn_bits = 5
    r = run_macro("pru0", max_steps=3000000, device=watch, CLK_DIV=str(tx_div), RX_CLK_DIV=str(rx_div),
                  RX_BYTES="1", TURN_BITS=str(turn_bits))
    assert r.halted and arms
    tx_end = drive_windows(r.channel())[-1][1]
    bit_ns = 1e9 / baud
    turnaround = arms[0] - tx_end
    # The wait is TURN_BITS bit periods; the register setup before the receiver is armed adds a
    # fraction of a microsecond, and the delay loop rounds to whole iterations.
    assert turn_bits * bit_ns * 0.99 <= turnaround <= turn_bits * bit_ns * 1.01 + 1500, (turnaround, turn_bits * bit_ns)
