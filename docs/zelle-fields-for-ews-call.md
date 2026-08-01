# Zelle facade — fields for the EWS call

A meeting-prep reference for the call with Early Warning Services (EWS). For every route it lists
the **headers** and **body fields**, and marks **who owns each one** — so you can walk the call
asking only two questions per field: *"is this mine to decide, or something I need from you?"*

> Open this in Word (File ▸ Open) or paste it in — it's plain Markdown. Ask and I'll also generate a
> `.docx`. Wire-level truth (vendor field names/lengths) lives in
> [zoms-api-reference.md](zoms-api-reference.md); the unresolved items are tracked in
> [architecture.md §12](architecture.md).

---

## The one idea to hold onto: two planes

The facade has **two completely separate sides**, and they never share vocabulary:

- **Northbound (mine).** Internal teams → the facade. camelCase JSON, no auth, simple. **I design
  every field here.** EWS never sees any of it directly.
- **Southbound (the wire to EWS).** The facade → EWS. This is where EWS's field names, lengths, and
  rules apply. The facade *translates* my northbound request into this.

So most "who owns it?" answers fall out of which plane the field lives on. The table legend:

| Owner tag | Meaning |
|---|---|
| **Mine — consumer** | The internal caller sends it on each request. I define it. Never leaves the facade unless noted. |
| **Mine — config** | The facade injects it from configuration (my org's identity/contacts). I own the value; EWS may dictate the format or the exact valid value. |
| **EWS — confirm** | The value, allowed set, or meaning comes from EWS. **These are your ask-list for the call.** |
| **Facade-only** | Used internally by the facade; **never sent to EWS**. |

---

## Which calls even reach EWS?

Useful context before the field tables — only the **mutations** talk to EWS; the reads are served
entirely from the facade's own MongoDB:

| Route | Calls EWS? | Touches Mongo? |
|---|---|---|
| `POST /v1/maintenance-events` (schedule) | **Yes** | writes |
| `POST /v1/maintenance-events/{id}/start` | **Yes** | writes |
| `POST /v1/maintenance-events/{id}/complete` | **Yes** | writes |
| `POST /v1/maintenance-events/{id}/cancel` | **Yes** | writes |
| `GET /v1/maintenance-events` (list) | **No** | reads only |
| `GET /v1/maintenance-events/{id}` (get one) | **No** | reads only |
| `POST /v1/admin/maintenance-events/{id}/resolve` | **No** | writes |

---

## Headers — all routes

**Every header is mine.** None come from EWS; none are sent to EWS. They are pure facade-plane.

| Header | On which routes | Required? | Owner | What it is |
|---|---|---|---|---|
| `X-Client-Id` | all | **required** | Mine — consumer | The caller's identity string. The facade attributes the call to it and checks it against the allowlist. Internal only. |
| `X-Correlation-Id` | all | optional | Mine — consumer | Request trace id. If you omit it, the facade mints `c-<uuid4>`. Echoed back on every response. Internal only. |
| `Idempotency-Key` | schedule only | optional | Mine — consumer | Safe-replay key. The facade uses it to dedupe retries in its ledger. **Not** sent to EWS. |
| `X-Confirm-Ticket` | start / complete / cancel | **required** | Mine — consumer | Typed "are you sure" — must exactly equal the event's `ticketNumber`, or the call is rejected. Internal only. |

---

## Schedule — `POST /v1/maintenance-events`

This is the important one. It has **two groups of fields**: what the consumer sends (northbound
body), and what the facade adds from config before calling EWS.

### Group A — northbound body (what the internal caller sends)

| Field (camelCase) | Type | Required? | Length | Owner | Sent to EWS as | Notes / what to confirm |
|---|---|---|---|---|---|---|
| `startTime` | date-time, tz-aware | **required** | — | Mine — consumer | `scheduledStartDate` | Window start. Must be `…Z` (UTC), not in the past. |
| `endTime` | date-time, tz-aware | **required** | must be > start | Mine — consumer | `scheduledEndDate` | Window end. |
| `ticketNumber` | string | **required** | 1–36 | Mine — consumer | `ticketNumber` | Our internal change ticket. **Confirm EWS's max length** (we assume 36). |
| `reason` | string | **required** | 1–255 | Mine — consumer | *(not sent)* | Stored locally + in audit only. **Facade-only.** |
| `holdMode` | enum: `SELF_HOLD` / `EWS_HOLD` | optional (defaults from config) | — | **EWS — confirm** | `ewsHold` | **Confirm the exact allowed values and what each does.** |
| `allowOverlap` | boolean | optional (`false`) | — | Facade-only | *(not sent)* | Purely a facade guardrail — whether to allow a window that overlaps another. |
| `suppressDuplicatePayments` | boolean | optional | — | **EWS — confirm** | `suppressDuplicatePayments` | **Confirm meaning and default.** |
| `networkNotificationId` | string | optional | 1–36 | **EWS — confirm** | `networkNotificationId` | **Confirm what it is, where the value comes from, and when it's required.** |

### Group B — facade config constants (added automatically; the caller never sends these)

These are injected from configuration in the facade's mapping layer. They are **mine** (my org's
identity), but EWS dictates the exact `orgId` and possibly the formats.

