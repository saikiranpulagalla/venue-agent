from fastapi.testclient import TestClient
import pytest
import hashlib

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


def test_plan_requires_capability_and_cannot_change_session_state(project_root):
    """Planning is session-sensitive: an untrusted handle must be inert."""
    from app.main import STORE

    with TestClient(app) as c:
        src = project_root / "backend/tests/fixtures/small_text.pptx"
        with src.open("rb") as f:
            created = c.post(
                "/api/upload",
                files={"file": ("small_text.pptx", f, "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
            )
        sid = created.json()["session_id"]
        headers = {"X-Venue-Token": created.json()["session_token"]}
        analyzed = c.post(
            f"/api/sessions/{sid}/analyze",
            headers=headers,
            json={"venue": {"active_image_height_m": 1.8, "farthest_viewer_distance_m": 10.8, "measurement_basis": "MEASURED"}},
        )
        assert analyzed.status_code == 200
        before = STORE.get(sid)
        before_state = before.state
        before_candidates = list(before.candidates)

        payload = {"constraints": {"max_mutations": 1, "max_scale_factor": 1.6, "minimum_target_coverage": 0.9, "protect_titles": True, "semantic_rewrite": False}}
        for unauthorized_headers in ({}, {"X-Venue-Token": "wrong-token"}):
            response = c.post(f"/api/sessions/{sid}/plan", headers=unauthorized_headers, json=payload)
            assert response.status_code == 404
            after = STORE.get(sid)
            assert after.state == before_state
            assert after.plan_hash is None
            assert after.approved_plan_hash is None
            assert after.output_path is None
            assert after.candidates == before_candidates

        authorized = c.post(f"/api/sessions/{sid}/plan", headers=headers, json=payload)
        assert authorized.status_code == 200


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


def _verified_output_session(project_root):
    from app.main import STORE
    content = b"verified-output-bytes"
    s = STORE.create("source.pptx", b"source", hashlib.sha256(b"source").hexdigest())
    token = s.take_issued_capability_token()
    output = s.root / "output.pptx"
    output.write_bytes(content)
    s.output_path = output
    s.verified_output_sha256 = hashlib.sha256(content).hexdigest()
    from app.domain.models import WorkflowState
    s.state = WorkflowState.VERIFIED
    return s, {"X-Venue-Token": token}, content


def test_verified_download_serves_exact_verified_byte_snapshot(project_root):
    with TestClient(app) as c:
        s, headers, content = _verified_output_session(project_root)
        response = c.get(f"/api/sessions/{s.session_id}/output", headers=headers)
        assert response.status_code == 200
        assert response.content == content


@pytest.mark.parametrize("replacement", [b"modified-after-verification", None])
def test_changed_or_deleted_verified_output_is_never_downloaded(project_root, replacement):
    with TestClient(app) as c:
        s, headers, _ = _verified_output_session(project_root)
        if replacement is None:
            s.output_path.unlink()
        else:
            s.output_path.write_bytes(replacement)
        response = c.get(f"/api/sessions/{s.session_id}/output", headers=headers)
        assert response.status_code in {404, 409}
        if replacement is not None:
            assert s.state.value == "FAILED"
            assert s.output_path is None


def test_stale_output_path_is_never_downloaded(project_root):
    with TestClient(app) as c:
        s, headers, _ = _verified_output_session(project_root)
        stale = s.root / "stale.pptx"
        stale.write_bytes(b"different artifact")
        s.output_path = stale
        response = c.get(f"/api/sessions/{s.session_id}/output", headers=headers)
        assert response.status_code == 409
        assert s.state.value == "FAILED"
