from __future__ import annotations

from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, Field, model_validator


class AnalysisCapability(StrEnum):
    ANALYZABLE = "ANALYZABLE"
    PARTIALLY_ANALYZABLE = "PARTIALLY_ANALYZABLE"
    NOT_ANALYZABLE = "NOT_ANALYZABLE"


class MutationCapability(StrEnum):
    SAFE_MUTATION = "SAFE_MUTATION"
    REVIEW_ONLY = "REVIEW_ONLY"
    NO_MUTATION = "NO_MUTATION"


class MappingConfidence(StrEnum):
    EXACT = "EXACT"
    STRONG = "STRONG"
    AMBIGUOUS = "AMBIGUOUS"
    PARTIAL = "PARTIAL"
    UNMAPPED = "UNMAPPED"


class ComparisonState(StrEnum):
    MEETS_TARGET = "MEETS_TARGET"
    BELOW_TARGET = "BELOW_TARGET"
    BOUNDARY_REVIEW = "BOUNDARY_REVIEW"
    NOT_ANALYZED = "NOT_ANALYZED"
    OUTSIDE_REFERENCE_RANGE = "OUTSIDE_REFERENCE_RANGE"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"


class WorkflowState(StrEnum):
    UPLOADED = "UPLOADED"
    ANALYZED = "ANALYZED"
    PLANNED = "PLANNED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFIED = "VERIFIED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NO_ACTION_REQUIRED = "NO_ACTION_REQUIRED"
    TARGET_ALREADY_SATISFIED = "TARGET_ALREADY_SATISFIED"
    NO_FEASIBLE_PLAN = "NO_FEASIBLE_PLAN"
    REJECTED_BY_USER = "REJECTED_BY_USER"
    FAILED = "FAILED"


class VenueProfile(BaseModel):
    active_image_height_m: float = Field(gt=0.1, le=30.0)
    farthest_viewer_distance_m: float = Field(ge=0.25, le=500.0)
    measurement_basis: Literal["MEASURED", "ESTIMATED"] = "ESTIMATED"

    @property
    def viewing_ratio(self) -> float:
        return self.farthest_viewer_distance_m / self.active_image_height_m


class TextElement(BaseModel):
    element_id: str
    slide_index: int
    shape_id: int | None = None
    source_text: str
    normalized_text: str
    role: Literal["TITLE", "BODY", "FOOTER", "OTHER"] = "OTHER"
    rendered_height_percent: float | None = None
    analysis_capability: AnalysisCapability
    mutation_capability: MutationCapability
    mapping_confidence: MappingConfidence
    explicit_font_sizes_pt: list[float] = Field(default_factory=list)
    requested_font_families: list[str] = Field(default_factory=list)
    rendered_font_families: list[str] = Field(default_factory=list)
    structural_bbox: tuple[float, float, float, float] | None = None
    rendered_bbox: tuple[float, float, float, float] | None = None
    rendered_span_count: int = 0
    rendered_line_count: int = 0
    notes: list[str] = Field(default_factory=list)


class ElementResult(BaseModel):
    element: TextElement
    state: ComparisonState
    required_percent: float | None = None
    actual_percent: float | None = None
    deficit_ratio: float | None = None
    reason: str | None = None


class AnalysisSummary(BaseModel):
    total_detected_text_elements: int
    analyzed_elements: int
    not_analyzed_elements: int
    below_target_elements: int
    meets_target_elements: int
    boundary_review_elements: int = 0
    outside_reference_elements: int
    unsupported_visible_content_elements: int = 0

    @property
    def target_coverage(self) -> float:
        if self.analyzed_elements <= 0:
            return 0.0
        return self.meets_target_elements / self.analyzed_elements

    @property
    def has_unknown_or_uncovered(self) -> bool:
        return any([
            self.not_analyzed_elements > 0,
            self.boundary_review_elements > 0,
            self.outside_reference_elements > 0,
            self.unsupported_visible_content_elements > 0,
        ])


class AnalysisResult(BaseModel):
    source_sha256: str
    reference_profile_id: str
    venue: VenueProfile
    results: list[ElementResult]
    summary: AnalysisSummary
    renderer_version: str | None = None


class RepairConstraints(BaseModel):
    max_mutations: int = Field(default=2, ge=1, le=3)
    max_scale_factor: float = Field(default=1.60, gt=1.0, le=1.60)
    minimum_target_coverage: float = Field(default=0.90, ge=0.0, le=1.0)
    protect_titles: bool = True
    semantic_rewrite: Literal[False] = False


class RepairCandidate(BaseModel):
    candidate_id: str
    issue_element_id: str
    operation: Literal["SCALE_TEXT"]
    parameters: dict[str, float]
    predicted_state: ComparisonState | None = None
    safe: bool = False
    policy_reasons: list[str] = Field(default_factory=list)
    actual_percent_before: float | None = None
    actual_percent_after: float | None = None
    issue_role: str | None = None
    deficit_ratio: float | None = None
    simulation_fingerprint: str | None = None


class PlanningContext(BaseModel):
    analyzed_elements: int
    meets_target_elements: int
    below_target_elements: int
    current_target_coverage: float
    below_target_issue_ids: list[str] = Field(default_factory=list)
    repairable_issue_ids: list[str] = Field(default_factory=list)


class PlanDecision(BaseModel):
    selected_candidate_ids: list[str]
    rationale: str
    requires_human_review: bool = False
    unresolved_issue_ids: list[str] = Field(default_factory=list)
    projected_target_coverage: float | None = None
    planner_used: str = "deterministic"
    fallback_used: bool = False
    fallback_reason: str | None = None


class PlanSimulationReport(BaseModel):
    selected_candidate_ids: list[str]
    safe: bool
    target_coverage_before: float
    target_coverage_after: float
    below_target_before: int
    below_target_after: int
    target_improvements: dict[str, bool] = Field(default_factory=dict)
    protected_text_unchanged: bool = False
    rendered_text_unchanged: bool = False
    analysis_universe_preserved: bool = False
    layout_safe: bool = False
    source_unchanged: bool = False
    output_reopens: bool = False
    simulation_fingerprint: str | None = None
    reasons: list[str] = Field(default_factory=list)


class ApprovalRecord(BaseModel):
    approval_id: str
    plan_hash: str
    source_sha256: str
    idempotency_key: str
    approved: bool


class VerificationReport(BaseModel):
    output_sha256: str
    source_sha256_unchanged: bool
    protected_text_unchanged: bool
    output_reopens: bool
    target_improved: bool
    analysis_universe_preserved: bool = False
    layout_safe: bool = False
    no_unknown_or_uncovered: bool = False
    final_state: WorkflowState
    reasons: list[str] = Field(default_factory=list)
    target_improvements: dict[str, bool] = Field(default_factory=dict)
    target_coverage_before: float | None = None
    target_coverage_after: float | None = None

    @model_validator(mode="after")
    def verified_requires_all(self):
        if self.final_state == WorkflowState.VERIFIED:
            if not all([
                self.source_sha256_unchanged,
                self.protected_text_unchanged,
                self.output_reopens,
                self.target_improved,
                self.analysis_universe_preserved,
                self.layout_safe,
                self.no_unknown_or_uncovered,
            ]):
                raise ValueError("VERIFIED requires every invariant to hold")
        return self
