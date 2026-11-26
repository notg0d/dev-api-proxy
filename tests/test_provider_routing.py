"""Provider-qualified picker models must route and authenticate independently."""

import asyncio
import gzip
import json
import os
import sys
import unittest
from unittest.mock import patch
import zlib

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chat_proxy
import proxy
import xingchi_proxy
from provider_routing import ProviderModelRouter, split_model_alias
from tests.test_xingchi_proxy import ByteStream


class ProviderRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.results = []

        async def respond(request):
            self.calls.append(request)
            if self.results:
                return self.results.pop(0)
            return httpx.Response(200, json={"id": "resp_1", "status": "completed", "output_text": "hello"})

        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        for setting in (
            patch.object(proxy, "_SHARED_CLIENT", self.upstream),
            patch.object(proxy, "PROXY_AUTH_TOKEN", ""),
            patch.object(proxy, "MAX_BODY_BYTES", 20971520),
            patch.object(xingchi_proxy, "XINGCHI_SESSION_FILTER_MODE", 0),
            patch.dict(os.environ, {"XINGCHI_API_KEY": "xingchi-key-fixture", "OMNIAI_API_KEY": "omniai-key-fixture",
                                    "FREEGG_API_KEY": "freegg-key-fixture", "MOYUU_API_KEY": "moyuu-key-fixture",
                                    "CODEX_GATEWAY_API_KEY": "primary-key-fixture"}, clear=True),
        ):
            setting.start()
            self.addCleanup(setting.stop)

    async def asyncTearDown(self):
        await self.upstream.aclose()

    def client(self, app):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local")

    async def test_picker_aliases_use_only_the_selected_provider_key(self):
        for app in (proxy.app, chat_proxy.app):
            async with self.client(app) as client:
                for provider, host in (("xingchi", "api.xingchiapi.com"), ("omniai", "sub.omniai.cc"),
                                       ("freegg", "freegg.app"), ("moyuu", "moyuu.cc")):
                    for path in ("/v1/responses", "/responses", "/v1/responses/compact"):
                        payload = {"model": provider + "/gpt-test", "input": "hi", "previous_response_id": "keep"}
                        result = await client.post(path + "?tag=a&tag=b", json=payload, headers={
                            "authorization": "Bearer unrelated-client-key", "x-api-key": "unrelated-api-key",
                            "x-goog-api-key": "unrelated-google-key", "cookie": "session=private",
                            "chatgpt-account-id": "private-account", "openai-organization": "private-org",
                        })
                        self.assertEqual(result.status_code, 200)
                        sent = self.calls[-1]
                        self.assertEqual(sent.url.host, host)
                        self.assertEqual(sent.url.params.get_list("tag"), ["a", "b"])
                        self.assertEqual(sent.headers["authorization"], f"Bearer {provider}-key-fixture")
                        for name in ("x-api-key", "x-goog-api-key", "cookie", "chatgpt-account-id", "openai-organization"):
                            self.assertNotIn(name, sent.headers)
                        self.assertEqual(json.loads(sent.content), {"model": "gpt-test", "input": "hi", "previous_response_id": "keep"})
                        self.assertEqual(payload["model"], provider + "/gpt-test")

    async def test_chat_aliases_use_the_native_or_converted_provider_route(self):
        async with self.client(chat_proxy.app) as client:
            for provider, target in (("xingchi", "/v1/responses"), ("omniai", "/v1/chat/completions"),
                                     ("freegg", "/v1/responses"), ("moyuu", "/v1/responses")):
                result = await client.post("/v1/chat/completions", json={
                    "model": provider + "/gpt-test", "messages": [{"role": "user", "content": "hi"}],
                })
                self.assertEqual(result.status_code, 200)
                self.assertEqual(self.calls[-1].url.path, target)
                self.assertEqual(json.loads(self.calls[-1].content)["model"], "gpt-test")

    async def test_desktop_and_codex_routes_rotate_sessions_and_preserve_streams(self):
        routes = (
            ("/v1/responses", "xingchi/gpt-test", "/v1/responses"),
            ("/xingchi/v1/responses", "xingchi/gpt-test", "/v1/responses"),
            ("/xingchi/codex/v1/responses", "gpt-test", "/backend-api/codex/responses"),
            ("/xingchi/backend-api/codex/responses", "gpt-test", "/backend-api/codex/responses"),
        )
        frames = (b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"hello"}\n\n'
                  b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp-1","status":"completed"}}\n\n')
        history = [{"type": "function_call", "call_id": "call-1", "name": "read_file", "arguments": "{}"},
                   {"type": "function_call_output", "call_id": "call-1", "output": "file contents"}]
        self.addCleanup(xingchi_proxy.reset_session_filter_state)
        for app in (proxy.app, chat_proxy.app):
            for path, model, target in routes:
                with self.subTest(app=app.title, path=path):
                    xingchi_proxy.reset_session_filter_state()
                    start = len(self.calls)
                    self.results.extend(httpx.Response(200, headers={"content-type": "text/event-stream"},
                                                       stream=ByteStream([frames])) for _ in range(3))
                    with patch.object(xingchi_proxy, "XINGCHI_SESSION_FILTER_MODE", 2), \
                            patch.object(xingchi_proxy, "SESSION_ROTATE_AFTER", 3), \
                            patch.object(xingchi_proxy.secrets, "token_hex", side_effect=["first", "rotated"]):
                        async with self.client(app) as client:
                            for _ in range(3):
                                result = await client.post(path, json={
                                    "model": model, "input": history, "stream": True, "store": False,
                                    "prompt_cache_key": "desktop-session", "previous_response_id": "resp-previous",
                                }, headers={"session_id": "desktop-session", "x-codex-turn-state": "private",
                                            "x-codex-turn-metadata": "private", "x-proxy-model-routing": "1"})
                                self.assertEqual(result.status_code, 200)
                                self.assertEqual(result.content, frames)
                    calls = self.calls[start:]
                    self.assertEqual([sent.headers["session_id"] for sent in calls], ["first", "first", "rotated"])
                    for sent in calls:
                        self.assertEqual(sent.url.path, target)
                        self.assertNotIn("x-codex-turn-state", sent.headers)
                        self.assertNotIn("x-codex-turn-metadata", sent.headers)
                        self.assertEqual(json.loads(sent.content), {
                            "model": "gpt-test", "input": history, "stream": True, "store": False,
                            "previous_response_id": "resp-previous",
                        })

    async def test_token_count_aliases_do_not_fall_back_to_the_anchor_provider(self):
        async with self.client(proxy.app) as client:
            for provider, host in (("xingchi", "api.xingchiapi.com"), ("omniai", "sub.omniai.cc")):
                result = await client.post("/v1/responses/input_tokens", json={"model": provider + "/gpt-test", "input": "hi"})
                self.assertEqual(result.status_code, 200)
                self.assertEqual(self.calls[-1].url.host, host)
                self.assertEqual(self.calls[-1].url.path, "/v1/responses/input_tokens")
                self.assertEqual(json.loads(self.calls[-1].content)["model"], "gpt-test")
            before = len(self.calls)
            result = await client.post("/xingchi/v1/responses/input_tokens", json={"model": "freegg/gpt-test", "input": "hi"})
            self.assertEqual(result.status_code, 400)
            self.assertEqual(result.json()["error"]["code"], "unsupported_provider_route")
            self.assertEqual(len(self.calls), before)

    async def test_unqualified_and_direct_native_requests_keep_original_auth(self):
        body = b'{ "model": "vendor/gpt-test", "input": "unchanged" }'
        async with self.client(proxy.app) as client:
            result = await client.post("/v1/responses/compact", content=body, headers={"authorization": "Bearer primary-client"})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(self.calls[-1].url.host, "agentrouter.org")
            self.assertEqual(self.calls[-1].content, body)
            self.assertEqual(self.calls[-1].headers["authorization"], "Bearer primary-client")
            result = await client.post("/omniai/responses", json={"model": "gpt-test", "input": "hi"},
                                       headers={"authorization": "Bearer omniai-client"})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(self.calls[-1].headers["authorization"], "Bearer omniai-client")

    async def test_matching_native_alias_keeps_the_existing_client_key(self):
        with patch.dict(os.environ, {"XINGCHI_API_KEY": ""}):
            async with self.client(proxy.app) as client:
                result = await client.post("/xingchi/v1/responses", json={"model": "xingchi/gpt-test", "input": "hi"},
                                           headers={"authorization": "Bearer existing-xingchi-key"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.calls[-1].url.host, "api.xingchiapi.com")
        self.assertEqual(self.calls[-1].headers["authorization"], "Bearer existing-xingchi-key")
        self.assertEqual(json.loads(self.calls[-1].content)["model"], "gpt-test")

    async def test_cross_provider_picker_switch_does_not_forward_the_anchor_key(self):
        async with self.client(chat_proxy.app) as client:
            for provider, host in (("omniai", "sub.omniai.cc"), ("freegg", "freegg.app"), ("moyuu", "moyuu.cc")):
                result = await client.post("/xingchi/v1/responses", json={"model": provider + "/gpt-test", "input": "hi"},
                                           headers={"authorization": "Bearer existing-xingchi-key"})
                self.assertEqual(result.status_code, 200)
                self.assertEqual(self.calls[-1].url.host, host)
                self.assertEqual(self.calls[-1].headers["authorization"], f"Bearer {provider}-key-fixture")
            with patch.dict(os.environ, {"OMNIAI_API_KEY": ""}):
                before = len(self.calls)
                result = await client.post("/xingchi/v1/responses", json={"model": "omniai/gpt-test", "input": "hi"},
                                           headers={"authorization": "Bearer existing-xingchi-key"})
                self.assertEqual(result.status_code, 400)
                self.assertEqual(result.json()["error"]["code"], "provider_api_key_missing")
                self.assertEqual(len(self.calls), before)

    async def test_missing_provider_key_is_a_local_non_retryable_error(self):
        with patch.dict(os.environ, {"OMNIAI_API_KEY": ""}):
            async with self.client(proxy.app) as client:
                result = await client.post("/v1/responses", json={"model": "omniai/gpt-test", "input": "hi"},
                                           headers={"authorization": "Bearer must-not-be-reused"})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.json()["error"]["code"], "provider_api_key_missing")
        self.assertFalse(result.json()["error"]["retryable"])
        self.assertNotIn("must-not-be-reused", result.text)
        self.assertEqual(self.calls, [])

    async def test_local_auth_and_upload_guard_run_before_routing(self):
        for app in (proxy.app, chat_proxy.app):
            self.assertIs(app.user_middleware[0].cls, proxy.ProxyRequestGuard)
            self.assertIs(app.user_middleware[1].cls, ProviderModelRouter)
            async with self.client(app) as client:
                with patch.object(proxy, "PROXY_AUTH_TOKEN", "local-token"):
                    result = await client.post("/v1/responses", json={"model": "xingchi/gpt-test", "input": "hi"})
                    self.assertEqual(result.status_code, 401)
                    result = await client.post("/v1/responses", json={"model": "xingchi/gpt-test", "input": "hi"},
                                               headers=[("x-proxy-token", "local-token")] * 2)
                    self.assertEqual(result.status_code, 401)
                with patch.object(proxy, "MAX_BODY_BYTES", 4):
                    result = await client.post("/v1/responses", json={"model": "xingchi/gpt-test", "input": "hi"})
                    self.assertEqual(result.status_code, 413)
        self.assertEqual(self.calls, [])

    async def test_local_token_never_reaches_the_selected_upstream(self):
        with patch.object(proxy, "PROXY_AUTH_TOKEN", "local-token"):
            async with self.client(proxy.app) as client:
                result = await client.post("/v1/responses", json={"model": "freegg/gpt-test", "input": "hi"},
                                           headers={"x-proxy-token": "local-token", "x-proxy-model-routing": "1"})
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("x-proxy-token", self.calls[-1].headers)
        self.assertNotIn("x-proxy-model-routing", self.calls[-1].headers)

    async def test_opaque_native_encoding_is_preserved_unless_picker_routing_is_required(self):
        async with self.client(proxy.app) as client:
            result = await client.post("/omniai/responses", content=b"opaque-encoded-body", headers={
                "content-type": "application/json", "content-encoding": "unsupported-codec",
                "authorization": "Bearer native-omniai-key",
            })
            self.assertEqual(result.status_code, 200)
            self.assertEqual(self.calls[-1].content, b"opaque-encoded-body")
            self.assertEqual(self.calls[-1].headers["content-encoding"], "unsupported-codec")
            before = len(self.calls)
            result = await client.post("/xingchi/responses", content=b"opaque-encoded-body", headers={
                "content-type": "application/json", "content-encoding": "unsupported-codec",
                "x-proxy-model-routing": "1", "authorization": "Bearer existing-xingchi-key",
            })
            self.assertEqual(result.status_code, 400)
            self.assertEqual(len(self.calls), before)

    async def test_encoded_aliases_are_decoded_and_length_is_recomputed(self):
        original = '{"model":"omniai/gpt-test","input":"你好"}'.encode("utf-8")
        for encoding, compress in (("gzip", gzip.compress), ("deflate", zlib.compress)):
            async with self.client(proxy.app) as client:
                result = await client.post("/v1/responses", content=compress(original), headers={
                    "content-type": "application/json", "content-encoding": encoding,
                })
            self.assertEqual(result.status_code, 200)
            sent = self.calls[-1]
            self.assertEqual(json.loads(sent.content), {"model": "gpt-test", "input": "你好"})
            self.assertNotIn("content-encoding", sent.headers)
            self.assertEqual(int(sent.headers["content-length"]), len(sent.content))

    async def test_compressed_expansion_cannot_bypass_the_body_limit(self):
        body = gzip.compress(json.dumps({"model": "omniai/gpt-test", "input": "x" * 4000}).encode())
        with patch.object(proxy, "MAX_BODY_BYTES", 256):
            async with self.client(proxy.app) as client:
                result = await client.post("/v1/responses", content=body, headers={
                    "content-type": "application/json", "content-encoding": "gzip",
                })
        self.assertEqual(result.status_code, 413)
        self.assertEqual(self.calls, [])

    async def test_alias_does_not_allow_a_different_target_url(self):
        async with self.client(proxy.app) as client:
            result = await client.post("/v1/responses", json={"model": "omniai/vendor/model", "input": "hi"})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(self.calls[-1].url.host, "sub.omniai.cc")
            self.assertEqual(json.loads(self.calls[-1].content)["model"], "vendor/model")
            before = len(self.calls)
            result = await client.post("/v1/responses", json={"model": "omniai/", "input": "hi"})
            self.assertEqual(result.status_code, 400)
            self.assertEqual(result.json()["error"]["code"], "invalid_model_alias")
            self.assertEqual(len(self.calls), before)

    async def test_aliased_sse_retains_disconnect_cancellation(self):
        stream = ByteStream([b'data: {"type":"response.output_text.delta","delta":"first"}\n\n'], stall=True)
        self.results.append(httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream))
        messages = asyncio.Queue()
        await messages.put({"type": "http.request", "body": b'{"model":"omniai/gpt-test","input":"hi","stream":true}', "more_body": False})
        first = asyncio.Event()

        async def send(message):
            if b"first" in message.get("body", b""):
                first.set()

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                 "scheme": "http", "path": "/v1/responses", "raw_path": b"/v1/responses", "query_string": b"",
                 "root_path": "", "headers": [], "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 2)}
        task = asyncio.create_task(chat_proxy.app(scope, messages.get, send))
        try:
            await asyncio.wait_for(first.wait(), timeout=2)
            self.assertFalse(task.done())
            await messages.put({"type": "http.disconnect"})
            await asyncio.wait_for(task, timeout=2)
            self.assertTrue(stream.closed)
            self.assertFalse(self.upstream.is_closed)
            self.assertEqual(self.calls[-1].url.host, "sub.omniai.cc")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class ModelAliasTests(unittest.TestCase):
    def test_alias_parsing(self):
        self.assertEqual(split_model_alias("OmniAi/vendor/gpt-test"), ("omniai", "vendor/gpt-test"))
        self.assertIsNone(split_model_alias("gpt-test"))
        self.assertIsNone(split_model_alias("unrelated/gpt-test"))
        for model in ("omniai/", "xingchi/ ", "freegg/gpt\n", "moyuu/"):
            with self.assertRaises(ValueError):
                split_model_alias(model)


if __name__ == "__main__":
    unittest.main()
