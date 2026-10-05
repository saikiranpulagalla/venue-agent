from pathlib import Path

from app.domain.models import AnalysisCapability, MappingConfidence, MutationCapability
from app.pptx import parser
from app.pptx.parser import ShapeRecord


def _shape(shape_id: int, bbox: tuple[float, float, float, float]) -> ShapeRecord:
    return ShapeRecord(
        slide_index=0,
        shape_id=shape_id,
        text="Repeated label",
        normalized_text="repeated label",
        role="BODY",
        explicit_font_sizes_pt=[18.0],
        mutation_capability=MutationCapability.SAFE_MUTATION,
        notes=[],
        normalized_bbox=bbox,
    )


def _span(span_id: int, bbox: tuple[float, float, float, float], height: float) -> dict:
    return {
        "span_id": span_id,
        "slide_index": 0,
        "text": "Repeated label",
        "normalized_text": "repeated label",
        "bbox": bbox,
        "normalized_bbox": bbox,
        "height_percent": height,
    }


def test_duplicate_text_is_mapped_by_geometry_not_by_slide_text(monkeypatch):
    shapes = [
        _shape(10, (0.05, 0.10, 0.45, 0.30)),
        _shape(20, (0.55, 0.60, 0.95, 0.80)),
    ]
    spans = [
        _span(1, (0.10, 0.15, 0.40, 0.20), 1.2),
        _span(2, (0.60, 0.65, 0.90, 0.74), 2.8),
    ]
    monkeypatch.setattr(parser, "parse_pptx_shapes", lambda _: shapes)
    monkeypatch.setattr(parser, "rendered_spans", lambda _: spans)

    out = parser.map_elements(Path("source.pptx"), Path("render.pdf"))

    assert [e.rendered_height_percent for e in out] == [1.2, 2.8]
    assert all(e.mapping_confidence == MappingConfidence.EXACT for e in out)
    assert all(e.analysis_capability == AnalysisCapability.ANALYZABLE for e in out)
    assert all(e.mutation_capability == MutationCapability.SAFE_MUTATION for e in out)


def test_overlapping_duplicate_mapping_fails_closed(monkeypatch):
    shapes = [
        _shape(10, (0.05, 0.10, 0.95, 0.80)),
        _shape(20, (0.05, 0.10, 0.95, 0.80)),
    ]
    spans = [_span(1, (0.20, 0.30, 0.70, 0.36), 1.5)]
    monkeypatch.setattr(parser, "parse_pptx_shapes", lambda _: shapes)
    monkeypatch.setattr(parser, "rendered_spans", lambda _: spans)

    out = parser.map_elements(Path("source.pptx"), Path("render.pdf"))

    assert all(e.rendered_height_percent is None for e in out)
    assert all(e.mapping_confidence == MappingConfidence.AMBIGUOUS for e in out)
    assert all(e.analysis_capability == AnalysisCapability.NOT_ANALYZABLE for e in out)
    assert all(e.mutation_capability == MutationCapability.NO_MUTATION for e in out)
    assert all("ambiguous_rendered_span_ownership" in e.notes for e in out)


def test_partial_rendered_text_reconstruction_fails_closed(monkeypatch):
    shape = ShapeRecord(
        slide_index=0,
        shape_id=10,
        text="Alpha Beta Gamma",
        normalized_text="alpha beta gamma",
        role="BODY",
        explicit_font_sizes_pt=[18.0],
        mutation_capability=MutationCapability.SAFE_MUTATION,
        notes=[],
        normalized_bbox=(0.05, 0.10, 0.95, 0.30),
    )
    span = {
        "span_id": 1,
        "line_id": 1,
        "slide_index": 0,
        "text": "Alpha",
        "normalized_text": "alpha",
        "bbox": (0.10, 0.15, 0.30, 0.20),
        "normalized_bbox": (0.10, 0.15, 0.30, 0.20),
        "height_percent": 1.2,
    }
    monkeypatch.setattr(parser, "parse_pptx_shapes", lambda _: [shape])
    monkeypatch.setattr(parser, "rendered_spans", lambda _: [span])

    out = parser.map_elements(Path("source.pptx"), Path("render.pdf"))[0]

    assert out.mapping_confidence == MappingConfidence.PARTIAL
    assert out.analysis_capability == AnalysisCapability.NOT_ANALYZABLE
    assert out.mutation_capability == MutationCapability.NO_MUTATION
    assert out.rendered_height_percent is None
    assert "partial_rendered_text_reconstruction" in out.notes
