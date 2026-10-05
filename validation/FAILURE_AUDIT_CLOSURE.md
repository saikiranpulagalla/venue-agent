# Failure Audit Closure — v12

This document maps the red-team failures found against v11 to their v12 closure evidence. It is intentionally narrower than a feature list: each item states the former failure, the new invariant, and the regression that keeps it closed.

| Severity | Former failure | v12 closure | Regression evidence |
|---|---|---|---|
| P0 | Font scaling could wrap/overlap text and still become `VERIFIED` | Baseline→output transition audit checks line count, overflow, overlap, unrelated movement, structural identity | `test_visual_overlap_or_wrap_cannot_receive_verified`; `test_candidate_simulation_blocks_target_seeking_scale_that_wraps_or_overlaps` |
| P0 | Image-only / outside-reference deck could return `NO_ACTION_REQUIRED` | `NO_ACTION_REQUIRED` requires positive analyzable coverage and zero review/unknown conditions | `test_image_only_deck_is_review_required_not_no_action`; `test_outside_reference_deck_is_review_required_not_no_action` |
| P0 | Coverage could rise by losing analyzable elements | Analysis universe must be preserved element-by-element; analyzability/mapping may not regress | `test_previously_analyzable_content_cannot_disappear_to_make_coverage_look_better` |
| P0 | Partial PDF text could be treated as strong mapping | Auto-mutation requires complete normalized rendered-text reconstruction | `test_partial_rendered_text_reconstruction_fails_closed` |
| P0 follow-up | `REVIEW_REQUIRED` artifact could be mechanically unsafe yet retained | Output is retained only when all mechanical verification invariants pass; unrelated unknowns may still require review | `test_layout_failed_review_output_must_not_be_retained`; `test_safe_local_repair_with_unrelated_unknowns_may_be_retained_for_review` |
| P1 | External relationship detection depended on exact XML serialization | Namespace-tolerant XML parsing checks `TargetMode` semantically | `test_external_relationship_with_legal_xml_whitespace_is_detected` |
| P1 | Autofit text could be auto-mutated | Any automatic fitting / inherited typography is review-only | `test_text_to_fit_shape_is_review_only` |
| P1 | Manual semantic titles could bypass title protection | Conservative prominent-top-text recognition; UI says “recognized titles” | `test_manual_prominent_top_text_is_conservatively_recognized_as_title` |
| P1 | Missing fonts silently substituted | Requested/rendered font families compared; substitution downgrades mutation to `REVIEW_ONLY` | `test_missing_requested_font_is_never_auto_mutable` |
| P1 | Planner mutated despite requested coverage already being satisfied | Zero-mutation decision is minimum action; remaining issues force review rather than green state | `test_planner_chooses_zero_mutations_when_requested_coverage_is_already_met`; `test_requested_coverage_already_met_does_not_greenlight_remaining_issues` |
| P1 | Workload could grow with deck/candidate count | 75-slide intake cap, 500 structural-item cap, bounded repair issues, 18 candidate simulations, 30s render wall timeout | `test_slide_count_is_bounded_before_rendering`; `test_candidate_simulation_budget_fails_closed_before_any_simulation` |
| P1 | Repeated judge demo/upload clicks abandoned sessions | Browser retires current session before allocating another | judge-page regression asserts `retireCurrentSession`; store capacity/TTL tests remain green |
| P1 | “Independent verification” could share a blind spot | Fresh output re-render + transition oracle adds a geometry/layout invariant independent of planner selection | v12 safety roundtrip + transition-audit unit tests |
| P2 | Unsupported table/group text disappeared from coverage | Explicit unsupported-visible structural records become `NOT_ANALYZED` | `test_table_text_is_surfaced_as_unsupported_visible_content`; real round-trip table test |
| P2 | Judge UI underreported unknown coverage | UI exposes detected/analyzed ratio, not analyzed, boundary review, outside reference, unsupported visible | judge-page regression |
| P2 | Gemini fallback provenance was silent | Plan includes `planner_used`, `fallback_used`, `fallback_reason`; UI exposes provenance | Gemini adapter fallback regression |
| P2 | Estimated geometry near reference boundary was over-precise | Conservative estimated-measurement band returns `BOUNDARY_REVIEW` | `test_estimated_measurement_near_threshold_requires_boundary_review` |

## Hard safety metrics after closure

The release contract remains zero-tolerance for unauthorized mutations, source mutations, protected text loss, false `VERIFIED`, prompt-injection escape, and unsafe/unknown candidate execution. v12 adds explicit mechanical layout and analysis-universe invariants to the definition of a valid verification result.

The test suite demonstrates absence of these failures only over the implemented fixtures/adversarial corpus; it is not a mathematical proof for arbitrary PowerPoint content. Unsupported or ambiguous content remains review-only / not analyzed by design.
