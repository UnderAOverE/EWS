# Consumer Integration Guide — Zelle Maintenance Events API

*Hand this doc to consumer teams (the UI, ops tooling) as-is. It deliberately
never mentions the upstream vendor, its field names, or how the facade
authenticates southbound — consumers don't need any of that. The deeper
internal picture lives in [how-it-all-works.md](how-it-all-works.md).*

---

## What this API does

This API books and controls **Zelle maintenance windows**. Scheduling a
window is harmless bookkeeping; **starting** one begins holding live Zelle
payment traffic until you **complete** it. Treat start/complete/cancel as
production-impacting actions — the API's guardrails assume you will.

There is no login. The API is reachable on the internal network only; you
identify yourself with a header (`X-Client-Id`) that must be on the
service's allowlist. Everything is JSON.

---

## Endpoints

All paths are relative to wherever the host app mounts the API (`$BASE`).

| What you want to do | Call this |
|---|---|
| Schedule a maintenance window | `POST /v1/maintenance-events` |
| Start a scheduled window (holds begin) | `POST /v1/maintenance-events/{eventId}/start` |
| Complete a running window (holds release) | `POST /v1/maintenance-events/{eventId}/complete` |
| Cancel a window that hasn't started | `POST /v1/maintenance-events/{eventId}/cancel` |
| List events (optionally `?status=`) | `GET /v1/maintenance-events` |
| Look up one event | `GET /v1/maintenance-events/{eventId}` |
| Check the live status at the source | `GET /v1/maintenance-events/{eventId}/upstream-status` |

