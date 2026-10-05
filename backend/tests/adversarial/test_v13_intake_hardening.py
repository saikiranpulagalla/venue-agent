from pathlib import Path
import io
import zipfile

import pytest

from app.security.intake import IntakeError, validate_pptx


def _write_zip(path: Path, entries: dict[str, bytes], *, compression=zipfile.ZIP_DEFLATED):
    with zipfile.ZipFile(path, 'w', compression=compression) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def _minimal_parts(slide_xml: bytes = b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'):
    return {
        '[Content_Types].xml': b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        '_rels/.rels': b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
        'ppt/presentation.xml': b'<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
        'ppt/_rels/presentation.xml.rels': b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
        'ppt/slides/slide1.xml': slide_xml,
    }


def test_intake_rejects_zip_that_is_not_an_ooxml_presentation(tmp_path):
    p = tmp_path / 'fake.pptx'
    _write_zip(p, {'ppt/slides/slide1.xml': b'<x/>'})
    with pytest.raises(IntakeError, match='missing required OOXML part'):
        validate_pptx(p)


def test_intake_rejects_package_path_aliases(tmp_path):
    p = tmp_path / 'alias.pptx'
    entries = _minimal_parts()
    entries['ppt/slides/./slide1.xml'] = b'<x/>'
    _write_zip(p, entries)
    with pytest.raises(IntakeError, match='non-canonical ZIP path'):
        validate_pptx(p)


def test_intake_rejects_embedded_ole_or_package_payloads(tmp_path):
    p = tmp_path / 'embedded.pptx'
    entries = _minimal_parts()
    entries['ppt/embeddings/oleObject1.bin'] = b'not-safe-for-v1'
    _write_zip(p, entries)
    with pytest.raises(IntakeError, match='embedded active/package content'):
        validate_pptx(p)


def test_intake_rejects_excessive_slide_text_before_renderer(tmp_path):
    p = tmp_path / 'textbomb.pptx'
    huge_text = b'A' * 350_000
    xml = (b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
           b'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>' + huge_text + b'</a:t></p:sld>')
    _write_zip(p, _minimal_parts(xml), compression=zipfile.ZIP_STORED)
    with pytest.raises(IntakeError, match='text character limit'):
        validate_pptx(p)


def test_intake_rejects_disguised_absolute_uri_without_external_targetmode(tmp_path):
    p = tmp_path / 'uri.pptx'
    entries = _minimal_parts()
    entries['ppt/slides/_rels/slide1.xml.rels'] = (
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        b'Target="file:///etc/passwd"/>'
        b'</Relationships>'
    )
    _write_zip(p, entries)
    with pytest.raises(IntakeError, match='unsafe relationship target'):
        validate_pptx(p)


def test_intake_allows_normal_parent_relative_internal_relationship(tmp_path):
    p = tmp_path / 'normal-rel.pptx'
    entries = _minimal_parts()
    entries['ppt/slides/_rels/slide1.xml.rels'] = (
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        b'Target="../media/image1.png"/>'
        b'</Relationships>'
    )
    entries['ppt/media/image1.png'] = b'png'
    _write_zip(p, entries)
    report = validate_pptx(p)
    assert report.has_external_relationships is False
