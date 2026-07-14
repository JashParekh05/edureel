from slowapi import Limiter
from fastapi import Request


def _real_ip(request: Request) -> str:
    # Take the LAST X-Forwarded-For hop: it's the one appended by our own proxy
    # (Render) and can't be forged. The first entry is client-supplied, so using
    # it lets anyone bypass rate limits by rotating a fake header.
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host


limiter = Limiter(key_func=_real_ip)
