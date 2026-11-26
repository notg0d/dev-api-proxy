"""Native Xingchi/Sub2API relay, isolated from AgentRouter API conversion."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import json
import logging
import math
import os
import random
import re
import secrets
import time
import zlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from provider_routing import PROVIDER_LABELS
from request_guard import XINGCHI_PREFIX, xingchi_upstream_path
import xingchi_context


def _upstream_base(raw: str) -> str:
    parts = urlsplit(raw.strip())
    if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment or parts.path.rstrip("/") not in ("", "/v1")):
        raise ValueError("XINGCHI_UPSTREAM must be an HTTP(S) server URL without credentials, query, or fragment; /v1 is optional")
    return f"{parts.scheme}://{parts.netloc}"


UPSTREAM = _upstream_base(os.environ.get("XINGCHI_UPSTREAM", "https://api.xingchiapi.com"))
USER_AGENT = os.environ.get("XINGCHI_USER_AGENT", "").strip()
logger = logging.getLogger("xingchi-proxy")
MAX_429_RETRIES = 2
MAX_429_RECOVERY_S = 8.0
MAX_SERVER_ERROR_RETRIES = 2
MAX_SERVER_ERROR_RECOVERY_S = 8.0
RETRYABLE_SERVER_STATUSES = frozenset({500, 502, 503, 504})
PERMANENT_PROVIDER_ERROR_CODES = frozenset({
    "api_key_required", "invalid_api_key", "unauthenticated", "permission_denied",
})
_SESSION_FILTER_NAMES = frozenset({
    "codex-session", "codex_session", "codexsession", "codex-session-id", "codex_session_id",
    "codexsessionid", "session-id", "session_id", "sessionid", "thread-id", "thread_id",
    "threadid", "x-client-request-id", "x-codex-window-id", "x-codex-turn-metadata",
    "prompt_cache_key", "client_metadata", "conversation_id", "x-conversation-id", "x-session-id",
    "x-session-affinity", "x-opencode-session", "x-thread-id", "x-codex-thread-id",
    "installation_id", "turn_id", "parent_turn_id", "root_turn_id", "parent_thread_id",
    "forked_from_thread_id", "window_id", "context_window_id", "x-codex-parent-thread-id",
    "x-codex-installation-id", "x-codex-turn-state", "x-codex-routing-hint",
})
_SESSION_HEADER_NAMES = (
    "x-session-id", "session-id", "session_id", "x-codex-session-id", "codex-session-id",
    "codex-session", "thread-id", "x-thread-id", "x-codex-thread-id", "x-opencode-session",
)
_PRESERVED_JSON_FIELDS = frozenset({"input", "messages", "tools", "functions", "text", "response_format"})
_JSON_CONTENT_TYPE = re.compile(r"^application/(?:[\w.-]+\+)?json(?:\s*;|$)", re.IGNORECASE)
_SESSION_FILTER_MODE_NAMES = {"0": 0, "1": 1, "2": 2}


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


_NORMALIZED_SESSION_HEADER_NAMES = frozenset(_normalize_name(name) for name in _SESSION_HEADER_NAMES)


def _configured_session_filter_mode() -> int:
    raw = os.environ.get("XINGCHI_SESSION_FILTER_MODE", "0").strip()
    try:
        return _SESSION_FILTER_MODE_NAMES[raw]
    except KeyError as exc:
        raise ValueError("XINGCHI_SESSION_FILTER_MODE must be 0, 1, or 2") from exc


XINGCHI_SESSION_FILTER_MODE = _configured_session_filter_mode()
XINGCHI_SESSION_FILTER_NAMES = tuple(
    name.strip() for name in os.environ.get("XINGCHI_SESSION_FILTER_NAMES", "").split(",") if name.strip()
)
_CUSTOM_FILTER_NAMES = frozenset(_normalize_name(name) for name in XINGCHI_SESSION_FILTER_NAMES)
_NORMALIZED_FILTER_NAMES = frozenset(
    _normalize_name(name)
    for name in (*_SESSION_FILTER_NAMES, *XINGCHI_SESSION_FILTER_NAMES)
)
_SESSION_REPLACEMENTS: dict[str, str] = {}
_SESSION_COUNTS: dict[str, int] = {}
SESSION_ROTATE_AFTER = 88


def prepare_client_headers(headers) -> dict:
    """Apply an optional Xingchi client identifier without changing credentials."""
    if not USER_AGENT:
        return dict(headers)
    output = {name: value for name, value in headers.items() if name.lower() != "user-agent"}
    output["user-agent"] = USER_AGENT
    return output


def session_filter_status(mode: int | None = None) -> str:
    """Return a concise status line for startup and diagnostics."""
    mode = XINGCHI_SESSION_FILTER_MODE if mode is None else mode
    if mode == 0:
        return "disabled (mode 0)"
    if mode == 1:
        return "metadata filtering (mode 1)"
    if mode == 2:
        return (f"metadata filtering; session rotation every {SESSION_ROTATE_AFTER} requests (mode 2); "
                f"history summaries at {xingchi_context.COMPACT_AT_TURNS} model turns")
    return f"unknown mode ({mode})"


def is_filtered_name(name: str) -> bool:
    normalized = _normalize_name(name)
    return normalized in _NORMALIZED_FILTER_NAMES or (
        "codex" in normalized and ("session" in normalized or "requestid" in normalized)
    )


def filter_headers(headers, *, mode: int | None = None, rotate_session: bool = True) -> dict:
    """Remove session-related headers; mode 2 retains the IDs it rotates."""
    mode = XINGCHI_SESSION_FILTER_MODE if mode is None else mode
    if mode == 0:
        return dict(headers)
    output = {}
    for name, value in headers.items():
        normalized = _normalize_name(name)
        if (mode == 2 and normalized in _NORMALIZED_SESSION_HEADER_NAMES
                and normalized not in _CUSTOM_FILTER_NAMES):
            output[name] = value
        elif not is_filtered_name(name):
            output[name] = value
    if mode == 2 and rotate_session:
        session_header = next((name for name in output
                               if _normalize_name(name) in _NORMALIZED_SESSION_HEADER_NAMES and output.get(name)), None)
        # Scope anonymous clients and reused client IDs to their upstream credentials.
        # Always send an explicit ID so Sub2API does not derive one from the input.
        if session_header is not None or "sessionid" not in _CUSTOM_FILTER_NAMES:
            credentials = sorted((name.lower(), value) for name, value in headers.items()
                                 if name.lower() in ("authorization", "x-api-key", "x-goog-api-key"))
            session_key = json.dumps([credentials, output.get(session_header)], separators=(",", ":"))
            replacement = _rotate_session_id(session_key)
            for name in output:
                if _normalize_name(name) in _NORMALIZED_SESSION_HEADER_NAMES:
                    output[name] = replacement
            if "sessionid" not in _CUSTOM_FILTER_NAMES:
                output["session_id"] = replacement
    return output


def filter_query_params(params, *, mode: int | None = None) -> list[tuple[str, str]]:
    """Filter query parameter names while preserving order and duplicate values."""
    mode = XINGCHI_SESSION_FILTER_MODE if mode is None else mode
    return list(params) if mode == 0 else [(name, value) for name, value in params if not is_filtered_name(name)]


def filter_json_value(value):
    """Filter session fields recursively without changing conversation or tool data."""
    if isinstance(value, list):
        return [filter_json_value(item) for item in value]
    if isinstance(value, dict):
        filtered = {}
        for name, item in value.items():
            if is_filtered_name(name):
                continue
            if name.lower() in _PRESERVED_JSON_FIELDS:
                filtered[name] = item
            else:
                filtered[name] = filter_json_value(item)
        return filtered
    return value


def _session_fingerprint(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _rotate_session_id(session_id: str) -> str:
    """Assign a fresh upstream ID, counting every send toward its rotation."""
    key = _session_fingerprint(session_id)
    count = _SESSION_COUNTS.get(key, 0) + 1
    if key not in _SESSION_REPLACEMENTS or count >= SESSION_ROTATE_AFTER:
        _SESSION_REPLACEMENTS[key] = secrets.token_hex(16)
        count = 1
    _SESSION_COUNTS[key] = count
    return _SESSION_REPLACEMENTS[key]


def reset_session_filter_state() -> None:
    """Clear the in-memory rotation state (primarily for tests)."""
    _SESSION_REPLACEMENTS.clear()
    _SESSION_COUNTS.clear()


def _zlib_decompress_limited(data: bytes, window_bits: int, limit: int) -> bytes:
    decoder = zlib.decompressobj(window_bits)
    output = bytearray()
    for offset in range(0, len(data), 65536):
        remaining = limit + 1 - len(output)
        if remaining <= 0:
            raise ValueError("decompressed JSON body exceeds the configured body limit")
        output.extend(decoder.decompress(data[offset:offset + 65536], remaining))
        if len(output) > limit or decoder.unconsumed_tail:
            raise ValueError("decompressed JSON body exceeds the configured body limit")
    remaining = limit + 1 - len(output)
    output.extend(decoder.flush(max(1, remaining)))
    if len(output) > limit:
        raise ValueError("decompressed JSON body exceeds the configured body limit")
    if not decoder.eof:
        raise ValueError("compressed JSON request body is incomplete")
    return bytes(output)


def _decompress_body(data: bytes, encodings, limit: int) -> bytes:
    for encoding in reversed(encodings):
        if encoding in ("", "identity"):
            continue
        if encoding == "gzip":
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError("decompressed JSON body exceeds the configured body limit")
        elif encoding == "deflate":
            try:
                data = _zlib_decompress_limited(data, zlib.MAX_WBITS, limit)
            except zlib.error:
                data = _zlib_decompress_limited(data, -zlib.MAX_WBITS, limit)
        elif encoding == "br":
            try:
                import brotli
            except ImportError:
                try:
                    import brotlicffi as brotli
                except ImportError as exc:
                    raise ValueError("Brotli JSON bodies require brotli or brotlicffi") from exc
            data = brotli.decompress(data)
            if len(data) > limit:
                raise ValueError("decompressed JSON body exceeds the configured body limit")
        elif encoding == "zstd":
            try:
                import zstandard
            except ImportError as exc:
                raise ValueError("Zstandard JSON bodies require the zstandard package") from exc
            data = zstandard.ZstdDecompressor().decompress(data, max_output_size=limit + 1)
            if len(data) > limit:
                raise ValueError("decompressed JSON body exceeds the configured body limit")
        else:
            raise ValueError(f"unsupported content-encoding: {encoding}")
    return data


def filter_json_body(body: bytes, original_headers, output_headers, *, max_body_bytes: int):
    """Filter an encoded JSON body and return its new bytes and headers."""
    content_type = next((value for name, value in original_headers.items() if name.lower() == "content-type"), "")
    if not body or not _JSON_CONTENT_TYPE.match(content_type):
        return body, output_headers, None
    content_encoding = next((value for name, value in original_headers.items() if name.lower() == "content-encoding"), "identity")
    encodings = [item.strip().lower() for item in content_encoding.split(",") if item.strip()]
    try:
        decoded = _decompress_body(body, encodings, max_body_bytes)
        value = json.loads(decoded)
        cleaned = json.dumps(filter_json_value(value), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except Exception as exc:
        error = JSONResponse(status_code=400, content={"error": {
            "type": "invalid_request_error", "code": "invalid_request_body",
            "message": f"Cannot filter JSON request body: {exc}",
        }})
        return body, output_headers, error
    if len(cleaned) > max_body_bytes:
        error = JSONResponse(status_code=413, content={"error": {
            "type": "invalid_request_error", "code": "payload_too_large",
            "message": "Filtered JSON body exceeds PROXY_MAX_BODY_BYTES",
        }})
        return body, output_headers, error
    cleaned_headers = {name: value for name, value in output_headers.items()
                       if name.lower() not in ("content-encoding", "content-length")}
    return cleaned, cleaned_headers, None


def prepare_session_filtered_request(headers, body: bytes, params, *, backend, mode: int | None = None,
                                     rotate_session: bool = True):
    """Prepare a Xingchi request after filtering headers, query, and JSON fields."""
    mode = XINGCHI_SESSION_FILTER_MODE if mode is None else mode
    if mode == 0:
        return dict(headers), body, list(params), None
    filtered_headers = filter_headers(headers, mode=mode, rotate_session=rotate_session)
    filtered_params = filter_query_params(params, mode=mode)
    filtered_body, filtered_headers, error = filter_json_body(
        body, headers, filtered_headers, max_body_bytes=backend.MAX_BODY_BYTES,
    )
    return filtered_headers, filtered_body, filtered_params, error


def _retry_delay(headers, retry: int) -> float:
    """Honor valid upstream hints without shortening them to fit our budget."""
    for name, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            delay = float(raw) * scale
        except ValueError:
            if name != "retry-after":
                continue
            try:
                date = parsedate_to_datetime(raw)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                delay = max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                continue
        if math.isfinite(delay) and delay >= 0:
            return delay + random.uniform(0.1, 0.4)
    return min(2.0 ** retry, 8.0) + random.uniform(0.1, 0.4)


async def _wait_retry(backend, request: Request, delay: float) -> None:
    """Do not touch AgentRouter's cooldown or its rate-limit wait counters."""
    async def wait():
        await asyncio.sleep(delay)
    await backend._run_unless_disconnected(wait, request)


