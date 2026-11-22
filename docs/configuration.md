# Configuration reference

Configure private values in the repository's `.env` or in the process environment.
Copy `.env.example` only when `.env` does not exist.
Restart the proxy after changing its configuration.

## Precedence

`proxy.py` loads `.env` before native provider modules read their settings.
Existing process environment values usually take precedence.
The exception is `PROXY_MAX_BODY_BYTES`: its local `.env` value overrides an inherited value.
The PowerShell launcher applies the same upload-limit rule.
The supervisor's worker import applies it too.

The loader supports simple `NAME=value` lines and matching outer quotes.
It does not expand shell expressions or OpenCode `{env:...}` and `{file:...}` substitutions.
Do not duplicate a setting in `.env`.

Client apps do not automatically load this file.
Configure client credentials separately or use proxy-side fallbacks for provider-qualified models.

## Providers and authentication

| Variable | Built-in default | Purpose |
| --- | --- | --- |
| `AGENTROUTER_UPSTREAM` | `https://agentrouter.org` | Primary server base, without `/v1`; name retained for compatibility |
| `CODEX_GATEWAY_API_KEY` | Empty | First primary fallback key |
| `AGENTROUTER_API_KEY` | Empty | Second primary fallback key |
| `XINGCHI_UPSTREAM` | `https://api.xingchiapi.com` | Xingchi server; optional trailing `/v1` is normalized |
| `XINGCHI_API_KEY` | Empty | Xingchi fallback only |
| `OMNIAI_UPSTREAM` | `https://sub.omniai.cc` | OmniAi server; optional trailing `/v1` is normalized |
| `OMNIAI_API_KEY` | Empty | OmniAi fallback only |
| `FREEGG_UPSTREAM` | `https://freegg.app` | FreeGG server; optional trailing `/v1` is normalized |
| `FREEGG_API_KEY` | Empty | FreeGG fallback only |
| `PROXY_AUTH_TOKEN` | Empty | Separate local access token; required on non-loopback startup |

Native server settings must be HTTP(S) origins, with optional `/v1`.
Credentials, query strings, fragments, and other path prefixes are rejected there.
Keep API keys out of URLs.

## Listener and upload limits

| Variable | Built-in default | Purpose |
| --- | --- | --- |
| `PROXY_PORT` | `8788` | Responses-mode port |
| `CHAT_PROXY_PORT` | `8789` | Combined-mode port |
| `PROXY_MAX_BODY_BYTES` | `20971520` | Maximum uploaded body: 20 MiB |
| `PROXY_BODY_UPLOAD_TIMEOUT_S` | `60` | Total upload deadline in seconds |
| `PROXY_MAX_SSE_FRAME_BYTES` | `8388608` | Maximum upstream SSE frame: 8 MiB |
| `PROXY_MAX_SSE_PRIME_BYTES` | `8388608` | Maximum primary pre-commit buffer: 8 MiB |
| `PROXY_MAX_IMAGES` | `35` | Primary recovery-pipeline image cap |

`.env.example` sets the upload limit to `1073741824`, or 1 GiB.
This is an example configuration, not the fallback used when the setting is absent.
The guard checks declared and chunked bodies on all permitted routes.
Primary recovery and model-alias rewrites also check serialized-body size.
Filtered JSON is size-checked by the Xingchi filter.
Direct provider Chat conversion does not add an independent converted-size check when filtering is disabled.

These are per-request limits, not total memory or concurrency limits.
Large simultaneous requests can use substantial memory.
Increasing the local limit does not increase an upstream service's limit.

The listen host is not an `.env` setting.
Use `--host` with Python or `-BindHost` with the PowerShell launcher.
Use `--port` or `-Port` to override the selected default.
The PowerShell launcher's default ports are selected by mode; pass `-Port` for a custom launcher port.

## Xingchi filtering

