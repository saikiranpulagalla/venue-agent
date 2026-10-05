from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import re
import posixpath
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile


MAX_BYTES = 20 * 1024 * 1024
MAX_ENTRIES = 5000
MAX_UNCOMPRESSED = 150 * 1024 * 1024
MAX_SINGLE_ENTRY_UNCOMPRESSED = 64 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200.0
MAX_SLIDES = 75
MAX_RELATIONSHIP_XML_BYTES = 2 * 1024 * 1024
MAX_SLIDE_XML_BYTES = 4 * 1024 * 1024
MAX_TOTAL_SLIDE_XML_BYTES = 24 * 1024 * 1024
MAX_SLIDE_TEXT_CHARACTERS = 300_000
MAX_SLIDE_TEXT_RUNS = 20_000

_REQUIRED_PACKAGE_PARTS = {
    "[content_types].xml",
    "_rels/.rels",
    "ppt/presentation.xml",
    "ppt/_rels/presentation.xml.rels",
}

_FORBIDDEN_EMBEDDED_PREFIXES = (
    "ppt/activex/",
    "ppt/embeddings/",
)
_FORBIDDEN_EMBEDDED_PARTS = {
    "ppt/vbaproject.bin",
}
_FORBIDDEN_RELATIONSHIP_SUFFIXES = {
    "/oleobject",
    "/package",
    "/activexcontrol",
    "/vbaproject",
}


class IntakeError(ValueError):
    pass


@dataclass(frozen=True)
class IntakeReport:
    sha256: str
    byte_size: int
    entries: int
    uncompressed_size: int
    slide_count: int
    has_external_relationships: bool


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _relationship_source_dir(relationship_part_name: str) -> str:
    # OPC relationship parts live beside the source part under a _rels directory.
    # Example: ppt/slides/_rels/slide1.xml.rels -> source ppt/slides/slide1.xml.
    if relationship_part_name == "_rels/.rels":
        return ""
    marker = "/_rels/"
    if marker not in relationship_part_name or not relationship_part_name.endswith(".rels"):
        raise IntakeError("Malformed relationship part path")
    prefix, rel_name = relationship_part_name.rsplit(marker, 1)
    source_name = rel_name[:-5]
    return posixpath.dirname(posixpath.join(prefix, source_name))


def _validate_internal_relationship_target(target: str, relationship_part_name: str) -> None:
    raw = (target or "").strip()
    if not raw:
        raise IntakeError("PPTX contains unsafe relationship target")
    if "\\" in raw:
        raise IntakeError("PPTX contains unsafe relationship target")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme or parsed.netloc or parsed.path.startswith("/"):
        raise IntakeError("PPTX contains unsafe relationship target")
    decoded = urllib.parse.unquote(parsed.path)
    if "\\" in decoded or decoded.startswith("/"):
        raise IntakeError("PPTX contains unsafe relationship target")
    source_dir = _relationship_source_dir(relationship_part_name)
    resolved = posixpath.normpath(posixpath.join(source_dir, decoded))
    if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
        raise IntakeError("PPTX contains unsafe relationship target")


def _relationship_flags(
    data: bytes, relationship_part_name: str | None = None
) -> tuple[bool, bool]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise IntakeError(f"Malformed relationship XML: {e}") from e
    external = False
    forbidden_embedded = False
    for elem in root.iter():
        if elem.tag.rsplit("}", 1)[-1].casefold() != "relationship":
            continue
        attrs = {key.rsplit("}", 1)[-1].casefold(): value for key, value in elem.attrib.items()}
        is_external = (attrs.get("targetmode") or "").strip().casefold() == "external"
        if is_external:
            external = True
        elif relationship_part_name is not None:
            _validate_internal_relationship_target(attrs.get("target") or "", relationship_part_name)
        rel_type = (attrs.get("type") or "").strip().casefold()
        if any(rel_type.endswith(suffix) for suffix in _FORBIDDEN_RELATIONSHIP_SUFFIXES):
            forbidden_embedded = True
    return external, forbidden_embedded


def _relationship_has_external_target(data: bytes) -> bool:
    # Retained as a narrow helper for tests/compatibility.
    return _relationship_flags(data)[0]


