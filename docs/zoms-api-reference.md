# Zelle Organization Maintenance Service (ZOMS) — API Reference Notes

> Transcribed from photographed vendor documentation ("Zelle® Organization Maintenance
> Self-Care Technical Specifications", Early Warning Services, LLC) plus a companion
> summary of the EWS OAuth2 token flow. Field lengths/examples copied as-is; anything
> not visible in the source pages is listed under **Open questions** at the bottom.

## 1. Overview

The Zelle Organization Maintenance Service (ZOMS) provides a REST API for financial
institutions (Participants) to manage **maintenance events**: programmatic scheduling,
starting, completing, and canceling of maintenance windows, during which connectivity
between the Participant and the Zelle Network may be interrupted. EWS can hold MQ
messages during a window and release them when the event completes.

### Service endpoints

| Environment | Base URL |
|---|---|
| CAT | `https://api.zelle.cat.earlywarning.io/zoms` |
| PROD | `https://api.zelle.earlywarning.com/zoms` |

## 2. Common request headers

| Header | Description | Required |
|---|---|---|
| `Authorization` | `Bearer <OAuth2 JWT access token>` | Yes |
| `accept` | `application/json` | Yes |
| `content-type` | `application/json` | Yes |
| `request-id` | Unique value (typically UUID) identifying the request for logging and troubleshooting | Yes |

## 3. End-to-end flows

All operations use OAuth scope **`maintenance-event`**.

### 3.1 Schedule Maintenance Event

- Endpoint: `POST /v1/events/schedule`
- Success: `201 Created`
- Additional header: `idempotency-id` (required) — client-generated UUID to prevent duplicate request processing

> ✅ **Response shape + scheduling rules confirmed 2026-08-12** from photographed pages of the
> full vendor spec *"Zelle Org Maintenance Self-Care Tech Specs and Use Cases July 2026.pdf"*
> (TR-CIS-ZELLE-0003, 78 pp.) — pp. 21, 59–61. See the response sample and rules below.

Request body:

| Field | Type | Length | Required | Description | Example |
|---|---|---|---|---|---|
| `orgId` | String | 3–3 | Yes | Organization ID or Reseller ID undergoing maintenance | `BBO` |
| `participantName` | String | 1–50 | Yes | Participant name for use in notifications | `Bob's Bank of Omaha` |
| `submittedName` | String | 1–50 | Yes | Responsible party authorizing the event | `Bob Barker` |
| `contactName` | String | 1–128 | Yes | Person to contact regarding the maintenance | `Terry Technology` |
| `contactPhone` | String | 9–12 | Yes | Contact phone number | `9999999977` |
| `contactEmail` | String | 1–255 | Yes | Contact email address | `TTechnology@BBO.com` |
| `scheduledStartDate` | String | — | Yes | Planned start, `YYYY-MM-DDTHH:MM:SS.NNNZ` | `2025-10-20T23:00:00.123Z` |
| `scheduledEndDate` | String | — | Yes | Planned end, same format | `2025-10-21T05:00:00.000Z` |
| `ewsHold` | String | — | Yes | Whether EWS holds messages. Allowed: `EWS_HOLD` or `SELF_HOLD` | `EWS_HOLD` |
| `suppressDuplicatePayments` | Boolean | — | No | Whether duplicate 'On New Payment' notifications will be created | `true` |
| `ticketNumber` | String | 1–36 | No | EWS Servicing Ticket or Participant reference number | `SVC02345` |
| `networkNotificationId` | String | 1–36 | No | ID to link to a Network notification record | `999` |

Sample payload:

```json
{
  "orgId": "BBO",
  "participantName": "Bobs Bank of Omaha",
  "submittedName": "Bob Barker",
  "contactName": "Terry Technology",
  "contactPhone": "9999999977",
  "contactEmail": "TTechnology@BBO.com",
  "scheduledStartDate": "2025-10-20T23:00:00.123Z",
  "scheduledEndDate": "2025-10-21T05:00:00.123Z",
  "ewsHold": "EWS_HOLD",
  "suppressDuplicatePayments": true,
  "ticketNumber": "SVC02345",
  "networkNotificationId": "999"
}
```

#### Schedule response (confirmed, spec p. 21)

**Every success body wraps the event in a `maintenanceEvent` envelope** — the id is NOT at the
top level:

```json
{
  "maintenanceEvent": {
    "maintenanceEventId": "ef30587c-eb05-46f2-b2a7-f44e6d360dd0",
    "orgId": "BAC",
    "participantName": "Reseller Participant",
    "contactName": "Contact Name",
    "contactPhone": "99955876",
    "contactEmail": "ews@example.com",
    "submittedBy": "Reseller Submitted",
    "submissionDate": "2020-04-03T10:37:28.123Z",
    "scheduledStartDate": "2020-04-03T10:37:28.123Z",
    "scheduledEndDate": "2020-04-03T10:37:28.123Z",
    "maintenanceType": "EMERGENCY_SCHEDULED",
    "status": "NOT_STARTED",
    "daysAdvanceNotice": 45,
    "suppressDuplicatePayments": false,
    "ewsHold": "EWS_HOLD",
    "ticketNumber": "SVC02345",
    "networkNotificationId": "74758"
  }
}
```

The response field table also lists **`location`** (String, "URI of the resource", required) —
a resource URI accompanies the created event.

#### Scheduling rules

> ✅ **Expanded 2026-08-18** from the internal rules doc *"Zelle Emergency Maintenance
> Scheduling Rules & Constraints"* (sourced from the July 2026 Zelle Network NewsFlash),
> plus a live CAT rejection observed the same day. Supersedes the earlier partial list.

- **Allowed maintenance window (the big one):** standard AND `EMERGENCY_SCHEDULED` events
  must fall **strictly within 11:00 PM to 5:00 AM CST / 12:00 AM to 6:00 AM CDT**. Both
  definitions are the **same fixed 05:00 to 11:00 UTC band year-round**. Violations return
  422 (observed live: detail *"Scheduling maintenance event outside the allowed times."*)
  or 400 with detail *"Scheduled outside allowed time."*.
- **`maintenanceType` is derived from lead time**: more than 15 days out is
  `MAINTENANCE_SCHEDULED`; 15 days or less is `EMERGENCY_SCHEDULED`; within 15 minutes of
  submission is `EMERGENCY_IMMEDIATE`, which **requires `emergencyImmediateStart: true`**
  in the schedule request and is **completely exempt from the allowed window** (24/7).
- **Error bodies are RFC 7807 problem details** (confirmed live 2026-08-18):
  `{"type", "title", "status", "detail", "instance"}`. Known `detail` strings:
  *"Scheduled outside allowed time."*, *"Start date must be less than end date"*,
  *"Maintenance event could not be created due to a scheduling conflict."* (overlap, also
  seen as HTTP 409), *"Scheduled maintenance event exceeds the limit."*,
  *"Value of ewsHold cannot be EWS_HOLD for this organization."*, and
  *"Event must be started within six (6) hours of scheduled time."*.
- **Limit: sixty (60)** scheduled-but-unstarted events per Organization.
- **Chronology**: start strictly before end; no historical times relative to submission.
- Only Parent Resellers can hold messages for a child Organization (reseller restriction).

### 3.2 Activate (Start) Maintenance Event

Changes event status to `IN_PROGRESS`, sets the actual start time, and initiates the
MQ Hold process if configured.

- Endpoint: `POST /v1/events/start`
- Success: `200 OK`

| Field | Type | Length | Required | Description |
|---|---|---|---|---|
| `maintenanceEventId` | String | 36–36 | Yes | Unique ID of the maintenance event to start |

```json
{ "maintenanceEventId": "f879562c-b912-44e9-a592-71d3aef09afb" }
```

Confirmed (spec pp. 24–25, 63–64; rules doc 2026-08-18): the 200 body is the same
`maintenanceEvent` envelope with `status: "IN_PROGRESS"` and an `actualStartDate`.
Validations: `maintenanceEventId` must exist. Start-window rule (the 6-hour gate): the start
call must land within six (6) hours of the scheduled start, **early or late** — outside that
margin returns 422/400 with *"Event must be started within six (6) hours of scheduled
time."*. An event whose window expires with no start is set to **`NO_SHOW`** by EWS; the
rules doc also names a **`/no-show` endpoint** for formally updating such events (422 unless
the scheduled end date is in the past) — no path/body spec seen; confirm before use.

### 3.3 Deactivate (Complete) Maintenance Event

Changes event status to `COMPLETE`, sets the actual end time, and releases any held
MQ messages.

- Endpoint: `POST /v1/events/complete`
- Success: `200 OK`
- Body: `maintenanceEventId` (as above)

Confirmed (spec pp. 37–38): 200 body is the `maintenanceEvent` envelope with
`status: "COMPLETE"`, `actualStartDate`, and `actualEndDate`. Validations with response codes:
`maintenanceEventId` must exist → **404**; must be in `IN_PROGRESS` or `PRE_COMPLETE` status →
**422**.

### 3.4 Cancel Maintenance Event

Cancels a scheduled maintenance event that has not yet started, changing its status
to `CANCELLED`.

- Endpoint: `POST /v1/events/cancel`
- Success: `200 OK`
- Body: `maintenanceEventId` (as above)

Confirmed (spec pp. 34–35): 200 body is the `maintenanceEvent` envelope with
`status: "CANCELLED"`. Validations: `maintenanceEventId` must exist; must be in
`NOT_STARTED` status.

### 3.5 Get Maintenance Events for orgId (the read)

> ✅ **Confirmed 2026-08-12** (spec pp. 50–52). The read is **org-scoped with query
> parameters** — there is **no per-id GET**, and the earlier ops-reported
> `/v1/events/{maintenanceEventId}` / `/v1/events.{id}` forms do not exist (a per-id path
> draws a 400).

- Endpoint: `GET /v1/events?orgId={orgId}&status={status}&dateFrom={date}&dateTo={date}`
  (`status`, `dateFrom`/`dateTo` optional; multiple events come back "in the order of their
  schedule date")
- Success: `200 OK`; OAuth scope `maintenance-event`
- Headers: common headers per §2; no `idempotency-id` (reads are naturally idempotent)

Response body — plural `maintenanceEvents` array of full event objects:

```json
{
  "maintenanceEvents": [
    {
      "maintenanceEventId": "7018f6e2-67e1-4d51-b8d8-3f0295d15ae9",
      "orgId": "CQ7",
      "participantName": "Participant Name",
      "contactName": "Contact Name",
      "contactPhone": "9995559999",
      "contactEmail": "ews@example.com",
      "submittedBy": "Submitted Name",
      "submissionDate": "2020-04-03T10:37:28.123Z",
      "scheduledStartDate": "2020-04-03T10:37:28.123Z",
      "scheduledEndDate": "2020-04-03T10:37:28.123Z",
      "maintenanceType": "MAINTENANCE_SCHEDULED",
      "status": "NOT_STARTED",
      "daysAdvanceNotice": 210,
      "suppressDuplicatePayments": false,
      "ewsHold": "EWS_HOLD"
    }
  ]
}
```

Completed entries additionally carry `actualStartDate` / `actualEndDate`.

> ⚠️ **Date handling on reads (July 2026 NewsFlash, transcribed 2026-08-18):** ZOMS stores
> all times in UTC, and GET responses return **date-only values** (e.g. `2020-04-03`) with
> **no time component**. The stored UTC date can roll past midnight versus the local date
> the participant entered, so lookups by date should use a **two-day `dateFrom`/`dateTo`
> range**. The NewsFlash example writes the range boundaries as `MMDDYYYY`
> (`07062026-07072026`) — confirm the actual wire format with EWS before relying on it.
> The same NewsFlash also names a per-id `GET /v1/events/{maintenanceEventId}` — which
> contradicts both the spec pages (orgId list only) and our live CAT test (a per-id path
> drew a 400). Treat the orgId list as the working read until EWS reconciles.

### 3.6 Count for orgId (queue depth)

Confirmed (spec pp. 56–57): counts the notifications currently held for the org, by queue.
Callable whether or not a maintenance event is in progress.

- Endpoint: `GET /v1/count?orgId={orgId}`
- Success: `200 OK`; OAuth scope `maintenance-event`

```json
{
  "queueDepths": [
    { "name": "rejected-payment", "count": 1594 },
    { "name": "create-payment-request", "count": 1917 }
  ]
}
```

Queue names seen: `rejected-payment`, `restrict-customer`, `change-payment-status`,
`organization-change`, `delete-profile`, `deactivate-payment-request`,
`create-payment-request`.

### 3.7 Create Pre-complete Details (endpoint unconfirmed)

> ⚠️ Named only (spec p. 62, *Process an Organization Maintenance Event* use case) — no
> path, method, or body was visible. During a window the participant "calls **Create
> Pre-complete Details** with a count specified for the notification type(s) to be tested"
> and EWS "releases all held messages of the specified type up to the requested count" — a
> partial-release mechanism for testing mid-maintenance. This plausibly explains the
> `PRE_COMPLETE` status accepted by complete (§3.3). Ask EWS for the endpoint spec before
> any implementation.

### Event lifecycle (confirmed vocabulary)

Upstream statuses (definitions confirmed 2026-08-18 by the internal rules doc):

- **`NOT_STARTED`** — successfully scheduled, not yet active (the spec never uses
  "SCHEDULED").
- **`IN_PROGRESS`** — started; messaging queues are held and SLA exclusions are active.
- **`PRE_COMPLETE`** — the participant triggered a **partial test release** of specific held
  notifications while the rest stay held (via the Create Pre-complete Details operation,
  §3.7).
- **`COMPLETE`** — finished; all remaining held MQ messages released, normal operations
  resume.
- **`CANCELLED`** — cancelled by the participant before its start time.
- **`NO_SHOW`** — set automatically (typically a day or more past the scheduled time) when
  an event was scheduled but never activated.

Flow: `NOT_STARTED` to `IN_PROGRESS` (start), to `CANCELLED` (cancel, before start only),
or to `NO_SHOW` (EWS timeout); `IN_PROGRESS` to `PRE_COMPLETE` (test release) or straight
to `COMPLETE`; `PRE_COMPLETE` to `COMPLETE` (complete).

## 4. OAuth2 access token flow

> ✅ **Confirmed 2026-08-11** via the EWS "Obtaining RESTful Service Authorizations"
> page (Early Warning Services Platform API OAuth Access User Guide v2.0) after a
> support exchange. The previously transcribed `auth.wallet.*` URLs were **Paze's**
> auth server — it answers `401 Unauthorized` to ZOMS clients.

| Environment | Token endpoint | `aud` claim value |
|---|---|---|
| CAT | `https://auth.zelle.cat.earlywarning.io/token` | `https://auth-zelle.cat.earlywarning.io/oauth2/access/v1/token` |
| PROD | `https://auth.zelle.earlywarning.com/token` | `https://auth-zelle.earlywarning.com/oauth2/access/v1/token` |

The `aud` is a **fixed URL-shaped string, deliberately NOT the token endpoint** —
note the `auth-zelle` host (with a dash) and the `/oauth2/access/v1/token` path.

> ⚠️ Discrepancy to reconcile with EWS: their support email quoted the CAT endpoint
> as `https://auth.zelle.cat.earlywarning.com/token` (`.com`, where the doc page says
> `.io` for CAT) and quoted the **PROD** audience for a CAT test. The doc-page values
> above are treated as authoritative (they are internally consistent: CAT=`.io`,
> PROD=`.com`); if CAT still 401s with them, try the email's variants via
> `ZELLE_TOKEN_URL` / `ZELLE_TOKEN_AUD` overrides and ask EWS to reconcile.

`POST /token` (form-encoded):

| Parameter | Value |
|---|---|
| `grant_type` | `client_credentials` |
| `client_assertion_type` | `urn:ietf:params:oauth:client-assertion-type:jwt-bearer` |
| `client_assertion` | JWS signed with the client's registered private key |
| `scope` (optional) | e.g. `maintenance-event` for ZOMS |

### `client_assertion` JWT structure

Header:

```json
{ "alg": "RS256", "kid": "<key id of registered keypair>" }
```

Claims:

| Claim | Meaning |
|---|---|
| `iss` | Your `client_id` (provided during onboarding) |
| `sub` | Also your `client_id` |
| `aud` | The fixed audience string from the table above — NOT the auth server root or token endpoint |
| `exp` | Expiration time (epoch seconds) |
| `nbf` | Not before |
| `iat` | Issued at |
| `jti` | Unique ID, typically a random UUID |
| `scope` | Requested scope |

### Token response

```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 1800
}
```

`expires_in` 1800 seconds = 30-minute token TTL. Endpoints may support standard TLS
and mutual TLS (mTLS).

## 5. Open questions to confirm with EWS

1. **Answered 2026-08-18** — error bodies are RFC 7807 problem details (confirmed live);
   the known `detail` catalog is in §3.1. Complete uses 404 (unknown id) / 422 (wrong
   state); most rejections are 422-or-400.
2. **Answered 2026-08-12/18** (spec pp. 21–52 + rules doc): the working read is the
   org-scoped list `GET /v1/events?orgId=...` (§3.5); success bodies wrap events in a
   `maintenanceEvent` envelope; the status vocabulary and `PRE_COMPLETE` semantics are
   confirmed. New wrinkle: the July 2026 NewsFlash names a per-id GET that our live test
   400'd — reconcile with EWS (see §3.5 note).
3. Does `idempotency-id` apply to `start`/`complete`/`cancel`, or only `schedule`?
4. Must `request-id` be unique per attempt (i.e., new value on retry) while
   `idempotency-id` stays constant?
5. **Partially answered 2026-08-11** — the auth server URLs and `aud` values are
   confirmed (see §4), modulo the `.com`/`.io` CAT discrepancy noted there. Still
   open: whether mTLS is required on the token endpoint and/or the API endpoints,
   and completion of our client registration (client_id, public key, kid).
6. Rate limits / concurrency limits, and clock-skew tolerance on JWT claims.
7. **Answered 2026-08-12/18** (spec pp. 21, 59–61 + rules doc): the schedule response is
   the `maintenanceEvent` envelope (§3.1). Scheduling constraints: the 11:00 PM to 5:00 AM
   CST / 12:00 AM to 6:00 AM CDT allowed window (05:00 to 11:00 UTC), `maintenanceType`
   lead-time tiers (15 days / 15 minutes, with `emergencyImmediateStart` exempting the
   window), overlap conflicts, `ewsHold` org entitlement, max 60 scheduled-but-unstarted
   events, the 6-hour start gate (early or late), `NO_SHOW` after an unstarted window.
8. New (2026-08-18): the `/no-show` endpoint's path/body spec; the `dateFrom`/`dateTo` wire
   format on the list read (the NewsFlash example reads as MMDDYYYY); and the per-id GET
   discrepancy in §3.5.
