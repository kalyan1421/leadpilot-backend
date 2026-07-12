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


def _client_ip(request: Request) -> str:
    """Real client IP for keying. Behind Render's proxy, request.client.host is
    the proxy address (shared by everyone), so prefer the left-most hop in
    X-Forwarded-For. A caller can spoof XFF to spread its requests, so this is a
    speed-bump against naive brute-force, not a hard guarantee — tighten later by
    trusting only the proxy-appended hop if needed."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=_client_ip)
