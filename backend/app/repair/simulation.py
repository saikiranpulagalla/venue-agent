from __future__ import annotations

from pathlib import Path
import tempfile

from app.domain.models import AnalysisResult, PlanSimulationReport, RepairCandidate
from app.pptx.mutations import ScaleMutation, apply_scale_plan, apply_scale_text
from app.pptx.renderer import render_pptx_to_pdf
from app.repair.invariants import canonical_fingerprint, normalized_rendered_pdf_text, normalized_visible_text, pptx_reopens
from app.repair.transition import audit_analysis_transition
from app.security.intake import sha256_path
from app.venue.analyzer import VenueAnalyzer


def _find_result(analysis: AnalysisResult, element_id: str):
    return next((r for r in analysis.results if r.element.element_id == element_id), None)


def _coverage(analysis: AnalysisResult) -> float:
    return analysis.summary.target_coverage


def _candidate_fingerprint(candidate: RepairCandidate) -> str:
    return canonical_fingerprint({
        "candidate_id": candidate.candidate_id,
        "issue_element_id": candidate.issue_element_id,
        "operation": candidate.operation,
        "parameters": candidate.parameters,
        "predicted_state": candidate.predicted_state,
        "safe": candidate.safe,
        "policy_reasons": candidate.policy_reasons,
        "actual_percent_before": candidate.actual_percent_before,
        "actual_percent_after": candidate.actual_percent_after,
        "issue_role": candidate.issue_role,
        "deficit_ratio": candidate.deficit_ratio,
    })


def _bind_candidate(candidate: RepairCandidate) -> RepairCandidate:
    candidate.policy_reasons = sorted(set(candidate.policy_reasons))
    candidate.simulation_fingerprint = _candidate_fingerprint(candidate)
    return candidate


def simulate_candidate(
    source: Path,
    baseline: AnalysisResult,
    candidate: RepairCandidate,
    analyzer: VenueAnalyzer,
) -> RepairCandidate:
    if sha256_path(source) != baseline.source_sha256:
        candidate.safe = False
        candidate.policy_reasons.append("source_changed_since_analysis")
        return _bind_candidate(candidate)
    issue = _find_result(baseline, candidate.issue_element_id)
    if issue is None or issue.element.shape_id is None:
        candidate.safe = False
        candidate.policy_reasons.append("issue_not_found")
        return _bind_candidate(candidate)

    source_sha = sha256_path(source)
    source_text = normalized_visible_text(source)
    with tempfile.TemporaryDirectory(prefix="venue-sim-") as td:
        td_path = Path(td)
        output = td_path / "candidate.pptx"
        try:
            apply_scale_text(
                source,
                output,
                issue.element.slide_index,
                issue.element.shape_id,
                float(candidate.parameters["scale"]),
            )
            if sha256_path(source) != source_sha:
                candidate.safe = False
                candidate.policy_reasons.append("source_changed_during_simulation")
                return _bind_candidate(candidate)
            if not pptx_reopens(output):
                candidate.safe = False
                candidate.policy_reasons.append("candidate_does_not_reopen")
                return _bind_candidate(candidate)
            if normalized_visible_text(output) != source_text:
                candidate.safe = False
                candidate.policy_reasons.append("protected_text_changed")
                return _bind_candidate(candidate)
            after = analyzer.analyze(output, baseline.venue, td_path / "analysis")
        except Exception as e:
            candidate.safe = False
            candidate.policy_reasons.append(f"simulation_failed:{type(e).__name__}")
            return _bind_candidate(candidate)

        transition = audit_analysis_transition(baseline, after, [candidate.issue_element_id])
        if not transition.analysis_universe_preserved:
            candidate.policy_reasons.append("analysis_universe_not_preserved")
        if not transition.layout_safe:
            candidate.policy_reasons.append("layout_regression")
        candidate.policy_reasons.extend(transition.reasons)

        after_issue = _find_result(after, candidate.issue_element_id)
        if after_issue is None:
            candidate.safe = False
            candidate.policy_reasons.append("target_missing_after_mutation")
            return _bind_candidate(candidate)

        before_actual = issue.actual_percent or 0.0
        after_actual = after_issue.actual_percent or 0.0
        candidate.actual_percent_before = before_actual
        candidate.actual_percent_after = after_actual
        candidate.issue_role = issue.element.role
        candidate.deficit_ratio = issue.deficit_ratio
        candidate.predicted_state = after_issue.state

        if after_actual <= before_actual:
            candidate.policy_reasons.append("target_not_improved")
        if after.summary.below_target_elements > baseline.summary.below_target_elements:
            candidate.policy_reasons.append("new_supported_violation")

        candidate.safe = not candidate.policy_reasons
        return _bind_candidate(candidate)


def _unsafe_report(
    baseline: AnalysisResult,
    selected: list[RepairCandidate],
    reasons: list[str],
    *,
    protected_text_unchanged: bool = False,
    rendered_text_unchanged: bool = False,
    analysis_universe_preserved: bool = False,
    layout_safe: bool = False,
    source_unchanged: bool = False,
    output_reopens: bool = False,
) -> PlanSimulationReport:
    report = PlanSimulationReport(
        selected_candidate_ids=[c.candidate_id for c in selected],
        safe=False,
        target_coverage_before=_coverage(baseline),
        target_coverage_after=_coverage(baseline),
        below_target_before=baseline.summary.below_target_elements,
        below_target_after=baseline.summary.below_target_elements,
        protected_text_unchanged=protected_text_unchanged,
        rendered_text_unchanged=rendered_text_unchanged,
        analysis_universe_preserved=analysis_universe_preserved,
        layout_safe=layout_safe,
        source_unchanged=source_unchanged,
        output_reopens=output_reopens,
        reasons=sorted(set(reasons)),
    )
    report.simulation_fingerprint = canonical_fingerprint(report.model_dump(mode="json", exclude={"simulation_fingerprint"}))
    return report


