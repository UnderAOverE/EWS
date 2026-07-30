# How to run & test the Zelle facade end-to-end

A linear runbook: get the code onto a server/container, set up the environment,
and drive a full end-to-end test against the **fake EWS** (no real EWS or CAT
credentials needed).

## What you're actually running

Two separate processes:

| Process | What it is | Needs a database? |
|---|---|---|
| **Fake EWS** (`src/fake_ews/app.py`) | A stub that pretends to be EWS: `/token` + the four ZOMS ops, with in-memory state and fault injection. | **No** — pure in-memory. |
| **The facade** (the `zelle` module) | The service under test. It's normally *mounted into the host app*; for local testing we run it with the tiny runner below. | **Yes** — MongoDB (`zelle_*` collections). |

So the fake EWS requires **nothing**. The facade requires **Python + MongoDB +
an RSA signing key**. The facade calls the fake EWS for `/token` and the ZOMS
operations; you call the facade's northbound REST API with `curl`.

```
curl ──▶ facade (:8000) ──▶ fake EWS (:9000)
              │
              ▼
          MongoDB (:27017)
```

---

## Step 0 — Prerequisites

- **Python 3.12+**
- **MongoDB** reachable (local `mongod`, a container, or a remote URI)
- **OpenSSL** (or Python) to generate a throwaway RSA key
- Network access to install pip packages (or a pre-built venv/image)

---

## Step 1 — Get the code onto the server/container

**Bare server:** clone or copy the repo.

```bash
git clone https://github.com/UnderAOverE/EWS.git /opt/zelle
cd /opt/zelle
# — or copy an archive —
# scp zelle.tar.gz user@server:/opt/ && tar -xzf /opt/zelle.tar.gz -C /opt/zelle
```

**Container (local test image):** a minimal Dockerfile.

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
COPY docs/ ./docs/
ENV PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1
```

> In real deployment the `zelle` module ships **inside the host app**
> (`fdn-c-amp-fapis-py`); this image is only for isolated local/CAT testing.

---

## Step 2 — Python environment + dependencies

```bash
python -m venv .venv
# Linux/macOS:  source .venv/bin/activate
# Windows:      .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`requirements.txt` covers runtime (fastapi, httpx, motor, pydantic, joserfc,
uvicorn) and dev (pytest, respx, mongomock-motor, ruff, mypy).

---

## Step 3 — Create the RSA signing key (local throwaway)

