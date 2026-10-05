import hashlib

from fastapi.testclient import TestClient

from app import main
from app.domain.models import AnalysisResult, AnalysisSummary, VenueProfile, WorkflowState


def test_plan_route_declares_the_same_capability_dependency_as_other_session_routes():
    route = next(route for route in main.app.routes if getattr(route, "path", None) == "/api/sessions/{sid}/plan")
    assert any(dep.call is main._require_session_capability for dep in route.dependant.dependencies)


def _analyzed_session():
    source = b"source"
    session = main.STORE.create("source.pptx", source, hashlib.sha256(source).hexdigest())
    token = session.take_issued_capability_token()
    session.analysis = AnalysisResult(
        source_sha256=session.source_sha256,
        reference_profile_id="test",
        venue=VenueProfile(active_image_height_m=2, farthest_viewer_distance_m=8),
        results=[],
        summary=AnalysisSummary(
            total_detected_text_elements=0,
            analyzed_elements=0,
            not_analyzed_elements=0,
            below_target_elements=0,
            meets_target_elements=0,
            boundary_review_elements=0,
            outside_reference_elements=0,
        ),
    )
    session.state = WorkflowState.ANALYZED
    return session, {"X-Venue-Token": token}


def test_plan_requires_capability_before_planner_or_state_change(monkeypatch):
    session, headers = _analyzed_session()

    def planner_must_not_run(*_args, **_kwargs):
        raise AssertionError("unauthorized request reached planner")

    monkeypatch.setattr(main.PLANNER, "choose", planner_must_not_run)
    with TestClient(main.app) as client:
        for bad_headers in ({}, {"X-Venue-Token": "wrong-token"}):
            response = client.post(f"/api/sessions/{session.session_id}/plan", headers=bad_headers, json={})
            assert response.status_code == 404
            assert session.state == WorkflowState.ANALYZED
            assert session.plan_hash is None
            assert session.approved_plan_hash is None
            assert session.output_path is None

        # No candidates are present, so the authorized route returns its normal
        # deterministic review result without ever invoking the planner.
        response = client.post(f"/api/sessions/{session.session_id}/plan", headers=headers, json={})
        assert response.status_code == 200
        assert response.json()["state"] == WorkflowState.REVIEW_REQUIRED
