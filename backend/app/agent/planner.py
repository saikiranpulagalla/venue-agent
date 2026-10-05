from __future__ import annotations

from itertools import combinations
import json
import os
import threading
import time
from typing import Protocol

from app.domain.models import (
    ComparisonState,
    PlanDecision,
    PlanningContext,
    RepairCandidate,
    RepairConstraints,
)


class Planner(Protocol):
    def choose(
        self,
        candidates: list[RepairCandidate],
        constraints: RepairConstraints,
        context: PlanningContext,
    ) -> PlanDecision: ...


def _candidate_allowed(c: RepairCandidate, constraints: RepairConstraints) -> bool:
    if not c.safe or not c.simulation_fingerprint:
        return False
    if float(c.parameters.get("scale", 99)) > constraints.max_scale_factor:
        return False
    # Defense in depth: even a forged/stale candidate cannot make a protected
    # heading executable. `protect_titles` is retained for API compatibility,
    # but cannot weaken the competition V1 policy.
    if c.issue_role == "TITLE":
        return False
    if c.predicted_state is None:
        return False
    return True


def _resolved_issue_ids(combo: tuple[RepairCandidate, ...] | list[RepairCandidate]) -> set[str]:
    return {
        c.issue_element_id
        for c in combo
        if c.predicted_state == ComparisonState.MEETS_TARGET
    }


def _all_issue_ids(candidates: list[RepairCandidate], context: PlanningContext) -> list[str]:
    if context.below_target_issue_ids:
        return sorted(set(context.below_target_issue_ids))
    return sorted({c.issue_element_id for c in candidates})


def _projected_coverage(combo: tuple[RepairCandidate, ...] | list[RepairCandidate], context: PlanningContext) -> float:
    if context.analyzed_elements <= 0:
        return 0.0
    newly_resolved = len(_resolved_issue_ids(combo))
    return min(1.0, (context.meets_target_elements + newly_resolved) / context.analyzed_elements)


def canonicalize_decision(
    selected_candidate_ids: list[str],
    rationale: str,
    candidates: list[RepairCandidate],
    constraints: RepairConstraints,
    context: PlanningContext,
    *,
    planner_used: str = "deterministic",
    fallback_used: bool = False,
    fallback_reason: str | None = None,
) -> PlanDecision:
    """Rebuild planner output from trusted server-side candidate state.

    The model may propose only IDs and prose. Coverage, unresolved issues, safety,
    and review status are recomputed by deterministic code.
    """
    by_id = {c.candidate_id: c for c in candidates if _candidate_allowed(c, constraints)}
    if len(selected_candidate_ids) != len(set(selected_candidate_ids)):
        raise ValueError("planner selected duplicate candidate IDs")
    selected: list[RepairCandidate] = []
    for cid in selected_candidate_ids:
        if cid not in by_id:
            raise ValueError("planner selected unknown/unsafe candidate")
        selected.append(by_id[cid])
    if len(selected) > constraints.max_mutations:
        raise ValueError("planner exceeded mutation budget")
    if len({c.issue_element_id for c in selected}) != len(selected):
        raise ValueError("planner selected multiple candidates for one issue")

    projected = _projected_coverage(selected, context)
    if selected and projected + 1e-12 < constraints.minimum_target_coverage:
        raise ValueError("planner plan does not reach target coverage")

    all_issues = set(_all_issue_ids(candidates, context))
    unresolved = sorted(all_issues - _resolved_issue_ids(selected))
    return PlanDecision(
        selected_candidate_ids=[c.candidate_id for c in selected],
        rationale=rationale,
        requires_human_review=bool(unresolved),
        unresolved_issue_ids=unresolved,
        projected_target_coverage=projected,
        planner_used=planner_used,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
    )


class DeterministicPlanner:
    """Safe constrained baseline planner.

    It searches only server-generated, simulated candidates and picks a global
    combination that satisfies the user's target coverage within the mutation
    budget. It never invents IDs or mutation parameters.
    """

    def choose(
        self,
        candidates: list[RepairCandidate],
        constraints: RepairConstraints,
        context: PlanningContext,
    ) -> PlanDecision:
        issue_ids = _all_issue_ids(candidates, context)
        if context.current_target_coverage + 1e-12 >= constraints.minimum_target_coverage:
            return PlanDecision(
                selected_candidate_ids=[],
                rationale=(
                    "Requested target coverage is already satisfied; zero mutations are the minimum justified action. "
                    "Any remaining below-target issues stay visible for review."
                ),
                requires_human_review=bool(issue_ids),
                unresolved_issue_ids=issue_ids,
                projected_target_coverage=context.current_target_coverage,
                planner_used="deterministic",
            )
        allowed = [c for c in candidates if _candidate_allowed(c, constraints)]
        if context.below_target_elements == 0:
            return PlanDecision(
                selected_candidate_ids=[],
                rationale="No below-target supported elements require repair.",
                requires_human_review=False,
                unresolved_issue_ids=[],
                projected_target_coverage=context.current_target_coverage,
            )
        if not allowed:
            return PlanDecision(
                selected_candidate_ids=[],
                rationale="No simulated candidate satisfies deterministic policy and user constraints.",
                requires_human_review=True,
                unresolved_issue_ids=issue_ids,
                projected_target_coverage=context.current_target_coverage,
            )

        feasible: list[tuple[tuple[RepairCandidate, ...], float]] = []
        max_k = min(constraints.max_mutations, len(allowed))
        for k in range(1, max_k + 1):
            for combo in combinations(allowed, k):
                if len({c.issue_element_id for c in combo}) != len(combo):
                    continue
                coverage = _projected_coverage(combo, context)
                if coverage + 1e-12 >= constraints.minimum_target_coverage:
                    feasible.append((combo, coverage))

        if not feasible:
            best_partial: tuple[RepairCandidate, ...] | None = None
            best_cov = context.current_target_coverage
            for k in range(1, max_k + 1):
                for combo in combinations(allowed, k):
                    if len({c.issue_element_id for c in combo}) != len(combo):
                        continue
                    cov = _projected_coverage(combo, context)
                    if cov > best_cov:
                        best_partial, best_cov = combo, cov
            return PlanDecision(
                selected_candidate_ids=[],
                rationale=(
                    "No permitted combination reaches the requested target coverage within the mutation budget. "
                    f"Best projected coverage is {best_cov:.3f}."
                ),
                requires_human_review=True,
                unresolved_issue_ids=issue_ids,
                projected_target_coverage=best_cov,
            )

        # Prefer fewer mutations, then lower total scaling, then higher projected coverage,
        # then greater measured improvement. This is intentionally conservative.
        feasible.sort(key=lambda item: (
            len(item[0]),
            sum(float(c.parameters.get("scale", 99)) - 1.0 for c in item[0]),
            -item[1],
            -sum((c.actual_percent_after or 0.0) - (c.actual_percent_before or 0.0) for c in item[0]),
        ))
        combo, _projected = feasible[0]
        return canonicalize_decision(
            [c.candidate_id for c in combo],
            "Selected the smallest server-generated simulated repair set that satisfies the requested target coverage and explicit mutation constraints.",
            candidates,
            constraints,
            context,
        )


