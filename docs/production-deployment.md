# Production deployment & go-live runbook

How the Zelle facade goes to CAT and then PROD. Unlike the local runbook
([how-to-run-and-test.md](how-to-run-and-test.md)), production talks to the
**real EWS** with **real credentials, secrets, and mTLS** — so the emphasis here
is prerequisites, secrets, config, index provisioning, certification, and a
go-live checklist tied to the **Oct 30, 2026** compliance deadline.

## What "deploying zelle" means

The `zelle` module is **not a standalone service** — it is a bounded context
**mounted into the host app** (`fdn-c-amp-fapis-py`). So "deploying zelle" means:

1. The host app ships with the `zelle` code (and its `common` base merged in),
2. The host's `main.py` includes the zelle routers + `add_zelle_exception_handlers`,
3. The host's **lifespan** builds the service with `ZelleService.get_service(...)`,
4. The `ZELLE_*` config, the signing key, the mTLS certs, and the Mongo indexes
   are all in place.

The host app's own deploy pipeline (image build, rollout, health checks) is
outside this doc — this covers the **zelle-specific** parts.

---

## Environments: CAT vs PROD

| | CAT (certification) | PROD |
|---|---|---|
| `ZELLE_IS_PRODUCTION` | `false` → CAT URLs | `true` → PROD URLs |
| Base URL (auto) | `https://api.zelle.cat.earlywarning.io/zoms` | `https://api.zelle.earlywarning.com/zoms` |
| Token URL (auto) | `https://auth.wallet.cat.earlywarning.io/token` | `https://auth.wallet.earlywarning.com/token` |
| Credentials | CAT `client_id` + `kid` registered with EWS | PROD `client_id` + `kid` |

> **Confirm with EWS before CAT:** the token URLs above (and `token_aud`) are the
> vendor doc's best-known values and flagged **unconfirmed** — override
> `ZELLE_TOKEN_URL` / `ZELLE_TOKEN_AUD` if EWS gives you different ones. See the
> open questions in [zoms-api-reference.md](zoms-api-reference.md).

---

## Step 1 — Vendor (EWS) prerequisites

These are **blockers** — you cannot connect to CAT without them. Confirm each
with the EWS team:

- [ ] **Client registered** — you have a `client_id` for CAT (and later PROD).
- [ ] **Public key on file (JWKS)** — EWS holds the public half of your RS256
      signing key, under a known **`kid`**. (You hold the private half.)
- [ ] **Token endpoint URL, `audience`, and `scope`** confirmed for ZOMS.
- [ ] **mTLS decision** — is mutual TLS required on the token and/or API
      endpoints? If yes, you have the **client cert + key** and the **CA chain**.
- [ ] **CAT access** granted; **Unassisted Certification Testing** slot booked
      with Zelle (a delivery milestone).
- [ ] **Org details provisioned** — `orgId`, participant/submitted names,
      contact block registered on EWS's side.

---

## Step 2 — Secrets (crown jewels)

The **RS256 signing private key** — and the **mTLS client keypair** if required —
are the crown jewels. They:

- live in the **bank's secret store**, mounted **read-only** into the container,
- are **never** in the repo, committed YAML, an env file, or a log line,
- are referenced by **path** in config (`ZELLE_SIGNING_KEY_PATH`, `ZELLE_CLIENT_*`).

The facade logs **token metadata only** (expiry, scope, kid) — never the token,
the assertion, `Authorization` headers, or key material.

---

## Step 3 — Configuration (`ZELLE_*` env)

Set these in the CAT/PROD deployment environment. Everything except
`is_production` comes from env; the URLs derive from `is_production`.