| Variable | Default | Purpose |
| --- | --- | --- |
| `XINGCHI_SESSION_FILTER_MODE` | `0` | `0`: disabled; `1`: metadata filtering; `2`: filtering plus session-header rotation |
| `XINGCHI_SESSION_FILTER_NAMES` | Empty | Comma-separated extra names to remove |

The mode must be `0`, `1`, or `2`.
The rotation threshold is `88` incoming requests for recognized session headers.
State and counters are in memory, per process.
Do not add required request fields or authentication headers to the extra-name list.
See [filter semantics](providers.md#xingchi-session-filtering).

## Timeouts and recovery

| Variable | Default | Purpose |
| --- | --- | --- |
| `PROXY_CONNECT_TIMEOUT_S` | `20` | Upstream connect timeout |
| `PROXY_READ_TIMEOUT_S` | `600` | Upstream read timeout |
| `PROXY_RETRY_DELAY_MS` | `300` | Delay factor for primary resource-mismatch retries |
| `PROXY_RETRY_ON_5XX` | `1` | Eligible server-error recovery |
| `PROXY_RETRY_AMBIGUOUS_ERRORS` | `1` | Eligible ambiguous read/write failures and primary empty/stalled streams |
| `PROXY_MAX_TRANSIENT_RETRIES` | `6` | Maximum primary transient retries, excluding the first attempt |
| `PROXY_MAX_RATE_LIMIT_WAIT_S` | `90` | Primary HTTP/rate-limit recovery window from initial launch |
| `PROXY_MAX_TRANSPORT_RECOVERY_WAIT_S` | `90` | Primary transport window from first eligible transport failure |
| `PROXY_RATE_LIMIT_BASE_S` | `2` | Base primary calculated backoff |
| `PROXY_RATE_LIMIT_MAX_DELAY_S` | `15` | Cap on calculated primary backoff, not upstream hints |
| `PROXY_RECOVERY_STAGGER_S` | `0.6` | Primary launch spacing after rate limits |
| `PROXY_SSE_PRIME_FIRST_TIMEOUT_S` | `45` | Initial primary SSE inspection timeout |
| `PROXY_SSE_PRIME_CREATED_GRACE_S` | `3` | Primary inspection grace after setup events |

HTTPX also uses a 60-second write timeout and 30-second pool timeout.
The native relay's two-retry, eight-second budget is defined in `xingchi_proxy.py`, not these primary retry settings.
Provider Chat conversion has one possible transport retry and no native HTTP retry window.
Recovery windows limit waits and new launches, not an in-flight model generation.
See the [recovery matrix](transport-reliability.md#recovery-matrix).

Size and upload-time settings validated by `_positive_setting()` must be positive and finite.
Other settings retain their existing parsing behavior; do not assume every numeric setting has the same validator.

## Logs and automatic updates

| Variable | Default | Purpose |
| --- | --- | --- |
| `PROXY_VERBOSE` | `1` | Additional primary request diagnostics |
| `PROXY_AUTO_UPDATE` | `1` | Enable the supervisor's updater |
| `PROXY_AUTO_UPDATE_ON_START` | `1` | Check and apply an eligible update before supervisor startup |
| `PROXY_AUTO_UPDATE_INTERVAL_S` | `3600` | Background check interval; minimum 60 seconds |
| `PROXY_AUTO_UPDATE_REMOTE` | `origin` when untracked | Remote fallback when no branch tracking configuration exists |

`-NoAutoUpdate` disables updates in a scheduled-task installation.
`--no-auto-update` disables them in `service.py`.
Direct `python proxy.py` and `python chat_proxy.py` do not start the updater.
Logs can contain upstream messages. Review and redact private content before sharing them.

## Picker headers

These are client headers, not environment settings:

| Header | Purpose |
| --- | --- |
| `X-Proxy-Token` | Local authentication |
| `X-Proxy-Model-Routing: 1` | Require decodable JSON for the provider-labelled picker protocol |

Both headers are removed before upstream transmission.
Unqualified requests and ordinary direct native authentication retain their existing behavior.
See [client setup](clients.md) for the model-prefix credential rules.
