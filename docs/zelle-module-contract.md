# Zelle Module — Implementation Contract

> Binding contract for implementing the `zelle` bounded context. Read together
> with [architecture.md](architecture.md) (behavior),
> [zoms-api-reference.md](zoms-api-reference.md) (vendor wire truth), and
> [../CLAUDE.md](../CLAUDE.md) (file template — every rule applies to every
> Python file, including `__init__.py`). Where this contract pins a name or
> signature, use it exactly — other files are written against it in parallel.

## Ground rules

- Imports are rooted at `src.apis.` and `src.fake_ews.`, matching the host
  app's convention exactly (confirmed 2026-07-18 from the host `main.py` /
  `Clusters.py`: internal modules import via the `src.` prefix). Example:
  `from src.apis.models.zelle.enums import EventStatus`. The repo root is on
  the path via `pytest.ini`.
- **Never import `common.*`** — those modules exist only in the host app
  (`fdn-c-amp-fapis-py`). The host's `httpx.AsyncClient` and Motor database
  are **injected** through `apis/dependencies/zelle.py`.
- Async end-to-end: Motor (`motor.motor_asyncio`), `httpx.AsyncClient`,
  `async def` everywhere I/O happens.
- Logging: stdlib `logging` — module-level `LOGGER = logging.getLogger(__name__)`
  under `# Local variables`. Never `print()`. Never log tokens, assertions,
  `Authorization` headers, or unmasked contact PII (mask emails `j***@x.com`,
  phones `99*******7`).
- All datetimes tz-aware UTC (`datetime.now(timezone.utc)`). Motor returns
  naive UTC datetimes unless the client is `tz_aware=True` — repositories
  defensively attach `timezone.utc` to naive datetimes on read.
- No retry library — retry loops are hand-rolled per the resilience matrix
  (small, explicit, auditable). Documented deviation from the architecture
  doc's tenacity mention.
- Line length target ≤ 100 chars to match the banner width.

## File map (owner slices)

```
src/apis/__init__.py                          (slice A)
src/apis/config/__init__.py                   (slice A)
src/apis/config/zelle.py                      (slice A)  ZelleSettings
src/apis/models/__init__.py                   (slice A)
src/apis/models/zelle/__init__.py             (slice A)
src/apis/models/zelle/enums.py                (slice A)
src/apis/models/zelle/errors.py               (slice A)
src/apis/models/zelle/northbound.py           (slice A)
src/apis/models/zelle/southbound.py           (slice A)
src/apis/models/zelle/records.py              (slice A)
src/apis/repositories/__init__.py             (slice B)
src/apis/repositories/zelle/__init__.py       (slice B)
src/apis/repositories/zelle/events.py         (slice B)  EventsRepository
src/apis/repositories/zelle/idempotency.py    (slice B)  IdempotencyRepository
src/apis/repositories/zelle/audit.py          (slice B)  AuditRepository
src/apis/repositories/zelle/leases.py         (slice B)  LeaseRepository
src/apis/services/__init__.py                 (slice C)
src/apis/services/zelle/__init__.py           (slice C)
src/apis/services/zelle/token_broker.py       (slice C)  CircuitBreaker, TokenBroker
src/apis/services/zelle/zoms_client.py        (slice C)  ZomsClient
src/apis/services/zelle/event_service.py      (slice D)  EventService
src/apis/services/zelle/watchdog.py           (slice D)  Watchdog
src/apis/routes/__init__.py                   (slice E)
src/apis/routes/zelle/__init__.py             (slice E)
src/apis/routes/zelle/events.py               (slice E)  events_router
src/apis/routes/zelle/admin.py                (slice E)  admin_router
src/apis/dependencies/__init__.py             (slice E)
src/apis/dependencies/zelle.py                (slice E)  ZelleRuntime, register_zelle
src/fake_ews/__init__.py                      (slice F)
src/fake_ews/app.py                           (slice F)  create_fake_ews_app
requirements.txt                              (slice F)
pytest.ini                                    (slice F)
```

