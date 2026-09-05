# Session context — Hooklab

A document for picking the project back up without re-reading the whole previous conversation.
**Last updated: 2026-09-04.**

---

## 1. What Hooklab is

A webhook gateway for development. You generate a public URL, and every request that arrives there:

1. is **captured** and shows up live in the browser (SSE),
2. has its provider **signature verified**, saying *why* it fails when it does,
3. is **forwarded** to your destination with retries, exponential backoff and idempotency.

**Goals, in order:** learn the stack → have a portfolio repo → maybe some money.

**Repository:** <https://github.com/ivanqenk/hooklab> · **Chosen domain:** `hooklab.dev`
(verified available, not registered yet — only needed at deploy time).

---

## 2. Exactly where we are

**Latest commit: `HL-24`.** Branch `main` in sync, CI green.
**Phases 1 to 4 are complete: Hooklab captures, streams live, verifies signatures and forwards
reliably.** The backend is feature-complete for its own purposes; what is left is a face for it.

| Commit | Contents |
|---|---|
| HL-1 | Local Docker environment, FastAPI skeleton, `/health` and `/ready` |
| HL-2 | This context document |
| HL-3 | First tests and CI on GitHub Actions |
| HL-4 | CI badge |
| HL-5 | Data model `endpoints` and `requests`, with Alembic |
| HL-6 | Endpoint creation and lookup API; code switched to English |
| HL-7, HL-8 | Updates to this document |
| HL-9 | Ingest route: captures any request at `/in/{ingest_token}` |
| HL-10 | Listing, detail and raw-body download for captures |
| HL-11, HL-12 | Context update; `CLAUDE.md` excluded from the repository |
| HL-13 | **Phase 2**: live feed with Redis Streams and SSE |
| HL-14 | Context update after phase 2 |
| HL-15 | README and `docs/` translated to English |
| HL-16 | **Phase 3**: Stripe and GitHub signature verifiers |
| HL-17 | Signature verification wired end to end |
| HL-18 | Documentation updated after phase 3 |
| HL-19 | **Phase 4**: SSRF defence |
| HL-20 | Retry policy: exponential backoff with jitter |
| HL-21 | Forwarding destinations, unverified on creation |
| HL-22 | Destination verification and the IP-pinned HTTP client |
| HL-23 | Delivery worker: retries, dead letters, circuit breaker |
| HL-24 | Documentation updated after phase 4 |

### Works and is verified

- Postgres 18.6 and Redis 8 in Docker, with healthchecks.
- Configuration validated at startup; async connections to both.
- `/health` and `/ready`, tested **on the failure path too**: with Redis down, `/health` stays 200
  and `/ready` returns 503 naming the failed dependency; it recovers on its own when Redis returns.
- **Full flow**: create endpoint → receive webhooks of any method and content type → list them
  paginated → view the detail → download the raw body.
- Migration applied, with a full down-and-up rollback tested.
- **Live feed**: `GET /api/endpoints/{view_token}/stream` delivers captures as they arrive, with
  `Last-Event-ID` reconnection and gap backfill from Postgres.
- **The architecture-validating test passes**: `scripts/verify_fanout.py` against
  `uvicorn --workers 4` — all 20 captures cross from one process to another, verified by PID that
  they really landed on different workers.
- **Signature verification**: `PUT /api/endpoints/{view_token}/signature` turns it on, and every
  capture is checked inline. The verdict travels in the listing, the detail and the live feed.
  Verified by hand against a real server with all six Stripe cases, each giving its own diagnosis.
- **Reliable forwarding**: register a destination, prove you control it, and every capture is
  forwarded with retries, exponential backoff, a stable idempotency key, a dead-letter state and a
  circuit breaker. The worker runs as its own process (`python -m app.worker.run`) and shuts down
  cleanly on SIGTERM rather than dying mid-delivery.
- 217 tests against real services, isolated by `TRUNCATE` between each one. The signature tests are
  anchored on GitHub's own published vector; the signature, SSRF and worker suites were each
  validated with deliberate mutations to prove they fail when the code breaks.
- CI: Python 3.12 and 3.14 matrix, with Postgres and Redis as services, running `ruff`,
  `ruff format --check`, `mypy`, migrations and `pytest`.
- Also verified by hand with `curl` against the real server.

### Missing