def _canonical_package_name(info: zipfile.ZipInfo) -> str:
    raw = info.filename
    if "\\" in raw:
        raise IntakeError("Package contains non-canonical ZIP path")
    is_dir = info.is_dir()
    candidate = raw[:-1] if is_dir and raw.endswith("/") else raw
    if not candidate or candidate.startswith("/"):
        raise IntakeError("Unsafe ZIP path")
    parts = candidate.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise IntakeError("Package contains non-canonical ZIP path")
    canonical = "/".join(parts)
    if raw != canonical + ("/" if is_dir else ""):
        raise IntakeError("Package contains non-canonical ZIP path")
    return canonical


def _count_slide_text(data: bytes) -> tuple[int, int]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise IntakeError(f"Malformed slide XML: {e}") from e
    chars = 0
    runs = 0
    for elem in root.iter():
        local = elem.tag.rsplit("}", 1)[-1].casefold()
        if local == "t" and elem.text:
            chars += len(elem.text)
        elif local == "r":
            runs += 1
    return chars, runs


def validate_pptx(path: Path) -> IntakeReport:
    if path.suffix.lower() != ".pptx":
        raise IntakeError("Only .pptx is supported")
    size = path.stat().st_size
    if size <= 0 or size > MAX_BYTES:
        raise IntakeError("File size outside processing limits")
    if not zipfile.is_zipfile(path):
        raise IntakeError("PPTX is not a valid ZIP package")

    total = 0
    external = False
    slide_count = 0
    slide_xml_total = 0
    slide_text_chars = 0
    slide_text_runs = 0
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise IntakeError("Too many package entries")
        normalized_names: set[str] = set()
        file_names: set[str] = set()
        for info in infos:
            name = _canonical_package_name(info)
            folded = name.casefold()
            if folded in normalized_names:
                raise IntakeError("Duplicate package entry")
            normalized_names.add(folded)
            if info.is_dir():
                continue
            file_names.add(folded)
            if info.flag_bits & 0x1:
                raise IntakeError("Encrypted package entries are not supported")
            if info.file_size > MAX_SINGLE_ENTRY_UNCOMPRESSED:
                raise IntakeError("Package entry exceeds processing limit")
            if info.file_size and info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO:
                raise IntakeError("Package entry compression ratio exceeds processing limit")
            total += info.file_size
            if total > MAX_UNCOMPRESSED:
                raise IntakeError("Uncompressed package exceeds processing limits")

            if folded in _FORBIDDEN_EMBEDDED_PARTS or any(
                folded.startswith(prefix) for prefix in _FORBIDDEN_EMBEDDED_PREFIXES
            ):
                raise IntakeError("PPTX contains embedded active/package content unsupported in competition V1")

            if re.fullmatch(r"ppt/slides/slide\d+\.xml", name, flags=re.IGNORECASE):
                slide_count += 1
                if slide_count > MAX_SLIDES:
                    raise IntakeError(f"Slide count exceeds processing limit ({MAX_SLIDES})")
                if info.file_size > MAX_SLIDE_XML_BYTES:
                    raise IntakeError("Slide XML exceeds processing limit")
                slide_xml_total += info.file_size
                if slide_xml_total > MAX_TOTAL_SLIDE_XML_BYTES:
                    raise IntakeError("Combined slide XML exceeds processing limit")
                data = zf.read(info)
                chars, runs = _count_slide_text(data)
                slide_text_chars += chars
                slide_text_runs += runs
                if slide_text_chars > MAX_SLIDE_TEXT_CHARACTERS:
                    raise IntakeError("Slide text character limit exceeded")
                if slide_text_runs > MAX_SLIDE_TEXT_RUNS:
                    raise IntakeError("Slide text run limit exceeded")

            if folded.endswith(".rels"):
                if info.file_size > MAX_RELATIONSHIP_XML_BYTES:
                    raise IntakeError("Relationship XML exceeds processing limit")
                data = zf.read(info)
                has_external, has_forbidden_embedded = _relationship_flags(data, name)
                external = external or has_external
                if has_forbidden_embedded:
                    raise IntakeError("PPTX contains embedded active/package content unsupported in competition V1")

        missing = sorted(_REQUIRED_PACKAGE_PARTS - file_names)
        if missing:
            raise IntakeError(f"PPTX missing required OOXML part: {missing[0]}")
        if slide_count == 0:
            raise IntakeError("PPTX contains no slide parts")

    return IntakeReport(
        sha256=sha256_path(path),
        byte_size=size,
        entries=len(infos),
        uncompressed_size=total,
        slide_count=slide_count,
        has_external_relationships=external,
    )
