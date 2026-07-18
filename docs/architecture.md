# ZOMS Facade & Token Broker — Architecture

> The approved implementation plan for this repo. Produced 2026-07-16 from a
> multi-proposal design review (security-first / simplicity-first /
> operations-first proposals, adversarial judge panel, synthesis). Vendor API
> truth lives in [zoms-api-reference.md](zoms-api-reference.md); code
> standards live in the repo [CLAUDE.md](../CLAUDE.md).
>
> **Revision 2 (2026-07-16): host-app + MongoDB adaptation.** The facade is
> NOT a standalone service — it is a `zelle` bounded context inside the
> existing `fdn-c-amp-fapis-py` FastAPI application (alongside `ose` and
> `saas`), reusing the app's baked-in async httpx client and Mongo client.
> Section 0 below supersedes the standalone repo tree (§2), the Postgres
> store (§5), and the single-replica deployment pin (§1) where they
> conflict. Everything else — token broker, northbound contract, retry
> matrix, security guardrails, testing strategy — stands as written.

## 0. Revision 2 — Integration into `fdn-c-amp-fapis-py` (Mongo, not Postgres)

**Placement.** `zelle` becomes a sibling of `ose`/`saas` in every layer of
the host app:

| Host-app location | Contents |
|---|---|
| `src/apis/models/zelle/` | `northbound.py`, `southbound.py`, `errors.py`, `enums.py` — the two model planes, never shared |
| `src/apis/routes/zelle/` | `events.py` (maintenance-event endpoints), `admin.py` (operator resolve — later phase) |
| `src/apis/services/zelle/` | `event_service.py` (state machine, guardrails, orchestration), `token_broker.py`, `zoms_client.py`, `watchdog.py` (later phase) |
| `src/apis/repositories/zelle/` | `events.py`, `idempotency.py`, `audit.py` — Mongo data access |
| `src/apis/dependencies/` | `zelle.py` — wiring: settings, broker/client/service instances on app state |
| config layer | `ZelleSettings` (pydantic-settings): CAT/PROD base URLs, token URL, `aud`, `scope`, org constants (orgId, participantName, submittedName, contact block), `kid` + private-key path, mTLS toggles/paths |

The token broker and ZOMS client are vendor-egress adapters, not data
repositories — they live under `services/zelle/`, and take the host app's
shared `httpx.AsyncClient` by injection (`common/httpx/client.py` owns the
instance; zelle never constructs its own).

**MongoDB replaces Postgres** (host constraint: Python + Mongo are the
approved tools):

- Collections: `zelle_events`, `zelle_idempotency` (unique compound index
  `(client_id, key)`), `zelle_audit` (append-only).
- The idempotency race closes the same way: insert the idempotency document
  **first** (unique index makes the duplicate lose deterministically), then
  the event document. On a replica set, wrap both in a multi-document
  transaction; otherwise ledger-first insert with `PENDING` recovery on
  startup gives the same safety.
- Append-only audit: enforce with a custom Mongo role granting only
  `insert`/`find` on `zelle_audit` if the DBA will grant it; otherwise
  app-level discipline (no update/delete code path exists) — documented
  honestly in the security-review package.
- The intent-row-before-southbound-call / outcome-update-after pattern (§5)
  is unchanged, just expressed as Mongo documents.
- Watchdog singleton: the Postgres advisory lock becomes a Mongo **lease
  document** (`findOneAndUpdate` upsert with holder + `expiresAt`, TTL
  index) — only needed when the watchdog phase lands.

**Deployment.** The single-replica pin from §1 no longer applies — zelle
inherits however the host app scales. That is safe because: per-worker
in-memory token caches remain **correct** (EWS permits multiple concurrent
valid tokens; each worker refreshes its own — merely a few extra `/token`
calls); all cross-request state (events, idempotency, audit, state machine)
lives in Mongo, not process memory; and anything that must be a singleton
(watchdog) uses the Mongo lease.

