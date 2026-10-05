from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


def request(path: str, method: str = "GET", headers: dict[str, str] | None = None):
    req = urllib.request.Request(BASE + path, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8")
        return resp.status, body, dict(resp.headers)


proc = subprocess.Popen(
    [
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", str(PORT), "--workers", "1", "--no-access-log",
    ],
    cwd=BACKEND,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    text=True,
    start_new_session=True,
)
try:
    deadline = time.time() + 15
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            status, body, _ = request("/health")
            if status == 200:
                break
        except Exception as exc:
            last_error = exc
            time.sleep(0.25)
    else:
        raise RuntimeError(f"server did not become healthy: {last_error}")

    _, readiness_raw, _ = request("/health/readiness")
    readiness = json.loads(readiness_raw)
    if readiness.get("status") != "ready":
        raise RuntimeError(f"readiness degraded: {readiness}")
    if readiness.get("required_replica_count") != 1:
        raise RuntimeError("single-replica contract missing")

    _, deep_raw, _ = request("/health/deep-readiness")
    deep = json.loads(deep_raw)
    if deep.get("status") != "ready" or not deep.get("renderer_version"):
        raise RuntimeError(f"deep readiness failed: {deep}")

    _, homepage, _ = request("/")
    if "Design for the room, not the laptop." not in homepage:
        raise RuntimeError("judge homepage content missing")

    _, demo_raw, headers = request("/api/demo", method="POST")
    demo = json.loads(demo_raw)
    if demo.get("state") != "UPLOADED" or not demo.get("session_id") or not demo.get("session_token"):
        raise RuntimeError(f"demo session failed: {demo}")
    lower_headers = {k.casefold(): v for k, v in headers.items()}
    if lower_headers.get("cache-control") != "no-store":
        raise RuntimeError("API no-store header missing")

    auth = {"X-Venue-Token": demo["session_token"]}
    _, status_raw, _ = request(f"/api/sessions/{demo['session_id']}", headers=auth)
    status_body = json.loads(status_raw)
    if status_body.get("session_id") != demo["session_id"]:
        raise RuntimeError("capability-authenticated status failed")

    request(f"/api/sessions/{demo['session_id']}", method="DELETE", headers=auth)

    print("HTTP SMOKE PASS")
    print(json.dumps({
        "readiness": readiness,
        "deep_readiness": deep,
        "demo_state": demo.get("state"),
        "capability_status": "PASS",
    }, indent=2))
finally:
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)
