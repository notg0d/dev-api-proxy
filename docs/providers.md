# Providers and API routes

Use the local namespace that matches your provider key and model access.
Both proxy modes install the same Xingchi, OmniAi, FreeGG, Moyuu, and model-prefix routes.
The exact route and method allowlist is in `../request_guard.py`.

## Client base URLs

These examples use combined mode on port `8789`. Use `8788` with `proxy.py`.

| Provider | Responses / Chat | Anthropic | Gemini |
| --- | --- | --- | --- |
| Primary | `http://127.0.0.1:8789/v1` | `http://127.0.0.1:8789` | Not exposed as a primary Gemini namespace |
| Xingchi | `http://127.0.0.1:8789/xingchi/v1` | `http://127.0.0.1:8789/xingchi` | `http://127.0.0.1:8789/xingchi/v1beta` |
| OmniAi | `http://127.0.0.1:8789/omniai/v1` | `http://127.0.0.1:8789/omniai` | `http://127.0.0.1:8789/omniai/v1beta` |
| FreeGG | `http://127.0.0.1:8789/freegg/v1` | `http://127.0.0.1:8789/freegg` | `http://127.0.0.1:8789/freegg/v1beta` |
| Moyuu | `http://127.0.0.1:8789/moyuu/v1` | `http://127.0.0.1:8789/moyuu` | `http://127.0.0.1:8789/moyuu/v1beta` |

Clients differ in whether their base URL includes `/v1`.
Check the final request path. Do not produce `/v1/v1/...`.
The native relays also accept unversioned aliases, such as `/omniai/responses`.

## Authentication

Direct native routes accept `Authorization: Bearer ...`, `x-api-key`, or `x-goog-api-key`.
A supplied native authentication header prevents fallback-key injection.
Without one, the relay uses its provider-specific variable:

| Namespace | Fallback variable | Default server |
| --- | --- | --- |
| `/xingchi` | `XINGCHI_API_KEY` | `https://api.xingchiapi.com` |
| `/omniai` | `OMNIAI_API_KEY` | `https://sub.omniai.cc` |
| `/freegg` | `FREEGG_API_KEY` | `https://freegg.app` |
| `/moyuu` | `MOYUU_API_KEY` | `https://moyuu.cc` |

Primary routes preserve `Authorization` and otherwise try `CODEX_GATEWAY_API_KEY`, then `AGENTROUTER_API_KEY`.
`OPENAI_API_KEY` is not a primary proxy fallback.
Do not reuse an unrelated provider key.
Prefer header authentication over query-string keys, which can appear in request logs.

Local authentication is independent. Set `PROXY_AUTH_TOKEN` and send it as `X-Proxy-Token` when required.
The proxy never forwards that local token.

## Primary routes

`POST /v1/responses` and `/responses` use primary history recovery and image pruning.
Combined mode also recovers translated primary Chat Completions on `/v1/chat/completions` and `/chat/completions`.

Other permitted primary routes use buffered forwarding:

- Discovery: `/v1/models`, `/models`, and single-model paths.
- Responses subpaths: `compact` and `input_tokens`.
- Completions, Anthropic messages and token counts, embeddings, moderation, rerank, and edits.
- The permitted `/v1/images/...` and `/v1/audio/...` endpoints.

Generic forwarding does not convert APIs or inspect SSE before output.
It buffers SSE instead of relaying events incrementally.
Primary forwarding collapses repeated query parameter names; native relays preserve them.
Use `request_guard.py` rather than this summary to determine every permitted path.

Set `AGENTROUTER_UPSTREAM` to the server base **without `/v1`**.
The proxy appends the required primary API path.

## Xingchi and OmniAi route matrix

Replace `{provider}` with `xingchi` or `omniai`.

