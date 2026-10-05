from pathlib import Path
import tempfile

from pptx import Presentation
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.util import Inches, Pt

from app.domain.models import MutationCapability
from app.pptx.parser import parse_pptx_shapes


def _run(box, text: str, size: int = 18, font: str = "DejaVu Sans"):
    box.text_frame.clear()
    r = box.text_frame.paragraphs[0].add_run()
    r.text = text
    r.font.size = Pt(size)
    r.font.name = font


def test_text_to_fit_shape_is_review_only():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "autofit.pptx"
        prs = Presentation(); slide = prs.slides.add_slide(prs.slide_layouts[6])
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1))
        box.text_frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        _run(box, "Autofit text")
        prs.save(path)
        rec = parse_pptx_shapes(path)[0]
    assert rec.mutation_capability == MutationCapability.REVIEW_ONLY
    assert any(n.startswith("automatic_text_fitting") for n in rec.notes)


def test_manual_prominent_top_text_is_conservatively_recognized_as_title():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "manual-title.pptx"
        prs = Presentation(); slide = prs.slides.add_slide(prs.slide_layouts[6])
        box = slide.shapes.add_textbox(Inches(1), Inches(0.3), Inches(8), Inches(0.8))
        box.text_frame.auto_size = MSO_AUTO_SIZE.NONE
        _run(box, "Quarterly Results", size=30)
        prs.save(path)
        rec = parse_pptx_shapes(path)[0]
    assert rec.role == "TITLE"


def test_table_text_is_surfaced_as_unsupported_visible_content():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "table.pptx"
        prs = Presentation(); slide = prs.slides.add_slide(prs.slide_layouts[6])
        table = slide.shapes.add_table(1, 1, Inches(1), Inches(1), Inches(4), Inches(1)).table
        table.cell(0, 0).text = "Visible table text"
        prs.save(path)
        recs = parse_pptx_shapes(path)
    assert len(recs) == 1
    assert recs[0].force_not_analyzable is True
    assert recs[0].unsupported_visible_content is True
    assert "unsupported_visible_table_text" in recs[0].notes


def test_structural_text_element_count_is_bounded():
    from app.pptx.parser import MAX_STRUCTURAL_TEXT_ELEMENTS, ParserLimitError
    import pytest
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "too-many-textboxes.pptx"
        prs = Presentation(); slide = prs.slides.add_slide(prs.slide_layouts[6])
        for i in range(MAX_STRUCTURAL_TEXT_ELEMENTS + 1):
            box = slide.shapes.add_textbox(Inches(0.1), Inches(0.1), Inches(1), Inches(0.2))
            box.text_frame.auto_size = MSO_AUTO_SIZE.NONE
            _run(box, f"x{i}", size=8)
        prs.save(path)
        with pytest.raises(ParserLimitError, match="structural_text_element_limit_exceeded"):
            parse_pptx_shapes(path)
