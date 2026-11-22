# Changelog

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