## Slice A — config + models

### `apis/config/zelle.py`

```python
class ZelleSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ZELLE_", env_nested_delimiter="__")

    environment: Literal["fake", "cat", "prod"] = "fake"
    api_base_url: str                     # e.g. https://api.zelle.cat.earlywarning.io/zoms
    token_url: str                        # e.g. https://auth.wallet.cat.earlywarning.io/token
    token_aud: str                        # explicit config, never derived
    token_scope: str = "maintenance-event"
    client_id: SecretStr
    signing_kid: str                      # must match the JWKS entry registered with EWS
    signing_key_path: Path
    # org constants injected into every schedule payload
    org_id: str                           # Annotated len 3..3
    participant_name: str                 # 1..50
    submitted_name: str                   # 1..50
    contact_name: str                     # 1..128
    contact_phone: str                    # 9..12
    contact_email: str                    # 1..255
    default_hold_mode: HoldMode = HoldMode.SELF_HOLD
    # guardrails
    client_allowlist: list[str] = []                # empty list = allow any (dev only)
    lifecycle_client_allowlist: list[str] = []      # empty list = same as client_allowlist
    # timeouts / broker
    token_connect_timeout_seconds: float = 3.0
    token_read_timeout_seconds: float = 7.0
    api_connect_timeout_seconds: float = 3.0
    api_read_timeout_seconds: float = 10.0
    breaker_failure_threshold: int = 5
    breaker_reset_seconds: float = 30.0
    # watchdog
    watchdog_enabled: bool = False
    watchdog_interval_seconds: float = 60.0
    watchdog_grace_seconds: float = 900.0
    # mongo
    mongo_collection_prefix: str = "zelle"
```

### `apis/models/zelle/enums.py` — all `StrEnum`

- `EventStatus`: `PENDING`, `PENDING_UPSTREAM_ID`, `SCHEDULED`, `IN_PROGRESS`,
  `COMPLETE`, `CANCELLED`, `UNCERTAIN`, `FAILED`
- `HoldMode`: `EWS_HOLD`, `SELF_HOLD`
- `ErrorCode`: `VALIDATION_FAILED`, `CONFLICT`, `FORBIDDEN_ACTION`, `NOT_FOUND`,
  `UPSTREAM_REJECTED`, `UPSTREAM_UNAVAILABLE`, `RATE_LIMITED`, `UPSTREAM_UNCERTAIN`
- `LifecycleAction`: `START`, `COMPLETE`, `CANCEL`
- `AuditKind`: `INTENT`, `OUTCOME`
- `AuditOutcome`: `SUCCESS`, `REJECTED`, `UNAVAILABLE`, `UNCERTAIN`, `REPLAYED`, `DRY_RUN`

### `apis/models/zelle/errors.py`

```python
class ErrorDetail(BaseModel):   # camelCase aliases
    code: ErrorCode
    message: str
    correlation_id: str         # alias correlationId
    retryable: bool

class ErrorEnvelope(BaseModel):
    error: ErrorDetail

class ZelleFacadeError(Exception):
    # attributes: code: ErrorCode, status_code: int, message: str,
    #             retryable: bool, retry_after_seconds: float | None = None
    ...

class ConflictError(ZelleFacadeError)            # 409, CONFLICT, retryable=False
class ForbiddenActionError(ZelleFacadeError)     # 403, FORBIDDEN_ACTION
class NotFoundError(ZelleFacadeError)            # 404, NOT_FOUND
class UpstreamRejectedError(ZelleFacadeError)    # 502, UPSTREAM_REJECTED
class UpstreamUnavailableError(ZelleFacadeError) # 503, UPSTREAM_UNAVAILABLE, retryable=True
class RateLimitedError(ZelleFacadeError)         # 503, RATE_LIMITED, retryable=True
class UpstreamUncertainError(ZelleFacadeError)   # 502, UPSTREAM_UNCERTAIN
class AuthConfigError(ZelleFacadeError)          # 502, UPSTREAM_REJECTED — key/config incident

def zelle_exception_handler(request, exc: ZelleFacadeError) -> JSONResponse
    # builds ErrorEnvelope (correlation id from request.state.correlation_id,
    # falls back to "unknown"), sets Retry-After header when retry_after_seconds
```

