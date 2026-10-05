from app import main
from app.domain.models import (
    AnalysisCapability,
    AnalysisResult,
    AnalysisSummary,
    ComparisonState,
    ElementResult,
    MappingConfidence,
    MutationCapability,
    RepairCandidate,
    TextElement,
    VenueProfile,
)


def _analysis(source_sha: str) -> AnalysisResult:
    element = TextElement(
        element_id="s1-sh1",
        slide_index=0,
        shape_id=1,
        source_text="tiny",
        normalized_text="tiny",
        role="BODY",
        rendered_height_percent=1.0,
        analysis_capability=AnalysisCapability.ANALYZABLE,
        mutation_capability=MutationCapability.SAFE_MUTATION,
        mapping_confidence=MappingConfidence.EXACT,
    )
    return AnalysisResult(
        source_sha256=source_sha,
        reference_profile_id="r",
        venue=VenueProfile(active_image_height_m=2, farthest_viewer_distance_m=8, measurement_basis="MEASURED"),
        results=[ElementResult(element=element, state=ComparisonState.BELOW_TARGET, required_percent=2.0, actual_percent=1.0, deficit_ratio=2.0)],
        summary=AnalysisSummary(
            total_detected_text_elements=1,
            analyzed_elements=1,
            not_analyzed_elements=0,
            below_target_elements=1,
            meets_target_elements=0,
            outside_reference_elements=0,
        ),
    )


def test_candidate_simulation_budget_fails_closed_before_any_simulation(monkeypatch):
    content = b"not parsed because analyzer is mocked"
    import hashlib
    s = main.STORE.create("x.pptx", content, hashlib.sha256(content).hexdigest())
    try:
        analysis = _analysis(s.source_sha256)
        monkeypatch.setattr(main.ANALYZER, "analyze", lambda *_args, **_kwargs: analysis)
        too_many = [
            RepairCandidate(
                candidate_id=f"c{i}",
                issue_element_id="s1-sh1",
                operation="SCALE_TEXT",
                parameters={"scale": 1.1},
            )
            for i in range(main.MAX_CANDIDATE_SIMULATIONS + 1)
        ]
        monkeypatch.setattr(main, "generate_candidates", lambda _analysis: too_many)
        monkeypatch.setattr(main, "simulate_candidate", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not simulate beyond budget")))

        body = main.analyze(s.session_id, main.AnalyzeRequest(venue=analysis.venue))
        assert body["state"].value == "REVIEW_REQUIRED"
        assert body["candidates"] == []
        assert f"candidate_simulation_budget_exceeded:{main.MAX_CANDIDATE_SIMULATIONS}" in body["review_reasons"]
    finally:
        main.STORE.delete(s.session_id)


def test_requested_coverage_already_met_does_not_greenlight_remaining_issues():
    import hashlib
    from app.domain.models import RepairConstraints

    content = b"planner-state-only"
    s = main.STORE.create("x.pptx", content, hashlib.sha256(content).hexdigest())
    try:
        # 1/3 currently meets target, so a user target of 0.20 requires zero
        # mutations, but two known below-target issues still require review.
        elements = []
        for i, state in enumerate([ComparisonState.MEETS_TARGET, ComparisonState.BELOW_TARGET, ComparisonState.BELOW_TARGET], start=1):
            el = TextElement(
                element_id=f"s1-sh{i}", slide_index=0, shape_id=i,
                source_text=f"e{i}", normalized_text=f"e{i}", role="BODY",
                rendered_height_percent=2.5 if state == ComparisonState.MEETS_TARGET else 1.0,
                analysis_capability=AnalysisCapability.ANALYZABLE,
                mutation_capability=MutationCapability.SAFE_MUTATION,
                mapping_confidence=MappingConfidence.EXACT,
            )
            elements.append(ElementResult(
                element=el, state=state, required_percent=2.0,
                actual_percent=el.rendered_height_percent,
                deficit_ratio=(2.0 / el.rendered_height_percent) if state == ComparisonState.BELOW_TARGET else None,
            ))
        s.analysis = AnalysisResult(
            source_sha256=s.source_sha256,
            reference_profile_id="r",
            venue=VenueProfile(active_image_height_m=2, farthest_viewer_distance_m=8, measurement_basis="MEASURED"),
            results=elements,
            summary=AnalysisSummary(
                total_detected_text_elements=3,
                analyzed_elements=3,
                not_analyzed_elements=0,
                below_target_elements=2,
                meets_target_elements=1,
                outside_reference_elements=0,
            ),
        )
        s.candidates = []
        body = main.plan(
            s.session_id,
            main.PlanRequest(constraints=RepairConstraints(max_mutations=2, minimum_target_coverage=0.20)),
        )
        assert body["state"].value == "REVIEW_REQUIRED"
        assert body["decision"].selected_candidate_ids == []
        assert body["decision"].requires_human_review is True
        assert "below_target_issues_remain_unresolved" in body["review_reasons"]
    finally:
        main.STORE.delete(s.session_id)