| Client route | Upstream route | Method |
| --- | --- | --- |
| `/{provider}/v1/responses` | `/v1/responses` | `POST` |
| `/{provider}/v1/responses/compact` | `/v1/responses/compact` | `POST` |
| `/{provider}/v1/responses/input_tokens` | `/v1/responses/input_tokens` | `POST` |
| `/{provider}/v1/chat/completions` | Xingchi: converted to Responses; OmniAi: native Chat | `POST` |
| `/{provider}/v1/messages` | `/v1/messages` | `POST` |
| `/{provider}/v1/messages/count_tokens` | `/v1/messages/count_tokens` | `POST` |
| `/{provider}/v1/models`, `/{provider}/v1/models/{model}` | Same path without the provider prefix | `GET` |
| `/{provider}/v1/usage`, `/{provider}/v1/sub2api/billing` | Same path without the provider prefix | `GET` |
| `/{provider}/v1/embeddings` | `/v1/embeddings` | `POST` |
| `/{provider}/v1/images/generations`, `/{provider}/v1/images/edits` | Same path without the provider prefix | `POST` |
| Image `generations/async` and `edits/async` subpaths | Same path without the provider prefix | `POST` |
| `/{provider}/v1/images/tasks/{task_id}` | `/v1/images/tasks/{task_id}` | `GET` |
| `/{provider}/v1beta/models`, single-model paths | Same path without the provider prefix | `GET` |
| `/{provider}/v1beta/models/{model}:{action}` | Same path without the provider prefix | `POST` |

Gemini actions: `generateContent`, `streamGenerateContent`, `countTokens`, `embedContent`, and `batchEmbedContents`.
Use `?alt=sse` when the Gemini client requires it.

### Compatibility paths

Both namespaces permit these Sub2API paths:

- `/backend-api/codex/models`, `/backend-api/codex/responses`, and the Responses `compact` and `input_tokens` subpaths.
- `/antigravity/models`, `/antigravity/v1/models`, `/antigravity/v1/usage`, and `/antigravity/v1/messages` with `count_tokens`.
- `/antigravity/v1beta/models` and the permitted Gemini model actions.

The `/{provider}/codex/v1` facade maps Responses and discovery to `/backend-api/codex/...`.
Its Chat Completions route converts to `/backend-api/codex/responses`.
It uses the provider API key, not a fabricated ChatGPT OAuth session.
Compatibility paths can be unavailable on a particular deployment.

## Xingchi session filtering

`XINGCHI_SESSION_FILTER_MODE` controls filtering only on Xingchi requests:

| Mode | Behavior |
| --- | --- |
| `0` | Disabled; preserve native metadata |
| `1` | Remove recognized session headers, query parameters, and JSON fields |
| `2` | Filter metadata, rotate upstream session IDs, and summarize older Responses history |

Names are matched without case or punctuation differences.
The filter also matches Codex names containing `session` or `requestid`.
Additional comma-separated names come from `XINGCHI_SESSION_FILTER_NAMES`.

Protected JSON subtrees are `input`, `messages`, `tools`, `functions`, `text`, and `response_format`.
The default filter does not recursively remove application data inside those subtrees.
Do not configure mandatory fields or authentication headers as extra filter names.
An extra name can remove the whole matching field.

Mode `2` recognizes session and thread headers, including `x-opencode-session` and normalized Codex aliases.
It assigns a fresh upstream ID on first use and synchronizes recognized headers to that ID.
It also sends `session_id` when the client has no recognized session header.
Clients without session headers share a rotation counter per upstream credential set.
Different credential sets have separate counters, including when they use the same client session ID.
The ID rotates when its send count reaches `88`. HTTP and transport retry attempts count toward this threshold.
Custom header exclusions still apply, including an explicit exclusion of `session_id`.
Other matching query and JSON fields are removed, not replaced with the new ID.
State is process-local and stored in memory. A restart assigns fresh upstream IDs.
The same filter applies to OpenCode, desktop picker requests, and Codex through the Xingchi routes.
This includes `/xingchi/codex/v1` and the native `/xingchi/backend-api/codex` compatibility paths.

Uncompressed, gzip, and deflate JSON can be filtered without additional packages.
Brotli and Zstandard require their optional decoders; unavailable or invalid encodings fail locally.
Non-JSON bodies, such as multipart images, are not rewritten by the session filter.
Mode `2` also summarizes Responses history when a request contains at least `88` model-turn items.
The proxy counts assistant messages and model tool calls. It normally summarizes older context in 64-turn chunks.
Larger tool-call groups stay together if they fit below the summary request's 88-turn threshold.
Recent context remains unchanged, and tool calls stay with their results.
The generation request keeps its original instructions, tools, and output format.
A completed summary replaces the old prefix only in forwarded requests. The client's saved history remains unchanged.
After compaction, the proxy removes `previous_response_id` so the request cannot reattach the old server-side chain.