class GeminiPlanner:
    """Optional bounded planner.

    The model sees only already-safe candidate metadata, a compact planning
    context, and user constraints. Presentation text is never included. The
    server reconstructs safety, coverage, unresolved issues, and review status
    from trusted candidate state; invalid model output falls back to the
    deterministic planner.
    """

    def __init__(self, fallback: Planner | None = None):
        self.fallback = fallback or DeterministicPlanner()
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
        self.timeout_ms = max(1000, int(os.getenv("GEMINI_TIMEOUT_MS", "8000")))
        self.circuit_cooldown_s = max(1.0, float(os.getenv("GEMINI_CIRCUIT_COOLDOWN_SECONDS", "60")))
        self._circuit_lock = threading.Lock()
        self._circuit_open_until = 0.0

    def _fallback_decision(
        self,
        candidates: list[RepairCandidate],
        constraints: RepairConstraints,
        context: PlanningContext,
        reason: str,
    ) -> PlanDecision:
        decision = self.fallback.choose(candidates, constraints, context)
        decision.planner_used = "deterministic_fallback"
        decision.fallback_used = True
        decision.fallback_reason = reason
        return decision

    def _circuit_is_open(self) -> bool:
        with self._circuit_lock:
            return time.monotonic() < self._circuit_open_until

    def _open_circuit(self) -> None:
        with self._circuit_lock:
            self._circuit_open_until = time.monotonic() + self.circuit_cooldown_s

    def _close_circuit(self) -> None:
        with self._circuit_lock:
            self._circuit_open_until = 0.0

    def choose(
        self,
        candidates: list[RepairCandidate],
        constraints: RepairConstraints,
        context: PlanningContext,
    ) -> PlanDecision:
        if not self.api_key or not self.model:
            return self.fallback.choose(candidates, constraints, context)
        if self._circuit_is_open():
            return self._fallback_decision(candidates, constraints, context, "circuit_open")
        try:
            from google import genai  # type: ignore
            try:
                from google.genai import types as genai_types  # type: ignore
                http_options = genai_types.HttpOptions(
                    timeout=self.timeout_ms,
                    retry_options=genai_types.HttpRetryOptions(attempts=1),
                )
            except Exception:
                # Keeps the adapter compatible with mocked/minimal clients while
                # still applying the SDK's documented timeout option.
                http_options = {"timeout": self.timeout_ms}
            client = genai.Client(api_key=self.api_key, http_options=http_options)
            allowed = [
                c.model_dump(mode="json")
                for c in candidates
                if _candidate_allowed(c, constraints)
            ]
            prompt = {
                "task": (
                    "Select only existing candidate IDs from the supplied safe list. Choose at most max_mutations, "
                    "never select two candidates for the same issue, and reach minimum_target_coverage if feasible. "
                    "Never invent IDs, parameters, operations, or facts. Return no IDs when no feasible plan exists."
                ),
                "constraints": constraints.model_dump(mode="json"),
                "planning_context": context.model_dump(mode="json"),
                "candidates": allowed,
            }
            response = client.models.generate_content(
                model=self.model,
                contents=json.dumps(prompt),
                config={
                    "response_mime_type": "application/json",
                    "response_schema": PlanDecision,
                },
            )
            decision = response.parsed
            if not isinstance(decision, PlanDecision):
                decision = PlanDecision.model_validate_json(response.text)
            trusted = canonicalize_decision(
                decision.selected_candidate_ids,
                decision.rationale or "Model-selected bounded repair plan.",
                candidates,
                constraints,
                context,
                planner_used=self.model,
                fallback_used=False,
            )
            self._close_circuit()
            return trusted
        except Exception as exc:
            self._open_circuit()
            return self._fallback_decision(candidates, constraints, context, type(exc).__name__)
