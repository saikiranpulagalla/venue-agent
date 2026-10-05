from fastapi.testclient import TestClient
from app.main import app


def test_readiness_contract_exposes_operational_mode():
    with TestClient(app) as c:
        r = c.get('/health/readiness')
        assert r.status_code == 200
        body = r.json()
        assert body['status'] in {'ready', 'degraded'}
        assert body['reference_profile']
        assert body['planner_mode'] in {'gemini_configured_with_deterministic_fallback', 'deterministic_fallback'}
        assert isinstance(body['demo_fixture'], bool)
        assert body['session_ttl_seconds'] >= 60
        assert body['max_sessions'] >= 1
        assert body['session_backend'] == 'process_local_ephemeral'
        assert body['required_replica_count'] == 1
        assert body['max_concurrent_renders'] >= 1
