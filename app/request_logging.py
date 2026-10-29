"""ASGI request metadata logging without reading request or response bodies."""

import json
import logging
import time
import uuid


LOGGER = logging.getLogger("llm.requests")
SAFE_PATHS = frozenset({"/health", "/metrics", "/v1/models", "/v1/chat/completions", "/v1/completions"})
SAFE_METHODS = frozenset({"GET", "POST", "HEAD", "OPTIONS", "PUT", "PATCH", "DELETE"})


class RequestMetadataMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        started = time.perf_counter()
        request_id = uuid.uuid4().hex
        status = 500
        completed = False
        failed = False

        async def send_with_metadata(message):
            nonlocal status, completed
            if message["type"] == "http.response.start":
                status = message["status"]
                message = dict(message)
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                completed = True

        try:
            await self.app(scope, receive, send_with_metadata)
        except BaseException:
            # Never log exception messages: they may contain submitted text.
            failed = True
            raise
        finally:
            path = scope.get("path", "")
            method = scope.get("method", "")
            LOGGER.info(json.dumps({
                "event": "http_request",
                "request_id": request_id,
                "method": method if method in SAFE_METHODS else "OTHER",
                "path": path if path in SAFE_PATHS else "other",
                "status": status,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "completed": completed,
                "failed": failed,
            }, separators=(",", ":")))
