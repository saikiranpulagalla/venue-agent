from pathlib import Path
import os
import stat
import subprocess
import threading
import time

import pytest

from app.pptx import renderer
from app.pptx.renderer import RenderError


def test_global_renderer_budget_rejects_excess_cross_session_work(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.pptx"
    source.write_bytes(b"pptx")
    monkeypatch.setattr(renderer.shutil, "which", lambda _: "/fake/soffice")
    monkeypatch.setattr(renderer, "_RENDER_SEMAPHORE", threading.BoundedSemaphore(1))
    monkeypatch.setattr(renderer, "RENDER_QUEUE_TIMEOUT_S", 0.05)
    entered = threading.Event()
    release = threading.Event()

    class BlockingProc:
        returncode = 0
        pid = 999998
        def __init__(self, cmd, **kwargs):
            self.cmd = cmd
        def communicate(self, timeout=None):
            entered.set()
            assert release.wait(timeout=2)
            outdir = Path(self.cmd[self.cmd.index("--outdir") + 1])
            (outdir / "source.pdf").write_bytes(b"%PDF-1.4\n")
            return "", ""
        def poll(self):
            return 0

    monkeypatch.setattr(renderer.subprocess, "Popen", BlockingProc)
    result = []

    def first():
        result.append(renderer.render_pptx_to_pdf(source, tmp_path / "one"))

    t = threading.Thread(target=first)
    t.start()
    assert entered.wait(timeout=1)
    with pytest.raises(RenderError, match="Renderer capacity is busy"):
        renderer.render_pptx_to_pdf(source, tmp_path / "two")
    release.set()
    t.join(timeout=2)
    assert not t.is_alive()
    assert result and result[0].exists()


def test_render_timeout_terminates_descendant_process_group(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.pptx"
    source.write_bytes(b"pptx")
    script = tmp_path / "fake-soffice.sh"
    pid_file = tmp_path / "child.pid"
    script.write_text("#!/bin/sh\nsleep 60 &\necho $! > \"$VENUE_CHILD_PID_FILE\"\nwait\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("VENUE_CHILD_PID_FILE", str(pid_file))
    monkeypatch.setattr(renderer.shutil, "which", lambda _: str(script))
    monkeypatch.setattr(renderer, "_RENDER_SEMAPHORE", threading.BoundedSemaphore(1))

    with pytest.raises(RenderError, match="LibreOffice render timeout"):
        renderer.render_pptx_to_pdf(source, tmp_path / "render", timeout_s=1)

    deadline = time.monotonic() + 2.0
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert pid_file.exists()
    child_pid = int(pid_file.read_text().strip())

    # A killed child can briefly remain as a zombie until reaped; either absent
    # or zombie means it no longer consumes renderer CPU/RAM.
    deadline = time.monotonic() + 2.0
    state = ""
    while time.monotonic() < deadline:
        ps = subprocess.run(["ps", "-o", "stat=", "-p", str(child_pid)], capture_output=True, text=True)
        state = ps.stdout.strip()
        if not state or state.startswith("Z"):
            break
        time.sleep(0.05)
    assert not state or state.startswith("Z"), f"renderer child survived timeout with state {state!r}"
