"""Normalisation of an incoming HTTP request into what we store."""

import json
from typing import Any

from fastapi import Request

# Content types whose body is worth parsing into JSONB as well as storing raw.
_JSON_HINTS = ("application/json", "text/json")


async def read_body_with_limit(request: Request, max_bytes: int) -> tuple[bytes, bool]:
    """Read the request body, stopping once `max_bytes` have been collected.

    Returns the bytes read and whether the body was truncated.

    The limit is enforced **while streaming**, which is the whole point. The
    obvious alternative -- `await request.body()` and then check the length --
    reads everything into memory first, so a 500 MB body exhausts the process
    before the check ever runs. That is precisely the attack: the check would be
    correct and useless.

    Reading stops at the limit rather than draining the rest. A client still
    sending may see a broken pipe, which is the right answer for a request that
    exceeded the limit.
    """
    chunks: list[bytes] = []
    total = 0
    truncated = False

    async for chunk in request.stream():
        # Truncation is decided by whether this chunk would push us past the
        # limit. A body of exactly max_bytes therefore finishes the loop
        # normally and is NOT reported as truncated.
        if total + len(chunk) > max_bytes:
            chunks.append(chunk[: max_bytes - total])
            truncated = True
            break
        chunks.append(chunk)
        total += len(chunk)

    return b"".join(chunks), truncated


def normalise_headers(request: Request) -> dict[str, str]:
    """Collapse the header multimap into a plain dict.

    HTTP allows a field name to appear more than once, and RFC 9110 says those
    lines are equivalent to a single one joined by commas -- so that is what we
    do, instead of silently dropping all but the last. Names arrive lowercased
    by the ASGI layer.
    """
    collected: dict[str, str] = {}
    for name, value in request.headers.items():
        if name in collected:
            collected[name] = f"{collected[name]}, {value}"
        else:
            collected[name] = value
    return collected


def normalise_query(request: Request) -> dict[str, str] | None:
    """Same treatment for query parameters. None when there are none."""
    if not request.query_params:
        return None

    collected: dict[str, str] = {}
    for key, value in request.query_params.multi_items():
        if key in collected:
            collected[key] = f"{collected[key]}, {value}"
        else:
            collected[key] = value
    return collected


def parse_json_body(body: bytes, content_type: str | None, truncated: bool) -> Any | None:
    """Parse the body as JSON when it makes sense, otherwise return None.

    A truncated body is never parsed: it is invalid JSON by definition, and
    reporting a parse failure for a body we cut ourselves would be misleading.

    This is only ever a *convenience copy*. The raw bytes remain the source of
    truth, because signature HMACs are computed over them.
    """
    if truncated or not body or not content_type:
        return None

    media_type = content_type.split(";", 1)[0].strip().lower()
    if not (media_type in _JSON_HINTS or media_type.endswith("+json")):
        return None

    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # A provider sending malformed JSON is exactly the kind of thing the
        # developer needs to see, so this is not an error: we simply keep the
        # raw body and offer no parsed copy.
        return None
