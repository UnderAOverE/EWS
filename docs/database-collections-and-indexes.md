# Database — collections and indexes to create

Everything the Zelle facade needs in MongoDB, and **why**. The running
application **never creates indexes** (it does no DDL on startup), so these
must be created **once, by hand or by your DBA**, before the service serves
traffic — and again only if this list changes.

- **Where they live:** the host application's MongoDB database, selected by the
  `DatabasesCollections` constant (`APPLICATION_MAIN_DATABASE = "fdn-c-amp-fapis-py"`).
  The facade uses the host's injected Motor **client** and reads the DB +
  collection names from `common/constants.py` — no configurable prefix.
- **Names** (all in `DatabasesCollections`): `zelle_events`, `zelle_idempotency`,
  `zelle_audit`, `zelle_leases`.
- **`_id`:** each document's `_id` is an **auto ObjectId** (base-repository
  convention). The meaningful keys — `event_id` and the lease `name` — are
  **regular unique-indexed fields**, not the `_id`.
- **Idempotent:** `createIndex` is a no-op if the index already exists, so
  re-running the commands below is safe.

---

## TL;DR — the commands

Run these once in `mongosh` against the `fdn-c-amp-fapis-py` database:

```javascript
// 1) zelle_events — one document per maintenance event
db.zelle_events.createIndex({ event_id: 1 }, { unique: true })   // ⚠ the facade id
db.zelle_events.createIndex({ status: 1 })
db.zelle_events.createIndex({ scheduled_start: 1, scheduled_end: 1 })

// 2) zelle_idempotency — schedule replay ledger
db.zelle_idempotency.createIndex({ client_id: 1, key: 1 }, { unique: true })

// 3) zelle_audit — append-only INTENT/OUTCOME trail
db.zelle_audit.createIndex({ event_id: 1 })
db.zelle_audit.createIndex({ ts: 1 })

// 4) zelle_leases — watchdog singleton lock
db.zelle_leases.createIndex({ name: 1 }, { unique: true })       // ⚠ one lease per name
db.zelle_leases.createIndex({ expires_at: 1 }, { expireAfterSeconds: 0 })
```

You do **not** create the collections themselves or their `_id` indexes —
MongoDB makes the collection on first write and always indexes `_id`
automatically.

---

## The four collections

### 1. `zelle_events` — the maintenance events

**What it holds:** one document per maintenance window — its status, the
scheduled start/end, the ticket number, hold mode, the EWS event id (once
known), and timestamps. `_id` is an auto ObjectId; the facade's `event_id`
(uuid4) is a **unique-indexed field** consumers key on. This is the source of
truth for `GET` reads and the state machine.

| Index | Why it's needed |
|---|---|
| `{ event_id: 1 }` **unique** | The facade id every read and every atomic state-machine transition filters on. The **unique** constraint guarantees one event per id. |
| `{ status: 1 }` | Listing/filtering events by status (`GET ?status=SCHEDULED`) and the startup sweep + watchdog scans query by status. Without it those become full-collection scans. |
| `{ scheduled_start: 1, scheduled_end: 1 }` | Overlap detection at schedule time — "does this window collide with an existing one?" queries a range on both fields. Without it every schedule scans the whole collection. |

**Criticality:** the unique `event_id` index is **correctness-adjacent** (reads
and transitions rely on it); the other two are performance. The state machine
itself is guarded atomically by the `find_one_and_update` expected-status
filter.

### 2. `zelle_idempotency` — the schedule replay ledger

**What it holds:** one document per `(client_id, Idempotency-Key)` — a body
fingerprint, the event id it created, a status (`pending`/`succeeded`/
`failed`), and the stored response to replay.

| Index | Why it's needed |
|---|---|
| `{ client_id: 1, key: 1 }` **unique** | **This is the correctness guarantee.** The unique constraint is what makes a duplicate schedule (a retry, a double-click) *lose the race* deterministically — the second insert is rejected, the caller replays the first result instead of booking a second maintenance window. Without the **unique** flag, retries would create duplicate events and double-book EWS. |

**Criticality:** ⚠️ **required for correctness.** This is the single most
important index in the system. Note the `{ unique: true }` — a plain index
here would not enforce anything.

### 3. `zelle_audit` — the append-only compliance trail

**What it holds:** two documents per southbound attempt (an `INTENT` before
the EWS call, an `OUTCOME` after) sharing an `attempt_id`. Never updated or
deleted — it's your forensic record of who did what and how EWS responded.

| Index | Why it's needed |
|---|---|
| `{ event_id: 1 }` | Pull the full audit history for one event during an incident or reconciliation. |
| `{ ts: 1 }` | Time-ordered scans of the trail (e.g. "everything in this window"). |

**Criticality:** performance / operability. The audit trail is written and
read correctly without these, but investigations get slow as the trail grows.

### 4. `zelle_leases` — the watchdog singleton lock

**What it holds:** lease documents with a unique `name` field (e.g.
`zelle-watchdog`) so that when the watchdog is enabled and you run more than
one replica, only one replica actually scans and pages. Only used when
`ZELLE_WATCHDOG_ENABLED=true`.

| Index | Why it's needed |
|---|---|
| `{ name: 1 }` **unique** | One lease per name. The acquire race upserts against this unique constraint — the loser collides and is reported "not acquired", so exactly one replica holds the lease. |
| `{ expires_at: 1 }` **TTL, `expireAfterSeconds: 0`** | A **TTL index** — Mongo auto-deletes a lease document once `expires_at` passes. This is how an abandoned lease (a replica that died mid-hold) gets garbage-collected so another replica can take over. Without it, a crashed holder's lease would linger and could block the watchdog. |

**Criticality:** ⚠️ **required if the watchdog is enabled** — both the unique
`name` (the singleton guarantee) and the TTL (GC). Otherwise the collection is
unused.

---

## Summary

| Collection | Index | Type | Must-have? |
|---|---|---|---|
| `zelle_events` | `{event_id}` | **unique** | **yes — reads/transitions** |
| `zelle_events` | `{status}` | plain | performance |
| `zelle_events` | `{scheduled_start, scheduled_end}` | plain (compound) | performance |
| `zelle_idempotency` | `{client_id, key}` | **unique** | **yes — correctness** |
| `zelle_audit` | `{event_id}` | plain | performance |
| `zelle_audit` | `{ts}` | plain | performance |
| `zelle_leases` | `{name}` | **unique** | **yes if watchdog on** |
| `zelle_leases` | `{expires_at}` | **TTL (0s)** | **yes if watchdog on** |

The **unique** indexes (`zelle_events.event_id`, `zelle_idempotency` compound,
`zelle_leases.name`) and the TTL are the ones that affect *correctness*. The
rest are for query performance and should still be created for any
production-scale deployment.

*The exact index definitions live in each repository's `ensure_indexes()`
method under [src/apis/repositories/zelle/](../src/apis/repositories/zelle/) —
that code is the source of truth if this doc and the code ever drift.*
