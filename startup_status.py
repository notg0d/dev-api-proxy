"""Build a consistent, credential-safe startup summary for every launcher."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit, urlunsplit


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:g} {unit}"
        value /= 1024
    return f"{size} B"


def display_url(raw: str) -> str:
    """Exclude credentials, queries, fragments, and terminal control characters."""
    try:
        parts = urlsplit(raw)
        host = parts.hostname
        if parts.scheme not in ("http", "https") or not host:
            return "[invalid upstream URL]"
        host = f"[{host}]" if ":" in host else host
        netloc = f"{host}:{parts.port}" if parts.port is not None else host
        safe = urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))
        return "".join(char for char in safe if char.isprintable())
    except ValueError:
        return "[invalid upstream URL]"


def build_summary(
    *, mode: str, host: str, port: int, providers, session_filter: str,
    auth_enabled: bool, max_body_bytes: int, max_sse_frame_bytes: int,
    connect_timeout_s: float, read_timeout_s: float, log_path: str | None = None,
    control: str = "Ctrl+C",
    version: str | None = None,
) -> list[str]:
    """Return lines containing only public configuration and credential presence."""
    host = host.strip().strip("[]") or "127.0.0.1"
    try:
        loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "*") else "::1" if host == "::" else host
    url_host = f"[{host}]" if ":" in host else host
    probe_url_host = f"[{probe_host}]" if ":" in probe_host else probe_host
    probe_base = f"http://{probe_url_host}:{port}"
    bind_note = "loopback only" if loopback else "firewall required"
    apis = "Responses + Chat Completions" if mode == "combined" else "Responses"
    lines = [
        "",
        f"  dev-api-proxy | {mode}",
        *([f"  Version      {version}"] if version else []),
        f"  Listen       http://{url_host}:{port} ({bind_note})",
        f"  Primary API  {apis}",
        "  Transport    HTTP + SSE (no WebSockets)",
        "",
        "  Upstreams    URL | local API base | credentials",
    ]
    for name, upstream, route, fallback_present in providers:
        credentials = "client key or configured fallback" if fallback_present else "client key"
        lines.append(f"    {name:<10} {display_url(upstream)} | {route} | {credentials}")
    lines.extend([
        "",
        f"  Xingchi      {session_filter}",
        f"  Local auth   {'enabled (X-Proxy-Token required)' if auth_enabled else 'disabled'}",
        f"  Limits       upload {format_bytes(max_body_bytes)}; SSE frame {format_bytes(max_sse_frame_bytes)}",
        f"  Timeouts     connect {connect_timeout_s:g}s; read {read_timeout_s:g}s",
        "",
        f"  Health       {probe_base}/_health",
        f"  Stats        {probe_base}/_stats",
    ])
    if log_path:
        lines.append(f"  Log          {log_path}")
    lines.extend([f"  Control      {control}", ""])
    return lines
