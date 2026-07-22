"""Shared rate limiter for the API.

Lives in its own module (not main.py) so route modules like app/api/auth.py can
apply `@limiter.limit(...)` without importing main.py, which would be a circular
import (main.py imports the routers).

Storage is in-memory: fine as a brute-force speed-bump on a single instance, and
it resets on restart. If the backend is ever scaled to multiple instances, point
`Limiter(storage_uri=...)` at Redis so the limit is shared across them.
"""

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings


def _client_ip(request: Request) -> str:
    """Real client IP for keying. Behind Render's proxy, request.client.host is
    the proxy address (shared by everyone), so the real client IP has to come
    from X-Forwarded-For. The left-most hop is whatever the ORIGINAL caller
    claims, which they can set to anything — trusting it let a single machine
    spread requests across unlimited fake buckets and bypass the limit
    entirely. Only the right-most `trusted_proxy_hops` entries are actually
    appended by proxies we trust; the client's real IP is the hop just before
    those."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        if hops:
            idx = max(len(hops) - settings.trusted_proxy_hops, 0)
            return hops[idx]
    return get_remote_address(request)


limiter = Limiter(key_func=_client_ip)
