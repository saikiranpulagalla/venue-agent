import pytest

pytestmark = pytest.mark.resource

from fastapi.testclient import TestClient

from app.main import app


def _upload(client, path):
    with path.open('rb') as f:
        r = client.post('/api/upload', files={'file': (path.name, f, 'application/vnd.openxmlformats-officedocument.presentationml.presentation')})
    assert r.status_code == 200, r.text
    return r.json()['session_id'], {'X-Venue-Token': r.json()['session_token']}


def test_image_only_deck_is_review_required_not_no_action(project_root):
    with TestClient(app) as c:
        sid, headers = _upload(c, project_root/'backend/tests/fixtures/image_only.pptx')
        try:
            r = c.post(f'/api/sessions/{sid}/analyze', headers=headers, json={'venue': {
                'active_image_height_m': 1.8,
                'farthest_viewer_distance_m': 10.8,
                'measurement_basis': 'MEASURED',
            }})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body['state'] == 'REVIEW_REQUIRED'
            assert body['analysis']['summary']['analyzed_elements'] == 0
            assert 'no_analyzable_text_elements' in body['review_reasons']
        finally:
            c.delete(f'/api/sessions/{sid}', headers=headers)


def test_outside_reference_deck_is_review_required_not_no_action(project_root):
    with TestClient(app) as c:
        sid, headers = _upload(c, project_root/'backend/tests/fixtures/safe_large.pptx')
        try:
            r = c.post(f'/api/sessions/{sid}/analyze', headers=headers, json={'venue': {
                'active_image_height_m': 1.0,
                'farthest_viewer_distance_m': 11.0,
                'measurement_basis': 'MEASURED',
            }})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body['state'] == 'REVIEW_REQUIRED'
            assert body['analysis']['summary']['outside_reference_elements'] > 0
            assert 'outside_reference_elements_present' in body['review_reasons']
        finally:
            c.delete(f'/api/sessions/{sid}', headers=headers)
