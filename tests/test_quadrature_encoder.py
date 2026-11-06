"""QuadratureIndexEncoder: forward motion has B lead A, and invalid parameters do not abort the run."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pru_device_models import buscosim  # noqa: E402
from pru_device_models.quadrature_index_encoder import QuadratureIndexEncoder  # noqa: E402


def _run(model, cycles):
    return [(c, model.tick(c, 0)) for c in range(cycles)]


def test_forward_motion_b_rises_before_a():
    m = QuadratureIndexEncoder(cpr=1000)
    m.set_motion([(10, 4, +1)])
    words = [w for _, w in _run(m, 60)]
    ab = []
    for w in words:
        s = (w & 1, (w >> 1) & 1)
        if not ab or ab[-1] != s:
            ab.append(s)
    # (A, B): 00 -> 01 -> 11 -> 10 -> 00
    assert ab[:5] == [(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]


def test_reverse_motion_a_rises_before_b():
    m = QuadratureIndexEncoder(cpr=1000)
    m.set_motion([(10, 2, -1)])
    words = [w for _, w in _run(m, 40)]
    ab = []
    for w in words:
        s = (w & 1, (w >> 1) & 1)
        if not ab or ab[-1] != s:
            ab.append(s)
    assert ab[:3] == [(0, 0), (1, 0), (1, 1)]


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


def _extract_transitions(words):
    seq = []
    for w in words:
        s = [w & 1, (w >> 1) & 1]
        if not seq or seq[-1] != s:
            seq.append(s)
    return seq


def test_cross_language_quadrature_phase_agreement():
    """Verify in-tool simulation model and Python model agree on phase sequence."""
    js_code = f"""
    const path = require('path');
    const fs = require('fs');
    const vm = require('vm');
    const simDir = path.join('{ROOT.as_posix()}', '.metadata', 'sysconfig', '.meta', 'pru_blocks', 'common', 'simulation');
    const scriptCache = {{}};
    function loadSim(p) {{
        const code = fs.readFileSync(p, 'utf8');
        const sandbox = {{
            console, Math, JSON, Number, Array, Object, Set, Error,
            system: {{ getScript: (sp) => scriptCache[path.basename(sp)] || loadSim(path.join(simDir, path.basename(sp))) }},
            exports: {{}}
        }};
        vm.createContext(sandbox);
        vm.runInContext(code, sandbox);
        return sandbox.exports;
    }}
    const models = loadSim(path.join(simDir, 'device_models.js'));
    function runJs(fwd) {{
        const enc = new models.QuadratureEncoder({{ aPin: 0, bPin: 1, zPin: -1, countsPerRev: 100, cyclesPerCount: 10, forward: fwd }});
        const words = [];
        for (let c = 0; c < 45; c++) {{
            const [, val] = enc.tick(c, 0);
            words.push(val);
        }}
        return words;
    }}
    console.log(JSON.stringify({{ fwd: runJs(true), rev: runJs(false) }}));
    """
    res = subprocess.run(["node", "-e", js_code], capture_output=True, text=True, check=True)
    js_data = json.loads(res.stdout)
    js_fwd_transitions = _extract_transitions(js_data["fwd"])
    js_rev_transitions = _extract_transitions(js_data["rev"])

    # Python forward
    m_fwd = QuadratureIndexEncoder(a_bit=0, b_bit=1, cpr=100)
    m_fwd.set_motion([(10, 4, +1)])
    py_fwd_words = [m_fwd.tick(c, 0) for c in range(45)]
    py_fwd_transitions = _extract_transitions(py_fwd_words)

    # Python reverse
    m_rev = QuadratureIndexEncoder(a_bit=0, b_bit=1, cpr=100)
    m_rev.set_motion([(10, 4, -1)])
    py_rev_words = [m_rev.tick(c, 0) for c in range(45)]
    py_rev_transitions = _extract_transitions(py_rev_words)

    # Both environments must produce identical Gray code phase transitions
    assert py_fwd_transitions == js_fwd_transitions == [[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]
    assert py_rev_transitions == js_rev_transitions == [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