```bash
# Environment selection — set from the host's IS_PRODUCTION_ENVIRONMENT
ZELLE_IS_PRODUCTION=true            # PROD; false for CAT

# Auth / signing (real, registered with EWS)
ZELLE_TOKEN_AUD=<EWS-confirmed audience>
ZELLE_TOKEN_SCOPE=maintenance-event
ZELLE_CLIENT_ID=<your client id>
ZELLE_SIGNING_KID=<the kid EWS registered>
ZELLE_SIGNING_KEY_PATH=/secrets/zelle/signing.pem     # mounted read-only

# mTLS — set ONLY if EWS requires it (both together, or neither)
# ZELLE_CA_CERTIFICATE_PATH=/secrets/zelle/ca.pem
# ZELLE_CLIENT_CERTIFICATE_PATH=/secrets/zelle/client.pem
# ZELLE_CLIENT_KEY_PATH=/secrets/zelle/client.key

# Corporate egress proxy — set ONLY if southbound traffic must go through a
# forward proxy (ConnectError on the token endpoint is the usual tell). May
# embed credentials; it is a SecretStr and never logged. When unset, ambient
# HTTPS_PROXY/NO_PROXY env vars still apply (httpx default).
# ZELLE_PROXY_URL=http://<user>:<pass>@proxy.bank.local:8080

# Org identity + contact block (injected into every EWS schedule)
ZELLE_ORG_ID=<3-char org id>
ZELLE_PARTICIPANT_NAME=<...>
ZELLE_SUBMITTED_NAME=<...>
ZELLE_CONTACT_NAME=<...>
ZELLE_CONTACT_PHONE=<...>
ZELLE_CONTACT_EMAIL=<...>

# Guardrails (recommended in prod)
ZELLE_CLIENT_ALLOWLIST=["payments-ops","change-mgmt"]   # who may call at all
ZELLE_LIFECYCLE_CLIENT_ALLOWLIST=["noc"]                # who may start/complete/cancel

# Watchdog + email alerts
ZELLE_WATCHDOG_ENABLED=true
ZELLE_ALERT_ONLY_IN_PRODUCTION=true                     # host EmailService self-gates too
```

> **Do NOT** set `ZELLE_API_BASE_URL` / `ZELLE_TOKEN_URL` in CAT/PROD unless you
> are overriding an unconfirmed value — let them derive from `ZELLE_IS_PRODUCTION`.
> Full config reference: [how-it-all-works.md](how-it-all-works.md) §7.

---

## Step 4 — Host-app wiring

In `main.py` (routers + handlers) and the lifespan (`initializer.py`), per the
host's `get_service` convention:

```python
# main.py
from src.apis.routes import zelle_events_router, zelle_admin_router
from src.apis.dependencies.services.zelle import add_zelle_exception_handlers
app.include_router(zelle_events_router)
app.include_router(zelle_admin_router)
add_zelle_exception_handlers(app)   # facade-error handler only — safe beside the host's handlers
```

