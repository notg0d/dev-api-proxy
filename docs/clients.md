# Desktop and CLI client setup

Run the proxy before using a client. The examples use combined mode on port `8789`.
Choose models available to the selected provider account.
The example model names do not establish access, prices, or token limits.

## OpenCode desktop and CLI

OpenCode desktop and CLI use the same global provider configuration:

```text
~/.config/opencode/opencode.jsonc
```

Use the current V2 `providers`, `package`, `settings`, and `models` fields.
See [the complete example](../examples/opencode.jsonc).

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "providers": {
    "OmniAi": {
      "name": "OmniAi (local proxy)",
      "package": "@opencode/ai/providers/openai-compatible/responses",
      "env": ["OMNIAI_API_KEY"],
      "settings": {
        "baseURL": "http://127.0.0.1:8789/omniai/v1",
        "transport": "http",
        "encoding": "none",
      },
      "headers": { "X-Proxy-Token": "{env:PROXY_AUTH_TOKEN}" },
      "models": {
        "gpt-6.1-sol": { "name": "OmniAi · GPT-6.1 Sol" },
      },
    },
  },
}
```

Connect the provider through the desktop's provider settings or `/connect`, then use the main model picker.
A model's map key is its selectable ID. Its `name` is only the display label.
Keep separate provider definitions for separate native URLs and credentials.

For Responses, use `@opencode/ai/providers/openai-compatible/responses`.
For Chat Completions, use `@opencode/ai/providers/openai-compatible`.
The proxy converts primary, Xingchi, and FreeGG Chat; OmniAi Chat is native.

For FreeGG Anthropic, use `@opencode/ai/providers/anthropic-compatible` and `/freegg/v1` as the package base.
It sends `/messages` relative to that base.
The proxy also accepts the unversioned `/freegg/messages` alias.
Use the API path convention required by your client package.

### Key sources

OpenCode supports saved provider accounts, server environment variables, and configuration references.
If you set a key in V2 configuration, use `settings.apiKey`, not a top-level provider `apiKey`.
Prefer references over literal keys:

```jsonc
"settings": {
  "baseURL": "http://127.0.0.1:8789/freegg/v1",
  "apiKey": "{env:FREEGG_API_KEY}"
}
```

For a private key file containing only the key, without a trailing newline:

```jsonc
"apiKey": "{file:~/.secrets/freegg.key}"
```

Do not reference an entire TOML or JSON configuration as a key file.
It would substitute the whole file, not one credential field.
Omit an explicit `settings.apiKey` when a saved OpenCode connection should supply the credential.
Do not overwrite that connection with an unresolved empty environment substitution.

Config files are watched. A provider-config edit alone does not require restarting the OpenCode service.
Use `/reload` if a full configuration reload is needed.
Environment changes must reach the service process; changing a terminal's environment does not update a running desktop service.
Use OpenCode's supported credential and service-environment controls, not direct database edits.

References: [OpenCode providers](https://opencode.ai/docs/providers), [configuration](https://opencode.ai/docs/config), and [credential storage](https://opencode.ai/docs/cli/providers).

## ChatGPT desktop and Codex

The current ChatGPT desktop app uses the Codex backend.
On Windows it reads:

```text
%USERPROFILE%\.codex\config.toml
```

Native Codex uses the same user-level file.
On macOS or Linux, the default is `~/.codex/config.toml`.
WSL uses its Linux home unless `CODEX_HOME` points elsewhere.

### Separate provider definitions

Keep root settings before the first TOML table. Merge definitions without duplicating an existing table.

```toml
model = "gpt-6.1-sol"
model_provider = "xingchi"

[model_providers.xingchi]
name = "Xingchi (local proxy)"
base_url = "http://127.0.0.1:8789/xingchi/v1"
wire_api = "responses"
env_key = "XINGCHI_API_KEY"
requires_openai_auth = false
supports_websockets = false
env_http_headers = { "X-Proxy-Token" = "PROXY_AUTH_TOKEN" }

[model_providers.omniai]
name = "OmniAi (local proxy)"
base_url = "http://127.0.0.1:8789/omniai/v1"
wire_api = "responses"
env_key = "OMNIAI_API_KEY"
requires_openai_auth = false
supports_websockets = false
env_http_headers = { "X-Proxy-Token" = "PROXY_AUTH_TOKEN" }