(There is also an `/v1/admin/...` surface for the operations team. It is not
for consumers — don't call it.)

## Headers

| Header | When | Meaning |
|---|---|---|
| `X-Client-Id` | **Every call** | Your assigned identity string. Allowlist-checked. |
| `Idempotency-Key` | Schedule — **send it always** | Makes retries safe; see the rule below. |
| `X-Confirm-Ticket` | **Required** on start / complete / cancel | Typed "are you sure": must exactly equal the event's `ticketNumber` (your ServiceNow ticket), or the call is rejected with a 409. Case-sensitive. |
| `X-Correlation-Id` | Optional, any call | Trace id. Echoed back on every response (minted for you if omitted). Quote it in support tickets. |

---

## Scheduling a window

```jsonc
// POST /v1/maintenance-events
// Headers: X-Client-Id: <you>   Idempotency-Key: <uuid, see below>
{
  "startTime": "2026-08-12T06:00:00Z",     // required — timezone-aware, not in the past
  "endTime":   "2026-08-12T08:00:00Z",     // required — must be after startTime
  "ticketNumber": "CHG0031234",            // required, 1–36 chars — your ServiceNow ticket
  "reason": "Quarterly DB failover drill"  // required, 1–255 chars
}
```

`holdMode`, `allowOverlap`, `suppressDuplicatePayments`, and
`networkNotificationId` also exist as optional fields; the defaults are
almost always what you want — ask the facade team before setting them.

**Datetimes.** Send ISO-8601 with an explicit timezone — UTC with a `Z`
suffix is strongly preferred (`2026-08-12T06:00:00Z`). A value with no
timezone is rejected with a 422. If you must send local time, generate the
offset from a real tz library (`America/New_York`) — never hardcode
`-05:00`, or DST will silently shift your window by an hour. All responses
come back in UTC regardless of what you sent.

**Ticket number.** Put the ServiceNow change/incident number in
`ticketNumber`. You will need to repeat it verbatim in `X-Confirm-Ticket`
on every start/complete/cancel — that's the "type the name to confirm"
safety catch.

**What comes back.** A `201` (or `202` — see statuses) with the event view:

```jsonc
{
  "eventId": "9f2c…",        // save this — it's the handle for every follow-up call
  "status": "SCHEDULED",
  "startTime": "2026-08-12T06:00:00Z",
  "endTime": "2026-08-12T08:00:00Z",
  "ticketNumber": "CHG0031234",
  "reason": "Quarterly DB failover drill",
  "holdMode": "SELF_HOLD",
  "correlationId": "c-3b1e…",
  "createdAt": "2026-08-04T14:03:00Z",
  "lastConfirmedUpstreamAt": "2026-08-04T14:03:01Z"  // may be null
}
```

Lost the `eventId`? `GET /v1/maintenance-events` and find your event by its
`ticketNumber`.

## The Idempotency-Key rule — one button click = one key

The key exists so a retry can never book a **second** window. It only works
if you use it correctly:

1. When the user clicks **Schedule**, generate a key right there —
   `crypto.randomUUID()` — and hold it with the form data.
2. Send it as the `Idempotency-Key` header on the POST.
3. On a timeout, network error, or 5xx: **retry with the same key and the
   same body.** Don't regenerate. The service recognizes the key and
   returns the original result instead of creating a duplicate.
4. On success, throw the key away. A later schedule — even an
   identical-looking one — is a new click, new key.
5. If the user edits the form and resubmits, that's a new request → new
   key. An old key with a changed body is rejected (409) on purpose.
6. Still disable the button while a request is in flight. The key makes
   double-submits *harmless*, not *invisible*.

Mental model: the key identifies **one press of the button**. Retries of
that press share it; everything else gets a fresh one.

---

## Start / complete / cancel

```
POST /v1/maintenance-events/{eventId}/start
X-Client-Id: <you>
X-Confirm-Ticket: CHG0031234        ← must equal the event's ticketNumber exactly
```

Same shape for `/complete` and `/cancel`. Rules:

- The event must be in the right state: `start` needs `SCHEDULED`,
  `complete` needs `IN_PROGRESS`, `cancel` needs `SCHEDULED`. Anything else
  is a 409.
- A wrong or missing `X-Confirm-Ticket` is a 409. The match is exact and
  case-sensitive — send the ticket verbatim as scheduled.
- **Rehearse with `?dry_run=true`.** It runs every check (identity, ticket
  match, state) and records the attempt in the audit trail, but changes
  nothing and touches nothing live. A dry run that passes means the real
  call would have been allowed at that moment.

## Statuses

| Status | What it means for you |
|---|---|
| `PENDING` | Mid-creation. Transient — you'll rarely see it. |
| `PENDING_UPSTREAM_ID` | Accepted, but confirmation is still pending — you got a **202** on schedule. An operator reconciles it; poll `GET` until it's `SCHEDULED`. |
| `SCHEDULED` | Booked. You can `start` or `cancel`. |
| `IN_PROGRESS` | Started — payment holds are active. You can `complete`. |
| `COMPLETE` | Finished cleanly. Terminal. |
| `CANCELLED` | Called off before starting. Terminal. |
| `UNCERTAIN` | The service isn't sure the last action landed. **All actions are blocked** until an operator resolves it. Safety stop, not a bug — contact the facade team; don't retry. |
| `FAILED` | The schedule never took. Terminal — schedule again with a fresh key. |

**Reads show last-known state**, not a live upstream query. `GET` is fast
and cheap but reflects intent as of the last confirmed action.

**The exception — the live status check.** `GET /{eventId}/upstream-status`
queries the source of truth in real time and returns both views side by side:

```jsonc
{
  "eventId": "9f2c…",
  "localStatus": "IN_PROGRESS",     // what the service has on record
  "upstreamStatus": "IN_PROGRESS",  // what the source reports right now (may be null)
  "checkedAt": "2026-08-06T14:02:11Z",
  "correlationId": "c-3b1e…"
}
```

It changes nothing — pure read. Use it deliberately (each call is a real
request to the upstream system, slower and rate-limited): pre-flighting a
start, investigating a stuck event, or a "refresh from source" button. Use
the plain `GET /{eventId}` for routine polling. A `409` means the event has
no upstream id yet (a `202`-scheduled event still being reconciled).

## Errors — one shape, always

```json
{
  "error": {
    "code": "UPSTREAM_UNAVAILABLE",
    "message": "The maintenance service is temporarily unreachable.",
    "correlationId": "c-3b1e…",
    "retryable": true
  }
}
```

Branch on `code` and `retryable` — never on the message text.

| Code | HTTP | Retryable | Rough meaning |
|---|---|---|---|
| `VALIDATION_FAILED` | 400/422 | no | Malformed request. Fix and resend (fresh key). |
| `CONFLICT` | 409 | no | Wrong state, ticket mismatch, overlap, or key reuse with a different body. |
| `FORBIDDEN_ACTION` | 403 | no | Your `X-Client-Id` isn't allowed to do that. |
| `NOT_FOUND` | 404 | no | No such event. |
| `UPSTREAM_REJECTED` | 502 | no | Rejected upstream. Fix the request; don't just retry. |
| `UPSTREAM_UNAVAILABLE` | 503 | **yes** | Temporarily unreachable. Retry later (same key on schedule). |
| `RATE_LIMITED` | 503 | **yes** | Slow down; honor the `Retry-After` header. |
| `UPSTREAM_UNCERTAIN` | 502 | no | Outcome unknown — needs an operator, not a retry. |

---

## Quick start — the whole flow in curl

```bash
BASE=https://internal.host.example/api   # wherever the host app mounts the API

# 1) Schedule (save eventId from the response; the key makes retries safe)
curl -sS -X POST "$BASE/v1/maintenance-events" \
  -H "X-Client-Id: payments-ops" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"startTime":"2026-08-12T06:00:00Z","endTime":"2026-08-12T08:00:00Z",
       "ticketNumber":"CHG0031234","reason":"Quarterly DB failover drill"}'

# 2) Rehearse the start (no state change, no live impact)
curl -sS -X POST "$BASE/v1/maintenance-events/$EVENT_ID/start?dry_run=true" \
  -H "X-Client-Id: payments-ops" -H "X-Confirm-Ticket: CHG0031234"

# 3) Start for real (holds begin)
curl -sS -X POST "$BASE/v1/maintenance-events/$EVENT_ID/start" \
  -H "X-Client-Id: payments-ops" -H "X-Confirm-Ticket: CHG0031234"

# 4) Complete (holds release)
curl -sS -X POST "$BASE/v1/maintenance-events/$EVENT_ID/complete" \
  -H "X-Client-Id: payments-ops" -H "X-Confirm-Ticket: CHG0031234"

# Check on it any time
curl -sS "$BASE/v1/maintenance-events/$EVENT_ID" -H "X-Client-Id: payments-ops"
```

*Questions, allowlist additions, or an `UNCERTAIN` event to unstick:
contact the facade team with the `correlationId` from the response.*