> `add_zelle_exception_handlers` always registers the `ZelleFacadeError` handler (zelle-specific,
> no clash with the host's `BaseAPIException` / 404 handlers). Its request-validation handler is
> **app-global** — it would override validation-error responses for *every* route, so it is opt-in:
> pass `include_validation_handler=True` only if you want zelle's 422 envelope to become the
> app-wide validation shape. Left off (the default), zelle body-validation errors use the host's
> existing validation convention.

```python
# initializer.py lifespan — in the try block, after mongo_client + email_service exist
zelle_service = await ZelleService.get_service(
    mongo_client=mongo_client,
    email_service=email_service,     # host EmailService (watchdog alerts)
)
application.state.zelle_service = zelle_service  # noqa
await zelle_service.startup_sweep()
zelle_service.start_watchdog()

# ... finally block:
if zelle_service:
    await zelle_service.aclose()
```

The facade builds its **own** southbound mTLS HTTP client from settings — it is
**not** injected. `is_production` comes from either `ZELLE_IS_PRODUCTION` (env) or
by passing `settings=ZelleSettings(is_production=IS_PRODUCTION_ENVIRONMENT)`.

---

## Step 5 — Database (indexes)

The running app **does not create indexes**. Your DBA creates them **once** in
the `fdn-c-amp-fapis-py` database, per
[database-collections-and-indexes.md](database-collections-and-indexes.md):

```javascript
use("fdn-c-amp-fapis-py")
db.zelle_events.createIndex({ event_id: 1 }, { unique: true })
db.zelle_events.createIndex({ status: 1 })
db.zelle_events.createIndex({ scheduled_start: 1, scheduled_end: 1 })
db.zelle_idempotency.createIndex({ client_id: 1, key: 1 }, { unique: true })
db.zelle_audit.createIndex({ event_id: 1 })
db.zelle_audit.createIndex({ ts: 1 })
db.zelle_leases.createIndex({ name: 1 }, { unique: true })
db.zelle_leases.createIndex({ expires_at: 1 }, { expireAfterSeconds: 0 })
```

> ⚠️ The **unique** indexes (idempotency, `event_id`, lease `name`) are
> correctness-critical — the facade must not serve traffic without them. Unlike
> local dev, there is **no** startup shortcut here.

---

## Step 6 — Deploy to CAT & certify

1. Provision the CAT indexes (Step 5).
2. Deploy the host app to CAT with `ZELLE_IS_PRODUCTION=false` and the CAT
   credentials/secrets.
3. **Verify at startup** (logs): the SSL/mTLS mode line, and a successful token
   acquisition (token *metadata* only). No key/token material should appear.
4. **Smoke test** the full lifecycle against CAT EWS:
   ```bash
   # schedule -> start -> complete, then read + check the audit trail
   curl -X POST $HOST/v1/maintenance-events -H "X-Client-Id: payments-ops" \
        -H "Idempotency-Key: cert-001" -d '{...}'
   curl -X POST $HOST/v1/maintenance-events/$EID/start   -H "X-Client-Id: payments-ops" -H "X-Confirm-Ticket: <ticket>"
   curl -X POST $HOST/v1/maintenance-events/$EID/complete -H "X-Client-Id: payments-ops" -H "X-Confirm-Ticket: <ticket>"
   ```
5. **Run Unassisted Certification Testing with Zelle** (the milestone). Capture
   the real CAT response bodies and error shapes; feed anything new back into
   [zoms-api-reference.md](zoms-api-reference.md).

---

## Step 7 — Go-live to PROD

1. Provision the PROD indexes.
2. Deploy the host app to PROD with `ZELLE_IS_PRODUCTION=true` and PROD
   credentials/secrets/mTLS.
3. Verify startup (token acquisition, SSL mode) as in Step 6.
4. A controlled first real maintenance window with the NOC watching.
5. **Decommission the old forms** and cut over all teams to the API-only workflow
   (risk R-04) — this is the compliance requirement due **Oct 30, 2026**.

---

## Operations after go-live

- **Watchdog** pages on stuck events via `CRITICAL` logs **and** email (your
  `EmailService`). Wire monitoring to alert on `CRITICAL`.
- **`UNCERTAIN` events** block all lifecycle verbs until an operator resolves
  them via `POST /v1/admin/maintenance-events/{id}/resolve` after reconciling
  with EWS. Write the escalation into the runbook (who gets paged, how to
  resolve).
- **Rollback:** the facade holds no destructive migration — rolling the host app
  back is safe; the `zelle_*` collections and the append-only audit trail are
  preserved. An in-flight EWS mutation that went `UNCERTAIN` still needs manual
  reconciliation regardless of rollback.
- **Never** put a token fetch in a health probe; readiness gates on the DB, not
  on EWS reachability (an EWS outage must not pull local reads/audit from
  operators).

---

## Go-live checklist

**Vendor**
- [ ] `client_id` + `kid` registered (CAT and PROD); public key in EWS JWKS
- [ ] Token URL, `audience`, `scope` confirmed
- [ ] mTLS requirement confirmed (+ certs provisioned if yes)
- [ ] Certification testing with Zelle passed

**Secrets & config**
- [ ] Signing key (+ mTLS keypair) in the secret store, mounted read-only
- [ ] All required `ZELLE_*` set; `is_production` wired from `IS_PRODUCTION_ENVIRONMENT`
- [ ] `ZELLE_CLIENT_ALLOWLIST` / `ZELLE_LIFECYCLE_CLIENT_ALLOWLIST` set

**Infra**
- [ ] Indexes created in `fdn-c-amp-fapis-py` (incl. the unique + TTL ones)
- [ ] Host app includes the zelle routers + exception handlers + lifespan wiring
- [ ] `startup_sweep()` runs on boot; watchdog enabled; `aclose()` on shutdown

**Verification**
- [ ] CAT smoke test (schedule→start→complete) passed; audit + idempotency verified
- [ ] Startup logs show token acquisition + correct SSL/mTLS mode, no secrets leaked
- [ ] Monitoring alerts on `CRITICAL`; operator resolve runbook written

**Cutover**
- [ ] Old downtime-request forms decommissioned; teams trained (R-04)
- [ ] Live before the **Oct 30, 2026** compliance deadline

---

## Known gaps to close before/at go-live

The vendor PPT shows API surface the facade **does not implement yet** — confirm
whether these are in scope for compliance:

- `POST /v1/events/modify` (modify a scheduled event)
- `GET /v1/events/{id}` (read from EWS — enables true reconciliation)
- `GET /v1/messages/count` (held-message count)
- **Event types**: `MAINTENANCE_SCHEDULED` / `EMERGENCY_SCHEDULED` / `EMERGENCY_IMMEDIATE`
- **Scheduling constraints**: allowed window (11PM–5AM CST) and **max 60 open
  events/org** (risks R-01/R-02/R-03)

---

*Design rationale: [architecture.md](architecture.md). Local testing:
[how-to-run-and-test.md](how-to-run-and-test.md). Config: 
[how-it-all-works.md](how-it-all-works.md). Vendor truth + open questions:
[zoms-api-reference.md](zoms-api-reference.md).*