### `apis/models/zelle/northbound.py` — camelCase wire

All models: `model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)`;
serialize with `model_dump(mode="json", by_alias=True)`.

```python
class ScheduleEventRequest(BaseModel):
    start_time: datetime            # must be tz-aware; validator rejects naive
    end_time: datetime
    ticket_number: str              # 1..36, required (bank change control)
    reason: str                     # 1..255
    hold_mode: HoldMode | None = None       # None -> settings.default_hold_mode
    allow_overlap: bool = False
    suppress_duplicate_payments: bool | None = None
    network_notification_id: str | None = None      # 1..36
    # model_validator: end_time > start_time; start_time not in the past
    # (allow 5-minute grace for clock skew)

class MaintenanceEventResponse(BaseModel):
    event_id: str
    status: EventStatus
    start_time: datetime
    end_time: datetime
    ticket_number: str
    reason: str
    hold_mode: HoldMode
    correlation_id: str
    created_at: datetime
    last_confirmed_upstream_at: datetime | None

class EventListResponse(BaseModel):
    events: list[MaintenanceEventResponse]

class ResolveRequest(BaseModel):
    actual_status: EventStatus       # allowed: SCHEDULED, IN_PROGRESS, COMPLETE, CANCELLED, FAILED
    attestation: str                 # 1..500, e.g. "EWS NOC ref 4471"
    ews_event_id: str | None = None  # required when resolving PENDING_UPSTREAM_ID
```

### `apis/models/zelle/southbound.py` — EWS wire (see zoms-api-reference.md)

```python
def format_ews_datetime(value: datetime) -> str
    # "%Y-%m-%dT%H:%M:%S." + milliseconds(3) + "Z", always UTC.
    # pydantic's default "+00:00" form is a likely silent CAT 400 — this
    # serializer is the only way datetimes reach the wire.

class EwsScheduleRequest(BaseModel)   # aliases: orgId, participantName, submittedName,
    # contactName, contactPhone, contactEmail, scheduledStartDate, scheduledEndDate,
    # ewsHold, suppressDuplicatePayments, ticketNumber, networkNotificationId.
    # scheduled dates are str (pre-formatted via format_ews_datetime).

class EwsLifecycleRequest(BaseModel)  # maintenanceEventId

class EwsScheduleResponse(BaseModel)  # LENIENT: model_config extra="allow";
    # maintenance_event_id: str | None (alias maintenanceEventId) — the 201
    # body shape is unconfirmed (open question #2).
```

### `apis/models/zelle/records.py` — internal persistence shapes

```python
class EventRecord(BaseModel):
    event_id: str                    # facade id, uuid4 str — Mongo _id
    ews_event_id: str | None
    status: EventStatus
    idempotency_id: str              # uuid4 sent to EWS on schedule (+retries)
    client_id: str
    ticket_number: str
    reason: str
    hold_mode: HoldMode
    scheduled_start: datetime
    scheduled_end: datetime
    payload_snapshot: dict[str, Any]     # the EWS request body as sent (PII: audit only)
    last_confirmed_upstream_at: datetime | None
    created_at: datetime
    updated_at: datetime

class IdempotencyRecord(BaseModel):
    client_id: str
    key: str
    body_hash: str                   # sha256 hex of canonical northbound JSON
    event_id: str
    status: Literal["pending", "succeeded", "failed"]
    response_snapshot: dict[str, Any] | None
    response_status_code: int | None
    created_at: datetime

class AuditRecord(BaseModel):
    attempt_id: str                  # links INTENT and OUTCOME documents
    kind: AuditKind
    ts: datetime
    actor_client_id: str
    correlation_id: str
    event_id: str
    action: str                      # "schedule" | "start" | "complete" | "cancel" | "resolve"
    ews_request_ids: list[str]
    outcome: AuditOutcome | None     # OUTCOME docs only
    http_status: int | None
    detail_redacted: str | None
```

