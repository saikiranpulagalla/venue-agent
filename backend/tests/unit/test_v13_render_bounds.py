from pathlib import Path
from types import SimpleNamespace

import pytest

from app.pptx import parser
from app.pptx.parser import ParserLimitError
from app.pptx import renderer
from app.pptx.renderer import RenderError


class _FakeDoc:
    def __init__(self, pages):
        self.pages = pages
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.closed = True

    def __iter__(self):
        return iter(self.pages)


def test_rendered_span_budget_fails_closed_and_closes_document(monkeypatch, tmp_path: Path):
    spans = [
        {"text": f"x{i}", "bbox": (0.0, 0.0, 1.0, 1.0), "font": "DejaVu Sans"}
        for i in range(parser.MAX_RENDERED_TEXT_SPANS + 1)
    ]
    page = SimpleNamespace(
        rect=SimpleNamespace(width=100.0, height=100.0),
        get_text=lambda mode: {"blocks": [{"type": 0, "lines": [{"spans": spans}]}]},
    )
    doc = _FakeDoc([page])
    monkeypatch.setattr(parser.fitz, "open", lambda _: doc)

    with pytest.raises(ParserLimitError, match="rendered_text_span_limit_exceeded"):
        parser.rendered_spans(tmp_path / "fake.pdf")
    assert doc.closed is True


def test_rendered_pdf_size_is_bounded_and_oversize_artifact_removed(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.pptx"
    source.write_bytes(b"pptx")
    out_dir = tmp_path / "render"

    monkeypatch.setattr(renderer.shutil, "which", lambda _: "/usr/bin/fake-soffice")

    class FakeProc:
        returncode = 0
        pid = 999999
        def __init__(self, cmd, **kwargs):
            self.cmd = cmd
        def communicate(self, timeout=None):
            outdir = Path(self.cmd[self.cmd.index("--outdir") + 1])
            outdir.mkdir(parents=True, exist_ok=True)
            pdf = outdir / "source.pdf"
            with pdf.open("wb") as f:
                f.truncate(renderer.MAX_RENDERED_PDF_BYTES + 1)
            return "", ""
        def poll(self):
            return 0

    monkeypatch.setattr(renderer.subprocess, "Popen", FakeProc)

    with pytest.raises(RenderError, match="Rendered PDF size outside processing limits"):
        renderer.render_pptx_to_pdf(source, out_dir)
    assert not (out_dir / "source.pdf").exists()


def test_failed_render_cannot_reuse_stale_pdf(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.pptx"
    source.write_bytes(b"pptx")
    out_dir = tmp_path / "render"
    out_dir.mkdir()
    stale = out_dir / "source.pdf"
    stale.write_bytes(b"%PDF-stale")
    monkeypatch.setattr(renderer.shutil, "which", lambda _: "/usr/bin/fake-soffice")

    class FailingProc:
        returncode = 1
        pid = 999999
        def __init__(self, cmd, **kwargs):
            self.cmd = cmd
        def communicate(self, timeout=None):
            return "", ""
        def poll(self):
            return 1

    monkeypatch.setattr(renderer.subprocess, "Popen", FailingProc)
    with pytest.raises(RenderError, match="LibreOffice render failed"):
        renderer.render_pptx_to_pdf(source, out_dir)
    assert not stale.exists()
