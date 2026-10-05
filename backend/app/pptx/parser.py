from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

import fitz
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.enum.text import MSO_AUTO_SIZE

from app.domain.models import AnalysisCapability, MappingConfidence, MutationCapability, TextElement


MAX_STRUCTURAL_TEXT_ELEMENTS = 500
MAX_RENDERED_TEXT_SPANS = 10_000
# A small edge contact is not enough evidence that an unmodeled visual hides
# text. This threshold is deliberately applied to the structural text box, not
# to the visual object's area, so a large background panel does not dominate
# the calculation.
MATERIAL_OCCLUSION_TEXT_AREA_RATIO = 0.05


class ParserLimitError(RuntimeError):
    pass


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().casefold()


def _font_key(name: str) -> str:
    name = (name or "").split("+")[-1]
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def _font_matches(requested: str, rendered: str) -> bool:
    a, b = _font_key(requested), _font_key(rendered)
    return bool(a and b and (a == b or a in b or b in a))


@dataclass
class ShapeRecord:
    slide_index: int
    shape_id: int
    text: str
    normalized_text: str
    role: str
    explicit_font_sizes_pt: list[float]
    mutation_capability: MutationCapability
    notes: list[str]
    normalized_bbox: tuple[float, float, float, float]
    requested_font_families: list[str] = field(default_factory=list)
    force_not_analyzable: bool = False
    unsupported_visible_content: bool = False


# These objects can contain text, obscure text, or otherwise materially alter a
# slide's visual layout. V1 deliberately does not attempt to interpret them.
# Their presence must still be part of the verification universe so a repair of
# another shape cannot become a whole-deck VERIFIED result by omission.
_UNSUPPORTED_VISIBLE_SHAPE_TYPES = {
    "CHART",
    "PICTURE",
    "MEDIA",
    "EMBEDDED_OLE_OBJECT",
    "LINKED_OLE_OBJECT",
    "OLE_OBJECT",
    "IGX_GRAPHIC",  # SmartArt/diagram graphic in python-pptx
    "SMART_ART",
    "DIAGRAM",
    "GRAPHIC_FRAME",
    "CANVAS",
    "FREEFORM",
    "CALLOUT",
    "LINE",
    "CONNECTOR",
}


def _shape_type_name(shape) -> str:
    value = getattr(shape, "shape_type", None)
    name = getattr(value, "name", None)
    raw = str(name or value or "").upper()
    # python-pptx releases expose either enum.name or strings such as
    # "CHART (3)". Keep the policy stable across both representations.
    return raw.split(" ", 1)[0].split("(", 1)[0]


def _unsupported_visual_kind(shape) -> str | None:
    kind = _shape_type_name(shape)
    return kind.casefold() if kind in _UNSUPPORTED_VISIBLE_SHAPE_TYPES else None


def _placeholder_role(shape) -> str | None:
    try:
        if not shape.is_placeholder:
            return None
        ptype = shape.placeholder_format.type
        if ptype in {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE, PP_PLACEHOLDER.VERTICAL_TITLE}:
            return "TITLE"
        if ptype in {PP_PLACEHOLDER.FOOTER, PP_PLACEHOLDER.DATE, PP_PLACEHOLDER.SLIDE_NUMBER}:
            return "FOOTER"
    except Exception:
        return None
    return None


def _role(shape, slide_h: float, explicit_sizes: list[float]) -> str:
    placeholder_role = _placeholder_role(shape)
    if placeholder_role:
        return placeholder_role
    name = (getattr(shape, "name", "") or "").lower()
    if "title" in name:
        return "TITLE"
    if "footer" in name:
        return "FOOTER"
    if getattr(shape, "has_text_frame", False):
        top_ratio = float(getattr(shape, "top", 0) or 0) / max(slide_h, 1.0)
        max_size = max(explicit_sizes, default=0.0)
        text = normalize_text(getattr(shape, "text", "") or "")
        paragraph_count = sum(
            1 for p in shape.text_frame.paragraphs if normalize_text(getattr(p, "text", "") or "")
        )
        # Conservative V1 heuristic for manually-created headings. It is
        # deliberately limited to short, top-third labels so a normal large
        # body paragraph is not broadly reclassified. False positives are
        # review-only; false negatives could mutate a protected heading.
        if (
            top_ratio <= 0.35
            and max_size >= 20.0
            and paragraph_count <= 2
            and len(text.split()) <= 14
        ):
            return "TITLE"
        if top_ratio >= 0.84 and max_size <= 18.0:
            return "FOOTER"
        return "BODY"
    return "OTHER"


