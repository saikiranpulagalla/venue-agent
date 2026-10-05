from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "frontend" / "static" / "index.html"


def main() -> None:
    node = shutil.which("node")
    if not node:
        raise SystemExit("node is unavailable; frontend JavaScript syntax was not checked")
    text = HTML.read_text(encoding="utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", text, flags=re.S | re.I)
    if len(scripts) != 1:
        raise SystemExit(f"expected exactly one inline script, found {len(scripts)}")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(scripts[0])
        path = Path(f.name)
    try:
        p = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        if p.returncode != 0:
            raise SystemExit(p.stderr or p.stdout or "frontend JS syntax check failed")
    finally:
        path.unlink(missing_ok=True)
    print("FRONTEND JS SYNTAX PASS")


if __name__ == "__main__":
    main()