Frontend, accounts and deployment.

---

## 3. Bringing up the environment

```bash
cd ~/learning/python/proyecto
docker compose up -d --wait          # Postgres + Redis

cd backend
.venv/bin/alembic upgrade head       # in case there are new migrations
.venv/bin/uvicorn app.main:app --port 8010 --reload

curl http://localhost:8010/ready     # {"status":"ready","checks":{...}}
```

Before every commit, run **exactly what CI runs**:

```bash
cd backend
.venv/bin/ruff check app/ tests/ scripts/
.venv/bin/ruff format --check app/ tests/ scripts/
.venv/bin/mypy app/ tests/ scripts/
.venv/bin/pytest -q
```

Watch the live feed by hand:

```bash
TOKEN=$(curl -s -X POST localhost:8010/api/endpoints -H 'content-type: application/json' \
  -d '{}' | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["view_token"], d["ingest_token"])')
curl -N localhost:8010/api/endpoints/${TOKEN%% *}/stream    # in one terminal
curl -X POST localhost:8010/in/${TOKEN##* } -d '{"hello":1}' # in another
```

The architecture-validating test (with the server on `--workers 4`):

```bash
.venv/bin/uvicorn app.main:app --port 8010 --workers 4
.venv/bin/python scripts/verify_fanout.py
```

---

## 4. Quirks of THIS machine

Things that already cost time once. No need to trip over them again.

| Detail | What to know |
|---|---|
| **Port 8010, not 8000** | 8000 is taken by another project of the user's (`~/learning/python/fastapi`, `users:app`). Do not kill it. |
| **Postgres 18 volume** | Mounts at `/var/lib/postgresql`, **not** `/var/lib/postgresql/data`. Version 18 changed the convention; with the old path the container will not start. Almost every tutorial is out of date. |
| **mypy + pydantic-settings** | `Settings()` with no arguments raises `call-arg`. Solved with a narrow, commented `type: ignore` in `config.py`. Do **not** give the required variables defaults: them being missing must break startup. |
| **ruff B008 + FastAPI** | `Depends()` in a default value triggers B008. Not silenced: `Annotated` aliases in `app/api/deps.py` are used instead, which is modern FastAPI style. |
| **Python 3.14** | Every dependency has wheels. psycopg3 chosen over asyncpg for availability. |
| **git** | Global identity `ivanqenk` / `ivanqenk@gmail.com`. ed25519 SSH key at `~/.ssh/id_ed25519`, registered on GitHub. |

---

## 5. Closed decisions — do not reopen

- **Code in English** (classes, functions, variables, columns, docstrings, comments).
  **`README.md` and `docs/` are in English too.** Commit messages and the conversation with the
  user stay in **Mexican Spanish**.
- **Commits prefixed `HL-N`.** To find the next number, look at `git log --oneline` and continue
  the sequence — do not trust a number written here, it goes stale on its own.
- **Redis Streams, not pub/sub**, for the SSE fan-out.
- **The `requests` bigserial is the `Last-Event-ID`.** No cursor table.
- **Two separate tokens**: `ingest_token` public, `view_token` secret.
- **Three schemas per resource** (Create / Created / Public). `Created` is the only response that
  carries `view_token`; since `Public` has no such field, leaking it is impossible by construction.
- **404 and not 403** for an invalid token: a 403 would confirm the token exists.
- **Cursor pagination, not offset.** With offset, a capture arriving while the user pages repeats
  one row and skips another. One extra row is fetched to detect a next page, instead of a `COUNT`
  over the whole table.
- **A raw body is never served with its original content type**: always `octet-stream`,
  `attachment` and `nosniff`. Returning `text/html` chosen by a stranger would turn the domain into
  malware hosting.
- **Access control lives in a dependency** (`EndpointDep`), not repeated in every route: that way
  it cannot be forgotten.
- **The body limit is enforced while reading the stream**, never after loading it into memory.
- **Body stored raw in `bytea`.** Signature HMACs are computed over the exact bytes.
- **Anonymous first**, endpoints claimable later. OAuth2 comes afterwards.
- **Capability-based access control** (tokens), not RLS.
- **Reliable forwarding and signature verification belong in the core**, not in a hypothetical v2.
- **Deployment on a VPS with Docker Compose and Caddy**, ~6 USD/month. Long-lived SSE rules out
  serverless platforms.