def _stream_error(path: str, code: str, message: str) -> bytes:
    if "v1beta/" in path:
        payload = {"error": {"code": 502, "message": message, "status": "UNAVAILABLE"}}
        event = b""
    elif path.endswith("/messages"):
        payload = {"type": "error", "error": {"type": "api_error", "message": message}}
        event = b"event: error\n"
    else:
        payload = {"error": {"type": "proxy_error", "code": code, "message": message}}
        event = b"event: error\n" if path.endswith("/responses") else b""
    return event + b"data: " + json.dumps(payload).encode("utf-8") + b"\n\n"


def _terminal_frame(backend, frame: bytes) -> tuple[bool, bool]:
    """Return (terminal_seen, stop_now); Gemini can send usage after finishReason."""
    event, obj, data = backend._parse_sse_frame(frame)
    if data == "[DONE]":
        return True, True
    if isinstance(obj, dict):
        if event in ("message_stop", "error", "response.completed", "response.incomplete", "response.failed"):
            return True, True
        if event in ("image_generation.completed", "image_edit.completed"):
            return True, False  # Multiple images can have separate completed events.
        if obj.get("error"):
            return True, True
        feedback = obj.get("promptFeedback")
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            return True, False
        candidates = obj.get("candidates")
        if isinstance(candidates, list) and any(isinstance(item, dict) and item.get("finishReason") for item in candidates):
            return True, False
    return False, False


