from fastapi.testclient import TestClient
import pytest

from app.main import app

pytestmark = pytest.mark.resource


def test_demo_requires_and_verifies_multi_issue_plan():
    with TestClient(app) as client:
        r = client.post('/api/demo')
        assert r.status_code == 200, r.text
        sid = r.json()['session_id']
        headers = {'X-Venue-Token': r.json()['session_token']}

        r = client.post(
            f'/api/sessions/{sid}/analyze', headers=headers,
            json={'venue': {
                'active_image_height_m': 1.8,
                'farthest_viewer_distance_m': 10.8,
                'measurement_basis': 'ESTIMATED',
            }},
        )
        assert r.status_code == 200, r.text
        analysis = r.json()['analysis']
        assert analysis['summary']['below_target_elements'] == 2
        assert analysis['summary']['meets_target_elements'] == 1

        r = client.post(
            f'/api/sessions/{sid}/plan', headers=headers,
            json={'constraints': {
                'max_mutations': 2,
                'max_scale_factor': 1.6,
                'minimum_target_coverage': 0.9,
                'protect_titles': True,
                'semantic_rewrite': False,
            }},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['state'] == 'AWAITING_APPROVAL', body
        ids = body['decision']['selected_candidate_ids']
        assert len(ids) == 2, body
        assert body['plan_simulation']['safe'] is True
        assert body['plan_simulation']['rendered_text_unchanged'] is True
        assert body['plan_simulation']['target_coverage_after'] >= 0.9
        assert all(body['plan_simulation']['target_improvements'].values())

        reversed_attempt = client.post(
            f'/api/sessions/{sid}/approve-and-apply', headers=headers,
            json={
                'candidate_ids': list(reversed(ids)),
                'plan_hash': body['plan_hash'],
                'idempotency_key': 'multi-demo-wrong-order',
                'approve': True,
            },
        )
        assert reversed_attempt.status_code == 409

        r = client.post(
            f'/api/sessions/{sid}/approve-and-apply', headers=headers,
            json={
                'candidate_ids': ids,
                'plan_hash': body['plan_hash'],
                'idempotency_key': 'multi-demo-idem-1',
                'approve': True,
            },
        )
        assert r.status_code == 200, r.text
        final = r.json()
        assert final['state'] == 'VERIFIED', final
        report = final['verification']
        assert len(report['target_improvements']) == 2
        assert all(report['target_improvements'].values())
        assert report['target_coverage_after'] >= report['target_coverage_before']
