from pathlib import Path
import tempfile
from app.domain.models import ComparisonState, VenueProfile
from app.repair.candidates import generate_candidates
from app.venue.analyzer import VenueAnalyzer
from app.venue.reference import PublicBDMReference


import pytest

pytestmark = pytest.mark.resource
def _a(root): return VenueAnalyzer(PublicBDMReference(root/'reference/PUBLIC_BDM_REFERENCE_V1.json'))

def test_safe_deck_generates_no_repair_at_moderate_ratio(project_root):
    with tempfile.TemporaryDirectory() as td:
        result=_a(project_root).analyze(project_root/'backend/tests/fixtures/safe_large.pptx',VenueProfile(active_image_height_m=1.8,farthest_viewer_distance_m=7.2),Path(td))
    assert result.summary.below_target_elements==0
    assert generate_candidates(result)==[]

def test_outside_reference_never_becomes_pass(project_root):
    with tempfile.TemporaryDirectory() as td:
        result=_a(project_root).analyze(project_root/'backend/tests/fixtures/safe_large.pptx',VenueProfile(active_image_height_m=1.0,farthest_viewer_distance_m=11.0),Path(td))
    assert result.results
    assert all(r.state==ComparisonState.OUTSIDE_REFERENCE_RANGE for r in result.results)