**Keys.** The bank's public key is already registered with EWS as a JWKS.
The assertion's `kid` header must match the JWKS entry's `kid`; the private
key is mounted/managed per host-app convention and referenced by path in
`ZelleSettings`.

## 1. Architecture at a Glance

```
Internal consumer ──HTTP, no auth (X-Client-Id, X-Correlation-Id)──▶ FastAPI facade
   ┌────────────────────────── facade internals ──────────────────────────┐
   │ api/v1 routers ─▶ EventService (state machine, guardrails, dry-run)  │
   │        │                    │                                        │
   │        ▼                    ▼                                        │
   │  Postgres (events,     ZomsClient (httpx, retry matrix,              │
   │  idempotency, audit)    fresh request-id per attempt)                │
   │        ▲                    │                                        │
   │  Watchdog (singleton   TokenBroker (RS256 assertion, monotonic       │
   │  via advisory lock)     cache, single-flight, breaker)               │
   └─────────────────────────────┼────────────────────────────────────────┘
                EWS auth /token  +  ZOMS /v1/events/*   (Bearer JWT, mTLS-ready)
```

Northbound is an **enriched** contract: consumers send only what a change ticket knows (window, ticket, reason, hold mode); config owns `orgId`, `participantName`, `submittedName`, and the contact block. Consumers never see EWS vocabulary, tokens, or error bodies. Southbound, the facade is the sole holder of the signing key and all EWS semantics.

**Deployment is pinned to `replicas: 1`, `workers: 1` in the manifest** — this is load-bearing for the in-memory token cache and the watchdog, and it is honest for a service handling single-digit calls per month. As a belt-and-braces guard, the watchdog and any background driver claim a Postgres advisory lock at startup, so an accidental scale-out produces idle replicas, not duplicate pagers or duplicate EWS mutations. Named triggers for revisiting: platform mandates multi-replica, or availability requirements exceed one pod — then move the token cache behind the DB and keep the advisory-lock singleton pattern.

## 2. Repo Structure

```
pyproject.toml  uv.lock  Dockerfile  README.md  RUNBOOK.md  .env.example
src/zoms_facade/
  main.py                 # app factory, lifespan (2 httpx clients, DB, watchdog, PENDING sweep)
  config.py               # pydantic-settings: env, URLs, aud, scope, org constants, {kid: path}
  middleware.py           # correlation-id contextvar, X-Client-Id allowlist, access log
  api/v1/events.py        # thin routers          api/health.py  # /livez /readyz
  api/v1/admin.py         # operator resolve/re-drive endpoints
  services/events.py      # orchestration, state machine, overlap check, guardrails
  services/watchdog.py    # stuck/orphan alerter (advisory-lock singleton)
  clients/ews.py          # ZomsClient: typed ops, retry matrix, EwsError, datetime serializer
  clients/token_broker.py # assertion, cache, single-flight, breaker
  models/northbound.py  models/southbound.py  models/errors.py   # never shared
  storage/repo.py  storage/schema.py          # SQLAlchemy 2.0 async
  fake_ews/app.py         # stub EWS + /token, fault injection
tests/ (unit/, contract/, api/, e2e_fake/)
```

## 3. Southbound Token Broker

Hand-rolled client assertion (~30 lines) with **joserfc** — EWS's `aud` = auth base URL, in-assertion `scope` claim, and mandatory `kid` all fight Authlib defaults. Assertion: header `{alg: RS256, kid: active_kid}`; claims `iss=sub=client_id`, `aud=settings.token_aud` (config, never derived — the ZOMS auth server is unconfirmed), `scope=settings.token_scope` (one config value feeds both the claim and the `scope` form field — no divergence), `jti=uuid4()` fresh per attempt, `iat=nbf=now-30s`, `exp=now+120s`.

