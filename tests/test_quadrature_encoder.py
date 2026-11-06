"""QuadratureIndexEncoder: forward motion has A lead B, and invalid parameters do not abort the run."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pru_device_models import buscosim  # noqa: E402
from pru_device_models.quadrature_index_encoder import QuadratureIndexEncoder  # noqa: E402


def _run(model, cycles):
    return [(c, model.tick(c, 0)) for c in range(cycles)]


def test_forward_motion_a_rises_before_b():
    m = QuadratureIndexEncoder(cpr=1000)
    m.set_motion([(10, 4, +1)])
    words = [w for _, w in _run(m, 60)]
    ab = []
    for w in words:
        s = (w & 1, (w >> 1) & 1)
        if not ab or ab[-1] != s:
            ab.append(s)
    # (A, B): 00 -> 10 -> 11 -> 01 -> 00
    assert ab[:5] == [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]


def test_reverse_motion_b_rises_before_a():
    m = QuadratureIndexEncoder(cpr=1000)
    m.set_motion([(10, 2, -1)])
    words = [w for _, w in _run(m, 40)]
    ab = []
    for w in words:
        s = (w & 1, (w >> 1) & 1)
        if not ab or ab[-1] != s:
            ab.append(s)
    assert ab[:3] == [(0, 0), (0, 1), (1, 1)]


def test_independent_decoder_counts_forward_positive():
    m = QuadratureIndexEncoder(cpr=1000)
    m.set_motion([(10, 6, +1)])
    dec = buscosim.decode_quadrature(_run(m, 100), 0, 1, 2)
    edges = [e for e in dec if e["kind"] == "edge"]
    assert [e["dir"] for e in edges] == [1] * 6


def test_zero_cpr_reports_fault_instead_of_raising():
    m = QuadratureIndexEncoder(cpr=0)
    m.set_motion([(10, 3, +1)])
    _run(m, 60)
    assert any("cpr" in f for f in m.faults())
