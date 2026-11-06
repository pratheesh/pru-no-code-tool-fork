"""scripts/block_validate_runner.js reads the instance from --inst-file.

The flag used to fall through to the positional arguments, so `--inst-file f.json` was taken as the
block path (or as the instance JSON) and the file was never read.
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "block_validate_runner.js"
BLOCK = "program_control/conditional_block"
INST = {"$name": "If_Else_0", "numOfInputPorts": 2,
        "input1": ["a"], "input2": ["b"], "T": ["x"], "F": []}


def _run(args):
    return subprocess.run(["node", str(RUNNER)] + args, capture_output=True, text=True,
                          cwd=str(ROOT), timeout=30)


def _inst_file(tmp_path):
    f = tmp_path / "inst.json"
    f.write_text(json.dumps(INST), encoding="utf-8")
    return str(f)


def test_inst_file_after_block_path_is_validated(tmp_path):
    proc = _run([BLOCK, "--inst-file", _inst_file(tmp_path)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout.strip())
    assert [e for e in data["errors"] if "f_next" in e["message"]], data


def test_inst_file_before_block_path_is_validated(tmp_path):
    proc = _run(["--inst-file", _inst_file(tmp_path), BLOCK])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout.strip())
    assert [e for e in data["errors"] if "f_next" in e["message"]], data


def test_inst_file_matches_inline_json(tmp_path):
    inline = _run([BLOCK, json.dumps(INST)])
    from_file = _run([BLOCK, "--inst-file", _inst_file(tmp_path)])
    assert inline.returncode == 0 and from_file.returncode == 0
    assert json.loads(from_file.stdout) == json.loads(inline.stdout)


def test_unreadable_inst_file_is_reported(tmp_path):
    proc = _run([BLOCK, "--inst-file", str(tmp_path / "missing.json")])
    assert proc.returncode == 2
    assert "inst file" in proc.stderr