## Slice B — repositories (Motor)

Every repository takes `database: AsyncIOMotorDatabase` and `collection_prefix: str`
(collections: `{prefix}_events`, `{prefix}_idempotency`, `{prefix}_audit`,
`{prefix}_leases`). Each exposes `async def ensure_indexes(self) -> None`.
**Audit is append-only: `AuditRepository` has NO update/delete methods — intent
and outcome are separate inserted documents sharing `attempt_id`.**

```python
class EventsRepository:
    async def ensure_indexes(self) -> None           # status; (scheduled_start, scheduled_end)
    async def create(self, record: EventRecord) -> None
    async def get(self, event_id: str) -> EventRecord | None
    async def list_events(self, status: EventStatus | None = None, limit: int = 100) -> list[EventRecord]
    async def transition(
        self, event_id: str, expected: tuple[EventStatus, ...], new_status: EventStatus,
        *, ews_event_id: str | None = None, confirmed_upstream: bool = False,
    ) -> EventRecord | None
        # find_one_and_update filtered on status ∈ expected — ATOMIC state-machine
        # edge; returns None when the precondition lost (caller raises ConflictError).
        # Sets updated_at; sets last_confirmed_upstream_at=now when confirmed_upstream.
    async def find_overlapping(self, start: datetime, end: datetime) -> list[EventRecord]
        # active statuses only (PENDING, PENDING_UPSTREAM_ID, SCHEDULED, IN_PROGRESS);
        # overlap: scheduled_start < end AND scheduled_end > start
    async def sweep_pending(self) -> list[EventRecord]
        # every PENDING -> UNCERTAIN (startup safety; never blind-replay a
        # schedule idempotency-id after a crash); returns swept records
    async def find_stuck(self, now: datetime, grace_seconds: float) -> list[EventRecord]
        # IN_PROGRESS past scheduled_end+grace, or SCHEDULED past scheduled_start+grace

class IdempotencyRepository:
    async def ensure_indexes(self) -> None           # UNIQUE (client_id, key)
    async def try_insert(self, record: IdempotencyRecord) -> bool
        # insert; DuplicateKeyError -> False (the concurrent duplicate loses)
    async def get(self, client_id: str, key: str) -> IdempotencyRecord | None
    async def mark_succeeded(self, client_id: str, key: str,
                             response_snapshot: dict[str, Any], status_code: int) -> None
    async def mark_failed(self, client_id: str, key: str) -> None
    async def reclaim_failed(self, client_id: str, key: str) -> bool
        # find_one_and_update status "failed" -> "pending"; True if reclaimed
        # (consumer retry after a clean pre-send failure re-drives safely)

class AuditRepository:
    async def ensure_indexes(self) -> None           # event_id; ts
    async def record_intent(self, record: AuditRecord) -> str      # returns attempt_id
    async def record_outcome(self, record: AuditRecord) -> None    # INSERT, never update

class LeaseRepository:
    async def ensure_indexes(self) -> None           # TTL on expires_at
    async def acquire(self, name: str, holder: str, ttl_seconds: float) -> bool
    async def renew(self, name: str, holder: str, ttl_seconds: float) -> bool
    async def release(self, name: str, holder: str) -> None
```

Storage note: `EventRecord.event_id` maps to Mongo `_id`. Repositories convert
model ↔ document explicitly (`_to_document` / `_from_document` helpers) and
coerce naive datetimes from Mongo back to UTC-aware.

