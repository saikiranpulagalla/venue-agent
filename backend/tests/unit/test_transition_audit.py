from app.domain.models import (
    AnalysisCapability, AnalysisResult, AnalysisSummary, ComparisonState,
    ElementResult, MappingConfidence, MutationCapability, TextElement, VenueProfile,
)
from app.repair.transition import audit_analysis_transition


def _element(eid, bbox, *, line_count=1, analyzable=True):
    return TextElement(
        element_id=eid,
        slide_index=0,
        shape_id=int(eid[-1]),
        source_text=eid,
        normalized_text=eid,
        role="BODY",
        rendered_height_percent=1.0 if analyzable else None,
        analysis_capability=AnalysisCapability.ANALYZABLE if analyzable else AnalysisCapability.NOT_ANALYZABLE,
        mutation_capability=MutationCapability.SAFE_MUTATION if analyzable else MutationCapability.NO_MUTATION,
        mapping_confidence=MappingConfidence.EXACT if analyzable else MappingConfidence.UNMAPPED,
        structural_bbox=(0.05, 0.05, 0.95, 0.95),
        rendered_bbox=bbox if analyzable else None,
        rendered_span_count=1 if analyzable else 0,
        rendered_line_count=line_count if analyzable else 0,
    )


def _analysis(target_bbox, other_bbox, *, other_analyzable=True, target_lines=1):
    t = ElementResult(element=_element("e1", target_bbox, line_count=target_lines), state=ComparisonState.BELOW_TARGET, required_percent=2, actual_percent=1)
    o_el = _element("e2", other_bbox, analyzable=other_analyzable)
    o = ElementResult(
        element=o_el,
        state=ComparisonState.MEETS_TARGET if other_analyzable else ComparisonState.NOT_ANALYZED,
        required_percent=2 if other_analyzable else None,
        actual_percent=2.5 if other_analyzable else None,
    )
    return AnalysisResult(
        source_sha256="x",
        reference_profile_id="r",
        venue=VenueProfile(active_image_height_m=2, farthest_viewer_distance_m=8, measurement_basis="MEASURED"),
        results=[t, o],
        summary=AnalysisSummary(
            total_detected_text_elements=2,
            analyzed_elements=2 if other_analyzable else 1,
            not_analyzed_elements=0 if other_analyzable else 1,
            below_target_elements=1,
            meets_target_elements=1 if other_analyzable else 0,
            outside_reference_elements=0,
        ),
    )


def test_new_text_overlap_is_a_layout_blocker():
    before = _analysis((0.10, 0.10, 0.30, 0.20), (0.10, 0.25, 0.30, 0.35))
    after = _analysis((0.10, 0.10, 0.30, 0.30), (0.10, 0.25, 0.30, 0.35))
    audit = audit_analysis_transition(before, after, ["e1"])
    assert audit.analysis_universe_preserved is True
    assert audit.layout_safe is False
    assert any(r.startswith("new_or_increased_text_overlap:e1:e2") for r in audit.reasons)


def test_previously_analyzable_content_cannot_disappear_to_make_coverage_look_better():
    before = _analysis((0.10, 0.10, 0.30, 0.20), (0.50, 0.10, 0.70, 0.20))
    after = _analysis((0.10, 0.10, 0.30, 0.20), None, other_analyzable=False)
    audit = audit_analysis_transition(before, after, ["e1"])
    assert audit.analysis_universe_preserved is False
    assert any("previously_analyzable_became_unknown:e2" in r for r in audit.reasons)


def test_target_wrap_change_is_a_layout_blocker():
    before = _analysis((0.10, 0.10, 0.30, 0.20), (0.50, 0.10, 0.70, 0.20), target_lines=1)
    after = _analysis((0.10, 0.10, 0.30, 0.22), (0.50, 0.10, 0.70, 0.20), target_lines=2)
    audit = audit_analysis_transition(before, after, ["e1"])
    assert audit.layout_safe is False
    assert any(r.startswith("target_line_count_changed:e1") for r in audit.reasons)
