"""A timed-out command in the TI-run wrapper is stopped together with its children on POSIX."""
import os
import signal
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "sim"))
import gui_run  # noqa: E402


def test_posix_kill_signals_the_process_group(monkeypatch):
    calls = []
    fake_os = types.SimpleNamespace(
        name="posix", getpgid=lambda pid: pid + 1000,
        killpg=lambda pgid, sig: calls.append((pgid, sig)))
    monkeypatch.setattr(gui_run, "os", fake_os)
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    proc = types.SimpleNamespace(pid=77, kill=lambda: calls.append("kill"),
                                 wait=lambda timeout=None: 0)
    gui_run.kill_process_tree(proc)
    assert calls == [(1077, 9)]


def test_posix_kill_tolerates_an_already_gone_group(monkeypatch):
    def gone(pgid, sig):
        raise ProcessLookupError
    fake_os = types.SimpleNamespace(name="posix", getpgid=lambda pid: pid, killpg=gone)
    monkeypatch.setattr(gui_run, "os", fake_os)
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    proc = types.SimpleNamespace(pid=5, kill=lambda: None, wait=lambda timeout=None: 0)
    gui_run.kill_process_tree(proc)


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX")
def test_timeout_kills_grandchildren(tmp_path):
    pidfile = tmp_path / "child.pid"
    script = f"sleep 60 & echo $! > {pidfile}; wait"
    with pytest.raises(gui_run.Refused):
        gui_run.run_logged(["sh", "-c", script], str(tmp_path), dict(os.environ),
                           str(tmp_path / "log.txt"), timeout=2)
    child = int(pidfile.read_text())
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail("the grandchild survived the timeout")
