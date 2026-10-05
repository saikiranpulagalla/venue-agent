import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "release_candidate.py"
spec = importlib.util.spec_from_file_location("release_candidate", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def test_empty_gemini_env_line_is_not_a_secret():
    text = "GEMINI_API_KEY=\nGEMINI_MODEL=gemini-3.8-flash\n"
    pattern = dict(module.SECRET_PATTERNS)["nonempty_gemini_env"]
    assert pattern.search(text) is None


def test_nonempty_gemini_env_line_is_detected():
    text = "GEMINI_API_KEY=actual-secret-value\n"
    pattern = dict(module.SECRET_PATTERNS)["nonempty_gemini_env"]
    assert pattern.search(text) is not None