def _shape_mutation_capability(shape) -> tuple[MutationCapability, list[str], list[float], list[str]]:
    notes: list[str] = []
    sizes: list[float] = []
    fonts: list[str] = []
    if not getattr(shape, "has_text_frame", False):
        return MutationCapability.NO_MUTATION, ["no_text_frame"], sizes, fonts
    if float(getattr(shape, "rotation", 0) or 0) % 360 != 0:
        return MutationCapability.NO_MUTATION, ["rotated_text"], sizes, fonts
    if shape.shape_type not in {MSO_SHAPE_TYPE.TEXT_BOX, MSO_SHAPE_TYPE.PLACEHOLDER, MSO_SHAPE_TYPE.AUTO_SHAPE}:
        return MutationCapability.NO_MUTATION, [f"unsupported_shape_type:{shape.shape_type}"], sizes, fonts

    auto_size = shape.text_frame.auto_size
    if auto_size not in {None, MSO_AUTO_SIZE.NONE}:
        notes.append(f"automatic_text_fitting:{auto_size}")

    for p in shape.text_frame.paragraphs:
        for run in p.runs:
            if run.text.strip():
                if run.font.size is None:
                    notes.append("inherited_or_unknown_font_size")
                else:
                    sizes.append(round(run.font.size.pt, 3))
                if run.font.name:
                    fonts.append(run.font.name)
                else:
                    notes.append("inherited_or_unknown_font_family")
    fonts = sorted(set(fonts))
    if not sizes:
        return MutationCapability.REVIEW_ONLY, sorted(set(notes + ["no_explicit_font_sizes"])), sizes, fonts
    # Any autofit or inherited typography is review-only. Automated mutation is
    # intentionally narrower than analysis because renderer-dependent fitting can
    # create silent layout changes after font scaling.
    if notes:
        return MutationCapability.REVIEW_ONLY, sorted(set(notes)), sizes, fonts
    return MutationCapability.SAFE_MUTATION, [], sizes, fonts


def _shape_bbox(shape, slide_w: float, slide_h: float) -> tuple[float, float, float, float]:
    return (
        float(shape.left) / slide_w,
        float(shape.top) / slide_h,
        float(shape.left + shape.width) / slide_w,
        float(shape.top + shape.height) / slide_h,
    )


