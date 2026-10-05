from __future__ import annotations

import sys
import types

from app.agent.planner import GeminiPlanner
from app.domain.models import (
    ComparisonState,
    PlanDecision,
    PlanningContext,
    RepairCandidate,
    RepairConstraints,
)


def _candidate(cid: str = "c1") -> RepairCandidate:
    return RepairCandidate(
        candidate_id=cid,
        issue_element_id="i1",
        operation="SCALE_TEXT",
        parameters={"scale": 1.2},
        predicted_state=ComparisonState.MEETS_TARGET,
        safe=True,
        simulation_fingerprint="sim-proof",
        actual_percent_before=1.0,
        actual_percent_after=1.3,
        issue_role="BODY",
    )


def _context() -> PlanningContext:
    return PlanningContext(
        analyzed_elements=1,
        meets_target_elements=0,
        below_target_elements=1,
        current_target_coverage=0.0,
        below_target_issue_ids=["i1"],
        repairable_issue_ids=["i1"],
    )


class _FakeModels:
    def __init__(self, response):
        self.response = response
        self.last_call = None

    def generate_content(self, **kwargs):
        self.last_call = kwargs
        return self.response


class _FakeClient:
    last_instance = None

    def __init__(self, *, api_key, http_options=None):
        self.api_key = api_key
        self.http_options = http_options
        self.models = _FakeModels(
            types.SimpleNamespace(
                parsed=PlanDecision(
                    selected_candidate_ids=["c1"],
                    rationale="bounded model choice",
                ),
                text="",
            )
        )
        _FakeClient.last_instance = self


def _install_fake_google(monkeypatch):
    fake_genai = types.SimpleNamespace(Client=_FakeClient)
    google = types.ModuleType("google")
    google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", google)


def test_live_adapter_uses_current_38_contract_without_deprecated_sampling(monkeypatch):
    _install_fake_google(monkeypatch)
    planner = GeminiPlanner()
    planner.api_key = "test-key"
    planner.model = "gemini-3.8-flash"

    decision = planner.choose(
        [_candidate()],
        RepairConstraints(max_mutations=1, minimum_target_coverage=1.0),
        _context(),
    )

    assert decision.selected_candidate_ids == ["c1"]
    assert decision.planner_used == "gemini-3.8-flash"
    assert decision.fallback_used is False
    call = _FakeClient.last_instance.models.last_call
    assert call["model"] == "gemini-3.8-flash"
    assert call["config"]["response_mime_type"] == "application/json"
    assert call["config"]["response_schema"] is PlanDecision
    opts = _FakeClient.last_instance.http_options
    timeout = opts.get("timeout") if isinstance(opts, dict) else getattr(opts, "timeout", None)
    assert timeout == planner.timeout_ms
    assert "temperature" not in call["config"]
    assert "top_p" not in call["config"]
    assert "top_k" not in call["config"]


def test_invalid_model_candidate_falls_back_to_deterministic_planner(monkeypatch):
    class BadModels(_FakeModels):
        def __init__(self):
            super().__init__(types.SimpleNamespace(
                parsed=PlanDecision(selected_candidate_ids=["invented-id"], rationale="bad"),
                text="",
            ))

    class BadClient:
        def __init__(self, *, api_key, http_options=None):
            self.http_options = http_options
            self.models = BadModels()

    google = types.ModuleType("google")
    google.genai = types.SimpleNamespace(Client=BadClient)
    monkeypatch.setitem(sys.modules, "google", google)

    planner = GeminiPlanner()
    planner.api_key = "test-key"
    planner.model = "gemini-3.8-flash"
    decision = planner.choose(
        [_candidate()],
        RepairConstraints(max_mutations=1, minimum_target_coverage=1.0),
        _context(),
    )
    assert decision.selected_candidate_ids == ["c1"]
    assert "smallest server-generated" in decision.rationale
    assert decision.planner_used == "deterministic_fallback"
    assert decision.fallback_used is True
    assert decision.fallback_reason == "ValueError"
    assert decision.planner_used == "deterministic_fallback"
    assert decision.fallback_used is True
    assert decision.fallback_reason


def test_model_cannot_self_certify_unresolved_issues(monkeypatch):
    _install_fake_google(monkeypatch)
    planner = GeminiPlanner()
    planner.api_key = "test-key"
    planner.model = "gemini-3.8-flash"
    ctx = PlanningContext(
        analyzed_elements=2,
        meets_target_elements=0,
        below_target_elements=2,
        current_target_coverage=0.0,
        below_target_issue_ids=["i1", "i2"],
        repairable_issue_ids=["i1"],
    )
    # Model chooses the only repairable issue. Server must keep i2 unresolved.
    decision = planner.choose(
        [_candidate()],
        RepairConstraints(max_mutations=1, minimum_target_coverage=0.5),
        ctx,
    )
    assert decision.selected_candidate_ids == ["c1"]
    assert decision.requires_human_review is True
    assert decision.unresolved_issue_ids == ["i2"]


def test_model_failure_opens_circuit_and_next_call_skips_model(monkeypatch):
    calls = {"count": 0}

    class FailingModels:
        def generate_content(self, **kwargs):
            calls["count"] += 1
            raise TimeoutError("network timeout")

    class FailingClient:
        def __init__(self, *, api_key, http_options=None):
            self.models = FailingModels()

    google = types.ModuleType("google")
    google.genai = types.SimpleNamespace(Client=FailingClient)
    monkeypatch.setitem(sys.modules, "google", google)

    planner = GeminiPlanner()
    planner.api_key = "test-key"
    planner.model = "gemini-3.8-flash"
    first = planner.choose([_candidate()], RepairConstraints(max_mutations=1, minimum_target_coverage=1.0), _context())
    second = planner.choose([_candidate()], RepairConstraints(max_mutations=1, minimum_target_coverage=1.0), _context())

    assert first.fallback_used is True
    assert first.fallback_reason == "TimeoutError"
    assert second.fallback_used is True
    assert second.fallback_reason == "circuit_open"
    assert calls["count"] == 1