- **The Stream position is taken BEFORE backfilling from Postgres.** The other way round, anything
  arriving during the backfill is lost with no way to detect it. The cost is the odd duplicate,
  which gets filtered.
- **The de-duplication guard is a set of ids, not a watermark.** The id is assigned at `flush` and
  published after `commit`, so under concurrency stream order does **not** follow id order. With a
  watermark, roughly half the captures were silently discarded, and only under load. There is a
  regression test.
- **The SSE route does not use `SessionDep`.** A `yield` dependency lives until the response ends,
  and an SSE response never ends: with the default pool, the sixteenth tab starves the whole app of
  connections. It uses `DetachedEndpointDep` and opens its own session only for the backfill.
- **`ready` is sent after the backfill**, so it means "you are caught up" and not just "socket
  open".
- **The signing secret is write-only**, encrypted with AES-GCM and returned by no route.
- **Nothing is forwarded to an unverified destination.** The SSRF rules stop Hooklab being aimed at
  *our* network; they do nothing about it being aimed at a stranger's site, which would make us a
  free amplifier. The destination has to echo a token back to prove someone controls it.
- **The echo must be the token exactly, not merely contain it.** A public request-reflecting service
  returns the challenge header inside a JSON blob, so a "contains" rule would let anyone verify a
  destination they do not own.
- **Addresses are validated against the resolved IP, never the hostname string**, and every address
  a name resolves to has to pass. Obfuscated forms (`127.1`, `2130706433`, `0x7f000001`) are covered
  for free, since what is judged is what the resolver returned.
- **Connect to the validated IP, sending the original hostname as `Host` and SNI.** Letting the HTTP
  client resolve again reopens the DNS-rebinding window that the check just closed.
- **The stdlib's own address categories are not enough.** `is_private` misses carrier-grade NAT and
  multicast; `is_global` is True for multicast *and* for `64:ff9b::/96`, the NAT64 prefix that
  carries an IPv4 address in its low bits. IPv4 embedded in IPv6 is unwrapped in all three forms
  before any category is applied.
- **No redirects are followed.** The new address never passed the checks, and following one is the
  classic way to walk an SSRF filter into the internal network.
- **The delivery queue is Postgres with `SKIP LOCKED`, not ARQ** -- a deliberate departure from the
  plan. Everything a task queue would hold already has to live in `deliveries` for the UI, and a
  second schedule in Redis could disagree with the one users see.
- **The address is revalidated immediately before connecting**, never trusted from registration
  time.
- **`idempotency_key` is stable across retries.** At-least-once delivery is unavoidable over HTTP;
  a key that changed per attempt would be useless to the receiver, and that is the easy mistake.
- **A 4xx other than 408/429 exhausts the delivery at once** instead of spending eight attempts
  repeating a refusal and hiding a permanent problem behind "pending".
- **A failed signature or a failed delivery never costs the capture.** Ingest still answers 200.
- **The signing secret is write-only**: encrypted with AES-GCM at rest and returned by no route, in
  no form. Not masked -- absent. A masked value still leaks its length and invites someone to "show
  just a few characters" later.
- **AES-GCM, not plain AES**, with the key derived via HKDF under a fixed label. Authenticated
  encryption means tampering fails loudly; deriving keeps this key separate from any future use of
  `SECRET_KEY`.
- **Rotating `SECRET_KEY` makes stored secrets unreadable**, by design. It is reported as its own
  case, never as an invalid signature: the fix is to set the secret again.
- **Verification is configured behind the *view* token**, never the ingest one. The public token
  reaches logs and configuration panels; if it could set the secret, anyone who saw the URL could
  disable verification or point it at a secret of their own.
- **A failed signature never costs the capture.** Ingest still answers 200 and still stores the
  body: refusing it would hide the very request needed to diagnose the failure.
- **Verification runs inline during ingest.** One HMAC over at most a megabyte is well under a
  millisecond, and deferring it would show the browser a capture whose verdict lands later.
- **The diagnosis is the feature, not the boolean.** Every case carries a machine-readable reason
  and a sentence that says what to do next -- including the one only we can give: a body Hooklab
  itself truncated can never match, and saying "invalid signature" would blame a correct secret.
