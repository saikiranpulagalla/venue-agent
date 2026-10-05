from __future__ import annotations

from pathlib import Path

from app.domain.models import (
    AnalysisCapability, AnalysisResult, AnalysisSummary, ComparisonState,
    ElementResult, VenueProfile,
)
from app.pptx.parser import map_elements
from app.pptx.renderer import libreoffice_version, render_pptx_to_pdf
from app.security.intake import sha256_path
from app.venue.reference import PublicBDMReference


ESTIMATED_BOUNDARY_RELATIVE_MARGIN = 0.05


class VenueAnalyzer:
    def __init__(self, reference: PublicBDMReference):
        self.reference = reference

    def analyze(self, source: Path, venue: VenueProfile, work_dir: Path) -> AnalysisResult:
        pdf = render_pptx_to_pdf(source, work_dir / "render")
        elements = map_elements(source, pdf)
        required = self.reference.required_percent(venue.viewing_ratio)
        results: list[ElementResult] = []
        for e in elements:
            if e.analysis_capability != AnalysisCapability.ANALYZABLE or e.rendered_height_percent is None:
                results.append(ElementResult(
                    element=e,
                    state=ComparisonState.NOT_ANALYZED,
                    reason="Element not completely and safely structurally mapped",
                ))
                continue
            if required is None:
                results.append(ElementResult(
                    element=e,
                    state=ComparisonState.OUTSIDE_REFERENCE_RANGE,
                    actual_percent=e.rendered_height_percent,
                    reason="Viewing ratio is outside public reference range",
                ))
                continue
            actual = e.rendered_height_percent
            if (
                venue.measurement_basis == "ESTIMATED"
                and required > 0
                and abs(actual - required) / required <= ESTIMATED_BOUNDARY_RELATIVE_MARGIN
            ):
                results.append(ElementResult(
                    element=e,
                    state=ComparisonState.BOUNDARY_REVIEW,
                    required_percent=required,
                    actual_percent=actual,
                    reason=(
                        "Estimated venue geometry places this element within the "
                        f"{ESTIMATED_BOUNDARY_RELATIVE_MARGIN:.0%} decision boundary; manual review required"
                    ),
                ))
                continue
            state = ComparisonState.MEETS_TARGET if actual >= required else ComparisonState.BELOW_TARGET
            deficit = (required / actual) if actual > 0 else None
            results.append(ElementResult(
                element=e,
                state=state,
                required_percent=required,
                actual_percent=actual,
                deficit_ratio=deficit,
            ))
        summary = AnalysisSummary(
            total_detected_text_elements=len(results),
            analyzed_elements=sum(r.state in {ComparisonState.MEETS_TARGET, ComparisonState.BELOW_TARGET} for r in results),
            not_analyzed_elements=sum(r.state == ComparisonState.NOT_ANALYZED for r in results),
            below_target_elements=sum(r.state == ComparisonState.BELOW_TARGET for r in results),
            meets_target_elements=sum(r.state == ComparisonState.MEETS_TARGET for r in results),
            boundary_review_elements=sum(r.state == ComparisonState.BOUNDARY_REVIEW for r in results),
            outside_reference_elements=sum(r.state == ComparisonState.OUTSIDE_REFERENCE_RANGE for r in results),
            unsupported_visible_content_elements=sum(
                "unsupported_visible_content" in r.element.notes for r in results
            ),
        )
        return AnalysisResult(
            source_sha256=sha256_path(source),
            reference_profile_id=self.reference.profile_id,
            venue=venue,
            results=results,
            summary=summary,
            renderer_version=libreoffice_version(),
        )