The token broker signs a real RS256 client assertion, so it needs a private key
on disk. For local testing any RSA key works (the fake EWS doesn't verify it).

```bash
openssl genrsa -out /opt/zelle/secrets/signing.pem 2048
```

> In CAT/PROD this is the **real** key registered with EWS, mounted read-only
> from the secret store — never generated locally, never in git.

---

## Step 4 — MongoDB + indexes

Start MongoDB (skip if you already have one):

```bash
docker run -d --name zelle-mongo -p 27017:27017 mongo:7
```

Create the indexes once (the app never creates them). Full list + rationale is
in **[database-collections-and-indexes.md](database-collections-and-indexes.md)**;
the commands, run in `mongosh` against the `fdn-c-amp-fapis-py` database:

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

> **Local shortcut:** the runner in Step 7 calls each repo's `ensure_indexes()`
> on startup, so for a throwaway local DB you can skip the `mongosh` step. Do
> **not** rely on that for CAT/PROD — create them by hand there.

---

## Step 5 — Environment variables

The facade reads all config from `ZELLE_*` env vars. For local testing we point
the southbound URLs at the fake EWS (overriding the CAT defaults):

```bash
# Point southbound at the fake EWS (Step 6 runs it on :9000)
export ZELLE_IS_PRODUCTION=false
export ZELLE_API_BASE_URL="http://localhost:9000/zoms"
export ZELLE_TOKEN_URL="http://localhost:9000/token"
export ZELLE_TOKEN_AUD="http://localhost:9000"

# Auth / signing (local throwaway)
export ZELLE_CLIENT_ID="local-client"
export ZELLE_SIGNING_KID="local-kid"
export ZELLE_SIGNING_KEY_PATH="/opt/zelle/secrets/signing.pem"

# Org constants injected into every schedule (lengths per the vendor spec)
export ZELLE_ORG_ID="BBO"
export ZELLE_PARTICIPANT_NAME="Bobs Bank of Omaha"
export ZELLE_SUBMITTED_NAME="Bob Barker"
export ZELLE_CONTACT_NAME="Terry Technology"
export ZELLE_CONTACT_PHONE="9999999977"
export ZELLE_CONTACT_EMAIL="TTechnology@BBO.com"

# For the local runner's Mongo connection
export MONGO_URI="mongodb://localhost:27017"
```

(On Windows PowerShell use `$env:ZELLE_IS_PRODUCTION="false"`, etc.)

> **CAT/PROD:** drop the three `ZELLE_API_BASE_URL/TOKEN_URL/…` overrides and set
> `ZELLE_IS_PRODUCTION` from your `IS_PRODUCTION_ENVIRONMENT` — the CAT/PROD URLs
> derive automatically. Add mTLS paths (`ZELLE_CA_CERTIFICATE_PATH`,
> `ZELLE_CLIENT_CERTIFICATE_PATH`, `ZELLE_CLIENT_KEY_PATH`) only when EWS requires
> them.

---

## Step 6 — Run the fake EWS

```bash
# from the repo root, with the venv active
PYTHONPATH=. uvicorn src.fake_ews.app:app --host 0.0.0.0 --port 9000
```

Leave it running. Sanity check in another shell:

```bash
curl -s -X POST http://localhost:9000/token \
  -d "grant_type=client_credentials" \
  -d "client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer" \
  -d "client_assertion=anything"
# -> {"access_token":"fake-token-…","token_type":"Bearer","expires_in":1800}
```

---

## Step 7 — End-to-end testing

### Option A (fastest) — the automated suite

This drives the **whole** flow (token broker → ZOMS client → state machine →
fake EWS) with an in-process fake EWS and an in-memory Mongo — **no external
Mongo or fake-EWS process needed**:

```bash
pytest -q
# 55 passed
```

Use this for the quickest confidence check and in CI.

### Option B — manual end-to-end with `curl`

Run the facade with this tiny local runner (save as `local_run.py` in the repo
root). It's the same wiring the host app does, minus the host's other services.

```python
import sys
sys.dont_write_bytecode = True

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

from src.apis.config.zelle import ZelleSettings
from src.apis.dependencies.services.zelle import add_zelle_exception_handlers
from src.apis.routes import zelle_admin_router, zelle_events_router
from src.apis.services.zelle.service import ZelleService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    mongo_client = AsyncIOMotorClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
    service = await ZelleService.get_service(
        mongo_client=mongo_client,
        settings=ZelleSettings(is_production=False),
        email_service=None,
    )
    app.state.zelle_service = service
    # Local convenience: create indexes (skip in CAT/PROD — DBA runs the mongosh runbook).
    await service.events.ensure_indexes()
    await service.idempotency.ensure_indexes()
    await service.audit.ensure_indexes()
    await service.leases.ensure_indexes()
    await service.startup_sweep()
    try:
        yield
    finally:
        await service.aclose()
        mongo_client.close()


app = FastAPI(title="zelle-local", lifespan=lifespan)
app.include_router(zelle_events_router)
app.include_router(zelle_admin_router)
# Standalone zelle test app: opt into the global validation handler so bad bodies also return
# the zelle 422 envelope. In the host app this is a decision (see production-deployment.md).
add_zelle_exception_handlers(app, include_validation_handler=True)
```

Run it (with the Step 5 env vars exported, the fake EWS from Step 6 running, and
Mongo up):

```bash
PYTHONPATH=. uvicorn local_run:app --host 0.0.0.0 --port 8000
```

Now drive the full lifecycle against the facade:

```bash
BASE=http://localhost:8000

# 1) Schedule (→ 201, returns an eventId)
curl -sS -X POST "$BASE/v1/maintenance-events" \
  -H "X-Client-Id: payments-ops" \
  -H "Idempotency-Key: chg-48213" \
  -H "Content-Type: application/json" \
  -d '{
        "startTime":"2026-08-01T06:00:00Z",
        "endTime":"2026-08-01T08:00:00Z",
        "ticketNumber":"CHG-48213",
        "reason":"local end-to-end test",
        "holdMode":"SELF_HOLD"
      }'
# copy the eventId from the response into EID:
EID=<paste-eventId>

# 2) Start (holds begin) — X-Confirm-Ticket must equal ticketNumber
curl -sS -X POST "$BASE/v1/maintenance-events/$EID/start" \
  -H "X-Client-Id: payments-ops" -H "X-Confirm-Ticket: CHG-48213"

# 3) Complete (holds release)
curl -sS -X POST "$BASE/v1/maintenance-events/$EID/complete" \
  -H "X-Client-Id: payments-ops" -H "X-Confirm-Ticket: CHG-48213"

# Read it back
curl -sS "$BASE/v1/maintenance-events/$EID" -H "X-Client-Id: payments-ops"
```

**Test the failure paths** by telling the fake EWS to misbehave — the facade
forwards no fake-EWS header, so inject at the fake directly, or exercise them via
the automated suite (Option A already covers 500 / 429 / 401-refresh / timeout).
The fake's fault switch is the `x-fake-fault` header (`500`, `429`, `401`,
`slow`) on its own endpoints.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `500` on schedule with a signing error at startup | `ZELLE_SIGNING_KEY_PATH` missing or not a valid RSA PEM (Step 3). |
| `UPSTREAM_UNAVAILABLE` on every call | Fake EWS not running, or `ZELLE_API_BASE_URL`/`ZELLE_TOKEN_URL` don't point at it (Step 5/6). |
| Duplicate events on retry | The unique `zelle_idempotency (client_id,key)` index wasn't created (Step 4). |
| `403 FORBIDDEN_ACTION` | `ZELLE_CLIENT_ALLOWLIST` is set and your `X-Client-Id` isn't in it (leave it unset locally). |
| Validation `422` on schedule | Body shape wrong — `startTime` must be tz-aware (`…Z`), `endTime > startTime`, not in the past. |
| Pydantic "Field required" at settings load | A required `ZELLE_*` var is missing (token_aud, client_id, signing_kid, signing_key_path, org_id, contact_*). |

---

*This runbook targets local/CAT-less testing against the fake EWS. Config
reference: [how-it-all-works.md](how-it-all-works.md) §7. Collections/indexes:
[database-collections-and-indexes.md](database-collections-and-indexes.md).
Vendor wire truth: [zoms-api-reference.md](zoms-api-reference.md).*