[model_providers.freegg]
name = "FreeGG (local proxy)"
base_url = "http://127.0.0.1:8789/freegg/v1"
wire_api = "responses"
env_key = "FREEGG_API_KEY"
requires_openai_auth = false
supports_websockets = false
env_http_headers = { "X-Proxy-Token" = "PROXY_AUTH_TOKEN" }
```

Restart ChatGPT desktop after editing its provider configuration.
Where available, open **Settings → Model Providers** and use **Switch provider** for new chats.
Changing the configured provider ID can prevent directly resuming chats saved under another provider.
Keep old definitions and IDs when preserving those chats is important.

Each provider needs its own credential.
Windows desktop apps launched from Explorer may not inherit variables set in a terminal.
Use the app's supported authentication controls, Windows user environment, or a configured credential helper.

Codex also accepts `experimental_bearer_token` in a private user-level provider definition.
That direct-key setting is discouraged; never put it in project files or published examples.
Do not combine a direct key, `env_key`, and command-backed `auth` for the same provider.
Protect private configuration, key files, and backups from other users.

References: [Windows desktop configuration](https://learn.chatgpt.com/docs/windows/windows-app) and [gateway setup](https://learn.chatgpt.com/docs/enterprise/connect-to-a-gateway).

## Several providers in ChatGPT's main picker

ChatGPT's ordinary model list belongs to its selected gateway connection.
Provider-labelled aliases let that one connection route models to different native relays.
For example, `omniai/gpt-6.1-sol` becomes `gpt-6.1-sol` on OmniAi.

### Prepare a valid source catalog

Obtain full, unqualified Codex model metadata from an existing client catalog or a compatible gateway catalog endpoint.
A plain OpenAI list containing only model IDs is insufficient.
The `model/list` picker response is also not a replacement for full Codex model metadata.

The source must include the fields required by your Codex version.
These include `slug`, display metadata, reasoning levels, shell type, visibility, truncation policy, and model instructions.
Instructions must be supplied through `model_messages.instructions_template` or supported legacy `base_instructions`.
Do not fabricate model instructions, capabilities, or account availability.

Generate a separate output:

```powershell
python model_catalog.py --source source-models.json --output provider-picker-models.json
```

The generator:

- Creates Xingchi, OmniAi, and FreeGG labels and matching `provider/model` IDs.
- Copies source model instructions, capabilities, and reasoning options.
- Gives each provider its own compaction fingerprint prefix.
- Retains unqualified IDs as hidden compatibility entries for older chats.
- Writes output atomically and does not overwrite the source.

Four source models produce 12 labelled picker choices and four hidden compatibility entries.
Use `--overwrite` only to replace a previously generated output.
Do not publish source or generated catalogs containing private instructions.

### Configure the gateway

Use [the picker template](../examples/chatgpt-picker.toml), with an absolute path to the generated catalog.
For a new configuration, the ordinary `/v1` base is suitable:

```toml
model = "xingchi/gpt-6.1-sol"
model_provider = "local-proxy"
model_catalog_json = 'C:\path\to\provider-picker-models.json'

[model_providers.local-proxy]
name = "Local provider picker"
base_url = "http://127.0.0.1:8789/v1"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
http_headers = { "X-Proxy-Model-Routing" = "1" }
env_http_headers = { "X-Proxy-Token" = "PROXY_AUTH_TOKEN" }

[features]
enable_request_compression = false
```

Configure `XINGCHI_API_KEY`, `OMNIAI_API_KEY`, and `FREEGG_API_KEY` on the proxy for this canonical base.
The client gateway does not need an unrelated upstream key.

For an existing Xingchi connection, you can keep its provider ID, native base URL, and existing client key.
Add the routing header, use the generated catalog, and change the selected model to `xingchi/<model>`.
A matching native alias retains its client Xingchi authentication.
Other provider choices discard that key and use the selected provider's proxy-side fallback.
This avoids changing saved chats to another gateway provider ID.

### Routing and key isolation

| Inbound connection | Alias | Credential used upstream |
| --- | --- | --- |
| `/v1` | `omniai/<model>` | Proxy-side `OMNIAI_API_KEY` |
| `/xingchi/v1` | `xingchi/<model>` | Matching client key, or Xingchi fallback |
| `/xingchi/v1` | `omniai/<model>` | Proxy-side OmniAi key; the client Xingchi key is removed |
| `/xingchi/v1` | `freegg/<model>` | Proxy-side FreeGG key; the client Xingchi key is removed |

Cookie and unrelated account-routing credentials are removed from aliased requests.
Missing fallback keys return `400` with `provider_api_key_missing`, without an upstream request.
Unsupported provider routes return `unsupported_provider_route`; they do not fall back to the anchor provider.
FreeGG input-token counts are unsupported by the current allowlist.

`X-Proxy-Model-Routing: 1` makes undecodable picker requests fail locally instead of silently using the original native path.
The header is removed before upstream transmission.
Keep request compression disabled unless every required decoder is available and tested.

Restart the proxy and ChatGPT desktop after setup.
Select the provider-labelled model in the main picker.
Start a new chat when changing upstream providers to avoid incompatible response references or compaction state.
Catalog labels and routing do not prove live model access or increase provider limits.

## Codex CLI profiles

Modern Codex profiles are separate files beside `config.toml`:

```toml
# ~/.codex/omniai.config.toml
model_provider = "omniai"
model = "gpt-6.1-sol"
```

```powershell
codex --profile omniai
```

The provider definition remains in the main user-level config.
Use matching model metadata for that provider, or existing hidden compatibility metadata when appropriate.
Profiles are a CLI convenience, not the ChatGPT desktop provider-registration procedure.
See [Codex profiles](https://learn.chatgpt.com/docs/config-file/config-advanced#profiles).

## Other compatible clients

Use the provider's local base URL, HTTP Responses or Chat protocol, and an appropriate account key.
Clients that append `/v1` themselves should receive a base without that suffix.
For OpenMinis, select its custom API-key provider, not its built-in ChatGPT OAuth integration.
Use `/xingchi/codex` only when the account needs the Codex compatibility facade.
That facade does not create or impersonate a ChatGPT OAuth session.

For another device, replace `127.0.0.1` with the reachable proxy address and configure the listener explicitly.
Supply `X-Proxy-Token` separately when local authentication is enabled.
Use a firewall and TLS; do not expose a shared-key proxy publicly.

## Desktop launch errors

`Connection for host ID local not found` names ChatGPT's internal desktop-host connection.
It does not identify a provider URL or prove that the proxy failed.
Check the desktop launch log, local backend state, and any full popup stack trace.
If the popup persists, record the time and app version before changing settings.
Do not delete chat databases, credentials, or application state as a first troubleshooting step.

For proxy activation and transport errors, use [diagnostics in the README](../README.md#diagnostics) and [Transport reliability](transport-reliability.md).
