from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
REPORTS = ROOT / "validation" / "reports"
REQUIRED_DOCS = [
    ROOT / "README.md",
    ROOT / "docs" / "PRODUCT_CONTRACT.md",
    ROOT / "docs" / "PRIOR_RESEARCH_DISCLOSURE.md",
    ROOT / "docs" / "CLAIMS_AND_LIMITATIONS.md",
    ROOT / "docs" / "TESTING.md",
    ROOT / "docs" / "JUDGING_MAP.md",
    ROOT / "docs" / "DEMO_SCRIPT.md",
    ROOT / "docs" / "AI_TOOL_DISCLOSURE.md",
    ROOT / "validation" / "FAILURE_AUDIT_CLOSURE_V14.md",
    ROOT / "THIRD_PARTY_NOTICES.md",
]

SECRET_PATTERNS = [
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_-]{30,}")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("nonempty_gemini_env", re.compile(r"^[ \t]*GEMINI_API_KEY[ \t]*=[ \t]*[^#\s].*$", re.M)),
]
EXCLUDED_PARTS = {".git", "__pycache__", ".pytest_cache"}


def run(args: list[str], *, cwd: Path = ROOT, timeout: int = 180, capture: bool = False) -> subprocess.CompletedProcess:
    print("+", " ".join(args), flush=True)
    return subprocess.run(
        args,
        cwd=cwd,
        timeout=timeout,
        text=True,
        capture_output=capture,
        check=True,
        env=os.environ.copy(),
    )


def source_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in EXCLUDED_PARTS for part in rel.parts):
            continue
        if rel.parts[:2] == ("validation", "reports"):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        files.append(path)
    return sorted(files, key=lambda p: p.relative_to(ROOT).as_posix())


def source_manifest() -> tuple[str, list[dict[str, object]]]:
    rows: list[dict[str, object]] = []
    digest = hashlib.sha256()
    for path in source_files():
        rel = path.relative_to(ROOT).as_posix()
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        size = len(data)
        rows.append({"path": rel, "sha256": sha, "size": size})
        digest.update(rel.encode("utf-8") + b"\0" + sha.encode("ascii") + b"\0" + str(size).encode("ascii") + b"\n")
    return digest.hexdigest(), rows


def optional_git_metadata() -> dict[str, str] | None:
    if not (ROOT / ".git").exists():
        return None
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=10).strip()
        branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True, timeout=10).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True, timeout=10).strip()
    except Exception:
        return None
    return {"commit": commit, "branch": branch, "clean": str(not bool(dirty)).lower()}


def scan_source_secrets() -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path in source_files():
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append({"file": str(path.relative_to(ROOT)), "pattern": name})
    return findings


def collect_test_count() -> int:
    p = run([sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=BACKEND, timeout=60, capture=True)
    m = re.search(r"(\d+) tests collected", (p.stdout or "") + "\n" + (p.stderr or ""))
    if not m:
        raise SystemExit("Could not determine collected test count")
    return int(m.group(1))


def libreoffice_version() -> str | None:
    for exe in ("libreoffice", "soffice"):
        try:
            p = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if p.returncode == 0:
            return (p.stdout or p.stderr).strip()
    return None


def main() -> None:
    missing_docs = [str(p.relative_to(ROOT)) for p in REQUIRED_DOCS if not p.exists()]
    if missing_docs:
        raise SystemExit(f"Missing required release docs: {missing_docs}")

    secrets = scan_source_secrets()
    if secrets:
        raise SystemExit(f"Potential source secrets detected: {json.dumps(secrets, indent=2)}")

    before_digest, manifest = source_manifest()
    test_count = collect_test_count()
    checks: dict[str, str] = {}

    run([sys.executable, "scripts/release_gate.py"], cwd=ROOT, timeout=240)
    checks["mandatory_release_gate"] = "PASS"
    run([sys.executable, "scripts/http_smoke.py"], cwd=ROOT, timeout=120)
    checks["http_smoke"] = "PASS"
    run([sys.executable, "scripts/check_frontend_js.py"], cwd=ROOT, timeout=30)
    checks["frontend_js_syntax"] = "PASS"

    after_digest, after_manifest = source_manifest()
    if before_digest != after_digest or manifest != after_manifest:
        raise SystemExit("Release candidate changed source-tree identity during validation")
    checks["source_tree_unchanged_before_and_after"] = "PASS"

    report = {
        "report_version": 2,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_tree_sha256": before_digest,
        "source_file_count": len(manifest),
        "source_manifest": manifest,
        "git_metadata": optional_git_metadata(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "libreoffice_version": libreoffice_version(),
        "collected_test_count": test_count,
        "checks": checks,
        "hard_claims": {
            "false_verified_observed_in_implemented_fixtures": 0,
            "unauthorized_mutations_observed": 0,
            "source_mutations_observed": 0,
            "prompt_injection_tool_escapes_observed": 0,
        },
        "known_unverified": [
            "public hosted deployment",
            "Docker runtime build in this execution environment",
            "live Gemini planner with a real credential",
            "real user-research evidence",
            "browser E2E in this administrator-restricted Chromium environment",
            "multi-replica operation (competition V1 explicitly requires one replica)",
        ],
    }

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"release-tree-{before_digest[:12]}.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"RELEASE CANDIDATE PASS: {out}")


if __name__ == "__main__":
    main()