- **Read routes use an explicit outer join, not an ORM relationship.** A lazily-loaded attribute in
  async SQLAlchemy fires a query from wherever it is touched, including inside the response
  serialiser, long after the session is gone.

### Discarded ideas — do not propose them again

- **Acredia** (contractor compliance in Mexico): regulatory dependency, incumbents that sell it
  bundled, and legal liability if the software gets it wrong.
- **A Shopify app**: good business, bad portfolio piece — mostly platform glue, and impossible to
  demo without a store.

---

## 6. What comes next — phase 5: the frontend

The backend does everything it was designed to do. What is missing is a face, and
`docs/master-prompt.md` is clear that a modest thing people can use beats an ambitious one at 60%.

React + TypeScript + Tailwind, as chosen at the start. The parts that will actually be awkward are
already known and written down in the plan:

1. **`EventSource` vs `fetch` streaming.** `EventSource` reconnects and resends `Last-Event-ID` for
   free, but cannot send custom headers. While the view token lives in the URL it is the right
   choice; when accounts arrive and the token moves to an Authorization header, this has to become
   `fetch` + `ReadableStream` with the resume handled by hand. A conscious decision, not an accident.
2. **StrictMode mounts effects twice in development.** Without correct cleanup that means two live
   connections and duplicated events -- a ghost that does not exist in production.
3. **Backpressure.** Ten thousand captures cannot accumulate in React state. A sliding window in
   memory plus the existing cursor pagination going backwards.
4. **Batch the renders.** One `setState` per event dies under real traffic; buffer and flush about
   every 100 ms.
5. **Stored XSS is the real risk here.** The page renders attacker-written content: bodies, headers,
   names. `dangerouslySetInnerHTML` over captured content is forbidden, previews go in a sandboxed
   iframe without `allow-same-origin`, and `Referrer-Policy: no-referrer` stops the view token
   leaking through a link inside a payload.

After that: accounts (phase 6), then deployment.

### Known gaps, deliberately left

- **Captures taken before a secret was configured are never verified.** The data model supports
  re-verification -- `signature_checks` keys on `request_id` -- so it is an upsert away.
- **Only Stripe and GitHub sign.** Shopify (base64) and Twilio (HMAC-SHA1 over URL plus sorted
  parameters) plug into the same interface.
- **Forwarding requires a verified destination but not yet an account.** The plan wanted both;
  verification is the half that actually proves control, and the account gate layers on in phase 6
  without rework.
- **No retention worker yet.** Anonymous endpoints carry `expires_at` but nothing deletes them.

### Traps already paid for, worth not stepping on again

- **httpx's `ASGITransport` does not stream**: it runs the app to completion and concatenates the
  body, so it hangs forever against an SSE feed. `tests/sse.py` speaks ASGI directly instead.
- **pytest-asyncio creates one event loop per test**, and the global Redis client kept connections
  from the previous, closed one. It surfaces as `Event loop is closed` during the *setup* of an
  innocent test. The pool is disconnected at the end of each test in `conftest.py`.
- **Killing the uvicorn master does not kill its workers**, and their command line does not match
  `pgrep -f 'uvicorn app.main'`.
- **`pgrep -f` matches the invoking shell's own command line.** This has cost time three separate
  times. Capture the PID from `$!` at launch instead of searching for it afterwards; a bracket
  pattern only helps when the shell does not also contain the literal string.
- **Every test needs Postgres**, because the autouse fixture truncates. Without the container up,
  even the pure unit tests fail -- after minutes of connection retries.
- **`ruff`'s B008 exemption for FastAPI recognises built-in parameter types but not our own enums.**
  The fix is the `Annotated` alias form, which is modern FastAPI style anyway.
- **`.example` hostnames do not resolve**, so any test that goes through destination registration
  waits on DNS and then fails validation.

---

## 7. Where everything lives

| Document | Contents |
|---|---|
| `~/.claude/plans/espera-sigamos-analizando-tiene-merry-karp.md` | **The full plan**: architecture with its 7 decisions, threat model, SQL data model, phases and verification plan. Primary reference. Still in Spanish — it is outside the repository. |
| `docs/master-prompt.md` | Methodology: phases, approval gates, definition of done. |
| `README.md` | Public face of the project and getting started. |
| This file | State and context for picking the work back up. |

**When picking it back up:** read this file, bring up the environment (section 3), confirm `/ready`
answers, and start from section 6.