Each uncached chunk uses a regular model request, with no tools and a maximum of 4,096 output tokens.
Each later chunk includes the previous summary, so long histories do not create oversized summary requests.
The proxy caches up to 16 summaries for one hour, isolated by credentials, model, endpoint, and prefix.
Summary text is limited to 256 KiB. Complete response bodies and streams have a separate 16 MiB limit.
Streaming metadata does not consume the summary-text limit.
Summaries can omit older details. Failed, incomplete, oversized, empty, or turn-limit notice summaries produce an explicit error.
Partial summaries are never forwarded.
Manual `/responses/compact` requests remain unchanged. Modes `0` and `1` do not summarize history.
These features do not increase provider quotas or remove token limits.

For `403 client_not_allowed`, configure `XINGCHI_USER_AGENT` with a versioned client identifier, such as `opencode/2.4.2`.
An empty value preserves the caller's header. The override also applies to summary requests and converted Chat.
Other providers keep their original client identifiers. Provider authentication and model permissions still apply.

The behavior was informed by [XingChi-Session-Filter](https://github.com/nayumiDEV/XingChi-Session-Filter).
The original project is GPL-3.0; its binaries are not bundled with this proxy.

## OmniAi

OmniAi preserves native Chat Completions, Responses, Anthropic, and Gemini formats.
Xingchi session filtering does not apply to it.

On November 22, 2026, [public settings](https://sub.omniai.cc/api/v1/settings/public) reported Sub2API `0.2.18`.
The [matching gateway source](https://github.com/Wei-Shaw/sub2api/blob/v0.2.18/backend/internal/server/routes/gateway.go) defines the protocol routes.
Unauthenticated `/v1/models` returned `401`.
The `/backend-api/codex/models` probe returned the web interface, not a model catalog.
Use the normal `/omniai/v1` base unless account-specific requirements say otherwise.

These public probes did not verify authenticated generation, account quotas, or access to individual models.

## FreeGG and Moyuu

Both providers run New API and share one route allowlist.
Replace `{provider}` with `freegg` or `moyuu`.

| Client route | Method |
| --- | --- |
| `/{provider}/v1/models`, single-model paths | `GET` |
| `/{provider}/v1/responses`, `/{provider}/v1/responses/compact` | `POST` |
| `/{provider}/v1/chat/completions` | `POST`, converted to Responses |
| `/{provider}/v1/completions`, `/{provider}/v1/messages` | `POST`, native relay |
| `/{provider}/v1/embeddings`, `/{provider}/v1/moderations`, `/{provider}/v1/rerank` | `POST` |
| `/{provider}/v1/images/generations`, `/{provider}/v1/images/edits`, `/{provider}/v1/edits` | `POST` |
| `/{provider}/v1/audio/transcriptions`, `/{provider}/v1/audio/translations`, `/{provider}/v1/audio/speech` | `POST` |
| `/{provider}/v1beta/models`, `/{provider}/v1beta/openai/models` | `GET` |
| `/{provider}/v1beta/models/{model}:{action}`, `/{provider}/v1/models/{model}:{action}` | `POST` |

Gemini actions: `generateContent`, `streamGenerateContent`, `embedContent`, and `batchEmbedContents`.
Responses input-token counts, Anthropic token counts, Gemini `countTokens`, account administration, files, and WebSockets are not exposed.
Chat conversion is proxy compatibility policy, not a claim about every upstream deployment version.
The Xingchi session filter does not apply to either namespace.

### FreeGG

[Public status](https://freegg.app/api/status) previously reported New API `v1.0.0-rc.49`.
The [matching gateway source](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.49/router/relay-router.go) informed the route allowlist.
Authenticated model access, generation, prices, and quotas were not verified by those public probes.

### Moyuu

On November 23, 2026, [public status](https://moyuu.cc/api/status) reported `system_name` `Moyuu` and build `20261121-claude-image-preconsume-r1`.
That is a deployment-specific New API build string, not a tagged upstream release.
Unauthenticated probes returned `401` for the permitted generation, discovery, and Gemini action routes.
They returned `404` for `/v1/responses/input_tokens` and `/v1/messages/count_tokens`, matching the shared allowlist.
Gemini `:countTokens` answered `401` because that deployment authenticates before matching the action; it remains excluded as an unimplemented New API action.

These public probes did not verify authenticated generation, account quotas, or access to individual models.

## Errors and limits

Native relays preserve upstream status codes and error bodies, including `413`.
They do not use primary Azure-style history recovery or image pruning.
Native SSE is relayed incrementally, with bounded frames and cleanup on disconnect.
Converted Chat uses its own response translator and retry behavior.
See [Transport reliability](transport-reliability.md) before enabling or depending on retries.

Local route support does not guarantee that a provider account can use the endpoint or model.