| EWS field | From config setting | Length | Owner | What to confirm with EWS |
|---|---|---|---|---|
| `orgId` | `ZELLE_ORG_ID` | exactly **3** | Mine — config (value assigned by EWS) | **The exact 3-character org id EWS registered for us.** |
| `participantName` | `ZELLE_PARTICIPANT_NAME` | 1–50 | Mine — config | Exact value/format EWS expects. |
| `submittedName` | `ZELLE_SUBMITTED_NAME` | 1–50 | Mine — config | Who is submitting; any format rules. |
| `contactName` | `ZELLE_CONTACT_NAME` | 1–128 | Mine — config (PII) | Contact person. |
| `contactPhone` | `ZELLE_CONTACT_PHONE` | 9–12 | Mine — config (PII) | **Format — digits only? country code?** |
| `contactEmail` | `ZELLE_CONTACT_EMAIL` | 1–255 | Mine — config (PII) | Contact email. |
| `scheduledStartDate` | derived from `startTime` | string | Mine (formatted) | **Confirm the exact wire format** `YYYY-MM-DDTHH:MM:SS.NNNZ`. |
| `scheduledEndDate` | derived from `endTime` | string | Mine (formatted) | Same as above. |
| `ewsHold` | derived from `holdMode` | enum | **EWS — confirm** | Allowed values. |

**Response we get back:** `maintenanceEventId` — **owned by EWS** (they generate it). The facade
stores it and maps it to our own `eventId`. **Confirm: is it returned synchronously in the 201, and
under what key?** (This is the single biggest open item — [architecture.md §12 Q2](architecture.md).)

---

## Start / Complete / Cancel — `POST /v1/maintenance-events/{event_id}/{verb}`

The consumer sends **no body** here — just headers and the path id. The facade builds the EWS body.

**Northbound (mine):**

| Input | Where | Required? | Owner | Notes |
|---|---|---|---|---|
| `event_id` | path | **required** | Mine — consumer | The **facade** `eventId` from schedule (not EWS's id). |
| `X-Confirm-Ticket` | header | **required** | Mine — consumer | Must equal the event's `ticketNumber`. |
| `dry_run` | query | optional (`false`) | Facade-only | Audit the attempt without calling EWS or changing state. |

**Southbound to EWS (the facade fills this in):**

| EWS field | Length | Owner | Notes |
|---|---|---|---|
| `maintenanceEventId` | exactly **36** | **EWS — confirm** | The id EWS returned at schedule. The consumer never supplies it; the facade looks it up. **Confirm the 36-char format.** |

---

## Resolve (operator) — `POST /v1/admin/maintenance-events/{event_id}/resolve`

An internal operator-only tool — **does not call EWS**. It updates the facade's own state after a
human has reconciled with EWS out of band. Every field here is mine.

| Field | Where | Required? | Owner | Notes |
|---|---|---|---|---|
| `event_id` | path | **required** | Mine — operator | Facade event id to resolve. |
| `X-Client-Id` | header | **required** | Mine — operator | Operator identity; recorded in audit. |
| `actualStatus` | body | **required** | Mine — operator | The true status being attested. |
| `attestation` | body | **required** (1–500) | Mine — operator | Free-text justification (e.g. "EWS NOC ref 4471"); stored in audit. |
| `ewsEventId` | body | required only for `PENDING_UPSTREAM_ID` | EWS — obtained by operator | The EWS id you got from EWS by hand, so later lifecycle calls have an id. |

---

## The ask-list — pull these into the meeting

Everything tagged **EWS — confirm** above, consolidated:

1. **`orgId`** — the exact 3-character organisation id EWS registered for us.
2. **`holdMode` / `ewsHold`** — the exact allowed values and what `SELF_HOLD` vs `EWS_HOLD` each do.
3. **`suppressDuplicatePayments`** — what it does, its default, when to set it.
4. **`networkNotificationId`** — what it represents, where we get the value, and whether it's required.
5. **`maintenanceEventId`** — is it returned synchronously in the schedule `201`? under what JSON key? confirm the 36-char format.
6. **Field lengths** — confirm each max we assumed: `orgId` 3, `participantName`/`submittedName` 50, `contactName` 128, `contactPhone` 9–12, `contactEmail` 255, `ticketNumber` 36.
7. **Date/time format** — confirm `YYYY-MM-DDTHH:MM:SS.NNNZ` (UTC, exactly 3 millisecond digits, literal `Z`).
8. **`contactPhone` format** — digits only, or with country code / punctuation?
9. **Required vs optional** — which of the "optional" southbound fields EWS actually requires.
10. **Auth (blocks connectivity)** — CAT/PROD token URLs, required `aud`, the scope, and mTLS / CA-chain requirements ([architecture.md §12 Q1](architecture.md)).
11. **Error catalog** — the response shapes/codes for double-start, complete-without-start, cancel-after-start, and idempotency replay ([architecture.md §12 Q4](architecture.md)).

---

## One-line summary of ownership

- **All headers and all request bodies are mine** — I design the entire northbound plane; internal
  teams just send it.
- **The org constants and contact block are mine** (my org's identity), injected from config — but
  EWS decides the exact `orgId` value and may dictate formats.
- **The only data element EWS truly owns is `maintenanceEventId`** (they generate it; we store and
  reuse it).
- **`reason` and `allowOverlap` never leave the facade.**

*Source of truth: northbound fields → [northbound.py](../src/apis/models/zelle/northbound.py);
EWS wire fields/lengths → [southbound.py](../src/apis/models/zelle/southbound.py) and
[zoms-api-reference.md](zoms-api-reference.md); the config constants → `ZelleSettings` in
[config/zelle.py](../src/apis/config/zelle.py).*