## Slice C — token broker + ZOMS client

### `apis/services/zelle/token_broker.py`

```python
class CircuitBreaker:
    def __init__(self, failure_threshold: int, reset_seconds: float) -> None
    def check_or_raise(self) -> None      # raises UpstreamUnavailableError when open
    def record_success(self) -> None
    def record_failure(self) -> None
    # monotonic clock; open after N consecutive failures; half-open after reset_seconds

class TokenBroker:
    def __init__(self, settings: ZelleSettings, client: httpx.AsyncClient) -> None
        # loads + parses the private key at construction — fail fast
    async def get(self) -> str
    def invalidate(self, used: str) -> None
    def _fresh(self) -> bool
    def _build_assertion(self) -> str
    async def _post_token(self) -> dict[str, Any]
```

Behavior (architecture §3 is normative): `_fresh()` requires token is not None
AND `time.monotonic() < expires_at - margin`; `margin = max(120.0, 0.2 * last_ttl)`.
`get()`: fresh-check → `breaker.check_or_raise()` BEFORE the lock → `asyncio.Lock`
single-flight → re-check inside → `sent_at = time.monotonic()` before send →
`expires_at = sent_at + expires_in` (never hardcode 1800). `invalidate(used)`
only evicts when `used` is still the cached token; resets both fields.
Assertion (joserfc, RS256): header `{alg, kid}`; claims `iss=sub=client_id`,
`aud=token_aud`, `scope=token_scope`, `jti=uuid4`, `iat=nbf=now-30`, `exp=now+120`.
`_post_token()`: form body `grant_type=client_credentials`,
`client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer`,
`client_assertion`, `scope`; ≤2 attempts under a 15s deadline, fresh `jti` each;
429 honors Retry-After once (cap 10s); **400/401 → AuthConfigError, never retried**;
success/failure feed the breaker.

### `apis/services/zelle/zoms_client.py`

```python
class ZomsClient:
    def __init__(self, settings: ZelleSettings, client: httpx.AsyncClient,
                 broker: TokenBroker) -> None
    async def schedule(self, payload: EwsScheduleRequest, idempotency_id: str) -> tuple[EwsScheduleResponse, list[str]]
    async def start(self, ews_event_id: str) -> list[str]
    async def complete(self, ews_event_id: str) -> list[str]
    async def cancel(self, ews_event_id: str) -> list[str]
    # each returns the list of EWS request-ids used (one per attempt) for audit
```

Behavior: URLs `{api_base_url}/v1/events/{schedule|start|complete|cancel}`.
Headers per attempt: `Authorization: Bearer <broker.get()>`, `accept`,
`content-type: application/json`, fresh `request-id: uuid4` per attempt;
`idempotency-id` on schedule only (same value across retries). Response mapping:

| Outcome | schedule | start/complete/cancel |
|---|---|---|
| 2xx | parse lenient response | success |
| definite 401 | invalidate → fresh token → retry ONCE; 2nd 401 → AuthConfigError | same |
| 429 | retry once after Retry-After (cap 10s) else RateLimitedError | same |
| other 4xx | UpstreamRejectedError | UpstreamRejectedError |
| 5xx | retry (≤2 total attempts), then UpstreamUnavailableError | **UpstreamUncertainError** (response received; execution unknown) |
| pre-send failure (ConnectError/ConnectTimeout) | retry (≤2), then UpstreamUnavailableError | UpstreamUnavailableError (safe: never sent) |
| post-send failure (ReadTimeout/ReadError/WriteTimeout/RemoteProtocolError) | UpstreamUncertainError | UpstreamUncertainError |

Never log request/response bodies at the client (they carry PII + tokens);
log method, URL, status, request-id, elapsed.

## Slice D — event service + watchdog

### `apis/services/zelle/event_service.py`

