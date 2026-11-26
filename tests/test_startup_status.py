"""Verify shared startup output, credential safety, and duplicate suppression."""

import contextlib
import io
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chat_proxy
import proxy
from startup_status import build_summary, display_url, format_bytes


class StartupStatusTests(unittest.TestCase):
    def test_public_configuration_and_ipv6_probe(self):
        lines = build_summary(
            mode="combined", host="::", port=9001,
            providers=[("Primary", "https://username:private-key@example.com/v1?token=secret#private", "/v1", True),
                       ("OmniAi", "https://sub.omniai.cc", "/omniai/v1", False)],
            session_filter="session filter: disabled (mode 0)", auth_enabled=True,
            max_body_bytes=1073741824, max_sse_frame_bytes=8388608,
            connect_timeout_s=20, read_timeout_s=600,
        )
        text = "\n".join(lines)
        self.assertIn("http://[::]:9001 (firewall required)", text)
        self.assertIn("http://[::1]:9001/_health", text)
        self.assertIn("Responses + Chat Completions", text)
        self.assertIn("https://example.com/v1 | /v1 | client key or configured fallback", text)
        self.assertIn("https://sub.omniai.cc | /omniai/v1 | client key", text)
        self.assertIn("upload 1 GiB; SSE frame 8 MiB", text)
        self.assertIn("X-Proxy-Token required", text)
        for secret in ("username", "private-key", "secret", "#private"):
            self.assertNotIn(secret, text)

    def test_byte_sizes_and_invalid_url_redaction(self):
        for size, expected in ((0, "0 B"), (512, "512 B"), (1024, "1 KiB"), (20971520, "20 MiB")):
            self.assertEqual(format_bytes(size), expected)
        for raw in ("private-key", "https://host:badport", "file:///private-key"):
            self.assertEqual(display_url(raw), "[invalid upstream URL]")
        self.assertEqual(display_url("https://[::1]:1234/?key=secret"), "https://[::1]:1234")

    def test_summary_does_not_disclose_provider_keys(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"OMNIAI_API_KEY": "private-omniai-key", "FREEGG_API_KEY": "private-freegg-key",
                                     "MOYUU_API_KEY": "private-moyuu-key"}, clear=True), \
                patch.object(proxy, "PROXY_AUTH_TOKEN", "private-local-token"), contextlib.redirect_stdout(output):
            proxy.print_startup_summary(mode="combined", host="127.0.0.1", port=8789)
        text = output.getvalue()
        self.assertEqual(text.count("dev-api-proxy | combined"), 1)
        self.assertIn("Version      " + proxy.VERSION, text)
        self.assertIn("/omniai/v1 | client key or configured fallback", text)
        self.assertIn("/freegg/v1 | client key or configured fallback", text)
        self.assertIn("/moyuu/v1 | client key or configured fallback", text)
        self.assertNotIn("private-", text)
        self.assertNotIn("No fallback AgentRouter", text)


class StartupLifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_application_prints_one_summary(self):
        for module, mode in ((proxy, "responses"), (chat_proxy, "combined")):
            output = io.StringIO()
            with patch.object(module, "LISTEN_HOST", "127.0.0.1"), patch.object(proxy, "_SHARED_CLIENT", None), \
                    patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
                async with module._app_lifespan(module.app):
                    pass
            self.assertEqual(output.getvalue().count(f"dev-api-proxy | {mode}"), 1)
            self.assertIn("/omniai/v1", output.getvalue())

    async def test_launcher_suppresses_only_the_duplicate_worker_summary(self):
        for module in (proxy, chat_proxy):
            output = io.StringIO()
            with patch.object(module, "LISTEN_HOST", "127.0.0.1"), patch.object(proxy, "_SHARED_CLIENT", None), \
                    patch.dict(os.environ, {"PROXY_SUPPRESS_STARTUP_SUMMARY": "1"}), contextlib.redirect_stdout(output):
                async with module._app_lifespan(module.app):
                    pass
            self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