async def _relay_stream(backend, response: httpx.Response, path: str, label: str = "Xingchi"):
    """Forward bounded native frames without replaying output or inventing success."""
    frames = backend._SSEFrameBuffer()
    terminal_seen = False
    try:
        async for chunk in response.aiter_bytes():
            for frame in frames.feed(chunk):
                terminal, stop = _terminal_frame(backend, frame)
                terminal_seen = terminal_seen or terminal
                yield frame
                if stop:
                    return
        if frames.buf:
            frame = bytes(frames.buf)
            terminal, _ = _terminal_frame(backend, frame)
            terminal_seen = terminal_seen or terminal
            yield frame + b"\n\n"
        if not terminal_seen:
            yield _stream_error(path, "upstream_stream_interrupted", f"{label} stream ended without a terminal event. Partial output was not replayed.")
    except backend.SSEFrameTooLarge:
        yield _stream_error(path, "upstream_sse_frame_too_large", f"{label} SSE frame exceeds PROXY_MAX_SSE_FRAME_BYTES.")
    except Exception as exc:
        logger.warning("[%s Stream Interrupted] %s", label, backend.describe_exception(exc))
        yield _stream_error(path, "upstream_stream_interrupted", f"{label} stream was interrupted. Partial output was not replayed.")
    finally:
        await backend._close_upstream_response(response)


