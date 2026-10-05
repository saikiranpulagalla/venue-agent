from __future__ import annotations

import json
import os
from typing import Any


MAX_REQUEST_BODY_BYTES = max(1024, int(os.getenv("VENUE_MAX_REQUEST_BODY_BYTES", str(22 * 1024 * 1024))))


class _BodyTooLarge(Exception):
    pass


async def _send_json_413(send) -> None:
    body = json.dumps({"detail": "Request body too large"}, separators=(",", ":")).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


class RequestBodyLimitMiddleware:
    """Bound request bytes before multipart parsing/spooling occurs."""

    def __init__(self, app, max_bytes: int = MAX_REQUEST_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                if int(raw_length) > self.max_bytes:
                    await _send_json_413(send)
                    return
            except ValueError:
                pass

        seen = 0
        response_started = False

        async def limited_receive():
            nonlocal seen
            message = await receive()
            if message.get("type") == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise _BodyTooLarge
            return message

        async def tracked_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _BodyTooLarge:
            if not response_started:
                await _send_json_413(send)
            else:
                raise


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")

        async def secured_send(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {k.lower() for k, _ in headers}

                def add(name: bytes, value: bytes) -> None:
                    if name.lower() not in existing:
                        headers.append((name, value))
                        existing.add(name.lower())

                add(b"referrer-policy", b"no-referrer")
                add(b"x-content-type-options", b"nosniff")
                add(b"x-frame-options", b"DENY")
                add(b"permissions-policy", b"geolocation=(), camera=(), microphone=()")
                add(
                    b"content-security-policy",
                    b"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
                )
                if path.startswith("/api/") or path.startswith("/health"):
                    add(b"cache-control", b"no-store")
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, secured_send)
