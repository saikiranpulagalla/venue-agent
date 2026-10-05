from pathlib import Path
import argparse
import hashlib
import os
import shutil
import tempfile
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
RESOURCE_TIMEOUT_S = 120
_RUN_COUNTER = 0

CRITICAL_ARTIFACTS = [
    ROOT / "backend/tests/fixtures/safe_large.pptx",
    ROOT / "backend/tests/fixtures/small_text.pptx",
    ROOT / "backend/tests/fixtures/mixed_sizes.pptx",
    ROOT / "backend/tests/fixtures/image_only.pptx",
    ROOT / "demo/venue-agent-demo.pptx",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_artifacts() -> dict[str, str]:
    missing = [str(p) for p in CRITICAL_ARTIFACTS if not p.exists()]
    if missing:
        raise SystemExit(f"Missing critical release artifacts: {missing}")
    return {str(p.relative_to(ROOT)): sha256(p) for p in CRITICAL_ARTIFACTS}


def assert_artifacts_unchanged(before: dict[str, str]) -> None:
    after = snapshot_artifacts()
    changed = [name for name, digest in before.items() if after.get(name) != digest]
    if changed:
        raise SystemExit(f"P0 release-gate failure: tracked source/demo artifacts changed during tests: {changed}")


def run(args, *, timeout: int = RESOURCE_TIMEOUT_S):
    global _RUN_COUNTER
    _RUN_COUNTER += 1
    print("+", " ".join(args), flush=True)
    session_root = Path(tempfile.gettempdir()) / f"venue-agent-gate-{os.getpid()}-{_RUN_COUNTER}"
    shutil.rmtree(session_root, ignore_errors=True)
    env = os.environ.copy()
    env["VENUE_SESSION_ROOT"] = str(session_root)
    try:
        p = subprocess.run(args, cwd=BACKEND, timeout=timeout, env=env)
    except subprocess.TimeoutExpired as e:
        raise SystemExit(f"Test subprocess timed out after {timeout}s: {' '.join(args)}") from e
    finally:
        shutil.rmtree(session_root, ignore_errors=True)
    if p.returncode != 0:
        raise SystemExit(p.returncode)


parser = argparse.ArgumentParser()
parser.add_argument("--full", action="store_true", help="run every LibreOffice/resource regression in fresh pytest processes")
args = parser.parse_args()

artifact_snapshot = snapshot_artifacts()
try:
    run([sys.executable, "-m", "pytest", "-q", "-m", "not resource"], timeout=60)
    # Mandatory kill-gate: actual PPTX -> render -> mutate copy -> reopen -> verify.
    run([sys.executable, "-m", "pytest", "-q", "-m", "resource", "tests/integration/test_vertical_slice.py"])
    # Mandatory agent-depth gate: two distinct issues -> constrained multi-repair plan -> exact approval -> fresh verification.
    run([sys.executable, "-m", "pytest", "-q", "-m", "resource", "tests/integration/test_multi_plan_demo.py"])
    # Mandatory identity gate: repeated text in distinct shapes must stay bound
    # to the correct rendered geometry rather than cross-mapping by text alone.
    run([sys.executable, "-m", "pytest", "-q", "-m", "resource", "tests/integration/test_duplicate_text_mapping.py"])
    # Mandatory v12 safety-oracle gate: visual wrap/overlap cannot verify,
    # missing fonts are review-only, and unsupported table text remains visible.
    run([sys.executable, "-m", "pytest", "-q", "-m", "resource", "tests/integration/test_v12_safety_roundtrip.py"])
    run([sys.executable, "-m", "pytest", "-q", "-m", "resource", "tests/integration/test_unknown_state_api.py"])
    # Mandatory v14 runtime gate: platform readiness performs a real render.
    run([sys.executable, "-m", "pytest", "-q", "-m", "resource", "tests/integration/test_v14_deep_readiness.py"])

    if args.full:
        resource_nodes = [
            "tests/integration/test_api_flow.py",
            "tests/integration/test_holdout_contract.py",
            "tests/integration/test_no_action_and_range.py",
            "tests/adversarial/test_prompt_injection_is_data.py",
            # Stateful TestClient + LibreOffice cases are isolated one-per-process.
            # This keeps global ephemeral-session state from leaking between cases
            # and avoids interpreter-finalization stalls seen after several render
            # cycles in the same pytest process on constrained CI runners.
            "tests/adversarial/test_api_guards.py::test_stale_plan_hash_cannot_execute",
            "tests/adversarial/test_api_guards.py::test_double_apply_is_rejected",
            "tests/adversarial/test_api_guards.py::test_plan_evidence_tamper_invalidates_approval",
            "tests/adversarial/test_api_guards.py::test_reject_is_bound_to_current_plan_and_executes_nothing",
            "tests/adversarial/test_api_guards.py::test_reanalysis_explicitly_invalidates_old_plan_and_approval",
            "tests/adversarial/test_api_guards.py::test_source_tamper_is_blocked_before_any_mutation",
        ]
        for node in resource_nodes:
            run([sys.executable, "-m", "pytest", "-q", "-m", "resource", node])
finally:
    assert_artifacts_unchanged(artifact_snapshot)

print("RELEASE GATE PASS")
