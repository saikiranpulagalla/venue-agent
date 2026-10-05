from app.agent.planner import DeterministicPlanner
from app.domain.models import ComparisonState, PlanningContext, RepairCandidate, RepairConstraints


def ctx(analyzed=2, meets=1, below=1):
    return PlanningContext(
        analyzed_elements=analyzed,
        meets_target_elements=meets,
        below_target_elements=below,
        current_target_coverage=(meets / analyzed) if analyzed else 0.0,
    )


def test_planner_never_selects_unsafe_candidate():
    candidates = [
        RepairCandidate(candidate_id="bad", issue_element_id="i1", operation="SCALE_TEXT", parameters={"scale":1.2}, safe=False),
        RepairCandidate(candidate_id="good", issue_element_id="i1", operation="SCALE_TEXT", parameters={"scale":1.25}, safe=True, simulation_fingerprint="sim", predicted_state=ComparisonState.MEETS_TARGET, issue_role="BODY"),
    ]
    d = DeterministicPlanner().choose(
        candidates,
        RepairConstraints(max_mutations=1, minimum_target_coverage=1.0),
        ctx(),
    )
    assert d.selected_candidate_ids == ["good"]


def test_title_protection_can_make_plan_infeasible():
    candidates = [
        RepairCandidate(candidate_id="title", issue_element_id="i1", operation="SCALE_TEXT", parameters={"scale":1.2}, safe=True, simulation_fingerprint="sim", predicted_state=ComparisonState.MEETS_TARGET, issue_role="TITLE")
    ]
    d = DeterministicPlanner().choose(
        candidates,
        RepairConstraints(protect_titles=True, minimum_target_coverage=1.0),
        ctx(),
    )
    assert d.selected_candidate_ids == []
    assert d.requires_human_review


def test_global_plan_uses_two_distinct_issues_to_reach_target():
    candidates = [
        RepairCandidate(candidate_id="i1-small", issue_element_id="i1", operation="SCALE_TEXT", parameters={"scale":1.1}, safe=True, simulation_fingerprint="sim", predicted_state=ComparisonState.BELOW_TARGET, issue_role="BODY", actual_percent_before=1.0, actual_percent_after=1.1),
        RepairCandidate(candidate_id="i1-fix", issue_element_id="i1", operation="SCALE_TEXT", parameters={"scale":1.25}, safe=True, simulation_fingerprint="sim", predicted_state=ComparisonState.MEETS_TARGET, issue_role="BODY", actual_percent_before=1.0, actual_percent_after=1.25),
        RepairCandidate(candidate_id="i2-fix", issue_element_id="i2", operation="SCALE_TEXT", parameters={"scale":1.2}, safe=True, simulation_fingerprint="sim", predicted_state=ComparisonState.MEETS_TARGET, issue_role="BODY", actual_percent_before=1.0, actual_percent_after=1.2),
    ]
    context = PlanningContext(analyzed_elements=4, meets_target_elements=2, below_target_elements=2, current_target_coverage=0.5)
    d = DeterministicPlanner().choose(
        candidates,
        RepairConstraints(max_mutations=2, minimum_target_coverage=1.0),
        context,
    )
    assert set(d.selected_candidate_ids) == {"i1-fix", "i2-fix"}
    assert d.projected_target_coverage == 1.0


def test_partial_plan_is_rejected_when_budget_cannot_reach_target():
    candidates = [
        RepairCandidate(candidate_id="i1-fix", issue_element_id="i1", operation="SCALE_TEXT", parameters={"scale":1.2}, safe=True, simulation_fingerprint="sim", predicted_state=ComparisonState.MEETS_TARGET, issue_role="BODY"),
        RepairCandidate(candidate_id="i2-fix", issue_element_id="i2", operation="SCALE_TEXT", parameters={"scale":1.2}, safe=True, simulation_fingerprint="sim", predicted_state=ComparisonState.MEETS_TARGET, issue_role="BODY"),
    ]
    context = PlanningContext(analyzed_elements=4, meets_target_elements=2, below_target_elements=2, current_target_coverage=0.5)
    d = DeterministicPlanner().choose(
        candidates,
        RepairConstraints(max_mutations=1, minimum_target_coverage=1.0),
        context,
    )
    assert d.selected_candidate_ids == []
    assert d.requires_human_review
    assert d.projected_target_coverage == 0.75


