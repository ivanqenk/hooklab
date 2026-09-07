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

> **Status: usable end to end in the browser.** Capture, the live feed, signature verification and
> reliable forwarding all work and are all driveable from the web interface. Accounts and deployment
> are what remain.

## Requirements

- Python 3.12 or newer
- Node 20 or newer
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

# 4. Frontend, in another terminal
cd frontend
npm install
npm run dev
```

Check that it came up:

```bash
curl http://localhost:8010/health   # {"status":"ok"}
curl http://localhost:8010/ready    # {"status":"ready","checks":{...}}
```

The interface is at <http://localhost:5173> and interactive API docs at
<http://localhost:8010/docs>. The dev server proxies `/api` and `/in` to the backend, so the browser
only ever sees one origin — no CORS in development, and the same shape as production behind a
reverse proxy.

Everything below can be done from that interface. It is shown with `curl` because the API is the
contract, and because a copy-pasteable command is easier to read than a screenshot.

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

## The interface

<http://localhost:5173> gives you the same thing without the terminal: press one button for an
endpoint, copy the ingest URL into the provider's panel, and watch captures land. Selecting one
shows its headers, query, pretty-printed body and signature verdict, plus what happened to it on
the way to each destination. Settings is where you paste a signing secret and register somewhere to
forward to.

The view token lives in the URL **fragment**, which browsers never send to a server: it stays out
of access logs and out of every proxy in between. Keep that link and you keep the endpoint.

Because the page renders whatever the sender wrote — headers, bodies, paths — and the URL carries
the view token, stored XSS is the risk that matters here. React escapes text by default, and a test
walks the source and fails the build on `dangerouslySetInnerHTML`, `innerHTML`, `srcdoc`, on any
`href` not built by our own API client, and on any use of `localStorage`, `sessionStorage` or
`document.cookie`. A link built from a payload would hand the view token to a stranger in the
`Referer` header.

## Verify a signature

Point an endpoint at a provider and every capture gets checked as it arrives:

```bash
curl -X PUT localhost:8010/api/endpoints/<view_token>/signature \
  -H 'content-type: application/json' \
  -d '{"provider":"github","secret":"It'"'"'s a Secret to Everybody"}'
```

The verdict rides along with the capture, in the listing, the detail and the live feed:

```json
{
  "valid": false,
  "reason": "timestamp_out_of_tolerance",
  "detail": "The signature is genuine, so your secret is correct, but the timestamp is 400 seconds old and the tolerance is 300. Either this is a replay of an earlier event, or this machine's clock has drifted."
}
```

That sentence is the feature. Every other tool tells you `invalid` — which you already knew.

The secret is **write-only**: encrypted at rest and returned by no route, in any form.

## Forward with retries

Register a destination and prove you control it. Nothing is forwarded until you do — otherwise
Hooklab would be a free amplifier anyone could aim at a stranger's server.

```bash
# 1. Register. Comes back unverified, with a challenge token.
curl -X POST localhost:8010/api/endpoints/<view_token>/destinations \
  -H 'content-type: application/json' -d '{"target_url":"https://my-app.example/hooks"}'

# 2. Make your server answer with exactly that token when a request carries
#    the x-hooklab-verification header, then:
curl -X POST localhost:8010/api/endpoints/<view_token>/destinations/<id>/verify

# 3. Run the worker beside the API.
.venv/bin/python -m app.worker.run
```

From then on every capture is forwarded, retried with exponential backoff and jitter, and ends up
either delivered or in the dead-letter state where you can retry it by hand:

```bash
curl localhost:8010/api/endpoints/<view_token>/deliveries?state=exhausted
curl -X POST localhost:8010/api/endpoints/<view_token>/deliveries/<id>/retry
```

Delivery is **at-least-once, not exactly-once** — that is a property of HTTP, not a shortcut. When a
request times out there is no way to tell whether the destination processed it and the reply was
lost, or whether it never arrived. Retrying risks a duplicate; not retrying risks a loss. Hooklab
retries, and sends a `X-Hooklab-Idempotency-Key` that stays identical across every attempt so the
receiver can recognise the repeat.

## Quality

```bash
cd backend
.venv/bin/ruff check app/ tests/ scripts/       # linter
.venv/bin/ruff format --check app/ tests/ scripts/
.venv/bin/mypy app/ tests/ scripts/             # types
.venv/bin/pytest -q                             # tests

cd ../frontend
npx oxlint src                                  # linter
npm run build                                   # types, via tsc -b
npm test                                        # tests
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

**The diagnosis is the product, not the boolean.** The failures that actually happen are few and
each has a different fix: a test-mode secret used with a live-mode event, a body signed without the
timestamp prefix, an event replayed outside the tolerance window, a signature header that never
arrived. Naming which one it was is the difference between a five-second fix and an afternoon.
Hooklab can also give a diagnosis nobody else can: when it truncated the body at its own size limit,
the HMAC cannot match, and blaming the secret would be actively misleading.

**Validate the resolved address, never the hostname.** `evil.com` is free to resolve to
`127.0.0.1`, so a blocklist of names stops nobody. Every address a name resolves to must pass, and
the connection is then opened to that exact IP with the original hostname sent as `Host` and TLS
SNI — letting the HTTP client resolve a second time reopens the DNS-rebinding window the check just
closed. The standard library's own categories are not enough on their own: `is_private` misses
carrier-grade NAT, and `is_global` returns True for multicast *and* for `64:ff9b::/96`, the NAT64
prefix that carries an IPv4 address in its low 32 bits — so `64:ff9b::7f00:1` reaches `127.0.0.1`.

**The delivery queue is Postgres, not a task queue.** Everything a job queue would hold — when the
next attempt is due, how many have been made, what failed last time — already has to live in a table
because the UI shows it. Running a second scheduler alongside would mean two answers that can
disagree, and the one users see would be the wrong one. `SELECT ... FOR UPDATE SKIP LOCKED` gives a
queue over the table that already exists, and lets several workers run without either serialising
or double-sending.

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