```python
@dataclass
class ScheduleResult:
    response: MaintenanceEventResponse
    status_code: int          # 201 normal, 202 PENDING_UPSTREAM_ID, 200/201 on replay
    replayed: bool

class EventService:
    def __init__(self, settings, events, idempotency, audit, zoms) -> None
    async def schedule(self, request: ScheduleEventRequest, *, client_id: str,
                       idempotency_key: str | None, correlation_id: str) -> ScheduleResult
    async def lifecycle(self, event_id: str, action: LifecycleAction, *, client_id: str,
                        confirm_ticket: str, correlation_id: str,
                        dry_run: bool = False) -> MaintenanceEventResponse
    async def get_event(self, event_id: str, *, correlation_id: str) -> MaintenanceEventResponse
    async def list_events(self, status: EventStatus | None, *, correlation_id: str) -> EventListResponse
    async def resolve(self, event_id: str, request: ResolveRequest, *, client_id: str,
                      correlation_id: str) -> MaintenanceEventResponse
    async def startup_sweep(self) -> int      # PENDING -> UNCERTAIN, returns count
```

State machine (the ONLY legal transitions; `transition()` enforces atomically):
`PENDING → SCHEDULED | PENDING_UPSTREAM_ID | UNCERTAIN | FAILED`;
`PENDING_UPSTREAM_ID → SCHEDULED (resolve)`;
`SCHEDULED → IN_PROGRESS (start) | CANCELLED (cancel) | UNCERTAIN`;
`IN_PROGRESS → COMPLETE (complete) | UNCERTAIN`;
`UNCERTAIN → SCHEDULED | IN_PROGRESS | COMPLETE | CANCELLED | FAILED (resolve only)`.
Preconditions: start requires SCHEDULED; complete requires IN_PROGRESS; cancel
requires SCHEDULED. Violations → ConflictError. UNCERTAIN blocks all lifecycle
verbs (409) until admin resolve.

Schedule flow: allowlist check → idempotency lookup when key present
(`succeeded` + matching body_hash → replay stored response, `replayed=True`;
`pending` → ConflictError "in flight"; body_hash mismatch → ConflictError;
`failed` → `reclaim_failed` and re-drive) → overlap check (unless allow_overlap;
overlap → ConflictError) → build EventRecord (PENDING, fresh event_id + EWS
idempotency_id) → `events.create` + `idempotency.try_insert` (ledger-first;
losing the unique-index race → re-read and replay/409) → audit INTENT →
`zoms.schedule` → audit OUTCOME → on success: `maintenance_event_id` present
→ transition to SCHEDULED (201); absent → PENDING_UPSTREAM_ID (202) — either
way `mark_succeeded` with the northbound response snapshot. On
UpstreamUncertainError → transition UNCERTAIN, `mark_failed` NOT called (key
stays pending → consumer retry 409s until resolve), re-raise. On
UpstreamUnavailableError/RateLimitedError (clean pre-send) → transition FAILED
+ `mark_failed` (retryable), re-raise. Body hash: sha256 of
`json.dumps(request.model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":"))`.

Lifecycle flow: allowlist (lifecycle_client_allowlist when non-empty, else
client_allowlist) → load event (404) → `confirm_ticket != event.ticket_number`
→ ConflictError → precondition state check → dry_run: audit (outcome DRY_RUN)
and return current state WITHOUT calling EWS or transitioning → audit INTENT →
`zoms.<action>(ews_event_id)` → audit OUTCOME → atomic transition (expected =
precondition state). `ews_event_id is None` (PENDING_UPSTREAM_ID) → ConflictError.
On UpstreamUncertainError → transition UNCERTAIN + re-raise. On
UpstreamUnavailable/RateLimited → NO transition (event state unchanged — the
call never executed), re-raise.

Resolve flow: load (404) → current status must be UNCERTAIN or
PENDING_UPSTREAM_ID (else ConflictError) → PENDING_UPSTREAM_ID requires
`ews_event_id` in request → transition to `actual_status` → audit INTENT+OUTCOME
with attestation in `detail_redacted`.