def test_planner_never_selects_two_candidates_for_same_issue():
    candidates = [
        RepairCandidate(candidate_id="a", issue_element_id="i1", operation="SCALE_TEXT", parameters={"scale":1.1}, safe=True, simulation_fingerprint="sim", predicted_state=ComparisonState.MEETS_TARGET, issue_role="BODY"),
        RepairCandidate(candidate_id="b", issue_element_id="i1", operation="SCALE_TEXT", parameters={"scale":1.2}, safe=True, simulation_fingerprint="sim", predicted_state=ComparisonState.MEETS_TARGET, issue_role="BODY"),
        RepairCandidate(candidate_id="c", issue_element_id="i2", operation="SCALE_TEXT", parameters={"scale":1.2}, safe=True, simulation_fingerprint="sim", predicted_state=ComparisonState.MEETS_TARGET, issue_role="BODY"),
    ]
    context = PlanningContext(analyzed_elements=4, meets_target_elements=2, below_target_elements=2, current_target_coverage=0.5)
    d = DeterministicPlanner().choose(
        candidates,
        RepairConstraints(max_mutations=2, minimum_target_coverage=1.0),
        context,
    )
    selected = [next(c for c in candidates if c.candidate_id == cid) for cid in d.selected_candidate_ids]
    assert len({c.issue_element_id for c in selected}) == len(selected)


def test_unrepairable_issue_remains_visible_even_when_target_is_met():
    candidates = [
        RepairCandidate(
            candidate_id="i1-fix",
            issue_element_id="i1",
            operation="SCALE_TEXT",
            parameters={"scale": 1.2},
            safe=True,
            simulation_fingerprint="sim-i1",
            predicted_state=ComparisonState.MEETS_TARGET,
            issue_role="BODY",
        )
    ]
    context = PlanningContext(
        analyzed_elements=4,
        meets_target_elements=2,
        below_target_elements=2,
        current_target_coverage=0.5,
        below_target_issue_ids=["i1", "i2"],
        repairable_issue_ids=["i1"],
    )
    d = DeterministicPlanner().choose(
        candidates,
        RepairConstraints(max_mutations=1, minimum_target_coverage=0.75),
        context,
    )
    assert d.selected_candidate_ids == ["i1-fix"]
    assert d.projected_target_coverage == 0.75
    assert d.requires_human_review is True
    assert d.unresolved_issue_ids == ["i2"]


def test_candidate_without_simulation_fingerprint_is_not_executable():
    candidates = [
        RepairCandidate(
            candidate_id="unbound",
            issue_element_id="i1",
            operation="SCALE_TEXT",
            parameters={"scale": 1.2},
            safe=True,
            predicted_state=ComparisonState.MEETS_TARGET,
            issue_role="BODY",
        )
    ]
    d = DeterministicPlanner().choose(
        candidates,
        RepairConstraints(max_mutations=1, minimum_target_coverage=1.0),
        ctx(),
    )
    assert d.selected_candidate_ids == []
    assert d.requires_human_review is True


def test_planner_chooses_zero_mutations_when_requested_coverage_is_already_met():
    candidates = [
        RepairCandidate(candidate_id="c1", issue_element_id="i1", operation="SCALE_TEXT", parameters={"scale":1.2}, safe=True, simulation_fingerprint="sim", predicted_state=ComparisonState.MEETS_TARGET, issue_role="BODY")
    ]
    context = PlanningContext(
        analyzed_elements=3,
        meets_target_elements=1,
        below_target_elements=2,
        current_target_coverage=1/3,
        below_target_issue_ids=["i1", "i2"],
        repairable_issue_ids=["i1"],
    )
    d = DeterministicPlanner().choose(
        candidates,
        RepairConstraints(max_mutations=2, minimum_target_coverage=0.20),
        context,
    )
    assert d.selected_candidate_ids == []
    assert d.projected_target_coverage == context.current_target_coverage
    assert d.unresolved_issue_ids == ["i1", "i2"]
