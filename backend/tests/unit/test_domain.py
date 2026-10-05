import pytest
from app.domain.models import VenueProfile, VerificationReport, WorkflowState


def test_viewing_ratio():
    v = VenueProfile(active_image_height_m=2, farthest_viewer_distance_m=8)
    assert v.viewing_ratio == 4


def test_verified_cannot_be_constructed_with_failed_invariant():
    with pytest.raises(ValueError):
        VerificationReport(
            output_sha256="x",
            source_sha256_unchanged=True,
            protected_text_unchanged=True,
            output_reopens=True,
            target_improved=False,
            final_state=WorkflowState.VERIFIED,
        )


def test_verified_requires_no_unknown_or_uncovered_content():
    with pytest.raises(ValueError):
        VerificationReport(
            output_sha256='x',
            source_sha256_unchanged=True,
            protected_text_unchanged=True,
            output_reopens=True,
            target_improved=True,
            analysis_universe_preserved=True,
            layout_safe=True,
            no_unknown_or_uncovered=False,
            final_state=WorkflowState.VERIFIED,
        )
