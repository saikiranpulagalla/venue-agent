from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
from pptx import Presentation

from app.security.intake import sha256_path


class MutationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScaleMutation:
    slide_index: int
    shape_id: int
    scale: float


def _scale_shape(target, scale: float) -> int:
    changed = 0
    for p in target.text_frame.paragraphs:
        for run in p.runs:
            if run.text.strip():
                if run.font.size is None:
                    raise MutationError("Inherited/unknown font size cannot be mutated safely")
                run.font.size = int(round(run.font.size * scale))
                changed += 1
    return changed


def apply_scale_plan(source: Path, output: Path, mutations: list[ScaleMutation]) -> None:
    if not mutations:
        raise MutationError("No mutations supplied")
    if len({(m.slide_index, m.shape_id) for m in mutations}) != len(mutations):
        raise MutationError("Duplicate target in one repair plan")
    for m in mutations:
        if not (1.0 < m.scale <= 1.60):
            raise MutationError("Scale outside allowed range")

    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(output.parent, 0o700)
    except OSError:
        pass
    shutil.copy2(source, output)
    original_hash = sha256_path(source)
    prs = Presentation(str(output))

    changed = 0
    for m in mutations:
        if not (0 <= m.slide_index < len(prs.slides)):
            raise MutationError("Target slide missing")
        slide = prs.slides[m.slide_index]
        target = next((s for s in slide.shapes if s.shape_id == m.shape_id), None)
        if target is None or not getattr(target, "has_text_frame", False):
            raise MutationError("Target shape missing or unsupported")
        changed += _scale_shape(target, m.scale)

    if changed == 0:
        raise MutationError("No explicit text run changed")
    prs.save(str(output))
    try:
        os.chmod(output, 0o600)
    except OSError:
        pass
    if sha256_path(source) != original_hash:
        raise MutationError("Invariant violation: source file changed")


def apply_scale_text(source: Path, output: Path, slide_index: int, shape_id: int, scale: float) -> None:
    apply_scale_plan(source, output, [ScaleMutation(slide_index=slide_index, shape_id=shape_id, scale=scale)])
