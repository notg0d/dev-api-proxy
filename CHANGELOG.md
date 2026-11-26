# Changelog

## 1.9.0 - 2026-11-26

### Added

- Native Moyuu routes under `/moyuu`, with an isolated `MOYUU_API_KEY` fallback and `MOYUU_UPSTREAM` setting.
- Moyuu picker aliases such as `moyuu/gpt-6.1-sol`, catalog labels, and a provider-specific compaction fingerprint.
- Moyuu OpenCode and Codex examples, configuration rows, and provider notes recording public route probes.
- Automatic Xingchi history summaries in mode `2`, before the 100-turn payload limit.
- Optional `XINGCHI_USER_AGENT` override for native, converted Chat, and history summary requests.

### Improved

- FreeGG and Moyuu share one New API route allowlist instead of separate copies.
- Relay labels and generated catalog labels read from the single provider registry.
- Startup output, health, key redaction, launcher credential loading, and the updater module set include Moyuu.
- Xingchi session rotation counts every upstream send, including summaries and retry attempts.
- Session aliases include OpenCode and normalized Codex headers, with counters isolated by upstream credentials.

### Fixed

- Summary requests use bounded chunks instead of sending an oversized history back to the same 100-turn guard.
- Streaming metadata has a separate 16 MiB response limit from the 256 KiB summary-text limit.
- Empty, failed, incomplete, oversized, and turn-limit notice summaries are rejected instead of replacing history.
- The updater compiles `xingchi_context.py` before an update becomes active.

### Upgrade notes

Both modes now report `1.9.0`. Restart the proxy service so workers load all new modules and settings.
Preserve `.env`; add `MOYUU_API_KEY` only when you want a proxy-side Moyuu fallback.
Moyuu Chat Completions are converted to Responses, matching the existing New API policy.
Regenerate a provider-labelled catalog to add Moyuu picker entries; existing model IDs stay valid.
Moyuu responses input-token counts, Anthropic token counts, and Gemini `countTokens` are not exposed.

Xingchi history summaries require `XINGCHI_SESSION_FILTER_MODE=2`; the default remains `0`.
Each uncached summary chunk uses an additional model request. Summaries can omit older details.
For `403 client_not_allowed`, configure a versioned client identifier, such as `XINGCHI_USER_AGENT=opencode/2.4.2`.
Keep valid provider credentials; the header override does not change account or model permissions.

## 1.8.0 - 2026-11-22

### Added

- Native OmniAi routes, isolated authentication, and Sub2API compatibility paths.
- Provider-qualified model routing for Responses, Chat, compaction, and supported input-token counts.
- Provider-labelled desktop catalogs with hidden legacy IDs and provider-specific compaction fingerprints.
- Optional Xingchi metadata filtering and 88-request session-header rotation.
- A shared release version used by both proxy modes and startup diagnostics.
- Client examples and focused guides for providers, desktop setup, configuration, reliability, and Windows supervision.

### Improved

- Native relay retries now include eligible `500`, `502`, `503`, and `504` failures.
- Primary recovery recognizes maintenance-related errors before response commitment.
- Cross-provider picker choices remove unrelated credentials and require the selected provider's key.
- Encoded picker requests fail locally when required decoding is unavailable.
- Startup output lists all providers once and no longer warns about normal system-Python or client-key use.
- The updater's compilation set includes all provider, routing, catalog, and startup modules.
- Generated catalog output is atomic; source metadata and temporary files are kept out of publication paths.

### Upgrade notes

Both modes now report `1.8.0`, replacing their different legacy component-version strings.
Restart old workers to load the new modules; re-running the launcher can reuse an existing process.
Preserve `.env`. The built-in body-limit fallback remains 20 MiB; the example sets 1 GiB.
Session filtering is disabled unless explicitly enabled.
Picker aliases need a generated full Codex catalog, not a plain list of model IDs.
On canonical `/v1`, configure each provider's key on the proxy.
An existing matching native connection can retain its client key without sharing it with other providers.

Private client configuration, API keys, and user-specific model catalogs are not part of the release.
Model access and paid generation remain account-specific and are not established by the automated tests.

## Earlier releases

### 1.3.5 - 2026-11-20

- Persisted the local `.env` upload limit over stale inherited values.
- Set the example upload limit to 1 GiB and checked existing launcher workers for a matching limit.

See [GitHub releases](https://github.com/notg0d/dev-api-proxy/releases) for prior release notes and source archives.