### `apis/services/zelle/watchdog.py`

```python
class Watchdog:
    def __init__(self, settings, events: EventsRepository, leases: LeaseRepository) -> None
    async def run_forever(self) -> None    # loop: acquire/renew lease -> scan -> sleep
    async def scan_once(self) -> list[EventRecord]   # find_stuck + log CRITICAL per stuck event
    def stop(self) -> None
```

Lease name `"zelle-watchdog"`, holder = uuid4-per-instance, ttl = 2× interval.
No lease → sleep and retry (idle replica). Alerting = `LOGGER.critical` with
event id/status/ticket (host monitoring picks up CRITICAL logs).

## Slice E — routes + dependencies

### `apis/dependencies/zelle.py`

```python
@dataclass
class ZelleRuntime:
    settings: ZelleSettings
    broker: TokenBroker
    zoms_client: ZomsClient
    events: EventsRepository
    idempotency: IdempotencyRepository
    audit: AuditRepository
    leases: LeaseRepository
    service: EventService
    watchdog: Watchdog | None

def build_zelle_runtime(settings: ZelleSettings, http_client: httpx.AsyncClient,
                        database: AsyncIOMotorDatabase) -> ZelleRuntime

async def register_zelle(app: FastAPI, settings: ZelleSettings,
                         http_client: httpx.AsyncClient,
                         database: AsyncIOMotorDatabase) -> ZelleRuntime
    # build runtime -> app.state.zelle_runtime = runtime -> ensure_indexes on all
    # repos -> startup_sweep -> include events_router + admin_router -> register
    # zelle_exception_handler -> start watchdog task when enabled. The HOST APP
    # calls this from its lifespan with its baked-in client + database.

# FastAPI providers (read request.app.state.zelle_runtime):
def get_runtime(request: Request) -> ZelleRuntime
def get_service(request: Request) -> EventService
async def get_correlation_id(request: Request, x_correlation_id: str | None = Header(None)) -> str
    # accept or mint "c-<uuid4>"; store on request.state.correlation_id
async def require_client_id(request: Request, x_client_id: str | None = Header(None)) -> str
    # missing -> 400 envelope (VALIDATION_FAILED); not in allowlist (when
    # non-empty) -> 403 FORBIDDEN_ACTION
```

### `apis/routes/zelle/events.py` — `events_router = APIRouter(prefix="/v1/maintenance-events", tags=["zelle-maintenance-events"])`

| Route | Handler notes |
|---|---|
| `POST ""` | headers: X-Client-Id (required), Idempotency-Key (optional), X-Correlation-Id (optional). Returns JSONResponse with `result.status_code`; body `MaintenanceEventResponse` by_alias. |
| `POST "/{event_id}/start"`, `"/complete"`, `"/cancel"` | header X-Confirm-Ticket (required) + X-Client-Id; query `dry_run: bool = False`; 200. |
| `GET ""` | query `status: EventStatus | None`; 200 EventListResponse. |
| `GET "/{event_id}"` | 200 MaintenanceEventResponse. |

Every response sets `X-Correlation-Id`. Handlers are thin: resolve deps, call
service, serialize. No business logic in routes.

### `apis/routes/zelle/admin.py` — `admin_router = APIRouter(prefix="/v1/admin/maintenance-events", tags=["zelle-admin"])`

`POST "/{event_id}/resolve"` — body ResolveRequest; X-Client-Id required; 200.

## Slice F — fake EWS + plumbing

### `src/fake_ews/app.py`

`def create_fake_ews_app() -> FastAPI` — self-contained stub:
- `POST /token`: validates form fields present (grant_type, client_assertion_type,
  client_assertion non-empty) WITHOUT verifying the signature; returns
  `{"access_token": "fake-token-<uuid4>", "token_type": "Bearer", "expires_in": 1800}`.