class _ProviderLogger(logging.LoggerAdapter):
    def process(self, message, kwargs):
        return message.replace("Xingchi", self.extra["label"]), kwargs


def register_chat_conversion(
    app: FastAPI, backend, *, prefix: str, upstream, provider: str,
    endpoint_path: str = "/v1/responses",
) -> None:
    """Translate Chat Completions to the provider's Responses endpoint."""
    async def handle_chat(request: Request):
        backend.STATS["requests_total"] += 1
        import chat_proxy

        payload, error = await backend.read_json_payload(request)
        if error is not None:
            return error
        messages = payload.get("messages")
        if (not isinstance(messages, list) or not messages
                or any(not isinstance(item, dict) for item in messages)
                or any(item.get("role") not in ("system", "developer", "user", "assistant", "tool") for item in messages)):
            return JSONResponse(status_code=400, content={"error": {
                "type": "invalid_request_error", "message": "messages must be a non-empty array with supported roles",
            }})

        headers = backend._filter_headers(
            dict(request.headers), fallback_keys=(provider.upper() + "_API_KEY",), agentrouter_compat=False,
        )
        if provider == "xingchi":
            headers = prepare_client_headers(headers)
        headers["content-type"] = "application/json"
        body = json.dumps(chat_proxy.build_responses_payload(payload), ensure_ascii=False).encode("utf-8")
        params = request.query_params.multi_items()
        if provider == "xingchi":
            # The translated JSON body is newly built and is not content-encoded.
            headers.pop("content-encoding", None)
            headers, body, params, filter_error = prepare_session_filtered_request(
                headers, body, params, backend=backend, rotate_session=False,
            )
            if filter_error is not None:
                return filter_error
        response = None
        try:
            if provider == "xingchi" and XINGCHI_SESSION_FILTER_MODE == 2:
                body, context_error = await xingchi_context.compact_request(
                    backend, request, url=(upstream() if callable(upstream) else upstream) + endpoint_path,
                    headers=headers, body=body, params=params,
                    prepare_headers=lambda: filter_headers(headers, mode=2),
                )
                if context_error is not None:
                    return context_error
            response = await backend._send_with_transport_retry(
                backend.get_shared_client(), method="POST", url=(upstream() if callable(upstream) else upstream) + endpoint_path,
                headers=headers, content=body, params=params,
                stream=bool(payload.get("stream")), max_retries=2, request=request,
                **({"prepare_headers": lambda: filter_headers(headers, mode=2)}
                   if provider == "xingchi" and XINGCHI_SESSION_FILTER_MODE == 2 else {}),
            )
            response_headers = backend._clean_response_headers(response.headers)
            if response.is_success and "text/event-stream" in response.headers.get("content-type", "").lower():
                queue = asyncio.Queue(maxsize=32)
                producer_exc = []
                producer_task = asyncio.create_task(backend._sse_producer(response, queue, producer_exc))
                return backend._UpstreamStreamingResponse(
                    chat_proxy._translate_responses_stream(
                        response, primed_chunks=None, remainder=b"", queue=queue,
                        producer_task=producer_task, producer_exc=producer_exc, model=payload["model"],
                    ), upstream_response=response, producer_task=producer_task,
                    status_code=response.status_code, headers={**response_headers, "cache-control": "no-cache"},
                    media_type="text/event-stream",
                )
            content = await backend._read_and_close_response(response, request)
            if not response.is_success:
                return Response(content=content, status_code=response.status_code, headers=response_headers)
            parsed = json.loads(content)
            return JSONResponse(content=chat_proxy.responses_json_to_chat(parsed, payload["model"]),
                                status_code=response.status_code, headers=response_headers)
        except backend._ClientDisconnected:
            return Response(status_code=499)
        except Exception as exc:
            if response is not None:
                await backend._close_upstream_response(response)
            return JSONResponse(status_code=502, content={"error": {
                "type": "proxy_error", "code": f"{provider}_upstream_error",
                "message": f"{provider} upstream error: {backend.describe_exception(exc)}",
            }})

    route_suffix = "_codex" if prefix.endswith("/codex") else ""
    app.add_api_route(prefix + "/v1/chat/completions", handle_chat, methods=["POST"],
                      name=f"{provider}_chat_completions_v1{route_suffix}")
    app.add_api_route(prefix + "/chat/completions", handle_chat, methods=["POST"],
                      name=f"{provider}_chat_completions{route_suffix}")