def simulate_plan(
    source: Path,
    baseline: AnalysisResult,
    selected: list[RepairCandidate],
    analyzer: VenueAnalyzer,
) -> PlanSimulationReport:
    reasons: list[str] = []
    if sha256_path(source) != baseline.source_sha256:
        return _unsafe_report(baseline, selected, ["source_changed_since_analysis"])
    if not selected:
        return _unsafe_report(baseline, selected, ["empty_plan"])
    if any(not c.safe or not c.simulation_fingerprint for c in selected):
        reasons.append("unsafe_or_unbound_candidate_in_plan")
    if len({c.issue_element_id for c in selected}) != len(selected):
        reasons.append("duplicate_issue_in_plan")
    if reasons:
        return _unsafe_report(baseline, selected, reasons)

    mutations: list[ScaleMutation] = []
    target_ids: list[str] = []
    for c in selected:
        issue = _find_result(baseline, c.issue_element_id)
        if issue is None or issue.element.shape_id is None:
            reasons.append(f"missing_issue:{c.issue_element_id}")
            continue
        mutations.append(ScaleMutation(
            slide_index=issue.element.slide_index,
            shape_id=issue.element.shape_id,
            scale=float(c.parameters["scale"]),
        ))
        target_ids.append(c.issue_element_id)
    if reasons:
        return _unsafe_report(baseline, selected, reasons)

    source_sha = sha256_path(source)
    source_text = normalized_visible_text(source)
    with tempfile.TemporaryDirectory(prefix="venue-plan-sim-") as td:
        td_path = Path(td)
        output = td_path / "plan.pptx"
        try:
            apply_scale_plan(source, output, mutations)
            source_unchanged = sha256_path(source) == source_sha
            output_reopens = pptx_reopens(output)
            protected_text_unchanged = output_reopens and normalized_visible_text(output) == source_text
            if not source_unchanged:
                reasons.append("source_changed_during_combined_simulation")
            if not output_reopens:
                reasons.append("combined_output_does_not_reopen")
            if not protected_text_unchanged:
                reasons.append("combined_protected_text_changed")
            if reasons:
                return _unsafe_report(
                    baseline,
                    selected,
                    reasons,
                    protected_text_unchanged=protected_text_unchanged,
                    source_unchanged=source_unchanged,
                    output_reopens=output_reopens,
                )
            after = analyzer.analyze(output, baseline.venue, td_path / "analysis")
            source_pdf = render_pptx_to_pdf(source, td_path / "source-render")
            output_pdf = td_path / "analysis" / "render" / f"{output.stem}.pdf"
            rendered_text_unchanged = (
                output_pdf.exists()
                and normalized_rendered_pdf_text(source_pdf) == normalized_rendered_pdf_text(output_pdf)
            )
            if not rendered_text_unchanged:
                reasons.append("combined_rendered_text_changed_or_lost")
        except Exception as e:
            return _unsafe_report(baseline, selected, [f"combined_simulation_failed:{type(e).__name__}"])

        transition = audit_analysis_transition(baseline, after, target_ids)
        reasons.extend(transition.reasons)
        if not transition.analysis_universe_preserved:
            reasons.append("combined_analysis_universe_not_preserved")
        if not transition.layout_safe:
            reasons.append("combined_layout_regression")

        improvements: dict[str, bool] = {}
        for c in selected:
            before = _find_result(baseline, c.issue_element_id)
            after_item = _find_result(after, c.issue_element_id)
            improved = bool(
                before and after_item and
                (after_item.actual_percent or 0.0) > (before.actual_percent or 0.0)
            )
            improvements[c.issue_element_id] = improved
            if not improved:
                reasons.append(f"target_not_improved:{c.issue_element_id}")
        if after.summary.below_target_elements > baseline.summary.below_target_elements:
            reasons.append("new_supported_violation")
        before_cov = _coverage(baseline)
        after_cov = _coverage(after)
        if after_cov + 1e-12 < before_cov:
            reasons.append("coverage_regressed")

        reasons = sorted(set(reasons))
        report = PlanSimulationReport(
            selected_candidate_ids=[c.candidate_id for c in selected],
            safe=not reasons,
            target_coverage_before=before_cov,
            target_coverage_after=after_cov,
            below_target_before=baseline.summary.below_target_elements,
            below_target_after=after.summary.below_target_elements,
            target_improvements=improvements,
            protected_text_unchanged=protected_text_unchanged,
            rendered_text_unchanged=rendered_text_unchanged,
            analysis_universe_preserved=transition.analysis_universe_preserved,
            layout_safe=transition.layout_safe,
            source_unchanged=source_unchanged,
            output_reopens=output_reopens,
            reasons=reasons,
        )
        report.simulation_fingerprint = canonical_fingerprint(report.model_dump(mode="json", exclude={"simulation_fingerprint"}))
        return report
