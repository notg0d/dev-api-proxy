"""Route provider-qualified model IDs through the existing isolated relays."""

from __future__ import annotations

import json
import os
from typing import Callable

from request_guard import allowed_methods


PROVIDER_LABELS = {"xingchi": "Xingchi", "omniai": "OmniAi", "freegg": "FreeGG", "moyuu": "Moyuu"}
MODEL_ROUTING_PATHS = frozenset({
    "/v1/responses", "/responses", "/v1/responses/compact", "/responses/compact",
    "/v1/responses/input_tokens", "/responses/input_tokens",
    "/v1/chat/completions", "/chat/completions",
})
_CLIENT_CREDENTIAL_HEADERS = frozenset({
    b"authorization", b"x-api-key", b"x-goog-api-key", b"cookie", b"chatgpt-account-id",
    b"openai-organization", b"openai-project", b"proxy-authorization",
})
_PROVIDER_AUTH_HEADERS = frozenset({b"authorization", b"x-api-key", b"x-goog-api-key"})


def split_model_alias(model: str) -> tuple[str, str] | None:
    provider, separator, upstream_model = model.partition("/")
    provider = provider.lower()
    if not separator or provider not in PROVIDER_LABELS:
        return None
    if not upstream_model.strip() or any(ord(char) < 32 for char in upstream_model):
        raise ValueError("A provider-qualified model ID must include an upstream model name")
    return provider, upstream_model


class ProviderModelRouter:
    """Run inside ProxyRequestGuard so auth and upload checks happen first.

    An alias can reuse a client key only when its inbound native namespace
    matches the selected provider. Cross-provider aliases use the selected
    provider's proxy-side key; client credentials never cross namespaces.
    """

    def __init__(self, app, *, max_body_bytes: Callable[[], int], stats: dict):
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.stats = stats

    async def __call__(self, scope, receive, send):
        inbound_provider = None
        relative_path = scope.get("path", "")
        for provider in PROVIDER_LABELS:
            prefix = "/" + provider
            if relative_path.startswith(prefix + "/"):
                inbound_provider = provider
                relative_path = relative_path[len(prefix):]
                break
        if (scope["type"] != "http" or scope.get("method") != "POST"
                or relative_path not in MODEL_ROUTING_PATHS):
            await self.app(scope, receive, send)
            return

        async def reject(status: int, code: str, message: str):
            self.stats["requests_rejected_total"] += 1
            body = json.dumps({"error": {
                "type": "invalid_request_error", "code": code, "message": message, "retryable": False,
            }}).encode("utf-8")
            await send({"type": "http.response.start", "status": status, "headers": [
                (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
            ]})
            await send({"type": "http.response.body", "body": body})

        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_body_bytes():
                await reject(413, "payload_too_large", "Request body exceeds PROXY_MAX_BODY_BYTES")
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = chunks[0] if len(chunks) == 1 else b"".join(chunks)
        del chunks, message, chunk
        next_scope = scope

        headers = scope.get("headers", ())
        routing_required = any(name.lower() == b"x-proxy-model-routing" and value == b"1" for name, value in headers)
        encoding = next((value.decode("ascii", errors="replace") for name, value in headers
                         if name.lower() == b"content-encoding"), "identity")
        encodings = [part.strip().lower() for part in encoding.split(",")]
        decoded = payload = None
        try:
            # Import lazily: proxy.py loads .env before provider configuration.
            from xingchi_proxy import _decompress_body

            decoded = _decompress_body(body, encodings, self.max_body_bytes())
            payload = json.loads(decoded)
        except (ValueError, UnicodeDecodeError) as exc:
            if inbound_provider is not None and not routing_required:
                payload = None  # Opaque native requests retain their original relay behavior.
            elif encoding not in ("", "identity") or routing_required:
                status = 413 if "body limit" in str(exc) else 400
                await reject(status, "invalid_request_body", "Cannot decode JSON for provider-model routing")
                return
            else:
                payload = None  # Preserve the original route's JSON validation.
        except Exception:
            if inbound_provider is not None and not routing_required:
                payload = None
            else:
                await reject(400, "invalid_request_body", "Cannot decode JSON for provider-model routing")
                return

        if isinstance(payload, dict) and isinstance(payload.get("model"), str):
            try:
                alias = split_model_alias(payload["model"])
            except ValueError as exc:
                await reject(400, "invalid_model_alias", str(exc))
                return
            if alias is not None:
                provider, upstream_model = alias
                path = "/" + provider + relative_path
                if scope["method"] not in allowed_methods(path):
                    await reject(400, "unsupported_provider_route", "This provider does not support the selected API route")
                    return
                key_name = provider.upper() + "_API_KEY"
                use_client_auth = provider == inbound_provider and any(
                    name.lower() in _PROVIDER_AUTH_HEADERS and value.strip() for name, value in headers
                )
                if not use_client_auth and not os.environ.get(key_name):
                    await reject(400, "provider_api_key_missing", f"Configure {key_name} on the proxy to use {PROVIDER_LABELS[provider]} models")
                    return
                payload["model"] = upstream_model
                body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if len(body) > self.max_body_bytes():
                    await reject(413, "payload_too_large", "Converted request body exceeds PROXY_MAX_BODY_BYTES")
                    return
                next_scope = dict(scope)
                next_scope["path"] = path
                next_scope["raw_path"] = path.encode("utf-8")
                removed_credentials = _CLIENT_CREDENTIAL_HEADERS
                if use_client_auth:
                    removed_credentials = removed_credentials - _PROVIDER_AUTH_HEADERS
                next_scope["headers"] = [
                    (name, value) for name, value in headers
                    if name.lower() not in removed_credentials
                    and name.lower() not in (b"content-length", b"content-encoding", b"content-type")
                ] + [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]

        # A long stream must not retain an extra decoded copy of the history.
        del decoded, payload
        delivered = False

        async def replay_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(next_scope, replay_receive, send)
