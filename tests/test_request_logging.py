import io
import json
import logging.config
from pathlib import Path
import unittest

from app.request_logging import RequestMetadataMiddleware


class RequestLoggingTests(unittest.TestCase):
    def run_coroutine(self, coroutine):
        # The fake ASGI app has no external I/O and never suspends.
        try:
            coroutine.send(None)
        except StopIteration as result:
            return result.value
        finally:
            coroutine.close()
        self.fail("Unexpected external I/O in the isolated middleware check")

    async def check_request(self, path, fail=False):
        config = json.loads(Path("config/logging.json").read_text())
        logging.config.dictConfig(config)
        output = io.StringIO()
        logger = logging.getLogger("llm.requests")
        logger.handlers[0].setStream(output)
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"PRIVATE_PROMPT"}

        async def send(message):
            sent.append(message)

        async def application(scope, receive, send):
            await receive()
            if fail:
                raise ValueError("PRIVATE_EXCEPTION_PROMPT")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"PRIVATE_OUTPUT", "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        middleware = RequestMetadataMiddleware(application)
        scope = {"type": "http", "path": path, "method": "POST",
                 "query_string": b"secret=PRIVATE_QUERY",
                 "headers": [(b"authorization", b"Bearer PRIVATE_TOKEN")]}
        if fail:
            with self.assertRaises(ValueError):
                await middleware(scope, receive, send)
        else:
            await middleware(scope, receive, send)
            self.assertEqual(sent[1]["body"], b"PRIVATE_OUTPUT")
            self.assertIn(b"x-request-id", dict(sent[0]["headers"]))
        logging.getLogger("vllm.some_module").error("PRIVATE_INTERNAL_ERROR")
        raw = output.getvalue()
        self.assertNotIn("PRIVATE", raw)
        lines = raw.splitlines()
        self.assertEqual(len(lines), 1)
        return json.loads(lines[0])

    def test_streaming_response_metadata_without_content(self):
        record = self.run_coroutine(self.check_request("/v1/chat/completions"))
        self.assertEqual(record["status"], 200)
        self.assertTrue(record["completed"])
        self.assertFalse(record["failed"])

    def test_unknown_path_is_not_logged_verbatim(self):
        record = self.run_coroutine(self.check_request("/PRIVATE_PATH"))
        self.assertEqual(record["path"], "other")

    def test_exception_content_is_not_logged(self):
        record = self.run_coroutine(self.check_request("/v1/chat/completions", fail=True))
        self.assertEqual(record["status"], 500)
        self.assertTrue(record["failed"])
        self.assertFalse(record["completed"])