```python
def _fresh(self) -> bool:
    # BOTH conditions: a token must exist AND be inside the margin.
    return self._token is not None and \
        time.monotonic() < self._expires_at - self._margin
    # margin = max(120, 0.2 * last_ttl) -> 360s for an 1800s TTL:
    # wide enough that a full southbound retry chain cannot outlive the token.

async def get(self) -> str:
    if self._fresh(): return self._token
    self._breaker.check_or_raise()        # BEFORE the lock: waiters fail fast
    async with self._lock:                # single-flight
        if self._fresh(): return self._token
        sent_at = time.monotonic()        # anchored at SEND, monotonic — no NTP
        resp = await self._post_token()   #  steps, no RTT-inflated lifetime
        self._token = resp["access_token"]
        self._expires_at = sent_at + resp["expires_in"]  # never hardcode 1800
        return self._token

def invalidate(self, used: str) -> None:
    if self._token == used:               # never evict a newer token
        self._token, self._expires_at = None, 0.0   # reset both
```

`_post_token()` makes at most **two attempts under a hard 15s deadline inside the lock** (connect=3, read=7 timeouts, fresh `jti` each); the breaker (open after 5 consecutive failures, half-open at 30s) is checked before the lock precisely so an outage returns fast 503s instead of stacking coroutines. A `Retry-After` on 429 is honored once (capped 10s). **400/401 from `/token` are never retried** — that is a key/config incident raised as `AuthConfigError` on a distinct alert channel from transient failures.

**401 from a ZOMS resource call** — the one reconciled exception to the lifecycle no-retry rule: a 401 is rejected by the gateway *before the operation executes*, so `invalidate(used)` → fresh token → retry the resource call exactly once is safe for every verb, including `start`. This applies only to a definite 401 response, never to a timeout. Second 401 → 502 + alert.

**Keys/mTLS:** `{kid: pem_path}` map + `active_kid` gives zero-downtime rotation (register new public key → deploy both → flip → retire; drill before prod). Two lifespan-built `httpx.AsyncClient`s — token and API — each with its own `ssl.SSLContext` (`load_cert_chain` when paths present) and independent `token_mtls` / `api_mtls` flags, since requirements may differ per endpoint.

## 4. Northbound API Contract

| Endpoint | Action |
|---|---|
| `POST /v1/maintenance-events` | schedule → 201 |
| `POST /v1/maintenance-events/{id}/start` \| `/complete` \| `/cancel` | lifecycle → 200 (`?dry_run=true` supported) |
| `GET /v1/maintenance-events[?status=…]`, `GET …/{id}` | local reads |
| `POST /v1/admin/maintenance-events/{id}/resolve` | operator unblock after manual EWS reconciliation |
| `GET /livez`, `GET /readyz` | probes |

Schedule request (headers: `X-Client-Id` required, `Idempotency-Key` optional, `X-Correlation-Id` optional):

```json
{"startTime":"2026-08-01T06:00:00Z","endTime":"2026-08-01T08:00:00Z",
 "ticketNumber":"CHG0012345","reason":"core banking patch","holdMode":"SELF_HOLD"}
```

`ticketNumber` is mandatory (bank change control). `holdMode` defaults from config, and that default is a deliberate decision: **prefer `SELF_HOLD` where the bank can release its own side** — it keeps the worst case (messages held by EWS while EWS is unreachable) out of the vendor's hands.

201 response (correlationId present on success, not just errors):

```json
{"eventId":"6f1c…","status":"SCHEDULED","startTime":"2026-08-01T06:00:00Z",
 "endTime":"2026-08-01T08:00:00Z","ticketNumber":"CHG0012345",
 "correlationId":"c-9a2f…","createdAt":"2026-07-16T14:02:11Z",
 "lastConfirmedUpstreamAt":"2026-07-16T14:02:11Z"}
```

