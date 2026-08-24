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
