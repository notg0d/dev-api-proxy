"""Native Moyuu/New API relay with provider-specific routes and credentials."""

import os
import xingchi_proxy
from request_guard import MOYUU_PREFIX, moyuu_upstream_path


def _upstream_base(raw: str) -> str:
    try:
        return xingchi_proxy._upstream_base(raw)
    except ValueError as exc:
        raise ValueError(str(exc).replace("XINGCHI_UPSTREAM", "MOYUU_UPSTREAM")) from None


UPSTREAM = _upstream_base(os.environ.get("MOYUU_UPSTREAM", "https://moyuu.cc"))


def register_routes(app, backend) -> None:
    """Install Moyuu's Responses-compatible relay and native API routes."""
    xingchi_proxy.register_chat_conversion(
        app, backend, prefix=MOYUU_PREFIX, upstream=lambda: UPSTREAM, provider="moyuu",
    )
    xingchi_proxy.register_routes(
        app, backend, provider="moyuu", upstream=lambda: UPSTREAM,
        prefix=MOYUU_PREFIX, path_mapper=moyuu_upstream_path,
    )