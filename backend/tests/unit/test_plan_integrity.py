from pathlib import Path

from app.domain.models import (
    ComparisonState,
    PlanSimulationReport,
    RepairCandidate,
    RepairConstraints,
)
from app.storage.sessions import Session


def _session(tmp_path: Path) -> Session:
    src = tmp_path / "source.pptx"
    src.write_bytes(b"fixture")
    s = Session(session_id="s", root=tmp_path, source_path=src, source_sha256="source-sha")
    s.candidates = [
        RepairCandidate(
            candidate_id="c1",
            issue_element_id="i1",
            operation="SCALE_TEXT",
            parameters={"scale": 1.2},
            predicted_state=ComparisonState.MEETS_TARGET,
            safe=True,
            simulation_fingerprint="candidate-proof-1",
            actual_percent_before=1.0,
            actual_percent_after=1.3,
            issue_role="BODY",
        )
    ]
    return s


def _report() -> PlanSimulationReport:
    return PlanSimulationReport(
        selected_candidate_ids=["c1"],
        safe=True,
        target_coverage_before=0.5,
        target_coverage_after=1.0,
        below_target_before=1,
        below_target_after=0,
        target_improvements={"i1": True},
        protected_text_unchanged=True,
        rendered_text_unchanged=True,
        source_unchanged=True,
        output_reopens=True,
        simulation_fingerprint="plan-proof-1",
    )


def test_plan_hash_changes_if_candidate_parameters_change(tmp_path):
    s = _session(tmp_path)
    constraints = RepairConstraints(max_mutations=1, minimum_target_coverage=1.0)
    report = _report()
    h1 = s.compute_plan_hash(["c1"], constraints, report)
    s.candidates[0].parameters["scale"] = 1.25
    h2 = s.compute_plan_hash(["c1"], constraints, report)
    assert h1 != h2


def test_plan_hash_changes_if_candidate_evidence_changes(tmp_path):
    s = _session(tmp_path)
    constraints = RepairConstraints(max_mutations=1, minimum_target_coverage=1.0)
    report = _report()
    h1 = s.compute_plan_hash(["c1"], constraints, report)
    s.candidates[0].simulation_fingerprint = "tampered"
    h2 = s.compute_plan_hash(["c1"], constraints, report)
    assert h1 != h2


def test_plan_hash_changes_if_combined_simulation_changes(tmp_path):
    s = _session(tmp_path)
    constraints = RepairConstraints(max_mutations=1, minimum_target_coverage=1.0)
    report = _report()
    h1 = s.compute_plan_hash(["c1"], constraints, report)
    report.simulation_fingerprint = "tampered-plan-proof"
    h2 = s.compute_plan_hash(["c1"], constraints, report)
    assert h1 != h2


def test_plan_hash_changes_if_combined_simulation_field_changes_without_refingerprinting(tmp_path):
    s = _session(tmp_path)
    constraints = RepairConstraints(max_mutations=1, minimum_target_coverage=1.0)
    report = _report()
    h1 = s.compute_plan_hash(["c1"], constraints, report)
    # Simulates an internal stale/tampered evidence object where the stored
    # fingerprint was not refreshed. The approval hash must still change.
    report.target_coverage_after = 0.75
    h2 = s.compute_plan_hash(["c1"], constraints, report)
    assert h1 != h2