- `POST /zoms/v1/events/schedule`: requires Authorization/accept/content-type/
  request-id/idempotency-id headers; validates body against the EWS field rules;
  in-memory store keyed by idempotency-id (replay returns the SAME 201 body);
  201 `{"maintenanceEventId": "<uuid4>", "status": "SCHEDULED"}`.
- `POST .../start|complete|cancel`: enforce the real lifecycle (start only from
  SCHEDULED, complete only from IN_PROGRESS, cancel only from SCHEDULED);
  violations → 409-ish error; success → 200 `{"status": ...}`.
- Fault injection via header `x-fake-fault`: `"500"` → 500, `"429"` → 429 with
  `Retry-After: 1`, `"401"` → 401 (once per unique request-id, then behave),
  `"slow"` → `asyncio.sleep(15)`.
- Exposes `app = create_fake_ews_app()` for `uvicorn fake_ews.app:app`.

### `requirements.txt` (loose pins) and `pytest.ini`

```
fastapi>=0.115  httpx>=0.27  motor>=3.6  pydantic>=2.8  pydantic-settings>=2.4
joserfc>=1.0  uvicorn>=0.30
# dev/test
pytest>=8.0  pytest-asyncio>=0.24  respx>=0.21  mongomock-motor>=0.0.30
```

```ini
[pytest]
pythonpath = .
testpaths = tests
asyncio_mode = auto
```

## Testing contract (test slices read the real code first)

- `tests/unit/zelle/test_token_broker.py` — `_fresh()` truth table (incl.
  token-is-None), margin math, single-flight (`asyncio.gather` × 50 → exactly 1
  token call, via respx or a counting fake transport), invalidate-only-if-same,
  breaker open/half-open, no-retry-on-400/401 → AuthConfigError, 429 honored once.
  Generate a throwaway RSA key in a fixture (joserfc `RSAKey.generate_key`).
- `tests/unit/zelle/test_zoms_client.py` — respx: exact URL/headers per op,
  fresh request-id per attempt, idempotency-id constant across schedule retries,
  401→refresh→retry-once (2nd 401 → AuthConfigError), 429 path, 5xx: schedule
  retries then 503-error vs lifecycle → UpstreamUncertainError, ConnectError vs
  ReadTimeout mapping, no token/PII in captured logs.
- `tests/unit/zelle/test_event_service.py` — mongomock-motor: schedule happy
  path (201, SCHEDULED), idempotent replay, body-hash mismatch 409, in-flight
  409, failed-then-reclaim re-drive, overlap 409 + allow_overlap bypass, state
  machine (start/complete/cancel preconditions), ticket mismatch 409, dry-run
  makes no EWS call and no transition, UNCERTAIN blocks lifecycle, resolve paths,
  startup sweep PENDING→UNCERTAIN. ZomsClient stubbed with an AsyncMock-style fake.
- `tests/unit/zelle/test_routes.py` — full ASGI app: build a FastAPI(), call
  `register_zelle` with a mongomock database and an httpx.AsyncClient whose
  transport is `ASGITransport(app=create_fake_ews_app())` (base_url per settings)
  — end-to-end schedule→start→complete against the fake, envelope shape on
  errors, X-Correlation-Id echo, missing X-Client-Id → 400, allowlist → 403.
- mongomock-motor limits: no real unique-index race under concurrency — test
  `try_insert` duplicate behavior sequentially; note anything unverifiable.

## Error → HTTP recap (single source: exceptions carry their own status)

422 VALIDATION_FAILED (also override FastAPI's RequestValidationError into the
envelope) · 409 CONFLICT · 403 FORBIDDEN_ACTION · 404 NOT_FOUND ·
502 UPSTREAM_REJECTED / UPSTREAM_UNCERTAIN · 503 UPSTREAM_UNAVAILABLE /
RATE_LIMITED (+ Retry-After when known).
