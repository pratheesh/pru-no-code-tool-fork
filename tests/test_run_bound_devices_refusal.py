"""run_bound_devices reports a missing TI pru-simulator as REFUSED (exit 2), not a traceback."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.sim import run_bound_devices  # noqa: E402
from scripts.sim.elf_cosim import SimulatorNotFound  # noqa: E402


def test_missing_simulator_is_refused(monkeypatch, capsys):
    monkeypatch.setattr(run_bound_devices.device_bindings, "load_manifest",
                        lambda path: {"bindings": [{"kind": "ssi"}]})

    def missing():
        raise SimulatorNotFound("no pru-simulator checkout found (set PRU_SIMULATOR_PATH)")

    monkeypatch.setattr(run_bound_devices, "find_pru_simulator", missing)
    try:
        rc = run_bound_devices.main(["fw.out", "manifest.json"])
    except SimulatorNotFound:
        # raised here (not skipped by the repository hook) because the runner must handle it itself
        raise AssertionError("SimulatorNotFound escaped main()") from None
    assert rc == run_bound_devices.EXIT_REFUSED
    assert "REFUSED: no pru-simulator checkout found" in capsys.readouterr().err
