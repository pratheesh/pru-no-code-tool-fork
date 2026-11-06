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

DEFAULT_ARGS = dict(RES="R10", DATA_IN_LO="R2", DATA_IN_HI="R3", DIR_PIN="2", WAKE_PIN="3",
                    CHANNEL="0", CLK_DIV="832", TX_BYTES="1", RX_BYTES="0", TURN_BITS="5",
                    WAKE_LPS="6400", PREAMBLE="0")


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
