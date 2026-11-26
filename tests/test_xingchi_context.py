"""Regress the actual per-payload model-turn guard, not only request counts."""

import json
import os
import unittest
from unittest.mock import patch

import httpx

import chat_proxy
import proxy
import xingchi_context
import xingchi_proxy
from tests.test_xingchi_proxy import ByteStream


def history(turns):
    items = []
    for number in range(turns):
        items.extend([{"role": "user", "content": f"Request {number}"},
                      {"role": "assistant", "content": f"Result {number}"}])
    return items + [{"role": "user", "content": "Continue the pending task."}]


def summary_response(text="Earlier decisions and unfinished work."):
    return httpx.Response(200, json={"status": "completed", "output": [{
        "type": "message", "content": [{"type": "output_text", "text": text}],
    }]})


def generation_response():
    return httpx.Response(200, json={"id": "resp-result", "status": "completed", "output": []})


class HistoryBoundaryTests(unittest.TestCase):
    def test_counts_assistant_messages_and_calls_but_not_results_or_reasoning(self):
        items = [{"role": "assistant", "content": "Working"}, {"type": "function_call"},
                 {"type": "function_call_output"}, {"type": "reasoning"}, {"role": "user"}]
        self.assertEqual(xingchi_context.model_turn_count(items), 2)

    def test_prefix_is_stable_as_the_conversation_grows(self):
        for count in (88, 100, 101, 150):
            items = history(count)
            cut = xingchi_context.compaction_cut(items, count)
            self.assertEqual(cut, 128)
        self.assertEqual(xingchi_context.compaction_cut(history(152), 152), 256)

    def test_cut_keeps_parallel_calls_and_results_together(self):
        items = history(62)[:-1] + [
            {"type": "function_call", "call_id": "a"},
            {"type": "function_call", "call_id": "b"},
            {"type": "function_call_output", "call_id": "a", "output": "a"},
            {"type": "function_call_output", "call_id": "b", "output": "b"},
        ] + history(36)
        cut = xingchi_context.compaction_cut(items, 100)
        self.assertEqual(items[cut - 1]["call_id"], "b")
        self.assertEqual(items[cut - 1]["type"], "function_call_output")

    def test_summary_chunks_keep_parallel_call_groups_together(self):
        items = history(60)[:-1] + [
            {"type": "function_call", "call_id": f"parallel-{number}"} for number in range(10)
        ] + [
            {"type": "function_call_output", "call_id": f"parallel-{number}", "output": "done"}
            for number in range(10)
        ] + history(58)[:-1]
        chunks = xingchi_context._summary_chunks(items)
        self.assertEqual([xingchi_context.model_turn_count(chunk) for chunk in chunks], [60, 64, 4])
        self.assertEqual([item for chunk in chunks for item in chunk], items)
        pending = set()
        for chunk in chunks:
            for item in chunk:
                if item.get("type") == "function_call":
                    pending.add(item["call_id"])
                elif item.get("type") == "function_call_output":
                    pending.remove(item["call_id"])
            self.assertFalse(pending)

    def test_summary_chunks_allow_one_larger_safe_parallel_group(self):
        for turns in (80, 100):
            items = [{"type": "function_call", "call_id": str(number)} for number in range(turns)]
            items += [{"type": "function_call_output", "call_id": str(number), "output": "done"}
                      for number in range(turns)]
            chunks = xingchi_context._summary_chunks(items)
            if turns == 80:
                self.assertEqual(chunks, [items])
            else:
                self.assertIsNone(chunks)


class XingchiContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.script = []

        async def respond(request):
            self.calls.append(request)
            if not self.script:
                raise AssertionError("Unexpected upstream call")
            return self.script.pop(0)

        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        for setting in (
            patch.object(proxy, "_SHARED_CLIENT", self.upstream),
            patch.object(proxy, "PROXY_AUTH_TOKEN", ""),
            patch.object(proxy, "MAX_BODY_BYTES", 20971520),
            patch.object(xingchi_proxy, "XINGCHI_SESSION_FILTER_MODE", 2),
            patch.dict(os.environ, {"XINGCHI_API_KEY": "fixture-key"}, clear=True),
        ):
            setting.start()
            self.addCleanup(setting.stop)
        xingchi_context.reset_context_state()
        xingchi_proxy.reset_session_filter_state()
        self.addCleanup(xingchi_context.reset_context_state)
        self.addCleanup(xingchi_proxy.reset_session_filter_state)

    async def asyncTearDown(self):
        await self.upstream.aclose()

    def client(self, app=chat_proxy.app):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local")

    async def test_100_turn_payload_is_summarized_on_desktop_and_codex_routes(self):
        routes = ("/xingchi/v1/responses", "/v1/responses", "/xingchi/codex/v1/responses",
                  "/xingchi/backend-api/codex/responses")
        for app in (proxy.app, chat_proxy.app):
            for path in routes:
                with self.subTest(app=app.title, path=path):
                    xingchi_context.reset_context_state()
                    start = len(self.calls)
                    self.script.extend([summary_response(), generation_response()])
                    model = "test" if "/codex/" in path else "xingchi/test"
                    payload = {"model": model, "input": history(100), "store": False,
                               "instructions": "Original trusted instructions", "previous_response_id": "old-chain",
                               "tools": [{"type": "function", "name": "read_file", "parameters": {}}],
                               "reasoning": {"effort": "high"}, "text": {"verbosity": "low"}}
                    async with self.client(app) as client:
                        result = await client.post(path, json=payload)
                    self.assertEqual(result.status_code, 200)
                    summary = json.loads(self.calls[start].content)
                    forwarded = json.loads(self.calls[start + 1].content)
                    self.assertEqual(xingchi_context.model_turn_count(summary["input"]), 64)
                    self.assertEqual(xingchi_context.model_turn_count(forwarded["input"]), 36)
                    self.assertEqual(forwarded["input"][1:], payload["input"][128:])
                    for name in ("instructions", "tools", "reasoning", "text", "store"):
                        self.assertEqual(forwarded[name], payload[name])
                    self.assertEqual(forwarded["model"], "test")
                    self.assertNotIn("previous_response_id", forwarded)
                    self.assertNotIn("tools", summary)
                    self.assertEqual(xingchi_context.model_turn_count(payload["input"]), 100)
                    self.assertEqual(self.calls[start].headers["authorization"], "Bearer fixture-key")

    async def test_chat_conversion_compacts_after_translation(self):
        for app in (proxy.app, chat_proxy.app):
            for prefix in ("/xingchi/v1", "/xingchi/codex/v1"):
                xingchi_context.reset_context_state()
                start = len(self.calls)
                self.script.extend([summary_response(), generation_response()])
                async with self.client(app) as client:
                    result = await client.post(prefix + "/chat/completions", json={
                        "model": "test", "messages": history(100),
                    })
                self.assertEqual(result.status_code, 200)
                self.assertEqual(xingchi_context.model_turn_count(json.loads(self.calls[start + 1].content)["input"]), 36)

    async def test_summary_and_generation_share_the_configured_client_user_agent(self):
        self.script.extend([summary_response(), generation_response()])
        with patch.object(xingchi_proxy, "USER_AGENT", "opencode/2.4.2"):
            async with self.client() as client:
                result = await client.post("/xingchi/v1/responses", json={"model": "test", "input": history(100)},
                                           headers={"user-agent": "python-httpx"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(self.calls), 2)
        for call in self.calls:
            self.assertEqual(call.headers["user-agent"], "opencode/2.4.2")
            self.assertEqual(call.headers["authorization"], "Bearer fixture-key")

    async def test_trusted_instructions_and_recent_tool_results_are_preserved(self):
        trusted = {"role": "developer", "content": "Preserve the project rules exactly."}
        recent = [{"type": "function_call", "call_id": "recent-call", "name": "read_file", "arguments": "{}"},
                  {"type": "function_call_output", "call_id": "recent-call", "output": "Important file content"}]
        items = [trusted] + history(99)[:-1] + recent
        self.script.extend([summary_response(), generation_response()])
        async with self.client() as client:
            result = await client.post("/xingchi/v1/responses", json={"model": "test", "input": items})
        self.assertEqual(result.status_code, 200)
        forwarded = json.loads(self.calls[1].content)["input"]
        self.assertEqual(forwarded[0], trusted)
        self.assertEqual(forwarded[-2:], recent)
        self.assertEqual(items[0], trusted)

    async def test_cache_is_bounded_and_expired_summaries_are_replaced(self):
        with patch.object(xingchi_context, "MAX_CACHE_ENTRIES", 1):
            self.script.extend([summary_response(), generation_response()] * 2)
            async with self.client() as client:
                for model in ("first", "second"):
                    result = await client.post("/xingchi/v1/responses", json={"model": model, "input": history(100)})
                    self.assertEqual(result.status_code, 200)
            self.assertEqual(len(xingchi_context._SUMMARY_CACHE), 1)
        with patch.object(xingchi_context, "CACHE_TTL_S", -1):
            self.script.extend([summary_response(), generation_response()])
            async with self.client() as client:
                result = await client.post("/xingchi/v1/responses", json={"model": "second", "input": history(100)})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(len(self.calls), 6)

    async def test_disconnected_summary_is_closed_and_not_cached(self):
        stream = ByteStream([b"pending summary"])
        self.script.append(httpx.Response(200, stream=stream))
        with patch.object(xingchi_context, "_read_summary", side_effect=proxy._ClientDisconnected):
            async with self.client() as client:
                result = await client.post("/xingchi/v1/responses", json={"model": "test", "input": history(100)})
        self.assertEqual(result.status_code, 499)
        self.assertTrue(stream.closed)
        self.assertEqual(len(xingchi_context._SUMMARY_CACHE), 0)

    async def test_summary_is_reused_for_an_unchanged_prefix(self):
        self.script.extend([summary_response(), generation_response(), generation_response()])
        async with self.client() as client:
            for turns in (100, 101):
                result = await client.post("/xingchi/v1/responses", json={"model": "test", "input": history(turns)})
                self.assertEqual(result.status_code, 200)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len(xingchi_context._SUMMARY_CACHE), 1)

    async def test_credentials_and_changed_prefixes_do_not_share_summaries(self):
        self.script.extend([summary_response(), generation_response()] * 3)
        async with self.client() as client:
            for number, key in enumerate(("first", "second", "second")):
                items = history(100)
                if number == 2:
                    items[0]["content"] = "Changed old request"
                result = await client.post("/xingchi/v1/responses", json={"model": "test", "input": items},
                                           headers={"authorization": "Bearer " + key})
                self.assertEqual(result.status_code, 200)
        self.assertEqual(len(self.calls), 6)

    async def test_short_histories_disabled_modes_and_manual_compaction_are_unchanged(self):
        async with self.client() as client:
            for mode, turns, path in ((2, 87, "/xingchi/v1/responses"),
                                      (0, 100, "/xingchi/v1/responses"),
                                      (1, 100, "/xingchi/v1/responses"),
                                      (2, 100, "/xingchi/v1/responses/compact")):
                self.script.append(generation_response())
                with patch.object(xingchi_proxy, "XINGCHI_SESSION_FILTER_MODE", mode):
                    result = await client.post(path, json={"model": "test", "input": history(turns)})
                self.assertEqual(result.status_code, 200)
                self.assertEqual(xingchi_context.model_turn_count(json.loads(self.calls[-1].content)["input"]), turns)
        self.assertEqual(len(self.calls), 4)

    async def test_failed_summary_is_closed_and_generation_is_not_sent(self):
        stream = ByteStream([b'{"error":{"message":"rate limit"}}'])
        self.script.append(httpx.Response(429, stream=stream))
        async with self.client() as client:
            result = await client.post("/xingchi/v1/responses", json={"model": "test", "input": history(100)})
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.json()["error"]["code"], "xingchi_context_compaction_failed")
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(stream.closed)
        self.assertEqual(len(xingchi_context._SUMMARY_CACHE), 0)

    async def test_non_json_content_is_not_rewritten(self):
        body = json.dumps({"model": "test", "input": history(100)}).encode()
        self.script.append(generation_response())
        async with self.client() as client:
            result = await client.post("/xingchi/v1/responses", content=body, headers={"content-type": "text/plain"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0].content, body)

    async def test_partial_failed_empty_and_oversized_summaries_are_never_used(self):
        bad = [
            httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=ByteStream([
                b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'])),
            httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=ByteStream([
                b'data: {"type":"response.incomplete","response":{"status":"incomplete"}}\n\n'])),
            summary_response(""), summary_response("x" * (xingchi_context.MAX_SUMMARY_BYTES + 1)),
        ]
        async with self.client() as client:
            for response in bad:
                self.script.append(response)
                before = len(self.calls)
                result = await client.post("/xingchi/v1/responses", json={"model": "test", "input": history(100)})
                self.assertEqual(result.status_code, 502)
                self.assertEqual(len(self.calls), before + 1)
                self.assertEqual(len(xingchi_context._SUMMARY_CACHE), 0)

    async def test_streamed_summary_and_generation_remain_separate(self):
        summary = (b'data: {"type":"response.output_text.delta","delta":"history summary"}\n\n'
                   b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n')
        final = b'event: response.completed\ndata: {"type":"response.completed","response":{"status":"completed"}}\n\n'
        self.script.extend([
            httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=ByteStream([summary])),
            httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=ByteStream([final])),
        ])
        async with self.client() as client:
            result = await client.post("/xingchi/v1/responses", json={"model": "test", "input": history(100), "stream": True})
        self.assertEqual(result.content, final)
        self.assertNotIn(b"history summary", result.content)

    async def test_stream_metadata_does_not_count_against_summary_text_limit(self):
        events = []
        for number in range(4096):
            event = {"type": "response.output_text.delta", "delta": "s", "sequence_number": number,
                     "item_id": "msg-" + "a" * 32, "response_id": "resp-" + "b" * 32,
                     "output_index": 0, "content_index": 0}
            events.append(b"data: " + json.dumps(event).encode() + b"\n\n")
        events.append(b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n')
        self.assertGreater(sum(map(len, events)), xingchi_context.MAX_SUMMARY_BYTES)
        stream = ByteStream(events)
        self.script.extend([
            httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream),
            generation_response(),
        ])
        async with self.client() as client:
            result = await client.post("/xingchi/v1/responses", json={"model": "test", "input": history(100)})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(stream.closed)
        forwarded = json.loads(self.calls[1].content)
        self.assertTrue(forwarded["input"][0]["content"].endswith("s" * 4096))

    async def test_stream_and_extracted_text_each_have_a_size_limit(self):
        for content, setting in (
            (b": " + b"metadata" * 64 + b"\n\n", "MAX_SUMMARY_RESPONSE_BYTES"),
            (b'data: {"type":"response.output_text.delta","delta":"' + b"s" * 129 + b'"}\n\n',
             "MAX_SUMMARY_BYTES"),
            (b'data: {"type":"response.completed","response":{"status":"completed","output":'
             b'[{"type":"message","content":[{"type":"output_text","text":"'
             + b"s" * 129 + b'"}]}]}}\n\n', "MAX_SUMMARY_BYTES"),
        ):
            with self.subTest(setting=setting):
                stream = ByteStream([content])
                self.script.append(httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream))
                before = len(self.calls)
                with patch.object(xingchi_context, setting, 128):
                    async with self.client() as client:
                        result = await client.post("/xingchi/v1/responses", json={"model": "test", "input": history(100)})
                self.assertEqual(result.status_code, 502)
                self.assertEqual(len(self.calls), before + 1)
                self.assertTrue(stream.closed)
                self.assertEqual(len(xingchi_context._SUMMARY_CACHE), 0)

    async def test_large_histories_use_bounded_summary_requests(self):
        for turns, summaries in ((152, 2), (216, 3), (300, 4)):
            with self.subTest(turns=turns):
                xingchi_context.reset_context_state()
                start = len(self.calls)
                self.script.extend([summary_response(f"Summary through chunk {number}.")
                                    for number in range(summaries)])
                self.script.append(generation_response())
                async with self.client() as client:
                    result = await client.post("/xingchi/v1/responses", json={"model": "test", "input": history(turns)})
                self.assertEqual(result.status_code, 200)
                sent = [json.loads(call.content) for call in self.calls[start:]]
                self.assertEqual(len(sent), summaries + 1)
                for number, summary in enumerate(sent[:-1]):
                    self.assertLessEqual(xingchi_context.model_turn_count(summary["input"]), 64)
                    if number:
                        self.assertIn(f"Summary through chunk {number - 1}.", summary["input"][0]["content"])
                forwarded = sent[-1]
                self.assertLess(xingchi_context.model_turn_count(forwarded["input"]), xingchi_context.COMPACT_AT_TURNS)
                self.assertEqual(forwarded["input"][1:], history(turns)[summaries * 128:])
                self.assertIn(f"Summary through chunk {summaries - 1}.", forwarded["input"][0]["content"])

    async def test_growing_history_reuses_earlier_chunk_summaries(self):
        self.script.extend([summary_response("First chunk summary."), generation_response(),
                            summary_response("Combined chunk summary."), generation_response()])
        async with self.client() as client:
            for turns in (100, 164):
                result = await client.post("/xingchi/v1/responses", json={"model": "test", "input": history(turns)})
                self.assertEqual(result.status_code, 200)
        self.assertEqual(len(self.calls), 4)
        next_summary = json.loads(self.calls[2].content)
        self.assertEqual(xingchi_context.model_turn_count(next_summary["input"]), 64)
        self.assertIn("First chunk summary.", next_summary["input"][0]["content"])

    async def test_later_chunk_failure_does_not_forward_partial_history(self):
        self.script.extend([summary_response("First chunk only."), httpx.Response(503)])
        async with self.client() as client:
            result = await client.post("/xingchi/v1/responses", json={"model": "test", "input": history(164)})
            self.assertEqual(result.status_code, 503)
            self.assertEqual(len(self.calls), 2)
            self.script.extend([summary_response("Complete combined history."), generation_response()])
            result = await client.post("/xingchi/v1/responses", json={"model": "test", "input": history(164)})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(self.calls), 4)
        self.assertIn("First chunk only.", json.loads(self.calls[2].content)["input"][0]["content"])
        self.assertIn("Complete combined history.", json.loads(self.calls[3].content)["input"][0]["content"])

    async def test_turn_limit_notice_is_not_cached_as_a_summary(self):
        notices = [
            "This conversation has reached 100 model turns. Run /compact to continue with a summarized context, or /new to start a new session.",
            "当前会话模型已回复 100 轮，达到上限。Codex 请输入 /compact 压缩上下文后继续，或输入 /new 新开会话；其他客户端请新建对话。",
        ]
        async with self.client() as client:
            for notice in notices:
                with self.subTest(notice=notice):
                    self.script.append(summary_response(notice))
                    before = len(self.calls)
                    result = await client.post("/xingchi/v1/responses", json={"model": "test", "input": history(100)})
                    self.assertEqual(result.status_code, 502)
                    self.assertEqual(result.json()["error"]["code"], "xingchi_context_compaction_failed")
                    self.assertEqual(len(self.calls), before + 1)
                    self.assertEqual(len(xingchi_context._SUMMARY_CACHE), 0)

    async def test_pending_tool_call_groups_are_not_split_or_discarded(self):
        items = history(1)[:-1]
        items += [{"type": "function_call", "call_id": f"pending-{number}", "name": "read_file", "arguments": "{}"}
                  for number in range(99)]
        async with self.client() as client:
            result = await client.post("/xingchi/v1/responses", json={"model": "test", "input": items})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
