#!/usr/bin/env python3
"""Tests for `tools/local_model_server.py` (PLAN.md phase N3).

No model is loaded here and no weights are needed: the HTTP layer, the request
validation and the response schema are exercised with a fake generator, and the
one end-to-end test runs the real study harness against that fake server. What
these tests prove is that the shim speaks the schema `eval/providers.py` expects
— not that any particular model is good at writing Nava.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.local_model_server import (  # noqa: E402
    BadRequest, build_completion, make_handler, parse_request,
)

#: A correct answer for `sum_to_n`, used by the end-to-end test.
CORRECT_NAVA = """fn main() {
    let n = read_i64()
    let total = 0
    for i in 1..n + 1 {
        total = total + i
    }
    print(total)
}
"""


class FakeModel:
    """The smallest thing `make_handler` needs, with the real method names."""

    def __init__(self, text: str = "ok", revision: str | None = "abc123"):
        self.name = "fake/model"
        self.revision = revision
        self.text = text
        self.requests: list[dict] = []
        self.prompt_tokens = 11
        self.completion_tokens = 7

    def describe(self) -> dict:
        return {"model": self.name, "revision": self.revision, "dtype": "float32",
                "parameters": 1, "threads": 1, "max_tokens_cap": 64, "seed": 0}

    def generate(self, request: dict):
        self.requests.append(request)
        return self.text, self.prompt_tokens, self.completion_tokens, "stop"


class RequestValidationTests(unittest.TestCase):
    def test_a_valid_request_is_normalised(self):
        request = parse_request({"model": "m", "messages": [{"role": "user", "content": "hi"}],
                                 "temperature": 0.2, "max_tokens": 64, "stop": ["\n\n"]})
        self.assertEqual(request["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(request["temperature"], 0.2)
        self.assertEqual(request["max_tokens"], 64)
        self.assertEqual(request["stop"], ["\n\n"])

    def test_streaming_is_refused_rather_than_faked(self):
        with self.assertRaises(BadRequest) as caught:
            parse_request({"stream": True, "messages": [{"role": "user", "content": "hi"}]})
        self.assertIn("stream", str(caught.exception))

    def test_messages_are_required(self):
        for payload in ({}, {"messages": []}, {"messages": "hi"},
                        {"messages": [{"role": "user"}]},
                        {"messages": [{"content": "hi"}]}):
            with self.subTest(payload=payload), self.assertRaises(BadRequest):
                parse_request(payload)

    def test_an_unsupported_role_is_named_in_the_error(self):
        with self.assertRaises(BadRequest) as caught:
            parse_request({"messages": [{"role": "tool", "content": "hi"}]})
        self.assertIn("tool", str(caught.exception))

    def test_impossible_sampling_settings_are_refused(self):
        base = {"messages": [{"role": "user", "content": "hi"}]}
        for extra in ({"temperature": -1}, {"temperature": 9}, {"max_tokens": 0},
                      {"max_completion_tokens": -5}):
            with self.subTest(extra=extra), self.assertRaises(BadRequest):
                parse_request({**base, **extra})

    def test_a_non_object_body_is_refused(self):
        for payload in ([], "hi", 3):
            with self.subTest(payload=payload), self.assertRaises(BadRequest):
                parse_request(payload)

    def test_stop_sequences_are_ignored_not_silently_honoured(self):
        # The server records but cannot enforce `stop`; it must not pretend to.
        request = parse_request({"messages": [{"role": "user", "content": "hi"}],
                                 "stop": ["STOP"]})
        self.assertEqual(request["stop"], ["STOP"])


class ResponseSchemaTests(unittest.TestCase):
    def test_the_body_matches_the_openai_shape(self):
        body = build_completion(model="m", revision="r1", text="hello", prompt_tokens=5,
                                completion_tokens=2, finish_reason="stop", created=1)
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["created"], 1)
        self.assertEqual(body["model"], "m@r1")
        self.assertEqual(body["choices"][0]["message"],
                         {"role": "assistant", "content": "hello"})
        self.assertEqual(body["choices"][0]["finish_reason"], "stop")
        self.assertEqual(body["usage"], {"prompt_tokens": 5, "completion_tokens": 2,
                                         "total_tokens": 7})

    def test_usage_is_the_sum_of_what_was_processed(self):
        body = build_completion(model="m", revision=None, text="", prompt_tokens=0,
                                completion_tokens=0, finish_reason="stop")
        self.assertEqual(body["usage"]["total_tokens"], 0)
        self.assertEqual(body["model"], "m")

    def test_each_response_gets_its_own_id(self):
        first = build_completion(model="m", revision=None, text="", prompt_tokens=1,
                                 completion_tokens=1, finish_reason="stop")
        second = build_completion(model="m", revision=None, text="", prompt_tokens=1,
                                  completion_tokens=1, finish_reason="stop")
        self.assertNotEqual(first["id"], second["id"])


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = FakeModel()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.model, verbose=False))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _post(self, path: str, payload, *, raw: bool = False):
        data = payload if raw else json.dumps(payload).encode("utf-8")
        if isinstance(data, str):
            data = data.encode("utf-8")
        request = urllib.request.Request(f"{self.base}{path}", data=data, method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as handle:
                return handle.status, json.loads(handle.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_a_completion_round_trips(self):
        status, body = self._post("/chat/completions",
                                  {"model": "fake/model", "temperature": 0.2, "max_tokens": 32,
                                   "messages": [{"role": "user", "content": "hello"}]})
        self.assertEqual(status, 200)
        self.assertEqual(body["choices"][0]["message"]["content"], "ok")
        self.assertEqual(body["usage"]["total_tokens"], 18)
        self.assertEqual(self.model.requests[-1]["max_tokens"], 32)
        self.assertEqual(self.model.requests[-1]["messages"][0]["content"], "hello")

    def test_a_streaming_request_gets_a_400_with_a_reason(self):
        status, body = self._post("/chat/completions",
                                  {"stream": True, "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(status, 400)
        self.assertIn("stream", body["error"]["message"])

    def test_malformed_json_gets_a_400(self):
        status, body = self._post("/chat/completions", "{not json", raw=True)
        self.assertEqual(status, 400)
        self.assertIn("message", body["error"])

    def test_an_unknown_path_gets_a_404(self):
        status, body = self._post("/completions", {"messages": []})
        self.assertEqual(status, 404)
        self.assertIn("unknown path", body["error"]["message"])

    def test_the_models_endpoint_describes_the_weights(self):
        with urllib.request.urlopen(f"{self.base}/models", timeout=10) as handle:
            body = json.loads(handle.read().decode("utf-8"))
        self.assertEqual(body["data"][0]["revision"], "abc123")
        self.assertEqual(body["data"][0]["model"], "fake/model")


class LazinessTests(unittest.TestCase):
    def test_importing_the_server_does_not_import_an_ml_stack(self):
        code = ("import sys; sys.path.insert(0, %r); import tools.local_model_server; "
                "print('torch' in sys.modules, 'transformers' in sys.modules)" % str(ROOT))
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                cwd=str(ROOT), timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(result.stdout.split(), ["False", "False"])


class StudyAgainstTheShimTests(unittest.TestCase):
    """The shim's real contract: `eval/study.py` runs against it unchanged."""

    def test_a_correct_answer_through_the_shim_is_a_measured_result(self):
        model = FakeModel(text=CORRECT_NAVA, revision="deadbeef")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(model, verbose=False))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            out = ROOT / "build" / "test-local-server-study.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            environment = dict(os.environ, NAVA_TEST_LOCAL_KEY="local")
            result = subprocess.run(
                [sys.executable, str(ROOT / "eval" / "study.py"),
                 "--provider", "openai", "--model", "fake/model",
                 "--api-key-env", "NAVA_TEST_LOCAL_KEY",
                 "--base-url", f"http://127.0.0.1:{server.server_address[1]}/v1",
                 "--task", "sum_to_n", "--languages", "nava", "--attempts", "1",
                 "--isolation", "auto", "--out", str(out), "--quiet"],
                capture_output=True, text=True, cwd=str(ROOT), env=environment, timeout=300)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            record = json.loads(out.read_text(encoding="utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            if out.exists():
                out.unlink()
        self.assertEqual(record["provider"]["provider"], "openai-compatible")
        self.assertEqual(record["model_measurements"]["status"], "measured")
        self.assertEqual(record["model_measurements"]["pass_at_1"], 1.0)
        self.assertEqual(record["attempts"][0]["rounds"][0]["model"], "fake/model@deadbeef")
        # The revision is what makes the record reproducible.
        self.assertIn("@deadbeef", record["attempts"][0]["rounds"][0]["model"])


if __name__ == "__main__":
    unittest.main()
