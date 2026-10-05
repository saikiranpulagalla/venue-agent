from __future__ import annotations

from dataclasses import dataclass

from app.domain.models import AnalysisResult, ComparisonState, MappingConfidence


ANALYZABLE_STATES = {ComparisonState.MEETS_TARGET, ComparisonState.BELOW_TARGET}
BBOX_COORD_TOLERANCE = 0.0025
OVERLAP_AREA_TOLERANCE = 0.0001


@dataclass(frozen=True)
class TransitionAudit:
    analysis_universe_preserved: bool
    layout_safe: bool
    reasons: list[str]


def _by_id(analysis: AnalysisResult):
    return {r.element.element_id: r for r in analysis.results}


def _bbox_close(a, b, tol: float = BBOX_COORD_TOLERANCE) -> bool:
    if a is None or b is None:
        return a is b
    return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))


def _overflow(box, container) -> float:
    if box is None or container is None:
        return float("inf")
    x0, y0, x1, y1 = box
    cx0, cy0, cx1, cy1 = container
    return (
        max(0.0, cx0 - x0)
        + max(0.0, cy0 - y0)
        + max(0.0, x1 - cx1)
        + max(0.0, y1 - cy1)
    )


def _intersection_area(a, b) -> float:
    if a is None or b is None:
        return 0.0
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def audit_analysis_transition(
    before: AnalysisResult,
    after: AnalysisResult,
    target_element_ids: list[str] | set[str],
) -> TransitionAudit:
    """Fail-closed audit for a proposed mutation transition.

    This is intentionally independent of the planner. It compares the baseline
    and fresh rendered analyses element-by-element. A mutation may improve its
    target only if the previously analyzable universe remains analyzable and the
    rendered layout does not introduce wrapping, overflow, overlap, or movement
    of unrelated text.
    """
    targets = set(target_element_ids)
    reasons: list[str] = []
    universe_reasons: list[str] = []
    layout_reasons: list[str] = []
    before_by = _by_id(before)
    after_by = _by_id(after)

    if set(before_by) != set(after_by):
        missing = sorted(set(before_by) - set(after_by))
        added = sorted(set(after_by) - set(before_by))
        if missing:
            universe_reasons.append("analysis_elements_missing_after:" + ",".join(missing))
        if added:
            universe_reasons.append("analysis_elements_added_after:" + ",".join(added))

    if after.summary.total_detected_text_elements != before.summary.total_detected_text_elements:
        universe_reasons.append("analysis_element_count_changed")
    if after.summary.not_analyzed_elements > before.summary.not_analyzed_elements:
        universe_reasons.append("not_analyzed_count_increased")
    if after.summary.outside_reference_elements != before.summary.outside_reference_elements:
        universe_reasons.append("outside_reference_count_changed")
    if after.summary.unsupported_visible_content_elements != before.summary.unsupported_visible_content_elements:
        universe_reasons.append("unsupported_visible_content_count_changed")
    if after.summary.boundary_review_elements > before.summary.boundary_review_elements:
        universe_reasons.append("boundary_review_count_increased")

    for element_id, b in before_by.items():
        a = after_by.get(element_id)
        if a is None:
            continue
        if b.state in ANALYZABLE_STATES and a.state not in ANALYZABLE_STATES:
            universe_reasons.append(f"previously_analyzable_became_unknown:{element_id}:{a.state}")
        if b.element.analysis_capability.value == "ANALYZABLE" and a.element.analysis_capability.value != "ANALYZABLE":
            universe_reasons.append(f"mapping_analyzability_regressed:{element_id}")
        if b.element.mapping_confidence in {MappingConfidence.EXACT, MappingConfidence.STRONG} and a.element.mapping_confidence not in {MappingConfidence.EXACT, MappingConfidence.STRONG}:
            universe_reasons.append(f"mapping_confidence_regressed:{element_id}")

    # Layout audit only trusts elements that exist in both analyses.
    for element_id, b in before_by.items():
        a = after_by.get(element_id)
        if a is None:
            continue
        be, ae = b.element, a.element
        if be.structural_bbox is not None and ae.structural_bbox is not None and not _bbox_close(be.structural_bbox, ae.structural_bbox, tol=1e-6):
            layout_reasons.append(f"structural_bbox_changed:{element_id}")

        if element_id in targets:
            if be.rendered_bbox is None or ae.rendered_bbox is None:
                layout_reasons.append(f"target_rendered_bbox_missing:{element_id}")
                continue
            if be.rendered_line_count != ae.rendered_line_count:
                layout_reasons.append(
                    f"target_line_count_changed:{element_id}:{be.rendered_line_count}->{ae.rendered_line_count}"
                )
            before_overflow = _overflow(be.rendered_bbox, be.structural_bbox)
            after_overflow = _overflow(ae.rendered_bbox, ae.structural_bbox)
            if after_overflow > before_overflow + BBOX_COORD_TOLERANCE:
                layout_reasons.append(f"target_text_overflow_increased:{element_id}")
        else:
            # Scaling one shape must not move/reflow unrelated rendered text.
            if be.rendered_bbox is not None and ae.rendered_bbox is not None:
                if not _bbox_close(be.rendered_bbox, ae.rendered_bbox):
                    layout_reasons.append(f"unrelated_rendered_bbox_changed:{element_id}")
                if be.rendered_line_count != ae.rendered_line_count:
                    layout_reasons.append(f"unrelated_line_count_changed:{element_id}")

    # Detect any newly-created or materially increased intersection between a
    # mutated target and another rendered text element on the same slide.
    for target_id in targets:
        b = before_by.get(target_id)
        a = after_by.get(target_id)
        if b is None or a is None or a.element.rendered_bbox is None:
            continue
        for other_id, other_before in before_by.items():
            if other_id == target_id or other_before.element.slide_index != b.element.slide_index:
                continue
            other_after = after_by.get(other_id)
            if other_after is None or other_after.element.rendered_bbox is None:
                continue
            before_overlap = _intersection_area(b.element.rendered_bbox, other_before.element.rendered_bbox)
            after_overlap = _intersection_area(a.element.rendered_bbox, other_after.element.rendered_bbox)
            if after_overlap > before_overlap + OVERLAP_AREA_TOLERANCE:
                layout_reasons.append(f"new_or_increased_text_overlap:{target_id}:{other_id}")

    reasons.extend(sorted(set(universe_reasons)))
    reasons.extend(sorted(set(layout_reasons)))
    return TransitionAudit(
        analysis_universe_preserved=not universe_reasons,
        layout_safe=not layout_reasons,
        reasons=reasons,
    )
