"""Moyuu routing, native forwarding, credential isolation, and safety tests."""

import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chat_proxy
import moyuu_proxy
import proxy
import xingchi_proxy
from request_guard import allowed_methods, moyuu_upstream_path
from tests.test_xingchi_proxy import ByteStream


class MoyuuTests(unittest.IsolatedAsyncioTestCase):
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
        for setting in (patch.object(proxy, "_SHARED_CLIENT", self.upstream),
                        patch.object(proxy, "PROXY_AUTH_TOKEN", ""),
                        patch.object(proxy, "MAX_BODY_BYTES", 20971520),
                        patch.object(moyuu_proxy, "UPSTREAM", "https://moyuu.cc"),
                        patch.dict(os.environ, {}, clear=True)):
            setting.start()
            self.addCleanup(setting.stop)

    async def asyncTearDown(self):
        await self.upstream.aclose()

    def client(self, app):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local")

    def test_route_allowlist_and_configuration(self):
        for path in ("/moyuu/models", "/moyuu/v1/models/gpt-test", "/moyuu/v1beta/models",
                     "/moyuu/v1beta/openai/models"):
            self.assertEqual(allowed_methods(path), {"GET"}, path)
        for path in ("/moyuu/chat/completions", "/moyuu/responses", "/moyuu/responses/compact",
                     "/moyuu/completions", "/moyuu/messages", "/moyuu/embeddings", "/moyuu/moderations",
                     "/moyuu/rerank", "/moyuu/edits", "/moyuu/audio/speech", "/moyuu/images/edits",
                     "/moyuu/v1beta/models/gemini-test:streamGenerateContent",
                     "/moyuu/v1/models/gemini-test:embedContent"):
            self.assertEqual(allowed_methods(path), {"POST"}, path)
        for path in ("/moyuu", "/moyuu/admin", "/moyuu/api/token", "/moyuu/v1/files",
                     "/moyuu/messages/count_tokens", "/moyuu/usage", "/moyuu/v1/responses/input_tokens",
                     "/moyuu/v1/models/..", "/moyuu/v1beta/models/x:countTokens",
                     "/moyuu/v1beta/models/x:delete", "/moyuu/v1beta/models/x/other:generateContent"):
            self.assertFalse(allowed_methods(path), path)
        self.assertEqual(moyuu_upstream_path("/moyuu/responses"), "/v1/responses")
        self.assertEqual(moyuu_upstream_path("/moyuu/v1beta/models"), "/v1beta/models")
        self.assertEqual(moyuu_upstream_path("/freegg/responses"), "")
        for raw in ("https://moyuu.cc", "https://moyuu.cc/", "https://moyuu.cc/v1/"):
            self.assertEqual(moyuu_proxy._upstream_base(raw), "https://moyuu.cc")
        for raw in ("https://key@moyuu.cc", "https://moyuu.cc?key=secret", "file:///x", "https://moyuu.cc/api"):
            with self.assertRaisesRegex(ValueError, "MOYUU_UPSTREAM"):
                moyuu_proxy._upstream_base(raw)

    async def test_native_paths_bodies_and_queries_on_both_apps(self):
        body = b'{"model":"test","messages":[{"role":"user","content":"hi"}],"previous_response_id":"keep","reasoning_effort":"xhigh"}'
        for app in (proxy.app, chat_proxy.app):
            async with self.client(app) as client:
                for path in ("chat/completions", "responses", "messages", "images/edits", "audio/speech"):
                    self.results.append(httpx.Response(200, json={
                        "id": "resp", "model": "test", "status": "completed", "output": [],
                    }) if path == "chat/completions" else httpx.Response(200, content=b"native"))
                    result = await client.post("/moyuu/" + path + "?tag=a&tag=b", content=body)
                    if path != "chat/completions":
                        self.assertEqual(result.content, b"native")
                    self.assertEqual(self.calls[-1].url.host, "moyuu.cc")
                    self.assertEqual(
                        self.calls[-1].url.path,
                        "/v1/responses" if path == "chat/completions" else "/v1/" + path,
                    )
                    if path == "chat/completions":
                        translated = json.loads(self.calls[-1].content)
                        self.assertEqual(translated["input"][0]["content"], "hi")
                        self.assertEqual(translated["model"], "test")
                        self.assertNotIn("messages", translated)
                    else:
                        self.assertEqual(self.calls[-1].content, body)
                    self.assertEqual(self.calls[-1].url.params.get_list("tag"), ["a", "b"])

    async def test_chat_completions_are_converted_for_moyuu(self):
        self.results.append(httpx.Response(200, json={
            "id": "resp_1", "model": "test", "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "hello"}]}],
        }))
        async with self.client(proxy.app) as client:
            result = await client.post("/moyuu/v1/chat/completions", json={
                "model": "test", "messages": [{"role": "user", "content": "hi"}],
            })
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["choices"][0]["message"]["content"], "hello")
        self.assertEqual(self.calls[-1].url.path, "/v1/responses")
        self.assertEqual(json.loads(self.calls[-1].content)["input"][0]["content"], "hi")

    async def test_session_filter_does_not_apply_to_moyuu(self):
        body = b'{"model":"test","input":"hi","client_metadata":{"session_id":"keep"}}'
        with patch.object(xingchi_proxy, "XINGCHI_SESSION_FILTER_MODE", 2):
            async with self.client(proxy.app) as client:
                self.results.append(httpx.Response(200, content=b"native"))
                await client.post("/moyuu/responses", content=body, headers={"session_id": "native-session"})
        sent = self.calls[-1]
        self.assertEqual(sent.content, body)
        self.assertEqual(sent.headers["session_id"], "native-session")

    async def test_provider_keys_do_not_cross_namespaces(self):
        with patch.dict(os.environ, {"AGENTROUTER_API_KEY": "agent", "XINGCHI_API_KEY": "xingchi",
                                     "FREEGG_API_KEY": "freegg"}):
            async with self.client(chat_proxy.app) as client:
                self.results.append(httpx.Response(200, json={}))
                await client.get("/moyuu/models")
                self.assertNotIn("authorization", self.calls[-1].headers)
                with patch.dict(os.environ, {"MOYUU_API_KEY": "moyuu-only"}):
                    for headers, expected in (({}, "Bearer moyuu-only"), ({"x-api-key": "native"}, None),
                                              ({"x-goog-api-key": "native"}, None),
                                              ({"authorization": "Bearer client"}, "Bearer client")):
                        self.results.append(httpx.Response(200, json={}))
                        await client.get("/moyuu/models", headers=headers)
                        self.assertEqual(self.calls[-1].headers.get("authorization"), expected)
                    for path, key in (("/xingchi/models", "xingchi"), ("/freegg/models", "freegg"),
                                      ("/v1/models", "agent")):
                        self.results.append(httpx.Response(200, json={}))
                        await client.get(path)
                        self.assertEqual(self.calls[-1].headers["authorization"], "Bearer " + key)

    async def test_guards_and_local_token_stripping(self):
        for app in (proxy.app, chat_proxy.app):
            async with self.client(app) as client:
                self.assertEqual((await client.get("/moyuu/admin")).status_code, 404)
                self.assertEqual((await client.get("/moyuu/responses")).status_code, 405)
                self.assertEqual((await client.delete("/moyuu/models")).status_code, 405)
                with patch.object(proxy, "MAX_BODY_BYTES", 4):
                    self.assertEqual((await client.post("/moyuu/messages", content=b"12345")).status_code, 413)
                with patch.object(proxy, "PROXY_AUTH_TOKEN", "local"):
                    self.assertEqual((await client.get("/moyuu/models")).status_code, 401)
                    self.results.append(httpx.Response(200, json={}))
                    await client.get("/moyuu/models", headers={"x-proxy-token": "local"})
                    self.assertNotIn("x-proxy-token", self.calls[-1].headers)

    async def test_native_streams_and_truncation_are_not_replayed(self):
        for app in (proxy.app, chat_proxy.app):
            async with self.client(app) as client:
                for path, body in (("messages", b'event: message_stop\ndata: {"type":"message_stop"}\n\n'),
                                   ("responses", b'data: {"type":"response.completed"}\n\n')):
                    stream = ByteStream([body[:7], body[7:]], stall=True)
                    self.results.append(httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream))
                    result = await client.post("/moyuu/" + path, json={})
                    self.assertEqual(result.content, body)
                    self.assertTrue(stream.closed)
                complete = (b'event: response.output_text.delta\ndata: {"delta":"hello"}\n\n'
                            b'event: response.completed\ndata: {"response":{"status":"completed"}}\n\n')
                self.results.append(httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=ByteStream([complete])))
                result = await client.post("/moyuu/chat/completions", json={
                    "model": "test", "stream": True, "messages": [{"role": "user", "content": "hi"}],
                })
                self.assertIn(b'"content": "hello"', result.content)
                self.assertIn(b'data: [DONE]', result.content)
                partial = b'event: response.output_text.delta\ndata: {"delta":"partial"}\n\n'
                stream = ByteStream([partial])
                self.results.append(httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream))
                result = await client.post("/moyuu/chat/completions", json={
                    "model": "test", "stream": True, "messages": [{"role": "user", "content": "hi"}],
                })
                self.assertEqual(result.content.count(b"partial"), 1)
                self.assertIn("upstream_stream_interrupted", result.text)
                self.assertIn("[DONE]", result.text)
                self.assertTrue(stream.closed)

    async def test_native_truncation_reports_the_moyuu_label(self):
        partial = b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
        stream = ByteStream([partial])
        self.results.append(httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream))
        async with self.client(proxy.app) as client:
            result = await client.post("/moyuu/responses", json={"stream": True})
        self.assertIn("Moyuu", result.text)
        self.assertIn("upstream_stream_interrupted", result.text)
        self.assertTrue(stream.closed)
        self.assertFalse(self.upstream.is_closed)

    async def test_retry_policy_and_redaction(self):
        self.results.extend([httpx.Response(429, json={}), httpx.Response(200, json={"ok": True})])
        with patch.object(xingchi_proxy, "_wait_retry", new_callable=AsyncMock) as wait:
            async with self.client(proxy.app) as client:
                result = await client.post("/moyuu/responses", json={})
                self.assertEqual(result.status_code, 200)
                self.assertEqual(wait.await_count, 1)
                self.results.append(httpx.Response(429, json={}, headers={"retry-after": "60"}))
                result = await client.post("/moyuu/responses", json={})
                self.assertEqual(result.status_code, 429)
                self.assertEqual(result.headers["retry-after"], "60")
                self.assertEqual(wait.await_count, 1)
                self.results.append(httpx.Response(413, content=b"original"))
                before = len(self.calls)
                result = await client.post("/moyuu/responses", json={})
                self.assertEqual(result.status_code, 413)
                self.assertEqual(result.content, b"original")
                self.assertEqual(len(self.calls), before + 1)
                self.results.append(httpx.ReadError("moyuu-secret"))
                with patch.dict(os.environ, {"MOYUU_API_KEY": "moyuu-secret"}), patch.object(proxy, "RETRY_AMBIGUOUS_ERRORS", False):
                    result = await client.get("/moyuu/models")
                self.assertEqual(result.status_code, 502)
                self.assertEqual(result.json()["error"]["code"], "moyuu_upstream_error")
                self.assertNotIn("moyuu-secret", result.text)

    async def test_health_reports_moyuu(self):
        for module in (proxy, chat_proxy):
            data = await module.health()
            self.assertEqual(data["proxy"], "dev-api-proxy")
            self.assertEqual(data["additional_upstreams"]["moyuu"], "https://moyuu.cc")
            self.assertIn("moyuu", data["provider_model_prefixes"])


if __name__ == "__main__":
    unittest.main()