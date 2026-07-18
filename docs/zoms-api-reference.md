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

### Event lifecycle (implied)

`SCHEDULED → IN_PROGRESS → COMPLETE`, with `SCHEDULED → CANCELLED` allowed only
before start.

## 4. OAuth2 access token flow

> ⚠️ The token endpoint URLs below come from a companion summary that cites **Paze**
> (also operated by EWS) documentation — confirm with the EWS team that the same
> auth server / audience applies to ZOMS, or get the ZOMS-specific values.

| Environment | Token endpoint |
|---|---|
| CAT | `https://auth.wallet.cat.earlywarning.io/token` |
| PROD | `https://auth.wallet.earlywarning.com/token` |

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
| `aud` | The authorization server URL (e.g. `https://auth.wallet.cat.earlywarning.io`) |
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
2. Is there a **read/status** endpoint (e.g. `GET /v1/events/{id}` or a list) to query
   event state, or is state only known from responses to the four POSTs?
3. Does `idempotency-id` apply to `start`/`complete`/`cancel`, or only `schedule`?
4. Must `request-id` be unique per attempt (i.e., new value on retry) while
   `idempotency-id` stays constant?
5. Confirm the ZOMS auth server URL, `aud` value, and whether mTLS is required on the
   token endpoint and/or the API endpoints (the URLs in §4 are from Paze docs).
6. Rate limits / concurrency limits, and clock-skew tolerance on JWT claims.
7. What the `schedule` response body contains (presumably `maintenanceEventId`) and
   whether scheduling constraints exist (lead time, max window length, overlap rules).
