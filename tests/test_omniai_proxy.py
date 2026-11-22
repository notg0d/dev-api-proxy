"""OmniAi routing, native protocol, credential isolation, and safety tests."""

import asyncio
import gzip
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chat_proxy
import omniai_proxy
import proxy
import xingchi_proxy
from request_guard import allowed_methods, omniai_upstream_path
from tests.test_xingchi_proxy import ByteStream


class OmniAiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.results = []

        async def respond(request):
            self.calls.append(request)
            if not self.results:
                raise AssertionError("Unexpected upstream request")
            result = self.results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        for setting in (
            patch.object(proxy, "_SHARED_CLIENT", self.upstream),
            patch.object(proxy, "PROXY_AUTH_TOKEN", ""),
            patch.object(proxy, "MAX_BODY_BYTES", 20971520),
            patch.object(omniai_proxy, "UPSTREAM", "https://sub.omniai.cc"),
            patch.dict(os.environ, {}, clear=True),
        ):
            setting.start()
            self.addCleanup(setting.stop)
        proxy.reset_rate_limit_state()

    async def asyncTearDown(self):
        await self.upstream.aclose()
        proxy.reset_rate_limit_state()

    def client(self, app):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local")

    def test_route_allowlist_and_upstream_validation(self):
        for path in (
            "/omniai/models", "/omniai/v1/models/gpt-test", "/omniai/usage",
            "/omniai/v1/sub2api/billing", "/omniai/v1beta/models/gemini-test",
            "/omniai/antigravity/v1/models", "/omniai/backend-api/codex/models",
            "/omniai/codex/v1/models", "/omniai/images/tasks/task_123",
        ):
            self.assertEqual(allowed_methods(path), {"GET"}, path)
        for path in (
            "/omniai/responses", "/omniai/chat/completions", "/omniai/messages",
            "/omniai/v1/responses/compact", "/omniai/v1/responses/input_tokens",
            "/omniai/messages/count_tokens", "/omniai/embeddings", "/omniai/images/edits",
            "/omniai/antigravity/v1beta/models/gemini-test:streamGenerateContent",
            "/omniai/v1beta/models/gemini-test:countTokens", "/omniai/codex/v1/chat/completions",
        ):
            self.assertEqual(allowed_methods(path), {"POST"}, path)
        for path in (
            "/omniai", "/omniai/admin", "/omniai/api/v1/keys", "/omniai/v1/files",
            "/omniai/v1/models/..", "/omniai/v1/models/", "/omniai/v1/responses/resp_1",
            "/omniai/backend-api/codex/chat/completions",
            "/omniai/v1beta/models/x:delete", "/omniai/v1beta/models/x/other:generateContent",
        ):
            self.assertFalse(allowed_methods(path), path)
        self.assertEqual(omniai_upstream_path("/omniai/responses"), "/v1/responses")
        self.assertEqual(omniai_upstream_path("/omniai/codex/v1/responses"), "/backend-api/codex/responses")
        self.assertEqual(omniai_upstream_path("/xingchi/responses"), "")
        for raw in ("https://sub.omniai.cc", "https://sub.omniai.cc/", "https://sub.omniai.cc/v1/"):
            self.assertEqual(omniai_proxy._upstream_base(raw), "https://sub.omniai.cc")
        for raw in ("https://key@sub.omniai.cc", "https://sub.omniai.cc?key=secret",
                    "file:///x", "https://sub.omniai.cc/api"):
            with self.assertRaisesRegex(ValueError, "OMNIAI_UPSTREAM"):
                omniai_proxy._upstream_base(raw)

    async def test_native_paths_bodies_and_queries_on_both_apps(self):
        body = b'{"model":"test","messages":[{"role":"user","content":"hi"}],"previous_response_id":"keep","client_metadata":{"session_id":"keep"}}'
        cases = (
            ("POST", "/omniai/chat/completions", "/v1/chat/completions"),
            ("POST", "/omniai/responses", "/v1/responses"),
            ("POST", "/omniai/messages", "/v1/messages"),
            ("POST", "/omniai/antigravity/v1/messages", "/antigravity/v1/messages"),
            ("POST", "/omniai/v1beta/models/gemini-test:generateContent", "/v1beta/models/gemini-test:generateContent"),
            ("POST", "/omniai/responses/compact", "/v1/responses/compact"),
            ("GET", "/omniai/models", "/v1/models"),
            ("GET", "/omniai/codex/v1/models", "/backend-api/codex/models"),
        )
        with patch.object(xingchi_proxy, "XINGCHI_SESSION_FILTER_MODE", 2):
            for app in (proxy.app, chat_proxy.app):
                async with self.client(app) as client:
                    for method, path, target in cases:
                        self.results.append(httpx.Response(200, content=b"native"))
                        result = await client.request(method, path + "?tag=a&tag=b", content=body,
                                                      headers={"session_id": "native-session"})
                        self.assertEqual(result.content, b"native")
                        sent = self.calls[-1]
                        self.assertEqual(str(sent.url.copy_with(query=None)), "https://sub.omniai.cc" + target)
                        self.assertEqual(sent.content, body)
                        self.assertEqual(sent.url.params.get_list("tag"), ["a", "b"])
                        self.assertEqual(sent.headers["session_id"], "native-session")

    async def test_codex_chat_facade_converts_to_omniai_responses(self):
        completion = {"id": "resp_1", "status": "completed", "output_text": "hello"}
        for app in (proxy.app, chat_proxy.app):
            self.results.append(httpx.Response(200, json=completion))
            async with self.client(app) as client:
                result = await client.post("/omniai/codex/v1/chat/completions", json={
                    "model": "test", "messages": [{"role": "user", "content": "hi"}],
                }, headers={"authorization": "Bearer omniai-key"})
            self.assertEqual(result.json()["choices"][0]["message"]["content"], "hello")
            sent = self.calls[-1]
            self.assertEqual(sent.url.path, "/backend-api/codex/responses")
            self.assertEqual(json.loads(sent.content)["input"][0]["content"], "hi")
            self.assertEqual(sent.headers["authorization"], "Bearer omniai-key")
            self.assertNotIn("originator", sent.headers)

    async def test_credentials_never_cross_provider_namespaces(self):
        with patch.dict(os.environ, {
            "CODEX_GATEWAY_API_KEY": "agent-key", "XINGCHI_API_KEY": "xingchi-key", "FREEGG_API_KEY": "freegg-key",
        }):
            async with self.client(chat_proxy.app) as client:
                self.results.append(httpx.Response(200, json={}))
                await client.get("/omniai/models")
                self.assertNotIn("authorization", self.calls[-1].headers)
                with patch.dict(os.environ, {"OMNIAI_API_KEY": "omniai-fallback"}):
                    for headers, expected in (
                        ({}, "Bearer omniai-fallback"), ({"authorization": "Bearer client"}, "Bearer client"),
                        ({"x-api-key": "native"}, None), ({"x-goog-api-key": "native"}, None),
                    ):
                        self.results.append(httpx.Response(200, json={}))
                        await client.get("/omniai/models", headers=headers)
                        self.assertEqual(self.calls[-1].headers.get("authorization"), expected)
                    for path, expected in (("/v1/models", "agent-key"), ("/xingchi/models", "xingchi-key"),
                                           ("/freegg/models", "freegg-key")):
                        self.results.append(httpx.Response(200, json={}))
                        await client.get(path)
                        self.assertEqual(self.calls[-1].headers["authorization"], "Bearer " + expected)

    async def test_guards_local_token_and_compressed_native_body(self):
        for app in (proxy.app, chat_proxy.app):
            async with self.client(app) as client:
                for method, path, status in (("GET", "/omniai/admin", 404), ("GET", "/omniai/responses", 405),
                                             ("DELETE", "/omniai/models", 405)):
                    self.assertEqual((await client.request(method, path)).status_code, status)
                with patch.object(proxy, "MAX_BODY_BYTES", 4):
                    self.assertEqual((await client.post("/omniai/messages", content=b"12345")).status_code, 413)
                with patch.object(proxy, "PROXY_AUTH_TOKEN", "local-token"):
                    self.assertEqual((await client.get("/omniai/models")).status_code, 401)
                    self.assertEqual((await client.get("/omniai/models", headers=[("x-proxy-token", "local-token")] * 2)).status_code, 401)
                    body = gzip.compress(b'{"input":"hi"}')
                    self.results.append(httpx.Response(200, json={}))
                    await client.post("/omniai/responses", content=body, headers={
                        "x-proxy-token": "local-token", "content-type": "application/json",
                        "content-encoding": "gzip", "connection": "x-remove", "x-remove": "private",
                    })
                    sent = self.calls[-1]
                    self.assertEqual(sent.content, body)
                    self.assertEqual(sent.headers["content-encoding"], "gzip")
                    self.assertNotIn("x-proxy-token", sent.headers)
                    self.assertNotIn("x-remove", sent.headers)

    async def test_native_sse_and_stream_ownership(self):
        streams = (
            ("messages", b'event: message_stop\ndata: {"type":"message_stop"}\n\n'),
            ("responses", b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'),
            ("chat/completions", b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n'),
            ("v1beta/models/gemini-test:streamGenerateContent", b'data: {"candidates":[{"finishReason":"STOP"}]}\n\ndata: {"usageMetadata":{"totalTokenCount":1}}\n\n'),
        )
        for app in (proxy.app, chat_proxy.app):
            async with self.client(app) as client:
                for path, body in streams:
                    stream = ByteStream([body[:7], body[7:]])
                    self.results.append(httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream))
                    result = await asyncio.wait_for(client.post("/omniai/" + path, json={"stream": True}), timeout=2)
                    self.assertEqual(result.content, body)
                    self.assertTrue(stream.closed)
                partial = b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
                stream = ByteStream([partial])
                self.results.append(httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream))
                with patch.object(xingchi_proxy, "_wait_retry", new_callable=AsyncMock) as wait:
                    before = len(self.calls)
                    result = await client.post("/omniai/responses", json={"stream": True})
                self.assertEqual(len(self.calls), before + 1)
                self.assertEqual(result.content.count(b"partial"), 1)
                self.assertIn("upstream_stream_interrupted", result.text)
                self.assertIn("OmniAi", result.text)
                self.assertTrue(stream.closed)
                self.assertFalse(self.upstream.is_closed)
                wait.assert_not_awaited()

    async def test_retry_budget_logs_and_permanent_errors(self):
        async with self.client(proxy.app) as client:
            for status in (429, 502, 503, 504):
                self.results.extend([httpx.Response(status, json={}), httpx.Response(200, json={"ok": True})])
                with patch.object(xingchi_proxy, "_wait_retry", new_callable=AsyncMock) as wait, \
                        self.assertLogs("xingchi-proxy", level="INFO") as logs:
                    result = await client.post("/omniai/responses", json={})
                self.assertEqual(result.status_code, 200)
                self.assertEqual(wait.await_count, 1)
                self.assertIn("[OmniAi Recovered]", "\n".join(logs.output))
            for status in (401, 403, 413):
                self.results.append(httpx.Response(status, content=b"original", headers={"x-request-id": "req_1"}))
                before = len(self.calls)
                result = await client.post("/omniai/responses", json={})
                self.assertEqual(result.status_code, status)
                self.assertEqual(result.content, b"original")
                self.assertEqual(result.headers["x-request-id"], "req_1")
                self.assertEqual(len(self.calls), before + 1)
            for headers, payload in (({"retry-after": "60"}, {}), ({}, {"error": {"code": "insufficient_quota"}})):
                self.results.append(httpx.Response(429, headers=headers, json=payload))
                with patch.object(xingchi_proxy, "_wait_retry", new_callable=AsyncMock) as wait:
                    result = await client.post("/omniai/responses", json={})
                self.assertEqual(result.status_code, 429)
                wait.assert_not_awaited()
        self.assertEqual(proxy._RATE_LIMIT_STATE["cooldown_until"], 0)

    async def test_bounded_transport_retries_and_omniai_secret_redaction(self):
        self.results.extend([httpx.ConnectError("omniai-secret") for _ in range(3)])
        with patch.dict(os.environ, {"OMNIAI_API_KEY": "omniai-secret"}), \
                patch.object(xingchi_proxy, "_wait_retry", new_callable=AsyncMock) as wait, \
                self.assertLogs("xingchi-proxy", level="INFO") as logs:
            async with self.client(proxy.app) as client:
                result = await client.get("/omniai/models")
        self.assertEqual(result.status_code, 502)
        self.assertEqual(result.json()["error"]["code"], "omniai_upstream_error")
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(wait.await_count, 2)
        self.assertNotIn("omniai-secret", result.text + "\n".join(logs.output))

    async def test_health_and_codex_discovery_keep_native_format(self):
        for module in (proxy, chat_proxy):
            health = await module.health()
            self.assertEqual(health["additional_upstreams"]["omniai"], "https://sub.omniai.cc")
        body = {"models": [{"slug": "gpt-test", "display_name": "GPT Test"}]}
        self.results.append(httpx.Response(200, json=body))
        async with self.client(proxy.app) as client:
            result = await client.get("/omniai/v1/models?client_version=0.154.0")
        self.assertEqual(result.json(), body)
        self.assertEqual(self.calls[-1].url.params["client_version"], "0.154.0")


if __name__ == "__main__":
    unittest.main()
