from pathlib import Path
import subprocess
import sys
import pytest


@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def ensure_fixtures(project_root):
    """Generate project-owned fixtures only when a clean checkout is missing them.

    Re-generating tracked PPTX zip packages on every test run changes internal
    timestamps and dirties the worktree even when semantic content is identical.
    Release tests must preserve source identity, so existing fixtures are reused.
    """
    expected = [
        project_root / "backend/tests/fixtures/safe_large.pptx",
        project_root / "backend/tests/fixtures/small_text.pptx",
        project_root / "backend/tests/fixtures/mixed_sizes.pptx",
        project_root / "backend/tests/fixtures/image_only.pptx",
    ]
    if not all(p.exists() for p in expected):
        script = project_root / "scripts" / "make_fixtures.py"
        subprocess.run([sys.executable, str(script)], check=True)
