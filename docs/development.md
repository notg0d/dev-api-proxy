# Development and releases

Use Python 3.10 or newer and the pinned packages in `requirements.txt`.
Do not use real provider keys or paid model requests for automated tests.

## Modules

| File | Responsibility |
| --- | --- |
| `version.py` | Shared release version |
| `proxy.py` | Primary Responses recovery, pool ownership, diagnostics, and Responses-mode app |
| `chat_proxy.py` | Primary Chat conversion and combined-mode app |
| `request_guard.py` | Route/method allowlist, local authentication, and bounded uploads |
| `provider_routing.py` | Provider-qualified model dispatch and cross-provider credential isolation |
| `xingchi_proxy.py` | Native relay implementation, provider Chat conversion, and optional Xingchi filtering |
| `xingchi_context.py` | Bounded Xingchi history summaries, stream validation, and credential-scoped summary cache |
| `omniai_proxy.py` | OmniAi registration, upstream setting, and compatibility facade |
| `freegg_proxy.py` | FreeGG registration and upstream setting |
| `moyuu_proxy.py` | Moyuu registration and upstream setting |
| `model_catalog.py` | Provider-labelled catalog generator with hidden compatibility entries |
| `startup_status.py` | Shared credential-safe startup formatter |
| `service.py` | Headless supervision, rotating logs, and update loop |
| `updater.py` | Fast-forward eligibility, compilation preflight, and dependency synchronization |

The ASGI order is request guard, model router, then the selected API handler.
Authentication and upload checks must happen before routing or upstream work.
Keep middleware streaming-compatible; do not add a buffering BaseHTTPMiddleware layer around SSE.

## Validation

From the repository root:

```powershell
python -m unittest discover -s tests -v
.\tests\test_launchers.ps1
git diff --check
```

The Python tests use mock transports and temporary loopback servers.
They cover recovery budgets, maintenance errors, no replay, disconnect races, provider isolation, filtering, and picker aliases.
The PowerShell tests inspect syntax and exercise isolated helpers without changing scheduled tasks or production services.

Check changed Python modules with `py_compile` or the same complete module set used by the updater.
The updater validates every core module, including the provider router, catalog generator, startup formatter, and shared version.
Add new runtime files to `_CORE_PYTHON_FILES`.

Validate JSONC and TOML examples after editing them.
Check local Markdown links and anchors when moving documentation sections.
Verify both apps report `version.VERSION` in OpenAPI and health output.

## Test boundaries

Mock success does not prove live provider access, account quotas, or available model names.
An HTTP success after retries does not prove a stream completed.
Metadata-only desktop checks do not establish successful model generation.
Do not claim those outcomes without separate, authorized evidence.

## Credential and source review

Before staging a release:

- Inspect the complete diff, including changes that existed before your task.
- Stage only reviewed source, tests, examples, and reader-facing docs.
- Keep `.env`, private key files, client configs, databases, and local catalogs out of Git.
- Scan candidate files for actual configured key values without printing the values.
- Confirm that generated metadata and temporary files are ignored or outside the checkout.
- Do not overwrite or discard unrelated user changes.

Examples must use references or placeholders, not real credentials.
Review provider error logs before sharing them: redaction does not guarantee absence of quoted private content.

## Release procedure

This repository uses annotated version tags and GitHub releases with automatic source archives.
No compiled executable is required for the current release convention.

1. Inspect existing tags and remote branch state.
2. Set the next version in `version.py`.
3. Update `CHANGELOG.md` and the release notes under `docs/releases/`.
4. Run the full validation and credential review.
5. Commit the reviewed changes on the intended branch.
6. Push the commit and an annotated tag without force.
7. Publish the release from that exact tag.
8. Verify the tag target and published release URL.

For example, after an approved release commit and tag exist:

```powershell
git push origin main
git push origin v1.9.0
gh release create v1.9.0 --verify-tag --title "dev-api-proxy v1.9.0" --notes-file docs/releases/v1.9.0.md
```

These commands publish work. Do not run them without release authorization.
Do not force-push to bypass branch protection or overwrite remote history.
Reconcile concurrent remote changes without resetting uncommitted work.

## Upgrade activation

Existing workers do not reload edited Python modules.
Wait for active work to finish, update a clean checkout, then restart the desired proxy mode.
Preserve `.env` and use health output to verify version, picker prefixes, and provider URLs.
Client changes are separate from source installation; a release does not modify private desktop configuration automatically.
