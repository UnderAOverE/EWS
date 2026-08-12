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

#### Scheduling rules (spec pp. 59–61 use cases)

- **`maintenanceType` is derived from lead time**: ≥ 15 days out → `MAINTENANCE_SCHEDULED`;
  < 15 days → `EMERGENCY_SCHEDULED`; within 15 minutes of current time →
  `EMERGENCY_IMMEDIATE` (the use case also mentions an `emergencyImmediateStart` indicator set
  to `True` for start-now flows — not in the §3.1 field table; confirm before use).
- **400 rejections carry an error `detail` string.** Documented details: *"Scheduled outside
  allowed time"*, *"Maintenance event could not be created due to a scheduling conflict"*
  (overlapping event), *"Value of ewsHold cannot be EWS_HOLD for this organization"*, and
  *"Scheduled maintenance event exceeds the limit"*.
- **Limit: sixty (60)** scheduled-but-not-started events per Organization.
- Only Resellers can hold messages for a specified Organization (reseller-child rejection).

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

Confirmed (spec pp. 24–25, 63–64): the 200 body is the same `maintenanceEvent` envelope with
`status: "IN_PROGRESS"` and an `actualStartDate`. Validations: `maintenanceEventId` must exist.
Start-window rules: *"Event must be started within six (6) hours of scheduled time"* (400 with
that detail otherwise); an event with no actual start a day or more after its scheduled start
is set to **`NO_SHOW`** by EWS.

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

### Event lifecycle (confirmed vocabulary)

Upstream statuses: **`NOT_STARTED`** (freshly scheduled — the spec never uses "SCHEDULED"),
`IN_PROGRESS`, `PRE_COMPLETE` (seen only in the complete-validation list; semantics
unconfirmed), `COMPLETE`, `CANCELLED`, `NO_SHOW` (set by EWS a day+ after a scheduled start
with no actual start). Flow: `NOT_STARTED → IN_PROGRESS → COMPLETE`, `NOT_STARTED →
CANCELLED` (only before start), `NOT_STARTED → NO_SHOW` (EWS-driven timeout).

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

1. **Partially answered 2026-08-12** — schedule 400s carry an error `detail` string (see
   §3.1); complete uses 404 (unknown id) / 422 (wrong state). Still open: the full error
   body schema and complete error-code catalog.
2. **Answered 2026-08-12** (spec pp. 21–52): the read is the org-scoped list
   `GET /v1/events?orgId=...` (§3.5) — no per-id GET exists. Response schemas and the
   status vocabulary are confirmed; success bodies wrap events in a `maintenanceEvent`
   envelope. Remaining niggle: the exact semantics of `PRE_COMPLETE`.
3. Does `idempotency-id` apply to `start`/`complete`/`cancel`, or only `schedule`?
4. Must `request-id` be unique per attempt (i.e., new value on retry) while
   `idempotency-id` stays constant?
5. **Partially answered 2026-08-11** — the auth server URLs and `aud` values are
   confirmed (see §4), modulo the `.com`/`.io` CAT discrepancy noted there. Still
   open: whether mTLS is required on the token endpoint and/or the API endpoints,
   and completion of our client registration (client_id, public key, kid).
6. Rate limits / concurrency limits, and clock-skew tolerance on JWT claims.
7. **Answered 2026-08-12** (spec pp. 21, 59–61): the schedule response is the
   `maintenanceEvent` envelope (§3.1). Scheduling constraints: `maintenanceType` lead-time
   tiers (15 days / 15 minutes), overlap conflicts → 400, `ewsHold` org entitlement → 400,
   max 60 scheduled-but-not-started events, start within 6 hours of the scheduled time,
   `NO_SHOW` after a day without a start.