def _intersection_area(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _materially_intersects_text(
    visual_bbox: tuple[float, float, float, float],
    text_bbox: tuple[float, float, float, float],
) -> bool:
    tx0, ty0, tx1, ty1 = text_bbox
    text_area = max(0.0, tx1 - tx0) * max(0.0, ty1 - ty0)
    return text_area > 0 and _intersection_area(visual_bbox, text_bbox) / text_area >= MATERIAL_OCCLUSION_TEXT_AREA_RATIO


def _is_potential_text_occluder(shape) -> bool:
    """Return only objects that can materially paint over text.

    python-pptx does not expose reliable transparency for every shape class.
    An unresolved *overlapping* fill is therefore conservative, while an
    object with no fill, or a line/connector, is not promoted to a blocker.
    """
    if getattr(shape, "shape_type", None) == MSO_SHAPE_TYPE.GROUP:
        return any(_is_potential_text_occluder(child) for child in shape.shapes)

    kind = _unsupported_visual_kind(shape)
    if kind in {"picture", "chart", "media", "embedded_ole_object", "linked_ole_object", "ole_object", "igx_graphic", "smart_art", "diagram", "graphic_frame", "canvas", "freeform", "callout"}:
        return True
    # Separators and connectors can cross text, but without a line-width and
    # visibility model they are not evidence of material occlusion.
    if kind in {"line", "connector"}:
        return False

    # Empty ordinary AutoShapes are the important FV-01 case. A no-fill shape
    # cannot obscure text; a fill whose opacity is unavailable is treated as a
    # possible occluder only when it materially overlaps text.
    fill = getattr(shape, "fill", None)
    return bool(fill is not None and getattr(fill, "type", None) not in {None, "BACKGROUND", 5})


def _table_text(shape) -> str:
    if not getattr(shape, "has_table", False):
        return ""
    texts: list[str] = []
    for row in shape.table.rows:
        for cell in row.cells:
            if (cell.text or "").strip():
                texts.append(cell.text)
    return "\n".join(texts)


def _group_text(shape) -> str:
    if shape.shape_type != MSO_SHAPE_TYPE.GROUP:
        return ""
    texts: list[str] = []
    for child in shape.shapes:
        if getattr(child, "has_text_frame", False) and (child.text or "").strip():
            texts.append(child.text)
        if getattr(child, "has_table", False):
            t = _table_text(child)
            if t:
                texts.append(t)
        if child.shape_type == MSO_SHAPE_TYPE.GROUP:
            t = _group_text(child)
            if t:
                texts.append(t)
    return "\n".join(texts)


def parse_pptx_shapes(source: Path) -> list[ShapeRecord]:
    prs = Presentation(str(source))
    slide_w = float(prs.slide_width)
    slide_h = float(prs.slide_height)
    out: list[ShapeRecord] = []

    def append_record(record: ShapeRecord) -> None:
        out.append(record)
        if len(out) > MAX_STRUCTURAL_TEXT_ELEMENTS:
            raise ParserLimitError(f"structural_text_element_limit_exceeded:{MAX_STRUCTURAL_TEXT_ELEMENTS}")

    def append_unsupported(
        *, slide_index: int, shape_id: int, bbox: tuple[float, float, float, float], kind: str, text: str = ""
    ) -> None:
        append_record(ShapeRecord(
            slide_index=slide_index,
            shape_id=shape_id,
            text=text or f"[unsupported visible {kind}]",
            normalized_text=normalize_text(text),
            role="OTHER",
            explicit_font_sizes_pt=[],
            mutation_capability=MutationCapability.NO_MUTATION,
            notes=[f"unsupported_visible_{kind}"],
            normalized_bbox=bbox,
            force_not_analyzable=True,
            unsupported_visible_content=True,
        ))

    for si, slide in enumerate(prs.slides):
        # python-pptx exposes slide.shapes in drawing order (back to front).
        # Keep that index so a visual behind text does not become a false
        # occlusion finding. Inherited layout/master objects have no reliable
        # cross-layer z-order, so only their material intersections are kept.
        slide_text_records: list[tuple[ShapeRecord, int]] = []
        visual_candidates: list[tuple[int, tuple[float, float, float, float], str, int | None]] = []

        for z_order, shape in enumerate(slide.shapes):
            if getattr(shape, "has_text_frame", False):
                text = shape.text or ""
                if text.strip():
                    cap, notes, sizes, fonts = _shape_mutation_capability(shape)
                    record = ShapeRecord(
                        slide_index=si,
                        shape_id=shape.shape_id,
                        text=text,
                        normalized_text=normalize_text(text),
                        role=_role(shape, slide_h, sizes),
                        explicit_font_sizes_pt=sizes,
                        mutation_capability=cap,
                        notes=notes,
                        normalized_bbox=_shape_bbox(shape, slide_w, slide_h),
                        requested_font_families=fonts,
                    )
                    append_record(record)
                    slide_text_records.append((record, z_order))
                    continue

            # Unsupported visible text must remain represented in the analysis
            # universe instead of silently disappearing from coverage.
            unsupported_text = _table_text(shape) or _group_text(shape)
            if unsupported_text.strip():
                kind = "table" if getattr(shape, "has_table", False) else "group"
                append_unsupported(
                    slide_index=si,
                    shape_id=shape.shape_id,
                    bbox=_shape_bbox(shape, slide_w, slide_h),
                    kind=f"{kind}_text",
                    text=unsupported_text,
                )
                continue

            if _is_potential_text_occluder(shape):
                visual_candidates.append((
                    z_order,
                    _shape_bbox(shape, slide_w, slide_h),
                    _unsupported_visual_kind(shape) or "filled_shape",
                    shape.shape_id,
                ))

        # python-pptx exposes only slide-local shapes above. Static text on a
        # layout or master can still render on the slide; record it as unknown
        # rather than incorrectly claiming whole-deck coverage. Synthetic IDs
        # cannot collide with real PowerPoint shape IDs and are never mutable.
        inherited_seen: set[tuple[str, int]] = set()
        inherited_id = 1
        for owner, prefix in ((slide.slide_layout, "layout"), (slide.slide_layout.slide_master, "master")):
            for shape in owner.shapes:
                # Built-in layout/master placeholders commonly contain template
                # editing prompts. They are not static inherited slide content;
                # effective user placeholder text is represented on the slide.
                if getattr(shape, "is_placeholder", False):
                    continue
                text = (getattr(shape, "text", "") or "").strip() if getattr(shape, "has_text_frame", False) else ""
                if not text:
                    if _is_potential_text_occluder(shape):
                        visual_candidates.append((
                            -1,
                            _shape_bbox(shape, slide_w, slide_h),
                            f"inherited_{prefix}_{_unsupported_visual_kind(shape) or 'filled_shape'}",
                            None,
                        ))
                    continue
                key = (prefix, int(getattr(shape, "shape_id", 0) or 0))
                if key not in inherited_seen:
                    inherited_seen.add(key)
                    append_unsupported(
                        slide_index=si,
                        shape_id=-(100000 + inherited_id),
                        bbox=_shape_bbox(shape, slide_w, slide_h),
                        kind=f"inherited_{prefix}_text",
                        text=text,
                    )
                    inherited_id += 1

        # Visuals become explicit unsupported coverage only if they can obscure
        # a structural text region. Slide-local ordering is known; inherited
        # objects are conservatively considered ambiguous when they intersect.
        occluder_id = 1
        for visual_z, visual_bbox, kind, shape_id in visual_candidates:
            affected = any(
                _materially_intersects_text(visual_bbox, text_record.normalized_bbox)
                and (visual_z < 0 or visual_z > text_z)
                for text_record, text_z in slide_text_records
            )
            if affected:
                append_unsupported(
                    slide_index=si,
                    shape_id=shape_id if shape_id is not None else -(200000 + occluder_id),
                    bbox=visual_bbox,
                    kind=f"potential_text_occluder_{kind}",
                )
                occluder_id += 1
    return out


def rendered_spans(pdf_path: Path) -> list[dict]:
    rows: list[dict] = []
    line_counter = 0
    with fitz.open(pdf_path) as doc:
        for page_index, page in enumerate(doc):
            pw = page.rect.width
            ph = page.rect.height
            if pw <= 0 or ph <= 0:
                raise ParserLimitError("rendered_page_has_invalid_geometry")
            data = page.get_text("dict")
            for block in data.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    line_counter += 1
                    for span in line.get("spans", []):
                        text = span.get("text", "")
                        if not text.strip():
                            continue
                        if len(rows) >= MAX_RENDERED_TEXT_SPANS:
                            raise ParserLimitError(
                                f"rendered_text_span_limit_exceeded:{MAX_RENDERED_TEXT_SPANS}"
                            )
                        x0, y0, x1, y1 = span["bbox"]
                        rows.append({
                            "span_id": len(rows),
                            "line_id": line_counter,
                            "slide_index": page_index,
                            "text": text,
                            "normalized_text": normalize_text(text),
                            "font": span.get("font") or "",
                            "bbox": (x0, y0, x1, y1),
                            "normalized_bbox": (x0 / pw, y0 / ph, x1 / pw, y1 / ph),
                            "height_percent": max(0.0, (y1-y0) / ph * 100.0),
                        })
    return rows


def _span_center_inside_shape(span: dict, rec: ShapeRecord, tolerance: float = 0.01) -> bool:
    x0, y0, x1, y1 = span["normalized_bbox"]
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    sx0, sy0, sx1, sy1 = rec.normalized_bbox
    return (
        sx0 - tolerance <= cx <= sx1 + tolerance
        and sy0 - tolerance <= cy <= sy1 + tolerance
    )


def _union_bbox(spans: list[dict]) -> tuple[float, float, float, float] | None:
    if not spans:
        return None
    boxes = [s["normalized_bbox"] for s in spans]
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _rendered_font_mismatch(requested: list[str], rendered: list[str]) -> bool:
    if not requested:
        return False
    if not rendered:
        return True
    return any(not any(_font_matches(req, actual) for actual in rendered) for req in requested)


def map_elements(source: Path, pdf_path: Path) -> list[TextElement]:
    shapes = parse_pptx_shapes(source)
    spans = rendered_spans(pdf_path)
    mapped: list[tuple[ShapeRecord, list[dict], list[dict], MappingConfidence]] = []

    for rec in shapes:
        if rec.force_not_analyzable:
            mapped.append((rec, [], [], MappingConfidence.UNMAPPED))
            continue
        candidates = [
            s for s in spans
            if s["slide_index"] == rec.slide_index and _span_center_inside_shape(s, rec)
        ]
        raw_claims = [
            s for s in candidates
            if s["normalized_text"] and s["normalized_text"] in rec.normalized_text
        ]
        raw_claims.sort(key=lambda s: int(s["span_id"]))
        exact = [s for s in raw_claims if s["normalized_text"] == rec.normalized_text]
        reconstructed = normalize_text(" ".join(s["text"] for s in raw_claims))

        if len(exact) == 1 and len(raw_claims) == 1:
            chosen = exact
            confidence = MappingConfidence.EXACT
        elif raw_claims and reconstructed == rec.normalized_text:
            chosen = raw_claims
            confidence = MappingConfidence.STRONG
        elif raw_claims:
            # Partial PDF extraction is evidence of uncertainty, not permission
            # to infer a measurement from the visible fragment.
            chosen = []
            confidence = MappingConfidence.PARTIAL
        else:
            chosen = []
            confidence = MappingConfidence.UNMAPPED
        mapped.append((rec, chosen, raw_claims, confidence))

    # A span plausibly claimed by multiple structural elements makes ownership
    # ambiguous even if only one of those elements reconstructs completely.
    owners: dict[int, int] = {}
    for _, _, raw_claims, _ in mapped:
        for span in raw_claims:
            sid = int(span["span_id"])
            owners[sid] = owners.get(sid, 0) + 1

    out: list[TextElement] = []
    for rec, chosen, raw_claims, confidence in mapped:
        ambiguous = any(owners.get(int(span["span_id"]), 0) > 1 for span in raw_claims)
        if ambiguous:
            confidence = MappingConfidence.AMBIGUOUS
            chosen = []

        rendered_fonts = sorted({str(s.get("font") or "") for s in chosen if s.get("font")})
        if chosen:
            height = min(s["height_percent"] for s in chosen)
            analysis_cap = AnalysisCapability.ANALYZABLE
            rendered_bbox = _union_bbox(chosen)
            line_count = len({int(s.get("line_id", s["span_id"])) for s in chosen})
        else:
            height = None
            analysis_cap = AnalysisCapability.NOT_ANALYZABLE
            rendered_bbox = None
            line_count = 0

        mutation_cap = rec.mutation_capability
        notes = list(rec.notes)
        if confidence not in {MappingConfidence.EXACT, MappingConfidence.STRONG}:
            mutation_cap = MutationCapability.NO_MUTATION
        if confidence == MappingConfidence.PARTIAL:
            notes.append("partial_rendered_text_reconstruction")
        if ambiguous:
            notes.append("ambiguous_rendered_span_ownership")
        if rec.unsupported_visible_content:
            notes.append("unsupported_visible_content")
            analysis_cap = AnalysisCapability.NOT_ANALYZABLE
            mutation_cap = MutationCapability.NO_MUTATION
        if chosen and _rendered_font_mismatch(rec.requested_font_families, rendered_fonts):
            notes.append("font_substitution_or_unverified")
            # The current renderer geometry may still be analyzable, but a saved
            # PPTX cannot be auto-repaired safely when its requested font is not
            # the font that generated the evidence.
            mutation_cap = MutationCapability.REVIEW_ONLY

        out.append(TextElement(
            element_id=f"s{rec.slide_index+1}-sh{rec.shape_id}",
            slide_index=rec.slide_index,
            shape_id=rec.shape_id,
            source_text=rec.text,
            normalized_text=rec.normalized_text,
            role=rec.role,
            rendered_height_percent=height,
            analysis_capability=analysis_cap,
            mutation_capability=mutation_cap,
            mapping_confidence=confidence,
            explicit_font_sizes_pt=rec.explicit_font_sizes_pt,
            requested_font_families=rec.requested_font_families,
            rendered_font_families=rendered_fonts,
            structural_bbox=rec.normalized_bbox,
            rendered_bbox=rendered_bbox,
            rendered_span_count=len(chosen),
            rendered_line_count=line_count,
            notes=sorted(set(notes)),
        ))
    return out
