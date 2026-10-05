from app.domain.models import VerificationReport, WorkflowState
from app.main import _verification_output_is_mechanically_safe


def _report(**overrides):
    values = dict(
        output_sha256='x',
        source_sha256_unchanged=True,
        protected_text_unchanged=True,
        output_reopens=True,
        target_improved=True,
        analysis_universe_preserved=True,
        layout_safe=True,
        no_unknown_or_uncovered=False,
        final_state=WorkflowState.REVIEW_REQUIRED,
        reasons=['unknown_or_uncovered_content_remains'],
    )
    values.update(overrides)
    return VerificationReport(**values)


def test_safe_local_repair_with_unrelated_unknowns_may_be_retained_for_review():
    assert _verification_output_is_mechanically_safe(_report()) is True


def test_layout_failed_review_output_must_not_be_retained():
    assert _verification_output_is_mechanically_safe(_report(layout_safe=False, reasons=['layout_regression'])) is False


def test_analyzability_regression_output_must_not_be_retained():
    assert _verification_output_is_mechanically_safe(_report(analysis_universe_preserved=False, reasons=['analysis_universe_not_preserved'])) is False
