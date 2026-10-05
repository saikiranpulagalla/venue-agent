from fastapi.testclient import TestClient
import pytest

from app.main import app

pytestmark = pytest.mark.resource


def _planned_session(client, project_root):
    src = project_root / "backend/tests/fixtures/small_text.pptx"
    with src.open("rb") as f:
        r = client.post('/api/upload', files={'file': ('small_text.pptx', f, 'application/vnd.openxmlformats-officedocument.presentationml.presentation')})
    sid = r.json()['session_id']
    headers = {'X-Venue-Token': r.json()['session_token']}
    r = client.post(f'/api/sessions/{sid}/analyze', headers=headers, json={'venue': {'active_image_height_m': 1.8, 'farthest_viewer_distance_m': 10.8, 'measurement_basis': 'MEASURED'}})
    assert r.status_code == 200
    r = client.post(f'/api/sessions/{sid}/plan', headers=headers, json={'constraints': {'max_mutations': 1, 'max_scale_factor': 1.6, 'minimum_target_coverage': 0.9, 'protect_titles': True, 'semantic_rewrite': False}})
    assert r.status_code == 200
    return sid, headers, r.json()


def test_stale_plan_hash_cannot_execute(project_root):
    with TestClient(app) as c:
        sid, headers, plan = _planned_session(c, project_root)
        cid = plan['decision']['selected_candidate_ids'][0]
        r = c.post(f'/api/sessions/{sid}/approve-and-apply', headers=headers, json={'candidate_id': cid, 'plan_hash': '0' * 64, 'idempotency_key': 'stale', 'approve': True})
        assert r.status_code == 409


def test_double_apply_is_rejected(project_root):
    with TestClient(app) as c:
        sid, headers, plan = _planned_session(c, project_root)
        cid = plan['decision']['selected_candidate_ids'][0]
        payload = {'candidate_id': cid, 'plan_hash': plan['plan_hash'], 'idempotency_key': 'same-key', 'approve': True}
        first = c.post(f'/api/sessions/{sid}/approve-and-apply', headers=headers, json=payload)
        assert first.status_code == 200
        second = c.post(f'/api/sessions/{sid}/approve-and-apply', headers=headers, json=payload)
        assert second.status_code == 409


def test_plan_evidence_tamper_invalidates_approval(project_root):
    from app.main import STORE
    with TestClient(app) as c:
        sid, headers, plan = _planned_session(c, project_root)
        ids = plan['decision']['selected_candidate_ids']
        assert ids
        session = STORE.get(sid)
        target = next(cand for cand in session.candidates if cand.candidate_id == ids[0])
        target.parameters['scale'] = min(float(target.parameters['scale']) + 0.01, 1.6)
        r = c.post(
            f'/api/sessions/{sid}/approve-and-apply',
            headers=headers,
            json={
                'candidate_ids': ids,
                'plan_hash': plan['plan_hash'],
                'idempotency_key': 'tampered-evidence',
                'approve': True,
            },
        )
        assert r.status_code == 409
        assert 'Plan evidence changed' in r.text


def test_reject_is_bound_to_current_plan_and_executes_nothing(project_root):
    from app.main import STORE
    with TestClient(app) as c:
        sid, headers, plan = _planned_session(c, project_root)
        ids = plan['decision']['selected_candidate_ids']
        wrong = c.post(
            f'/api/sessions/{sid}/approve-and-apply',
            headers=headers,
            json={
                'candidate_ids': ids,
                'plan_hash': '0' * 64,
                'idempotency_key': 'reject-wrong-plan',
                'approve': False,
            },
        )
        assert wrong.status_code == 409
        ok = c.post(
            f'/api/sessions/{sid}/approve-and-apply',
            headers=headers,
            json={
                'candidate_ids': ids,
                'plan_hash': plan['plan_hash'],
                'idempotency_key': 'reject-right-plan',
                'approve': False,
            },
        )
        assert ok.status_code == 200
        assert ok.json()['state'] == 'REJECTED_BY_USER'
        session = STORE.get(sid)
        assert session.output_path is None
        assert not (session.root / 'output.pptx').exists()


def test_reanalysis_explicitly_invalidates_old_plan_and_approval(project_root):
    from app.main import STORE
    with TestClient(app) as c:
        sid, headers, plan = _planned_session(c, project_root)
        ids = plan['decision']['selected_candidate_ids']

        rerun = c.post(
            f'/api/sessions/{sid}/analyze',
            headers=headers,
            json={'venue': {'active_image_height_m': 1.8, 'farthest_viewer_distance_m': 9.0, 'measurement_basis': 'MEASURED'}},
        )
        assert rerun.status_code == 200, rerun.text
        session = STORE.get(sid)
        assert session.plan_hash is None
        assert session.approved_plan_hash is None
        assert session.selected_candidate_ids == []
        assert session.plan_simulation is None

        stale = c.post(
            f'/api/sessions/{sid}/approve-and-apply',
            headers=headers,
            json={
                'candidate_ids': ids,
                'plan_hash': plan['plan_hash'],
                'idempotency_key': 'old-plan-after-reanalysis',
                'approve': True,
            },
        )
        assert stale.status_code == 409


def test_source_tamper_is_blocked_before_any_mutation(project_root):
    from app.main import STORE
    with TestClient(app) as c:
        sid, headers, plan = _planned_session(c, project_root)
        ids = plan['decision']['selected_candidate_ids']
        session = STORE.get(sid)
        session.source_path.write_bytes(session.source_path.read_bytes() + b'\nsource-tamper')

        r = c.post(
            f'/api/sessions/{sid}/approve-and-apply',
            headers=headers,
            json={
                'candidate_ids': ids,
                'plan_hash': plan['plan_hash'],
                'idempotency_key': 'tampered-source',
                'approve': True,
            },
        )
        assert r.status_code == 409
        assert 'Source artifact changed after upload' in r.text
        assert session.state == 'AWAITING_APPROVAL'
        assert session.output_path is None
        assert not (session.root / 'output.pptx').exists()
