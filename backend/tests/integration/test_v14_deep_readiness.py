import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.resource


def test_deep_readiness_performs_real_cached_demo_render():
    with TestClient(app) as c:
        r = c.get('/health/deep-readiness')
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['status'] == 'ready', body
        assert body['renderer_version']
        assert body['renderer_probe'] in {'real_demo_render_pass', 'render_busy_using_recent_success'}
        assert body['session_backend'] == 'process_local_ephemeral'
        assert body['required_replica_count'] == 1