def register_routes(app: FastAPI, backend, *, provider: str = "xingchi", upstream=None,
                    prefix: str = XINGCHI_PREFIX, path_mapper=xingchi_upstream_path) -> None:
    """Install the same native relay on both proxy applications."""
    label = PROVIDER_LABELS[provider]
    logger = _ProviderLogger(logging.getLogger("xingchi-proxy"), {"label": label})
    if provider == "xingchi":
        register_chat_conversion(app, backend, prefix=prefix, upstream=upstream or UPSTREAM, provider=provider)
        register_chat_conversion(
            app, backend, prefix=prefix + "/codex", upstream=upstream or UPSTREAM, provider=provider,
            endpoint_path="/backend-api/codex/responses",
        )
    async def handle_xingchi(request: Request, path: str):
        backend.STATS["requests_total"] += 1
        upstream_path = path_mapper(request.url.path)
        headers = backend._filter_headers(
            dict(request.headers), fallback_keys=(provider.upper() + "_API_KEY",), agentrouter_compat=False,
        )
        if provider == "xingchi":
            headers = prepare_client_headers(headers)
        body = await request.body()
        params = request.query_params.multi_items()
        if provider == "xingchi":
            headers, body, params, filter_error = prepare_session_filtered_request(
                headers, body, params, backend=backend, rotate_session=False,
            )
            if filter_error is not None:
                return filter_error
        response = None
        retries = 0
        recovery_deadline = None
        try:
            if (provider == "xingchi" and XINGCHI_SESSION_FILTER_MODE == 2
                    and request.method == "POST" and upstream_path.endswith("/responses")):
                body, context_error = await xingchi_context.compact_request(
                    backend, request, url=(upstream() if upstream else UPSTREAM) + upstream_path,
                    headers=headers, body=body, params=params,
                    prepare_headers=lambda: filter_headers(headers, mode=2),
                )
                if context_error is not None:
                    return context_error
            while True:
                # A prior 429 is already closed. Never reuse it as this attempt's
                # response if sending fails, and never retry the relay iterator.
                response = None
                try:
                    response = await backend._send_with_transport_retry(
                        backend.get_shared_client(), method=request.method, url=(upstream() if upstream else UPSTREAM) + upstream_path,
                        headers=headers, content=body, params=params,
                        stream=True, max_retries=1, request=request,
                        **({"prepare_headers": lambda: filter_headers(headers, mode=2)}
                           if provider == "xingchi" and XINGCHI_SESSION_FILTER_MODE == 2 else {}),
                    )
                except backend.RETRYABLE_TRANSPORT_ERRORS as exc:
                    diagnostic = backend.describe_exception(exc)
                    if not backend._retryable_transport_error(exc):
                        logger.warning("[Xingchi Retry Stopped] Transport recovery disabled: %s", diagnostic)
                        raise
                    if recovery_deadline is None:
                        recovery_deadline = time.monotonic() + MAX_429_RECOVERY_S
                    delay = _retry_delay({}, retries)
                    if retries >= MAX_429_RETRIES or time.monotonic() + delay >= recovery_deadline:
                        logger.warning("[Xingchi Retry Exhausted] Transport failure; retries=%d: %s", retries, diagnostic)
                        raise
                    retries += 1
                    logger.warning("[Xingchi Retry %d/%d] Transport failure; waiting %.2fs: %s", retries, MAX_429_RETRIES, delay, diagnostic)
                    await _wait_retry(backend, request, delay)
                    if time.monotonic() >= recovery_deadline:
                        logger.warning("[Xingchi Retry Exhausted] Transport failure; reason=time budget: %s", diagnostic)
                        raise
                    continue
                is_rate_limit = response.status_code == 429
                is_server_error = response.status_code in RETRYABLE_SERVER_STATUSES
                if not is_rate_limit and not is_server_error:
                    break
                if recovery_deadline is None:
                    recovery_budget = MAX_429_RECOVERY_S if is_rate_limit else MAX_SERVER_ERROR_RECOVERY_S
                    recovery_deadline = time.monotonic() + recovery_budget
                content = await backend._read_and_close_response(response, request)
                try:
                    payload = json.loads(content)
                except (ValueError, UnicodeDecodeError):
                    payload = None
                code, message, _ = backend._extract_error_fields(payload if isinstance(payload, dict) else None, "")
                if is_rate_limit:
                    # Quota, authentication, and policy rejections can also use 429.
                    permanent = (
                        backend.classify_upstream_error(429, code, message) == "permanent"
                        or code.lower() in PERMANENT_PROVIDER_ERROR_CODES
                    )
                    retry_limit = MAX_429_RETRIES
                    retry_label = "HTTP 429"
                else:
                    permanent = (
                        backend.classify_upstream_error(response.status_code, code, message) != "transient"
                        or code.lower() in PERMANENT_PROVIDER_ERROR_CODES
                    )
                    retry_limit = MAX_SERVER_ERROR_RETRIES
                    retry_label = f"HTTP {response.status_code}"
                delay = _retry_delay(response.headers, retries)
                remaining = recovery_deadline - time.monotonic()
                if permanent or retries >= retry_limit or delay >= remaining:
                    reason = "permanent rejection" if permanent else "retry limit" if retries >= retry_limit else "time budget"
                    logger.warning("[Xingchi Retry Exhausted] %s; retries=%d; reason=%s", retry_label, retries, reason)
                    break
                retries += 1
                logger.warning("[Xingchi Retry %d/%d] %s; waiting %.2fs", retries, retry_limit, retry_label, delay)
                await _wait_retry(backend, request, delay)
                if time.monotonic() >= recovery_deadline:
                    logger.warning("[Xingchi Retry Exhausted] %s; retries=%d; reason=time budget", retry_label, retries - 1)
                    break

            if retries and response.status_code != 429:
                if response.is_success:
                    logger.info("[Xingchi Recovered] Upstream HTTP %d after %d retries; response completion not yet confirmed", response.status_code, retries)
                else:
                    logger.warning("[Xingchi Retry Stopped] HTTP %d after %d retries; not eligible for recovery", response.status_code, retries)
            response_headers = backend._clean_response_headers(response.headers)
            nominated = {name.strip().lower() for name in response.headers.get("connection", "").split(",")}
            response_headers = {name: value for name, value in response_headers.items() if name.lower() not in nominated}
            if response.is_success and "text/event-stream" in response.headers.get("content-type", "").lower():
                return backend._UpstreamStreamingResponse(
                    _relay_stream(backend, response, upstream_path, label), upstream_response=response, producer_task=None,
                    status_code=response.status_code, headers=response_headers,
                )
            content = await backend._read_and_close_response(response, request)
            return Response(content=content, status_code=response.status_code, headers=response_headers)
        except backend._ClientDisconnected:
            logger.info("[Client Disconnected] Xingchi request cancelled")
            if response is not None:
                await backend._close_upstream_response(response)
            return Response(status_code=499)
        except asyncio.CancelledError:
            if response is not None:
                await backend._close_upstream_response(response)
            raise
        except Exception as exc:
            if response is not None:
                await backend._close_upstream_response(response)
            diagnostic = backend.describe_exception(exc)
            logger.error("[%s Error] %s %s: %s", provider, request.method, upstream_path, diagnostic)
            return JSONResponse(status_code=502, content={"error": {
                "type": "proxy_error", "code": f"{provider}_upstream_error",
                "message": f"{provider} upstream error: {diagnostic}",
            }})

    # Applications call this before registering their generic catch-all route.
    app.add_api_route(prefix + "/{path:path}", handle_xingchi,
                      methods=["GET", "POST"], name=f"{provider}_relay")
