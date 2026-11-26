# Transport reliability

The proxy recovers from selected temporary failures. It cannot guarantee availability, capacity, or exactly-once model execution.
A retry after accepted work can cause duplicate execution or additional charges.

## Recovery matrix

| Path | Retry owner | Default limit | Initial SSE inspection |
| --- | --- | --- | --- |
| Primary Responses and primary Chat in combined mode | Shared primary recovery loop | Six transient retries; separate bounded resource-mismatch fallback | Yes |
| Native Xingchi, OmniAi, FreeGG, and Moyuu relays | Native relay loop | Two retries within eight seconds from first eligible failure | No |
| Converted Xingchi/FreeGG/Moyuu Chat and Codex-facade Chat | Transport helper | Two transport attempts total | No |
| Generic primary forwarding | Transport helper | Two transport attempts total | No; response is buffered |

Provider-qualified models use the selected relay's policy after dispatch.
The router itself adds no network retry layer.
After client response headers, no path automatically replays the request, delivered text, or tool calls.

## Primary recovery

Native primary Responses and translated primary Chat share one outer transient retry count.
Each outer iteration sends one transport attempt.
This avoids multiplying application and transport retry counts.

`PROXY_MAX_RATE_LIMIT_WAIT_S` defaults to 90 seconds from the first request launch.
It bounds HTTP rate-limit and server-error recovery waits and new launches.
`PROXY_MAX_TRANSPORT_RECOVERY_WAIT_S` defaults to 90 seconds from the first eligible transport failure.
A slow initial connection or header wait does not consume that separate transport window.
Repeated transport failures do not reset it.
Shared rate-limit cooldowns remain in effect during transport recovery.
Neither window cancels an in-flight model generation when its deadline expires.

Eligible failures include rate limits and configured temporary `408`, `500`, `502`, `503`, or `504` errors.
The pipeline can also inspect eligible JSON and SSE failures delivered with HTTP `200`.
Maintenance-related codes or error text can receive pre-commit transient recovery.

Known permanent authentication, quota, billing, invalid-input, and content-policy errors do not activate transient recovery.
Unknown provider error codes are not guaranteed to be classified correctly by a broad HTTP status alone.

### Resource-specific state

A recognized resource mismatch can activate stateless fallback using supplied conversation history.
Fallback removes incompatible response references, item IDs, reasoning state, and encrypted content.
It keeps function `call_id` links and applies the primary image cap.
The mismatch fallback has a separate bounded count from transient retries.

The proxy cannot fetch missing history from IDs or decrypt a foreign compaction checkpoint.
Insufficient replayable history returns `recovery_history_missing`.
Policy rejections never activate this sanitization.
Native provider relays and converted provider Chat do not use primary history recovery or image pruning.

## Native relay recovery

Native relays retry eligible HTTP `429`, `500`, `502`, `503`, and `504` responses and pre-header transport failures.
They share one retry counter and one eight-second recovery deadline.
The deadline starts with the first eligible failure, not the initial request launch.

Retries honor `Retry-After` seconds, HTTP dates, and `retry-after-ms`.
Calculated delays use exponential backoff and jitter.
An upstream hint longer than the remaining window is not shortened.
For example, a 40-second hint returns the latest error without a premature retry.

Known permanent quota, authentication, and policy errors are returned without another attempt.
Native response-body failures and already committed SSE do not enter the native retry loop.
The latest HTTP error body, status, and eligible headers are preserved when recovery stops.
Native retries do not start the primary shared cooldown.

## Provider Chat conversion

Xingchi, FreeGG, and Moyuu Chat requests are converted to Responses for compatibility.
The Codex facades also use direct conversion.
They can use one transport retry, not the native relay's HTTP retry window.

Non-streaming conversion uses the helper's buffered read; an eligible ambiguous failure can repeat that attempt.
Once an SSE conversion is handed to the downstream response, it does not receive pre-commit retry inspection.
Initial and later SSE errors are translated to client errors, not hidden by another request.
HTTP errors are preserved without native HTTP retry recovery in this conversion path.

## Conservative retry settings

```dotenv
PROXY_RETRY_ON_5XX=0
PROXY_RETRY_AMBIGUOUS_ERRORS=0
```

The first setting disables eligible server-error recovery.
The second disables ambiguous transport recovery and primary empty/stalled pre-commit stream recovery.
Connection and pool failures, rate limits, and resource mismatches can still use their applicable recovery paths.
These settings do not provide an exactly-once guarantee.

## Streams and connection ownership

Primary SSE inspection has separate frame and pre-commit byte limits.
The defaults are 8 MiB per frame and 8 MiB for the primed buffer.
Output, hosted-tool activity, a terminal event, or the setup grace period can end inspection.
No-replay protection applies after response commitment, even if the stream later fails.

Native streams keep their provider formats and relay frames incrementally.
Valid terminal events close the upstream even when the server keeps the connection open.
Gemini can deliver usage after a candidate finish reason, so that event does not immediately stop its relay.
Truncation without a terminal event produces `upstream_stream_interrupted`, not a fabricated success.
Oversized frames produce `upstream_sse_frame_too_large` without replay.
An oversized primary initial buffer produces `upstream_sse_prime_too_large`.

