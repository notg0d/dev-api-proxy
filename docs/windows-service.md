# Windows launcher and background service

Use `start-proxy.ps1` for interactive or headless Windows startup.
Use `install-autostart.ps1` to register a task at user logon.
These scripts do not start the gateway before logon or provide a system-wide public service.

## Modes

| Mode | Worker | Default port | Scheduled-task name |
| --- | --- | --- | --- |
| `responses` | `proxy.py` | `8788` | `AgentRouterCodexProxy` |
| `combined` | `chat_proxy.py` | `8789` | `AgentRouterCombinedProxy` |

The legacy task names preserve existing installations. They do not restrict which upstream or client you can use.
Use `-Mode combined` for both primary Responses and Chat Completions.
Both modes expose all native provider namespaces.

## Interactive startup

```powershell
.\start-proxy.ps1 -Mode combined
.\start-proxy.ps1 -Mode combined -Port 9001
.\start-proxy.ps1 -Mode combined -Verbose_
```

The launcher loads `.env`, resolves Python, verifies dependencies, and prints one shared startup summary.
System Python is valid when `.venv` is absent.
Missing fallback keys are not warnings: direct clients can send their own provider keys.
The summary never prints key values.
`-Verbose_` adds environment and dependency details.

Python defaults to loopback. For a controlled LAN deployment:

```powershell
.\start-proxy.ps1 -Mode combined -BindHost 0.0.0.0
```

Configure `PROXY_AUTH_TOKEN` before a non-loopback launch.
Restrict access with a firewall and use TLS before sending credentials across the network.

## Existing listeners

The launcher checks identity, listen address, API capabilities, and upload limit before reusing a worker.
A matching worker is left running.
A stale upload limit or unrelated listener produces a conflict message, not forced replacement.

Re-running the launcher does not reload edited Python files into an existing worker.
Wait for requests to finish, stop that worker from its original terminal, then start it again.

## Headless startup

```powershell
.\start-proxy.ps1 -Mode combined -Background
```

This starts `service.py` through `pythonw.exe` and checks for readiness.
The supervisor starts a headless Uvicorn worker and restarts it after failures with bounded backoff.
It preserves the selected mode, host, and port across its own restarts.
Repeated rapid failures eventually stop the supervisor.

The Python supervisor uses `logs/service.pid` and `logs/proxy.log`.
Both modes currently use these filenames; each still has its own worker and in-memory retry state.
Do not rely on a single PID file to distinguish two simultaneous supervisors.

`-Service` runs the PowerShell supervision loop with file logging instead of normal console output.
It differs from the detached Python supervisor used by `-Background`.

## Logon tasks

```powershell
.\install-autostart.ps1 -Mode combined
.\install-autostart.ps1 -Mode combined -NoAutoUpdate
.\install-autostart.ps1 -Mode combined -WhatIf_
```

The task runs as the current user with an interactive, limited principal.
The default logon delay is eight seconds.
`-WhatIf_` prints the registration plan without task changes.
Installation replaces an existing task with the same name and starts the new task immediately.
Use `-TaskName` for a distinct task and `-Port` for a custom listener.

To remove it:

```powershell
.\uninstall-autostart.ps1 -Mode combined
```

Removal stops and unregisters the selected task.
The script stops the listener only after a matching health probe.
It leaves unrelated listeners alone.
`-KeepRunning` skips explicit listener cleanup, but the task-removal step still stops the selected scheduled task.

## Logs

The supervisor writes `logs/proxy.log` and retains one previous generation as `.1`.
The default rotation threshold is 1,024 KiB, configurable with `-MaxLogKB` or `--max-log-kb`.
Rotation is checked periodically, so the file can temporarily exceed the threshold.
The Python logger clamps its threshold to at least 64 KiB.

Do not publish unreviewed logs. Provider error bodies can quote private request content.

## Automatic updates

The Python supervisor checks for updates on startup and then every 3,600 seconds by default.
It applies only an eligible fast-forward update from the tracked branch.
Tracked local edits, detached HEAD, divergence, or fetch failures prevent application of an update.
Compilation is checked before a restart is requested.
Dependencies are synchronized only when `requirements.txt` changes.

```powershell
python updater.py --check
python updater.py --apply
```

The updater can roll back a failed compile by resetting the fast-forwarded tracked files to the previous commit.
Do not edit tracked files while an update is running; there is no exclusive filesystem lock.
`--check` fetches remote state and writes update diagnostics, but does not apply source changes.

See [Configuration](configuration.md#logs-and-automatic-updates) for update variables and [Development](development.md) for validation.
