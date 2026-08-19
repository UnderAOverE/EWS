# Zelle Facade — API Testing Cookbook

> Request payloads and expected outcomes for every consumer-facing endpoint, one scenario
> per test. `{BASE}` is the host app root (for example
> `http://<host>:9000/fdn-c-amp-fapis-py`). Shift dates forward when testing later —
> "in-window" means inside EWS's allowed maintenance hours: **11:00 PM to 5:00 AM CST /
> 12:00 AM to 6:00 AM CDT** (the same 05:00 to 11:00 UTC band year-round).

## Common headers

| Header | Value | Notes |
|---|---|---|
| `Content-Type` | `application/json` | POST bodies only |
| `x-client-id` | `eamp-selfservice` | required; allowlist-checked when configured |
| `sm-user` | `sr87813` | SSO username; drives contact enrichment + email recipient |
| `X-Correlation-Id` | any string | optional; echoed back, minted when absent |
| `Idempotency-Key` | any string | schedule only, optional; enables safe replay |
| `X-Confirm-Ticket` | the event's `ticketNumber` | lifecycle verbs only, required |

## A. Schedule — `POST {BASE}/v1/maintenance-events`

### A1. Happy path, EMERGENCY_SCHEDULED (about 2 days out, in-window)

Expect **201**, `status: SCHEDULED`, notification email received.

```json
{
  "startTime": "2026-08-21T01:00:00-05:00",
  "endTime": "2026-08-21T03:00:00-05:00",
  "ticketNumber": "INC1000001",
  "reason": "EmSch happy path",
  "holdMode": "EWS_HOLD"
}
```

### A2. MAINTENANCE_SCHEDULED (30 days out)

Expect **201**; EWS classifies it as planned maintenance (more than 15 days of lead).

```json
{
  "startTime": "2026-09-18T01:00:00-05:00",
  "endTime": "2026-09-18T02:00:00-05:00",
  "ticketNumber": "INC1000002",
  "reason": "Planned window",
  "holdMode": "SELF_HOLD"
}
```

### A3. EMERGENCY_IMMEDIATE (start about now + 10 minutes, any time of day)

Expect **201**; the facade skips the window and lead gates; EWS requires the start to be
within about 15 minutes of submission.

```json
{
  "startTime": "<now + 10 min, e.g. 2026-08-19T14:30:00-05:00>",
  "endTime": "<start + 30 min>",
  "ticketNumber": "INC1000003",
  "reason": "Incident hold",
  "holdMode": "EWS_HOLD",
  "emergencyImmediateStart": true
}
```

### A4. Off-window rejection (facade fail-fast)

Expect **422** naming the allowed hours; the server log shows NO southbound call.

```json
{
  "startTime": "2026-08-21T14:00:00-05:00",
  "endTime": "2026-08-21T16:00:00-05:00",
  "ticketNumber": "INC1000004",
  "reason": "Daytime should fail",
  "holdMode": "EWS_HOLD"
}
```

### A5. Past start

Any `startTime` in the past. Expect **422**; the message prints the requested instant and
"now" in UTC (catches the Z-vs-offset confusion).

### A6. Inverted window

`endTime` at or before `startTime`. Expect **422** "endTime must be after startTime".

### A7. Idempotency replay

Send the SAME body twice with header `Idempotency-Key: demo-key-1` (use a fresh window and
ticket, for example Aug 22). First call **201**; second call returns the SAME response, no
second EWS call, no second event.

### A8. Idempotency key abuse

Same `Idempotency-Key: demo-key-1` but change `reason`. Expect **409** "already used with a
different request body".

### A9. Overlap guardrail

Schedule A1's exact window again (no key). Expect **409** naming the overlapping event id.
Add `"allowOverlap": true` to the body to bypass; expect **201**.

### A10. Directory fallback behavior

- `sm-user: ghost99` (unknown user): expect **201**; the email goes to the configured
  default contact address and both the email and the audit detail carry the
  "not found in GlobalDirectory" note.
- Omit `sm-user` entirely: expect **201**; defaults used; "no Sm-User header" note.

## B. Lifecycle — `POST {BASE}/v1/maintenance-events/{eventId}/start|complete|cancel`

Empty body (`-d ''`). Headers: `x-client-id`, `sm-user`, and
`X-Confirm-Ticket: <the event's ticketNumber>`.

| # | Call | Expect |
|---|---|---|
| B1 | `.../start?dry_run=true` | **200**, status unchanged, audit outcome DRY_RUN, email says DRY_RUN, no EWS call |
| B2 | `.../start` (within 6 hours of the scheduled start, either side) | **200**, `IN_PROGRESS`; EWS begins holding MQ traffic |
| B3 | `.../start` with `X-Confirm-Ticket: WRONG` | **409** ticket mismatch |
| B4 | `.../start` again after B2 | **409** "requires SCHEDULED" |
| B5 | `.../complete` after B2 | **200**, `COMPLETE`; held messages released |
| B6 | `.../cancel` on a still-SCHEDULED event | **200**, `CANCELLED`; on an IN_PROGRESS event: **409** |
| B7 | `.../start` more than 6 hours early or late | EWS rejects; translated **422** "within six (6) hours of its scheduled start time" |

## C. Reads

| # | Call | Expect |
|---|---|---|
| C1 | `GET {BASE}/v1/maintenance-events` and `?status=SCHEDULED` | **200**, local list (no EWS call) |
| C2 | `GET {BASE}/v1/maintenance-events/{eventId}` | **200**, one event, local view |
| C3 | `GET {BASE}/v1/maintenance-events/{eventId}/upstream-status` | **200**, live southbound read; compare `localStatus: SCHEDULED` with `upstreamStatus: NOT_STARTED` |
| C4 | `GET {BASE}/v1/maintenance-events/queue-depths` | **200**, live held-message counts per queue; run during an EWS_HOLD window and watch counts climb, then drain after complete |

## D. Admin resolve — `POST {BASE}/v1/admin/maintenance-events/{eventId}/resolve`

Only for events in `UNCERTAIN` or `PENDING_UPSTREAM_ID`. Expect **200**, event unstuck,
attestation preserved in audit, notification email sent.

```json
{
  "actualStatus": "SCHEDULED",
  "attestation": "Verified with EWS NOC ref 4471",
  "ewsEventId": "<EWS maintenanceEventId; required when resolving PENDING_UPSTREAM_ID>"
}
```

## Suggested end-to-end run

A1 (schedule) → B1 (dry-run) → B2 (start) → C3 (upstream check) → C4 (queue depths) →
B5 (complete). That exercises scheduling, enrichment, both gates, the real EWS lifecycle,
both live reads, the audit trail, and five notification emails.

## Quick term mapping (for reading logs and emails)

| Facade (northbound) | EWS (southbound) |
|---|---|
| `eventId` | `maintenanceEventId` |
| `startTime` / `endTime` | `scheduledStartDate` / `scheduledEndDate` (UTC `...Z` strings) |
| `holdMode` | `ewsHold` |
| `SCHEDULED` | `NOT_STARTED` |
| `IN_PROGRESS` | `IN_PROGRESS` (queues held) |
| `COMPLETE` | `COMPLETE` (or `PRE_COMPLETE` during a partial test release) |
| `CANCELLED` | `CANCELLED` |
| `PENDING_UPSTREAM_ID` / `UNCERTAIN` / `FAILED` | facade-only bookkeeping states |
| (never shown) | `NO_SHOW` (EWS expired an unstarted event) |