Each streaming response owns its iterator, upstream response, and any producer task.
Cleanup runs after normal completion, cancellation, and downstream transmission failure.
It cancels and joins producers and releases the response under a cancellation shield.
Cleanup errors do not replace the original error or retry completed work.

An individual failure does not reset the entire shared HTTPX client.
HTTPcore removes failed, closed, and expired pool connections.
A global client reset could interrupt unrelated streams.

## Disconnects

The proxy watches disconnects during sends, header waits, buffered reads, and retry delays.
A disconnect cancels pending work without another attempt.
Internal status `499` records cancellation; the disconnected client may not receive it.
Cancellation does not prove the upstream stopped generation or billing.
Model-prefix routing retains the original disconnect channel instead of replacing it with a dead receiver.

## Diagnostics and error guidance

An attempt can report a bounded explicit cause chain:

```text
ReadError (caused by BrokenResourceError -> ConnectionResetError: [Errno 10054] Connection reset by peer) (phase=response_headers elapsed=42.30s connection=new)
```

This is an example, not a diagnosis for every read failure.

| Field | Meaning |
| --- | --- |
| Exception type | Failure category; an unavailable cause remains unavailable |
| `phase` | Latest relevant HTTPcore operation, not proof of its exact duration |
| `elapsed` | Time since the attempt started |
| `connection=new` | TCP or TLS setup was recorded |
| `connection=reused` | An HTTP operation began without a connection-setup trace |
| `http_status` | Upstream status, if headers arrived |

The supported trace hook captures safe explicit cause strings before HTTPcore can suppress them during cleanup.
It does not retain request headers, bodies, or additional exception objects.
Several retry lines can belong to concurrent requests, each with its own counter.
A recovered-response log does not prove stream completion or client task success.

Primary transport exhaustion returns `upstream_transport_retries_exhausted` and same-session retry guidance.
With recovery disabled, it uses `upstream_transport_error`.
These errors do not require deleting or recreating the client session.

### Content policy

Primary pre-commit policy rejections preserve upstream fields and add non-retryable guidance and an available request ID.
Non-`200` upstream statuses remain unchanged.
A primary HTTP `200` JSON/SSE policy rejection becomes `400` before client headers.
After commitment, the error continues through the stream without replay.
Content-rejection diagnostics use counts rather than prompts or image URLs.
Other upstream error messages can quote private content; review logs before sharing them.

### Upload limits and maintenance

Local `413` comes from a configured request-size check.
Upstream `413` means the service rejected the body; changing the local limit cannot change that service's limit.
Maintenance recovery can retry temporary failures but cannot repair the upstream or guarantee eventual availability.

## Design references

These fixed source revisions informed the existing transport design in November 2026.
They are implementation references, not claims about every current deployment.

| Reference | Relevant topic |
| --- | --- |
| [LiteLLM HTTP handler](https://github.com/BerriAI/litellm/blob/ec48e2491c5132951cd2710671506019ce93f0a0/litellm/llms/custom_httpx/http_handler.py#L996-L1050) | Client and stream lifetime |
| [CLIProxyAPI stream](https://github.com/router-for-me/CLIProxyAPI/blob/bd9c766f34840b050b01cbdafad7cf29b3cb151f/internal/runtime/executor/codex_executor_stream.go#L164-L229) | Bounded bootstrap inspection and cancellation |
| [CLIProxyAPI transport cache](https://github.com/router-for-me/CLIProxyAPI/blob/bd9c766f34840b050b01cbdafad7cf29b3cb151f/internal/runtime/executor/helps/transport_cache.go#L155-L159) | Idle versus active connection cleanup |
| [New API HTTP client](https://github.com/QuantumNous/new-api/blob/b388140acfd80369ec6243b16869daff1b4051f4/service/http_client.go#L79-L125) | Pool and timeout limits |
| [Portkey retry handler](https://github.com/Portkey-AI/gateway/blob/669825cbe89ee51569918b8f78a9db486fd69dd4/src/handlers/retryHandler.ts) | Bounded retry policy |
| [OpenAI Python client](https://github.com/openai/openai-python/blob/3dae6090cc31cc64a437fe0bcee740b6d81ed2cc/src/openai/_base_client.py) | Retry classification and delay hints |
| [HTTPX transport docs](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/docs/advanced/transports.md) | Connection-only built-in transport retries |
| [HTTPcore cleanup](https://github.com/encode/httpcore/blob/10a658221deb38a4c5b16db55ab554b0bf731707/httpcore/_async/connection_pool.py#L248-L299) and [trace events](https://github.com/encode/httpcore/blob/10a658221deb38a4c5b16db55ab554b0bf731707/httpcore/_trace.py#L95-L107) | Cause suppression and safe capture timing |

Disabled TLS verification, unlimited retries, static-IP changes, and global pool resets are not general remedies.
Do not infer endpoint-wide failure from one request or network health from one successful small request.

## Tests and activation

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -s tests -p test_transport_reliability.py -v
```

Tests use simulated upstreams and temporary loopback servers, not paid generation.
Coverage includes slow first failures, fixed recovery deadlines, mixed failures, cause suppression, body truncation, and disconnect races.
It also covers provider-key isolation, aliased SSE cleanup, and no replay after partial output.

After an update, wait for active work to finish and restart the proxy.
A launcher health match can reuse an old worker; edited files alone do not activate new behavior.
Keep TLS verification enabled and redact credentials, prompts, and account data from diagnostic reports.
