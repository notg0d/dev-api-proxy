"""Native OmniAi/Sub2API relay with isolated routes and credentials."""

import os

import xingchi_proxy
from request_guard import OMNIAI_PREFIX, omniai_upstream_path


def _upstream_base(raw: str) -> str:
    try:
        return xingchi_proxy._upstream_base(raw)
    except ValueError as exc:
        raise ValueError(str(exc).replace("XINGCHI_UPSTREAM", "OMNIAI_UPSTREAM")) from None


UPSTREAM = _upstream_base(os.environ.get("OMNIAI_UPSTREAM", "https://sub.omniai.cc"))


def register_routes(app, backend) -> None:
    """Install native routes and the optional Codex Chat conversion facade."""
    xingchi_proxy.register_chat_conversion(
        app, backend, prefix=OMNIAI_PREFIX + "/codex", upstream=lambda: UPSTREAM,
        provider="omniai", endpoint_path="/backend-api/codex/responses",
    )
    xingchi_proxy.register_routes(
        app, backend, provider="omniai", upstream=lambda: UPSTREAM,
        prefix=OMNIAI_PREFIX, path_mapper=omniai_upstream_path,
    )
