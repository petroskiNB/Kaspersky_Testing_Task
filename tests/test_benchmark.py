import io
import json
import unittest
from unittest.mock import Mock, patch

from scripts.benchmark import request_once, summarize


class BenchmarkTests(unittest.TestCase):
    def test_stream_timing_uses_content_and_usage_not_sse_chunk_count(self):
        chunks = [
            {"choices": [{"delta": {"role": "assistant"}}]},
            {"choices": [{"delta": {"content": "first"}}]},
            {"choices": [{"delta": {"content": "second"}, "finish_reason": "length"}]},
            {"choices": [], "usage": {"prompt_tokens": 25, "completion_tokens": 5}},
        ]
        stream = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks)
        opener = Mock()
        opener.open.return_value = io.BytesIO(stream + b"data: [DONE]\n\n")
        with patch('urllib.request.build_opener', return_value=opener), \
             patch('scripts.benchmark.time.perf_counter', side_effect=[0, 1, 2, 3]):
            result = request_once('http://127.0.0.1:8001', 'SECRET', 'short', 64, 10)
        self.assertTrue(result['ok'])
        self.assertEqual(result['ttft_s'], 1)
        self.assertEqual(result['e2e_s'], 3)
        self.assertEqual(result['tpot_s'], 0.25)
        self.assertEqual(result['output_tokens'], 5)
        self.assertNotIn('SECRET', json.dumps(result))

    def test_incomplete_stream_is_not_reported_as_success(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'data: {"choices": []}\n\n')
        with patch('urllib.request.build_opener', return_value=opener):
            result = request_once('http://127.0.0.1:8001', 'SECRET', 'short', 64, 10)
        self.assertFalse(result['ok'])

    def test_aggregate_throughput_includes_time_and_failures(self):
        rows = [{'ok': True, 'ttft_s': 1, 'e2e_s': 2, 'output_tokens': 10,
                 'prompt_tokens': 5, 'tpot_s': 0.1, 'decode_tokens_s': 10},
                {'ok': False, 'e2e_s': 4}]
        result = summarize(rows, 5)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['requests_s'], 0.2)
        self.assertEqual(result['output_tokens_s'], 2)
