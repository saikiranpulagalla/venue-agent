from pathlib import Path
import tempfile

from app.domain.models import ComparisonState, VenueProfile, WorkflowState
from app.pptx.mutations import apply_scale_text
from app.repair.candidates import generate_candidates
from app.repair.simulation import simulate_candidate
from app.repair.verifier import verify_output
from app.security.intake import sha256_path, validate_pptx
from app.venue.analyzer import VenueAnalyzer
from app.venue.reference import PublicBDMReference


import pytest

pytestmark = pytest.mark.resource
def analyzer(project_root):
    return VenueAnalyzer(PublicBDMReference(project_root / "reference" / "PUBLIC_BDM_REFERENCE_V1.json"))


def test_full_vertical_slice_small_text(project_root):
    src = project_root / "backend/tests/fixtures/small_text.pptx"
    validate_pptx(src)
    source_sha = sha256_path(src)
    venue = VenueProfile(active_image_height_m=1.8, farthest_viewer_distance_m=10.8, measurement_basis="MEASURED")
    a = analyzer(project_root)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        baseline = a.analyze(src, venue, td / "baseline")
        assert baseline.summary.below_target_elements >= 1
        candidates = generate_candidates(baseline)
        assert candidates
        simulated = [simulate_candidate(src, baseline, c, a) for c in candidates]
        safe = [c for c in simulated if c.safe]
        assert safe, [c.model_dump() for c in simulated]
        chosen = safe[-1]
        issue = next(r for r in baseline.results if r.element.element_id == chosen.issue_element_id)
        output = td / "output.pptx"
        apply_scale_text(src, output, issue.element.slide_index, issue.element.shape_id, chosen.parameters["scale"])
        report = verify_output(src, output, baseline, chosen.issue_element_id, a, td / "verify")
        assert sha256_path(src) == source_sha
        assert report.final_state == WorkflowState.VERIFIED, report.model_dump()


def test_image_only_is_never_green(project_root):
    src = project_root / "backend/tests/fixtures/image_only.pptx"
    a = analyzer(project_root)
    venue = VenueProfile(active_image_height_m=1.8, farthest_viewer_distance_m=10.8)
    with tempfile.TemporaryDirectory() as td:
        result = a.analyze(src, venue, Path(td))
    assert result.summary.total_detected_text_elements == 0
    assert result.summary.meets_target_elements == 0