**Fallback if the EWS 201 turns out not to return `maintenanceEventId` synchronously** (open question #2): the facade returns **202** with its own `eventId` and status `PENDING_UPSTREAM_ID`; lifecycle verbs return 409 until an operator or a confirmed read API resolves the mapping. Consumers are told to key on the facade `eventId` either way.

**Error envelope** — EWS bodies never leak; raw responses land redacted in the audit table only:

```json
{"error":{"code":"UPSTREAM_UNCERTAIN","message":"Start outcome unknown; event locked pending reconciliation.",
 "correlationId":"c-9a2f…","retryable":false}}
```

Catalog: `VALIDATION_FAILED` (422), `CONFLICT` (409 — state machine, overlap, idempotency-body mismatch, ticket mismatch), `FORBIDDEN_ACTION` (403 — lifecycle allowlist), `UPSTREAM_REJECTED` (**502** — the rejected fields were facade-enriched, so a vendor 4xx is facade-owned, never a consumer 4xx), `UPSTREAM_UNAVAILABLE` (503 + `Retry-After`), `RATE_LIMITED` (503 + `Retry-After`), `UPSTREAM_UNCERTAIN` (502).

**Idempotency — closed race.** The facade generates the EWS `idempotency-id` at schedule time. In **one transaction before any network call** it inserts the event row (status `PENDING`, idempotency-id bound) and the `idempotency_keys` row with a **unique `(client_id, key)` constraint** plus a canonical body hash. A concurrent duplicate loses the insert, re-reads, and replays the stored response (or 409s if still `PENDING`). Same key with a **different body hash → 409 `CONFLICT`**, never silent replay. Facade→EWS schedule retries reuse the persisted idempotency-id verbatim; consumers never touch the EWS header.

**Correlation:** accept/mint `X-Correlation-Id`, echo it, bind it to a contextvar on every log line; mint a **fresh EWS `request-id` per HTTP attempt**; persist `(correlationId ↔ eventId ↔ attempt request-ids)` in audit.

**Dates:** an explicit southbound serializer emits `YYYY-MM-DDTHH:MM:SS.NNNZ` — pydantic v2's default `+00:00` form is a likely silent CAT 400.

## 5. Local State — Postgres, Persisted

No confirmed upstream GET means the local store is the only source for reads, the state machine, idempotency, the watchdog, and audit. **Postgres in CAT/prod** — chosen deliberately over SQLite because (a) append-only audit is enforced by revoking `UPDATE`/`DELETE` from the app role, which SQLite cannot do, and (b) SQLite WAL is unsafe on the network-backed volumes bank platforms typically mount. SQLite remains the zero-setup local-dev/fake-mode store only.

Tables: `events(id, ews_event_id, status, idempotency_id, payload_json, scheduled_start, scheduled_end, ticket_number, client_id, last_confirmed_upstream_at, created_at, updated_at)`; `idempotency_keys(client_id, key, body_hash, event_id, response_snapshot, created_at)` unique `(client_id, key)`; `audit_log(id, ts, actor_client_id, correlation_id, event_id, action, ews_request_id, outcome, http_status, detail_redacted)` — append-only by grants, **intent row written before every southbound call, outcome updated after** (a facade crash mid-call still leaves forensic evidence of an in-flight EWS mutation). Payload snapshots contain contact PII: encrypted at rest per platform standard.

`GET /events/{id}` returns `facadeStatus` + `lastConfirmedUpstreamAt`, documented as *last known intent*, never authoritative. Ambiguous outcomes (timeout after send) set `UNCERTAIN`, block further transitions, and page. The **admin resolve endpoint** is the exit: an operator confirms actual state with EWS by phone, then `POST /admin/…/resolve {"actualStatus":"COMPLETE","attestation":"EWS NOC ref 4471"}` — fully audited — so the state machine never becomes the outage. **Startup sweeps any `PENDING` row into `UNCERTAIN` with an alert**: replaying a schedule idempotency-id after a crash has unknown semantics, so we never blind-replay.

## 6. Security Decisions

- **No-auth northbound is documented, not assumed**: the security-review package states the network boundary explicitly and requires a NetworkPolicy/ingress rule restricting the facade to named internal namespaces as an enforced artifact.
- **Attribution:** required `X-Client-Id` validated against a config allowlist (400 otherwise); every audit row carries it. A separate `LIFECYCLE_CLIENT_ALLOWLIST` restricts who may `start`/`complete` in prod — honestly documented as advisory.
- **Typed confirmation that actually confirms:** lifecycle verbs require `X-Confirm-Ticket: <ticketNumber>` matching the event's stored ticket. Unlike echoing the eventId already in the URL, the ticket is *not* in the URL — blind automation replaying captured paths fails, and callers prove they know which change they're executing. Mismatch → unambiguous 409.
- **Secrets:** config holds **paths** to PEMs (Vault sidecar / K8s Secret mounts), never key material in env vars; `SecretStr` throughout; fail-fast parse at startup; cert/key expiry alert at <30 days.
- **Redaction proven by test:** an httpx event-hook filter strips `Authorization` and `client_assertion`, with unit tests asserting they never appear in captured log output. Contact PII masked in logs (`j***@bank.com`).
- Dropped from prior drafts: per-client rate limiting (no risk retired at this volume) and eventId-echo confirmation (no protection).

## 7. Resilience Policy

| Call | Timeout | Retry | Notes |
|---|---|---|---|
| `/token` | 3s/7s | ≤2 attempts, 15s deadline, fresh `jti`; 429 honors Retry-After once | never on 400/401 → `AuthConfigError`; breaker 5-fail/30s half-open, checked pre-lock |
| `schedule` | 3s/10s | 2× on connect/5xx, **same idempotency-id**, fresh request-id | that is what the header is for |
| `start/complete/cancel` | 3s/10s | **none automatic**, two exceptions: definite 401 (pre-execution, retry once post-refresh) and definite 429 (retry once after Retry-After) | idempotency semantics unknown; a doubled start manipulates MQ hold |
| any 4xx (other) | — | never | map per catalog |
| timeout after send | — | never | → `UNCERTAIN`, block, page |

Coarse ZOMS breaker: open after 5 failures/60s → 503 + `Retry-After` northbound. **`/readyz` gates on DB only**; broker/breaker state is reported in the body and metrics but never flips readiness — an EWS outage must not pull local reads and the audit trail from operators at exactly the moment the runbook needs them. Never fetch a token in a probe.

**EWS down during a live window** (can't send `complete`; MQ messages held): (a) watchdog pages at `scheduledEnd + grace` and **re-pages every 15 minutes**; (b) RUNBOOK.md names the EWS NOC escalation path for manual release; (c) a `pending_complete` auto-driver exists but is **config-off by default** and tightly gated: it re-attempts only when the prior attempt failed *cleanly pre-send* (connect error, breaker open — never after an ambiguous send), with exponential backoff, a hard attempt cap, and any 4xx treated as converged-pending-human-review. Until EWS confirms the error catalog, the default is page-plus-human. Watchdog also flags orphaned `SCHEDULED` events past `scheduledStart + grace`, and schedule-time overlap detection returns 409 for overlapping windows per orgId unless `allowOverlap=true`.

## 8. Observability

**structlog** JSON; every line carries `correlation_id`, `client_id`, `event_id`, `ews_request_id` via contextvars. Metrics: `token_refresh_total{outcome}`, `token_age_seconds`, `breaker_state`, `ews_request_duration_seconds{op,status}`, `ews_retries_total{op}`, `events_by_status`, `events_uncertain` (page >0), `events_stuck_total` (page), `pending_complete_attempts_total`. Alerts: stuck past end+grace (re-page 15 min), `UNCERTAIN` created, breaker open, `AuthConfigError`, cert expiry <30 days. The append-only audit table is the compliance artifact: who, what, when, every EWS attempt with request-id and status.

## 9. Testing & Fake EWS

- **Broker units** (time-machine + monotonic patching): `_fresh()` truth table including the token-is-None case, margin math, single-flight (`asyncio.gather` of 50 `get()`s → exactly one `/token` call), 401-invalidate-once, breaker transitions, no-retry-on-400.
- **respx contract tests** on `ZomsClient`: exact URLs/headers, idempotency-id constant across schedule retries, request-id fresh per attempt, date format `…000Z`, 5xx→retry, timeout→`UNCERTAIN`, 429→Retry-After path, redaction filter proven.
- **API tests** (`httpx.ASGITransport`): flows, envelope stability, state-machine 409s, idempotency replay and body-mismatch 409, dry-run, PENDING-sweep on startup.
- **`fake_ews/`**: FastAPI stub of `/token` + all four ops with in-memory lifecycle and **configurable fault injection** (latency, 5xx, 429, idempotency replay), built on the same southbound models. `ZOMS_ENV=fake` wires it in — the broker is verified end-to-end against the fake `/token` before CAT credentials exist, and consumers integrate today.

## 10. Tech Stack

uv (locked builds) · FastAPI + pydantic v2 (mandated; typed DTO boundary) · httpx (lifespan clients, SSLContext mTLS) · joserfc (strict RS256 + kid) · SQLAlchemy 2.0 async + asyncpg / aiosqlite-dev · tenacity (auditable retry policies for token/schedule only) · structlog (contextvar JSON) · prometheus-fastapi-instrumentator · ruff, mypy --strict, pytest + respx + time-machine.

## 11. Build Order

- **Phase 0 (days 1–2), walking skeleton:** repo, config, both model sets, fake_ews, `POST /maintenance-events` end-to-end against the fake, README curl examples. Demoable; consumer contract feedback starts now.
- **Phase 1 (days 3–4):** Postgres schema, all four ops, state machine, idempotency (unique constraint + insert-before-call), GETs, error envelope, intent/outcome audit.
- **Phase 2 (days 5–6):** token broker + assertion + single-flight + breaker + 401 path + mTLS toggles; full broker suite against fake `/token`.
- **Phase 3 (week 2):** correlation middleware, redaction tests, metrics, watchdog + advisory lock, PENDING sweep, admin resolve, RUNBOOK.md, Dockerfile (multi-stage, non-root), CI.
- **Phase 4, CAT:** confirm auth URL/`aud`/mTLS; capture real 201 and error bodies through **deliberately lenient parsers, then tighten to strict models**; probe idempotency replay.
- **Phase 5, prod:** key-rotation drill, cert-expiry alerts wired, security-review package (this doc, NetworkPolicy, audit demo, redaction tests), cutover. No load/soak — traffic is single-digit monthly.

## 12. Open Questions for EWS (prioritized)

1. **Auth:** exact CAT/PROD token URLs for scope `maintenance-event`, required `aud`, mTLS requirements + CA chain for token and API endpoints separately. *Blocks all connectivity; Paze URLs are a guess.*
2. **Schedule 201 body:** full schema — is `maintenanceEventId` returned synchronously, under what key? *Determines whether our 202/`PENDING_UPSTREAM_ID` fallback is ever exercised.*
3. **Idempotency semantics:** honored on start/complete/cancel? Schedule-key replay with a different body? Replay after client crash — original 201 echoed or duplicate error? *Gates any relaxation of the no-retry stance and the startup sweep.*
4. **Error catalog + body shape:** especially double-start, complete-without-start, cancel-after-start, idempotency replay — retryable vs terminal vs already-converged. *Gates enabling the pending_complete driver.*
5. **Reads/webhooks + operations:** any GET/list/status endpoint or state-change webhook; rate limits; JWT clock-skew tolerance; scheduling constraints (lead time, max window, overlap policy); **and the manual escalation path to release held MQ messages if `/complete` is unreachable during a window.**
