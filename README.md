# Hooklab

[![CI](https://github.com/ivanqenk/hooklab/actions/workflows/ci.yml/badge.svg)](https://github.com/ivanqenk/hooklab/actions/workflows/ci.yml)

**A webhook gateway for development: receive, verify and forward.**

Integrating Stripe, GitHub or any other webhook provider runs into three problems:

1. Your machine does not exist on the internet, so the provider cannot reach your `localhost`.
2. The documentation tells you what *should* arrive, but the real payload carries fields you did
   not expect and signature headers you have to validate.
3. When something breaks in production, your logs kept the stack trace but not the body — so you
   cannot reproduce it.

Hooklab gives you a public URL where every request is **captured and appears live**, has its
provider **signature verified** with an explanation of *why* it failed when it does, and is
**forwarded** to your destination with retries and exponential backoff.

> **Status: under construction.** Capture and the live feed work today — you can use Hooklab from
> `curl` with no frontend. Signature verification is next.

## Requirements

- Python 3.12 or newer
- Docker and Docker Compose

## Getting started

```bash
# 1. Configuration
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"   # paste into SECRET_KEY

# 2. Dependencies (Postgres + Redis)
docker compose up -d --wait

# 3. Backend
cd backend
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --port 8010 --reload
```

Check that it came up:

```bash
curl http://localhost:8010/health   # {"status":"ok"}
curl http://localhost:8010/ready    # {"status":"ready","checks":{...}}
```

Interactive API docs live at <http://localhost:8010/docs>.

## Try it

Create an endpoint, watch the live feed in one terminal and send it a webhook from another:

```bash
# Returns both tokens. ingest_token is public; view_token is secret.
curl -s -X POST localhost:8010/api/endpoints \
  -H 'content-type: application/json' -d '{"name":"demo"}'

curl -N localhost:8010/api/endpoints/<view_token>/stream

curl -X POST localhost:8010/in/<ingest_token> \
  -H 'content-type: application/json' -d '{"hello":"world"}'
```

The capture shows up on the stream immediately:

```
event: ready
data: {"endpoint_id": "bd74bc77-b14e-410b-9ffc-398ccd6aed9d"}

id: 1
event: request
data: {"id": 1, "method": "POST", "path": "", "content_type": "application/json", ...}
```

## Quality

```bash
cd backend
.venv/bin/ruff check app/ tests/ scripts/       # linter
.venv/bin/ruff format --check app/ tests/ scripts/
.venv/bin/mypy app/ tests/ scripts/             # types
.venv/bin/pytest -q                             # tests
```

Tests run against **real** Postgres and Redis rather than mocks. That is not purism: it is how a
real bug got caught — an `INET` column comes back from psycopg as an `IPv4Address`, not as text,
and no mock would ever have told us.

### The test that validates the architecture

```bash
.venv/bin/uvicorn app.main:app --port 8010 --workers 4
.venv/bin/python scripts/verify_fanout.py
```

Open one SSE connection, fire twenty webhooks concurrently, and every one of them must arrive. The
script checks by PID that some webhooks were served by a worker that does **not** hold the stream,
so the run cannot pass for the wrong reason.

This cannot be covered by the test suite: pytest drives the app in a single process, where an
in-memory list would pass every assertion. The bug only exists across processes — and with one
worker it never reproduces at all, which is exactly what makes it easy to ship.

## Technical decisions

The full reasoning, with the alternatives that were rejected, lives in `docs/`. The decisions that
shape the code the most:

**Redis Streams, not pub/sub.** The browser's SSE connection lives in one uvicorn process while the
incoming webhook may land in another. Without a shared bus the browser never sees it — and with a
single worker the bug does not show, which makes it especially treacherous. Streams also close the
reconnection gap natively, which is exactly what the SSE `Last-Event-ID` header is for.

**The capture id is the SSE event id.** `requests.id` is a `bigserial`, and being monotonic it
serves three purposes at once: primary key, pagination cursor (`?before=N`, stable even while new
captures arrive, which `offset` cannot manage), and `Last-Event-ID` for resuming a dropped stream.
No cursor table.

**Stream order does not follow id order.** An id is assigned at flush; the announcement goes out
after commit. Under concurrency those interleave, so capture 87 routinely reaches the stream ahead
of 85. De-duplicating with a "highest id seen" watermark silently discarded roughly half the
captures under load — and never once in a sequential test. The guard is a bounded set of ids.

**The body is stored raw.** Real webhooks send XML, `form-encoded` and binary, not just JSON. More
importantly, a signature HMAC is computed over the **exact bytes** of the body. If the framework
parses and re-serializes, verification fails even with the correct secret — the number one cause of
"my webhook validation doesn't work".

**Two separate tokens.** The ingest token is public and ends up in logs and screenshots; the view
token is secret and is the only one that can *read* the traffic. With a single token, anyone who
saw that URL in a configuration panel could read all your payloads.

**A raw body is never served with its original content type.** Always `octet-stream`, always an
attachment, always `nosniff`. Echoing back `text/html` chosen by a stranger would turn the domain
into free malware hosting.

**Liveness ≠ readiness.** `/health` deliberately does not check dependencies: if Postgres goes
down, the process is still healthy and restarting it would fix nothing. `/ready` does check them
and returns 503, so a load balancer stops sending traffic without killing the instance.

## License

To be decided.
