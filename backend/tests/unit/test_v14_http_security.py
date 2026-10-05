import asyncio

from fastapi.testclient import TestClient

from app.main import app
from app.security.http import RequestBodyLimitMiddleware


def test_session_public_id_does_not_authorize_without_separate_capability():
    with TestClient(app) as c:
        created = c.post('/api/demo')
        assert created.status_code == 200
        body = created.json()
        sid = body['session_id']
        token = body['session_token']
        assert token and token != sid and token not in sid

        missing = c.get(f'/api/sessions/{sid}')
        wrong = c.get(f'/api/sessions/{sid}', headers={'X-Venue-Token': token + 'x'})
        ok = c.get(f'/api/sessions/{sid}', headers={'X-Venue-Token': token})
        assert missing.status_code == 404
        assert wrong.status_code == 404
        assert ok.status_code == 200
        assert ok.json()['session_id'] == sid


def test_api_security_headers_are_no_store_and_non_referring():
    with TestClient(app) as c:
        r = c.get('/health')
        assert r.status_code == 200
        assert r.headers['cache-control'] == 'no-store'
        assert r.headers['referrer-policy'] == 'no-referrer'
        assert r.headers['x-content-type-options'] == 'nosniff'
        assert r.headers['x-frame-options'] == 'DENY'
        assert 'geolocation=()' in r.headers['permissions-policy']


def test_body_limit_rejects_chunked_request_without_content_length():
    sent = []
    chunks = iter([
        {'type': 'http.request', 'body': b'1234', 'more_body': True},
        {'type': 'http.request', 'body': b'5678', 'more_body': False},
    ])

    async def receive():
        return next(chunks)

    async def send(message):
        sent.append(message)

    async def inner(scope, receive, send):
        while True:
            message = await receive()
            if not message.get('more_body'):
                break
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'ok'})

    middleware = RequestBodyLimitMiddleware(inner, max_bytes=5)
    scope = {'type': 'http', 'path': '/api/upload', 'headers': []}
    asyncio.run(middleware(scope, receive, send))

    assert sent[0]['type'] == 'http.response.start'
    assert sent[0]['status'] == 413
