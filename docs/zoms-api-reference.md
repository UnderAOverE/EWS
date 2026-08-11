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

### 3.3 Deactivate (Complete) Maintenance Event

Changes event status to `COMPLETE`, sets the actual end time, and releases any held
MQ messages.

- Endpoint: `POST /v1/events/complete`
- Success: `200 OK`
- Body: `maintenanceEventId` (as above)

### 3.4 Cancel Maintenance Event

Cancels a scheduled maintenance event that has not yet started, changing its status
to `CANCELLED`.

- Endpoint: `POST /v1/events/cancel`
- Success: `200 OK`
- Body: `maintenanceEventId` (as above)

### 3.5 Get Maintenance Event (status read)

> ⚠️ Not in the photographed spec pages. Reported by our ops team (2026-08-06): EWS
> exposes a read endpoint keyed by the maintenance event id. Everything below except
> the path is **assumed** until EWS confirms — see open question #2.

- Endpoint: `GET /v1/events/{maintenanceEventId}` (path assumed to follow the
  standard REST form; the internal report wrote it as `/v1/events.{maintenanceEventId}`
  — confirm the separator)
- Success: `200 OK` (assumed)
- Headers: common headers per §2; no `idempotency-id` (reads are naturally idempotent)

Assumed response body (parse leniently until confirmed):

```json
{
  "maintenanceEventId": "f879562c-b912-44e9-a592-71d3aef09afb",
  "status": "SCHEDULED"
}
```

`status` presumably uses the lifecycle vocabulary: `SCHEDULED`, `IN_PROGRESS`,
`COMPLETE`, `CANCELLED`.

### Event lifecycle (implied)

`SCHEDULED → IN_PROGRESS → COMPLETE`, with `SCHEDULED → CANCELLED` allowed only
before start.

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

1. Error response body shape and error-code catalog (only success codes were visible).
2. **Partially answered 2026-08-06** — a status read keyed by `maintenanceEventId`
   exists (see §3.5). Still to confirm with EWS: the exact path form, the response
   body schema, the status vocabulary, error codes (404 for unknown id?), and
   whether a **list** endpoint also exists.
3. Does `idempotency-id` apply to `start`/`complete`/`cancel`, or only `schedule`?
4. Must `request-id` be unique per attempt (i.e., new value on retry) while
   `idempotency-id` stays constant?
5. **Partially answered 2026-08-11** — the auth server URLs and `aud` values are
   confirmed (see §4), modulo the `.com`/`.io` CAT discrepancy noted there. Still
   open: whether mTLS is required on the token endpoint and/or the API endpoints,
   and completion of our client registration (client_id, public key, kid).
6. Rate limits / concurrency limits, and clock-skew tolerance on JWT claims.
7. What the `schedule` response body contains (presumably `maintenanceEventId`) and
   whether scheduling constraints exist (lead time, max window length, overlap rules).
