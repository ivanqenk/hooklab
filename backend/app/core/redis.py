"""Shared Redis client."""

from redis.asyncio import Redis

from app.core.config import get_settings

settings = get_settings()

# decode_responses=False on purpose, and this is a design decision rather than a
# detail.
#
# Webhook bodies are BYTES: they arrive as XML, form-encoded, multipart or binary,
# not just JSON. More importantly, a signature HMAC is computed over the EXACT
# bytes of the body. Decoding to str and re-encoding can alter those bytes, and
# then EVERY signature check would fail even with the correct secret. That is the
# number one cause of "my webhook verification doesn't work".
redis: Redis = Redis.from_url(settings.redis_url, decode_responses=False)


def get_redis() -> Redis:
    """FastAPI dependency handing out the shared client.

    Not a yield dependency: redis-py manages its own connection pool, so there is
    nothing to release per request. Going through a dependency instead of
    importing the module global is what lets a test swap the client out.
    """
    return redis
