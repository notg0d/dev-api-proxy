# dev-api-proxy

`dev-api-proxy` is a local API gateway for OpenCode, ChatGPT desktop, Codex, and compatible AI clients.
It provides primary-upstream recovery and separate Xingchi, OmniAi, FreeGG, and Moyuu routes.
The default primary upstream is AgentRouter. You can use another compatible server.

**Current release: [v1.9.0](https://github.com/notg0d/dev-api-proxy/releases/tag/v1.9.0).**
Both proxy modes report this version in `/_health`.

## What it does

- Accepts Responses requests and, in combined mode, primary Chat Completions requests.
- Converts primary, Xingchi, FreeGG, and Moyuu Chat Completions requests to Responses.
- Relays OmniAi Chat Completions without conversion.
- Keeps each native provider's routes, keys, formats, and retry state separate.
- Adds provider-labelled models to the ChatGPT desktop model picker through a generated catalog.
- Supports optional Xingchi session filtering, session-header rotation, and history summaries.
- Applies local authentication, upload limits, SSE limits, and disconnect cancellation.
- Shows one startup summary with all configured providers and active settings.

It uses HTTP and server-sent events (SSE), not Responses WebSockets.
It cannot increase account quotas, model capacity, or upstream upload limits.
Retries can repeat accepted work and cause additional charges.

## Documentation

| Guide | Contents |
| --- | --- |
| [Providers and API routes](docs/providers.md) | Endpoints, conversion policy, authentication, and service references |
| [Desktop and CLI clients](docs/clients.md) | OpenCode, ChatGPT desktop, Codex, model catalogs, and key storage |
| [Configuration reference](docs/configuration.md) | Environment variables, defaults, limits, and precedence |
| [Transport reliability](docs/transport-reliability.md) | Recovery budgets, retry differences, stream ownership, and diagnostics |
| [Windows service](docs/windows-service.md) | Launcher, background mode, scheduled tasks, and updates |
| [Development and releases](docs/development.md) | Modules, validation, and release procedure |
| [Changelog](CHANGELOG.md) | Release changes and upgrade notes |

## Installation

Use Python **3.10 or newer**. Python 3.14 has been tested on Windows.
These commands use PowerShell:

```powershell
git clone https://github.com/notg0d/dev-api-proxy.git
cd dev-api-proxy
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On macOS or Linux, activate the environment with:

```bash
source .venv/bin/activate
```

A virtual environment is recommended, not required.
The Windows launcher can use system Python when `.venv` is absent.

If `.env` does not exist, create it from the example:

```powershell
Copy-Item .env.example .env
```

Do not overwrite an existing `.env`: it can contain your keys and local settings.
Set the provider keys you need, then start combined mode:

```powershell
python chat_proxy.py
```

For the Windows launcher:

```powershell
.\start-proxy.ps1 -Mode combined
```

## API routes

| Program | Default port | Primary API |
| --- | --- | --- |
| `chat_proxy.py` | `8789` | Responses and translated Chat Completions |
| `proxy.py` | `8788` | Responses recovery |

Both modes expose the same native provider namespaces and provider-model router.
Other permitted primary routes use buffered forwarding, not primary recovery.
See `allowed_methods()` in `request_guard.py` for the exact allowlist.
Unknown routes return `404`. Incorrect methods return `405`.

| Provider | Client base URL on port `8789` | Default upstream |
| --- | --- | --- |
| Primary | `http://127.0.0.1:8789/v1` | `https://agentrouter.org` |
| Xingchi | `http://127.0.0.1:8789/xingchi/v1` | `https://api.xingchiapi.com` |
| OmniAi | `http://127.0.0.1:8789/omniai/v1` | `https://sub.omniai.cc` |
| FreeGG | `http://127.0.0.1:8789/freegg/v1` | `https://freegg.app` |
| Moyuu | `http://127.0.0.1:8789/moyuu/v1` | `https://moyuu.cc` |

Use port `8788` with `proxy.py`.
For Anthropic and Gemini clients, see [protocol-specific URLs](docs/providers.md#client-base-urls).

### XingchiAPI routes

Xingchi has isolated Responses, Anthropic, Gemini, discovery, image, Codex-compatibility, and Antigravity routes.
Its Chat Completions requests are converted to Responses.
The default preserves native session headers and metadata.

Set `XINGCHI_SESSION_FILTER_MODE` to:

- `0`: disabled, the default.
- `1`: remove session metadata from headers, query parameters, and JSON outside protected conversation fields.
- `2`: filter metadata, rotate session headers at 88 sends, and summarize history at 88 model turns.

Rotation state and summary caches are in memory, per process. Retry attempts count toward rotation.
Mode `2` summarizes older context in forwarded Responses requests, including requests converted from Chat Completions.
It retains recent turns and leaves the client's saved conversation unchanged.
Summary requests use bounded chunks; unchanged prefixes reuse cached summaries.
Each uncached chunk uses an additional model request. Summaries can omit older details.
The proxy cannot increase provider quotas.
See [Xingchi session filtering](docs/providers.md#xingchi-session-filtering).

For `403 client_not_allowed`, set a versioned client identifier in `.env`, then restart the proxy service:

```dotenv
XINGCHI_USER_AGENT=opencode/2.4.2
```

Use the identifier for your client version. Empty preserves the caller's `User-Agent`.
The override applies only to Xingchi, including summary requests. Provider credentials and model permissions still apply.
See the [configuration reference](docs/configuration.md#providers-and-authentication).

### OmniAi routes

OmniAi has its own `/omniai` namespace and `OMNIAI_API_KEY` fallback.
Responses, Chat Completions, Anthropic, and Gemini keep their native protocol formats.
The Xingchi session filter does not apply to OmniAi.
Use `/omniai/v1` unless your account requires a compatibility path.
See [OmniAi support and verification](docs/providers.md#omniai).

### FreeGG routes

FreeGG has its own `/freegg` namespace and `FREEGG_API_KEY` fallback.
Its Chat Completions requests are converted to Responses for compatibility.
Native Anthropic, Gemini, image, audio, and other permitted routes remain available.
Responses input-token counts and Anthropic token-count routes are not exposed for FreeGG.
See [FreeGG routes](docs/providers.md#freegg).

### Moyuu routes

Moyuu has its own `/moyuu` namespace and `MOYUU_API_KEY` fallback.
It runs New API and shares the FreeGG route allowlist and Chat conversion policy.
Native Anthropic, Gemini, image, audio, and other permitted routes remain available.
The Xingchi session filter does not apply to Moyuu.
See [Moyuu support and verification](docs/providers.md#moyuu).

### Chat Completions conversion

| Request path | Behavior |
| --- | --- |
| Primary Chat in combined mode | Convert to Responses and use primary recovery |
| Xingchi, FreeGG, or Moyuu Chat | Convert directly to that provider's Responses endpoint |
| OmniAi Chat | Native relay |
| Xingchi or OmniAi Codex-facade Chat | Convert to `/backend-api/codex/responses` |

Converted output includes text, reasoning, and function calls.
Conversion does not implement every OpenAI API feature.
Native relays and converted provider Chat use different retry policies.
See the [recovery matrix](docs/transport-reliability.md#recovery-matrix).

## API key

Use a key accepted by the selected provider. Do not reuse keys across unrelated services.
Direct native routes accept `Authorization: Bearer ...`, `x-api-key`, or `x-goog-api-key`.
Without a client key, each native relay uses only its own configured fallback:

```dotenv
XINGCHI_API_KEY=your-xingchi-key
OMNIAI_API_KEY=your-omniai-key
FREEGG_API_KEY=your-freegg-key
MOYUU_API_KEY=your-moyuu-key
```

Primary forwarding preserves `Authorization` and otherwise tries `CODEX_GATEWAY_API_KEY`, then `AGENTROUTER_API_KEY`.
Keep real keys in private settings, environment variables, or private files. Never commit them.
Client processes do not automatically load the proxy's `.env`.

### Local authentication

Set `PROXY_AUTH_TOKEN` to require a separate local access token:

```text
X-Proxy-Token: your-local-token
Authorization: Bearer your-provider-key
```

The proxy removes `X-Proxy-Token` before upstream transmission.
`/_health` stays available for supervisor probes. Other permitted routes require the token when enabled.
Missing, incorrect, and duplicate local tokens return `401`.

**Keep the default loopback listener unless a firewall controls access.**
Non-loopback startup requires `PROXY_AUTH_TOKEN`.
The token does not encrypt traffic. Use TLS before transmitting credentials over a network.
Do not expose this gateway as a public service.

## Local agent configuration

### OpenCode

Use `~/.config/opencode/opencode.jsonc` for global desktop and CLI provider definitions.
Use separate provider entries and labels such as `OmniAi · GPT-6.1 Sol`.
The model display name does not change its wire ID or provider URL.
See [OpenCode setup](docs/clients.md#opencode-desktop-and-cli) and [the example](examples/opencode.jsonc).

### Multiple providers in the desktop model picker

ChatGPT desktop can use one gateway connection with provider-qualified model IDs:

| Picker choice | Wire model sent by the client | Actual upstream model |
| --- | --- | --- |
| `Xingchi · GPT-6.1 Sol` | `xingchi/gpt-6.1-sol` | `gpt-6.1-sol` on Xingchi |
| `OmniAi · GPT-6.1 Sol` | `omniai/gpt-6.1-sol` | `gpt-6.1-sol` on OmniAi |
| `FreeGG · GPT-6.1 Sol` | `freegg/gpt-6.1-sol` | `gpt-6.1-sol` on FreeGG |
| `Moyuu · GPT-6.1 Sol` | `moyuu/gpt-6.1-sol` | `gpt-6.1-sol` on Moyuu |

The proxy routes these IDs to the selected provider. It does not merely relabel the models.
Cross-provider choices use that provider's proxy-side key, never the original connection's key.
The generated catalog retains old IDs as hidden compatibility entries and namespaces compaction fingerprints by provider.
See [ChatGPT desktop picker setup](docs/clients.md#several-providers-in-chatgpts-main-picker).

### Codex example

Codex and ChatGPT desktop share the user-level `.codex/config.toml` on Windows.
CLI profiles are separate files; they are not the desktop setup procedure.
See [direct provider setup](docs/clients.md#chatgpt-desktop-and-codex) and [CLI profiles](docs/clients.md#codex-cli-profiles).

## Session recovery

Primary Responses recovery uses supplied conversation history after a recognized cross-resource or state-reference failure.
It removes response pointers, server-minted item IDs, reasoning state, and incompatible encrypted content.
It preserves function `call_id` values and applies the primary image limit.

The proxy cannot reconstruct missing history or decrypt foreign compaction checkpoints.
Insufficient replayable history returns `400` with `recovery_history_missing`.
Native provider relays do not use this recovery pipeline or its image pruning.
The proxy does not read, edit, or delete local chat history.

## Rate limits and timeouts

Primary recovery has a shared cooldown and bounded retry count.
Native provider relays use up to two retries within an eight-second window.
Known permanent errors do not receive automatic recovery.
After client response headers, the proxy never replays text, tool calls, or the request.

A retry after accepted work can cause duplicate execution or charges.
For conservative retry behavior, set:

```dotenv
PROXY_RETRY_ON_5XX=0
PROXY_RETRY_AMBIGUOUS_ERRORS=0
```

These settings do not guarantee exactly-once execution.
See [Transport reliability](docs/transport-reliability.md) for route-specific limits and error handling.

## Proxy configuration

The built-in upload limit is **20 MiB**. `.env.example` sets **1 GiB** for local coding sessions.
The `.env` upload limit overrides an inherited value; other existing environment values take precedence.
Limits apply per request, not to total process memory or concurrent uploads.
A larger local limit cannot override an upstream limit.
See the [complete configuration reference](docs/configuration.md).

## Windows background service

```powershell
.\start-proxy.ps1 -Mode combined -Background
.\install-autostart.ps1 -Mode combined
```

The default launcher mode is `responses`, on port `8788`.
Combined mode uses port `8789`. The supervisor writes rotating logs to `logs/proxy.log`.
Scheduled-task names retain their legacy names for compatibility.
See [Windows service setup](docs/windows-service.md).

## Diagnostics

```powershell
Invoke-RestMethod http://127.0.0.1:8789/_health
Invoke-RestMethod http://127.0.0.1:8789/_stats
```

Supply `X-Proxy-Token` for `/_stats` when local authentication is enabled.
Health reports the release version, API capabilities, provider URLs, picker prefixes, session-filter mode, and active limits.
It does not expose key values. Review logs before sharing them; upstream errors can quote private content.

| Symptom | Next action |
| --- | --- |
| Local `413` | Check `PROXY_MAX_BODY_BYTES` and request size |
| Upstream `413` | Shorten history, reduce images, or compact client-side; the service controls its own limit |
| `provider_api_key_missing` | Configure the selected provider's fallback key on the proxy |
| `xingchi_context_compaction_failed` | Retry `/compact` or start a new session; the client's saved history remains unchanged |
| New feature absent from health | Stop the old worker and restart it; the launcher can reuse an existing process |
| Model absent or unauthorized | Check the client credential source and account model access |
| Interrupted stream | Review the cause; delivered output is not replayed |
| ChatGPT `Connection for host ID local not found` | Inspect desktop launch logs; this is an internal app-host error, not a provider URL |

## Tests

```powershell
python -m unittest discover -s tests -v
.\tests\test_launchers.ps1
```

The tests use mock upstreams and temporary loopback servers, not paid model requests.
See [Development and releases](docs/development.md) for validation and publication steps.

## Updating

Wait for active requests to finish before stopping the proxy.
Update a clean checkout, preserve `.env`, then restart the selected proxy mode.
Restart ChatGPT desktop after changing its provider or catalog configuration.
OpenCode watches configuration files; a config change alone does not require a service restart.
See [v1.9.0 upgrade notes](CHANGELOG.md#190---2026-11-26).
