#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : tests/unit/zelle/test_event_service.py.                                             #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : EventService tests over mongomock-motor with a stubbed ZomsClient: the schedule     #
#                 flow (happy, 202, replay, mismatch, in-flight, reclaim, overlap), the state         #
#                 machine and its guardrails (preconditions, ticket confirmation, dry-run,            #
#                 UNCERTAIN lock-out), resolve paths, allowlists, and the startup sweep.              #
# Dependencies  : pytest, mongomock_motor, apis.services.zelle.event_service,                         #
#                 apis.repositories.zelle.*, apis.models.zelle.*.                                     #
# Modifications : 2026-07-18 Shane Reddy — Initial version.                                           #
#                                                                                                     #
# Contact       : shanevreddy@gmail.com.                                                              #
#                                                                                                     #
# ----------------------------------------------------------------------------------------------------#
#
#


# ----------------------------------------------------------------------------------------------------#
# Imports.                                                                                            #
# ----------------------------------------------------------------------------------------------------#

import sys

sys.dont_write_bytecode = True

# External imports

import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from mongomock_motor import AsyncMongoMockClient

# Internal imports

from src.apis.config.zelle import ZelleSettings
from src.apis.models.zelle.enums import EventStatus, LifecycleAction
from src.apis.models.zelle.errors import (
    ConflictError,
    ForbiddenActionError,
    NotFoundError,
    UpstreamRejectedError,
    UpstreamUnavailableError,
    UpstreamUncertainError,
    ValidationFailedError,
)
from src.apis.models.zelle.northbound import ResolveRequest, ScheduleEventRequest
from src.apis.models.zelle.southbound import (
    EwsEventStatusResponse,
    EwsScheduleRequest,
    EwsScheduleResponse,
)
from src.apis.repositories.zelle.audit import get_audit_repository
from src.apis.repositories.zelle.events import get_events_repository
from src.apis.repositories.zelle.idempotency import get_idempotency_repository
from src.apis.services.zelle.event_service import EventService
from src.apis.services.zelle.notifications import NotificationService
from src.common.employee_directory import EmployeeRecord

# Local variables

LOGGER = logging.getLogger(__name__)
CLIENT_ID = "ops-portal"
TICKET = "CHG0012345"
EWS_EVENT_ID = "f879562c-b912-44e9-a592-71d3aef09afb"


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class _StubZoms:

    """
    Duck-typed ZomsClient stub: records calls and returns configured results or raises the
    configured error.
    """

    def __init__(self) -> None:

        """
        Default to a successful schedule returning a fixed EWS event id.
        """

        self.schedule_response = EwsScheduleResponse(maintenance_event_id=EWS_EVENT_ID)
        self.schedule_error: Exception | None = None
        self.lifecycle_error: Exception | None = None
        self.status_response = EwsEventStatusResponse(
            maintenance_event_id=EWS_EVENT_ID,
            status="SCHEDULED",
        )
        self.status_error: Exception | None = None
        self.calls: list[tuple[str, str]] = []
    # endDef

    async def schedule(
        self,
        payload: EwsScheduleRequest,
        idempotency_id: str,
        ) -> tuple[EwsScheduleResponse, list[str]]:

        """
        Record the call; raise the configured error or return the configured response.
        """

        self.calls.append(("schedule", idempotency_id))
        if self.schedule_error is not None:
            raise self.schedule_error
        # endIf
        return self.schedule_response, [str(uuid.uuid4())]
    # endDef

    async def start(self, ews_event_id: str) -> list[str]:

        """
        Record and stub the start verb.
        """

        return await self._lifecycle("start", ews_event_id)
    # endDef

    async def complete(self, ews_event_id: str) -> list[str]:

        """
        Record and stub the complete verb.
        """

        return await self._lifecycle("complete", ews_event_id)
    # endDef

    async def cancel(self, ews_event_id: str) -> list[str]:

        """
        Record and stub the cancel verb.
        """

        return await self._lifecycle("cancel", ews_event_id)
    # endDef

    async def _lifecycle(self, name: str, ews_event_id: str) -> list[str]:

        """
        Shared stub body for the lifecycle verbs.
        """

        self.calls.append((name, ews_event_id))
        if self.lifecycle_error is not None:
            raise self.lifecycle_error
        # endIf
        return [str(uuid.uuid4())]
    # endDef

    async def get_status(
        self,
        ews_event_id: str,
        ) -> tuple[EwsEventStatusResponse, list[str]]:

        """
        Record the call; raise the configured error or return the configured status response.
        """

        self.calls.append(("status", ews_event_id))
        if self.status_error is not None:
            raise self.status_error
        # endIf
        return self.status_response, [str(uuid.uuid4())]
    # endDef
# endClass


@pytest.fixture
async def harness(
    settings: ZelleSettings,
    mongo_client: AsyncMongoMockClient,
    ) -> SimpleNamespace:

    """
    Real repositories over mongomock, a stub ZOMS client, and the service under test.
    """

    events = await get_events_repository(mongo_client)
    idempotency = await get_idempotency_repository(mongo_client)
    audit = await get_audit_repository(mongo_client)
    await events.ensure_indexes()
    await idempotency.ensure_indexes()
    await audit.ensure_indexes()
    zoms = _StubZoms()
    service = EventService(settings, events, idempotency, audit, zoms)  # type: ignore[arg-type]
    return SimpleNamespace(
        service=service,
        events=events,
        idempotency=idempotency,
        audit=audit,
        zoms=zoms,
        settings=settings,
    )
# endDef


def _request(
    *,
    hours_from_now: float = 1.0,
    duration_hours: float = 2.0,
    reason: str = "core banking patch",
    allow_overlap: bool = False,
    ) -> ScheduleEventRequest:

    """
    Build a valid future-window schedule request.
    """

    start = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
    return ScheduleEventRequest(
        start_time=start,
        end_time=start + timedelta(hours=duration_hours),
        ticket_number=TICKET,
        reason=reason,
        allow_overlap=allow_overlap,
    )
# endDef


async def test_schedule_happy_path(harness: SimpleNamespace) -> None:

    """
    A schedule with an upstream id lands SCHEDULED with 201 and a full INTENT/OUTCOME pair.
    """

    result = await harness.service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key="key-1",
        correlation_id="c-1",
    )
    assert result.status_code == 201
    assert result.replayed is False
    assert result.response.status is EventStatus.SCHEDULED
    stored = await harness.events.get(result.response.event_id)
    assert stored is not None
    assert stored.ews_event_id == EWS_EVENT_ID
    assert stored.last_confirmed_upstream_at is not None
    audit_count = await harness.audit._collection.count_documents({})
    assert audit_count == 2
# endDef


async def test_schedule_missing_upstream_id_is_202(harness: SimpleNamespace) -> None:

    """
    A 201 body without maintenanceEventId lands PENDING_UPSTREAM_ID with 202.
    """

    harness.zoms.schedule_response = EwsScheduleResponse()
    result = await harness.service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
    )
    assert result.status_code == 202
    assert result.response.status is EventStatus.PENDING_UPSTREAM_ID
# endDef


async def test_idempotent_replay(harness: SimpleNamespace) -> None:

    """
    The same key and body replays the stored response without a second EWS call.
    """

    request = _request()
    first = await harness.service.schedule(
        request,
        client_id=CLIENT_ID,
        idempotency_key="key-1",
        correlation_id="c-1",
    )
    second = await harness.service.schedule(
        request,
        client_id=CLIENT_ID,
        idempotency_key="key-1",
        correlation_id="c-2",
    )
    assert second.replayed is True
    assert second.status_code == first.status_code
    assert second.response.event_id == first.response.event_id
    assert second.response.correlation_id == "c-2"
    schedule_calls = [call for call in harness.zoms.calls if call[0] == "schedule"]
    assert len(schedule_calls) == 1
# endDef


async def test_idempotency_body_mismatch_conflicts(harness: SimpleNamespace) -> None:

    """
    Reusing a key with a different body is a 409, never a silent replay.
    """

    request = _request()
    await harness.service.schedule(
        request,
        client_id=CLIENT_ID,
        idempotency_key="key-1",
        correlation_id="c-1",
    )
    altered = request.model_copy(update={"reason": "a different change", "allow_overlap": True})
    with pytest.raises(ConflictError):
        await harness.service.schedule(
            altered,
            client_id=CLIENT_ID,
            idempotency_key="key-1",
            correlation_id="c-2",
        )
    # endWith
# endDef


async def test_uncertain_keeps_key_pending_and_blocks_retry(harness: SimpleNamespace) -> None:

    """
    An ambiguous schedule locks the event UNCERTAIN and keeps the key pending, so a consumer
    retry 409s until an operator resolves.
    """

    request = _request()
    harness.zoms.schedule_error = UpstreamUncertainError("ambiguous")
    with pytest.raises(UpstreamUncertainError):
        await harness.service.schedule(
            request,
            client_id=CLIENT_ID,
            idempotency_key="key-1",
            correlation_id="c-1",
        )
    # endWith
    records = await harness.events.list_events()
    assert len(records) == 1
    assert records[0].status is EventStatus.UNCERTAIN
    harness.zoms.schedule_error = None
    with pytest.raises(ConflictError):
        await harness.service.schedule(
            request,
            client_id=CLIENT_ID,
            idempotency_key="key-1",
            correlation_id="c-2",
        )
    # endWith
# endDef


async def test_failed_then_reclaim_re_drives(harness: SimpleNamespace) -> None:

    """
    A clean pre-send failure marks the event FAILED and the key failed; a retry reclaims the
    key and re-drives successfully.
    """

    request = _request()
    harness.zoms.schedule_error = UpstreamUnavailableError("connect failed")
    with pytest.raises(UpstreamUnavailableError):
        await harness.service.schedule(
            request,
            client_id=CLIENT_ID,
            idempotency_key="key-1",
            correlation_id="c-1",
        )
    # endWith
    ledger = await harness.idempotency.get(CLIENT_ID, "key-1")
    assert ledger is not None
    assert ledger.status == "failed"
    harness.zoms.schedule_error = None
    result = await harness.service.schedule(
        request,
        client_id=CLIENT_ID,
        idempotency_key="key-1",
        correlation_id="c-2",
    )
    assert result.status_code == 201
    schedule_calls = [call for call in harness.zoms.calls if call[0] == "schedule"]
    assert len(schedule_calls) == 2
# endDef


async def test_overlap_conflicts_unless_allowed(harness: SimpleNamespace) -> None:

    """
    A window overlapping an active event 409s unless allowOverlap is set.
    """

    await harness.service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
    )
    with pytest.raises(ConflictError):
        await harness.service.schedule(
            _request(hours_from_now=1.5),
            client_id=CLIENT_ID,
            idempotency_key=None,
            correlation_id="c-2",
        )
    # endWith
    result = await harness.service.schedule(
        _request(hours_from_now=1.5, allow_overlap=True),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-3",
    )
    assert result.status_code == 201
# endDef


async def test_lifecycle_happy_path(harness: SimpleNamespace) -> None:

    """
    start moves SCHEDULED -> IN_PROGRESS and complete moves IN_PROGRESS -> COMPLETE, both
    confirmed upstream.
    """

    scheduled = await harness.service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
    )
    event_id = scheduled.response.event_id
    started = await harness.service.lifecycle(
        event_id,
        LifecycleAction.START,
        client_id=CLIENT_ID,
        confirm_ticket=TICKET,
        correlation_id="c-2",
    )
    assert started.status is EventStatus.IN_PROGRESS
    completed = await harness.service.lifecycle(
        event_id,
        LifecycleAction.COMPLETE,
        client_id=CLIENT_ID,
        confirm_ticket=TICKET,
        correlation_id="c-3",
    )
    assert completed.status is EventStatus.COMPLETE
    assert ("start", EWS_EVENT_ID) in harness.zoms.calls
    assert ("complete", EWS_EVENT_ID) in harness.zoms.calls
# endDef


async def test_lifecycle_preconditions(harness: SimpleNamespace) -> None:

    """
    complete requires IN_PROGRESS and cancel requires SCHEDULED — violations 409.
    """

    scheduled = await harness.service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
    )
    event_id = scheduled.response.event_id
    with pytest.raises(ConflictError):
        await harness.service.lifecycle(
            event_id,
            LifecycleAction.COMPLETE,
            client_id=CLIENT_ID,
            confirm_ticket=TICKET,
            correlation_id="c-2",
        )
    # endWith
    await harness.service.lifecycle(
        event_id,
        LifecycleAction.START,
        client_id=CLIENT_ID,
        confirm_ticket=TICKET,
        correlation_id="c-3",
    )
    with pytest.raises(ConflictError):
        await harness.service.lifecycle(
            event_id,
            LifecycleAction.CANCEL,
            client_id=CLIENT_ID,
            confirm_ticket=TICKET,
            correlation_id="c-4",
        )
    # endWith
# endDef


async def test_ticket_mismatch_conflicts(harness: SimpleNamespace) -> None:

    """
    X-Confirm-Ticket must equal the stored ticket number — the typed confirmation that
    actually confirms.
    """

    scheduled = await harness.service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
    )
    with pytest.raises(ConflictError):
        await harness.service.lifecycle(
            scheduled.response.event_id,
            LifecycleAction.START,
            client_id=CLIENT_ID,
            confirm_ticket="CHG9999999",
            correlation_id="c-2",
        )
    # endWith
# endDef


async def test_dry_run_makes_no_call_and_no_transition(harness: SimpleNamespace) -> None:

    """
    dry_run audits the attempt but never calls EWS or transitions the event.
    """

    scheduled = await harness.service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
    )
    event_id = scheduled.response.event_id
    calls_before = list(harness.zoms.calls)
    response = await harness.service.lifecycle(
        event_id,
        LifecycleAction.START,
        client_id=CLIENT_ID,
        confirm_ticket=TICKET,
        correlation_id="c-2",
        dry_run=True,
    )
    assert response.status is EventStatus.SCHEDULED
    assert harness.zoms.calls == calls_before
    stored = await harness.events.get(event_id)
    assert stored is not None
    assert stored.status is EventStatus.SCHEDULED
# endDef


async def test_uncertain_blocks_lifecycle(harness: SimpleNamespace) -> None:

    """
    An UNCERTAIN event rejects every lifecycle verb until an operator resolves it.
    """

    scheduled = await harness.service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
    )
    event_id = scheduled.response.event_id
    await harness.events.transition(
        event_id,
        expected=(EventStatus.SCHEDULED,),
        new_status=EventStatus.UNCERTAIN,
    )
    with pytest.raises(ConflictError):
        await harness.service.lifecycle(
            event_id,
            LifecycleAction.START,
            client_id=CLIENT_ID,
            confirm_ticket=TICKET,
            correlation_id="c-2",
        )
    # endWith
# endDef


async def test_lifecycle_uncertain_locks_event(harness: SimpleNamespace) -> None:

    """
    An ambiguous lifecycle outcome locks the event UNCERTAIN and re-raises.
    """

    scheduled = await harness.service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
    )
    event_id = scheduled.response.event_id
    harness.zoms.lifecycle_error = UpstreamUncertainError("ambiguous")
    with pytest.raises(UpstreamUncertainError):
        await harness.service.lifecycle(
            event_id,
            LifecycleAction.START,
            client_id=CLIENT_ID,
            confirm_ticket=TICKET,
            correlation_id="c-2",
        )
    # endWith
    stored = await harness.events.get(event_id)
    assert stored is not None
    assert stored.status is EventStatus.UNCERTAIN
# endDef


async def test_lifecycle_unavailable_leaves_state(harness: SimpleNamespace) -> None:

    """
    A clean pre-send lifecycle failure leaves the event state untouched — the call never
    executed upstream.
    """

    scheduled = await harness.service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
    )
    event_id = scheduled.response.event_id
    harness.zoms.lifecycle_error = UpstreamUnavailableError("connect failed")
    with pytest.raises(UpstreamUnavailableError):
        await harness.service.lifecycle(
            event_id,
            LifecycleAction.START,
            client_id=CLIENT_ID,
            confirm_ticket=TICKET,
            correlation_id="c-2",
        )
    # endWith
    stored = await harness.events.get(event_id)
    assert stored is not None
    assert stored.status is EventStatus.SCHEDULED
# endDef


async def test_resolve_uncertain(harness: SimpleNamespace) -> None:

    """
    An operator resolves an UNCERTAIN event to its attested actual status.
    """

    scheduled = await harness.service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
    )
    event_id = scheduled.response.event_id
    await harness.events.transition(
        event_id,
        expected=(EventStatus.SCHEDULED,),
        new_status=EventStatus.UNCERTAIN,
    )
    resolved = await harness.service.resolve(
        event_id,
        ResolveRequest(actual_status=EventStatus.COMPLETE, attestation="EWS NOC ref 4471"),
        client_id="operator",
        correlation_id="c-2",
    )
    assert resolved.status is EventStatus.COMPLETE
# endDef


async def test_resolve_rejects_wrong_states(harness: SimpleNamespace) -> None:

    """
    Only UNCERTAIN and PENDING_UPSTREAM_ID events can be resolved; PENDING_UPSTREAM_ID also
    requires the EWS event id.
    """

    scheduled = await harness.service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
    )
    with pytest.raises(ConflictError):
        await harness.service.resolve(
            scheduled.response.event_id,
            ResolveRequest(actual_status=EventStatus.COMPLETE, attestation="nope"),
            client_id="operator",
            correlation_id="c-2",
        )
    # endWith
    harness.zoms.schedule_response = EwsScheduleResponse()
    pending = await harness.service.schedule(
        _request(hours_from_now=10.0),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-3",
    )
    with pytest.raises(ConflictError):
        await harness.service.resolve(
            pending.response.event_id,
            ResolveRequest(actual_status=EventStatus.SCHEDULED, attestation="missing id"),
            client_id="operator",
            correlation_id="c-4",
        )
    # endWith
    resolved = await harness.service.resolve(
        pending.response.event_id,
        ResolveRequest(
            actual_status=EventStatus.SCHEDULED,
            attestation="EWS NOC ref 4471",
            ews_event_id=EWS_EVENT_ID,
        ),
        client_id="operator",
        correlation_id="c-5",
    )
    assert resolved.status is EventStatus.SCHEDULED
    stored = await harness.events.get(pending.response.event_id)
    assert stored is not None
    assert stored.ews_event_id == EWS_EVENT_ID
# endDef


async def test_startup_sweep(harness: SimpleNamespace) -> None:

    """
    Startup sweeps every PENDING event into UNCERTAIN and reports the count.
    """

    harness.zoms.schedule_error = RuntimeError("crash mid-call")
    with pytest.raises(RuntimeError):
        await harness.service.schedule(
            _request(),
            client_id=CLIENT_ID,
            idempotency_key=None,
            correlation_id="c-1",
        )
    # endWith
    swept = await harness.service.startup_sweep()
    assert swept == 1
    records = await harness.events.list_events(status=EventStatus.UNCERTAIN)
    assert len(records) == 1
# endDef


async def test_allowlists(
    signing_key_path: object,
    settings: ZelleSettings,
    mongo_client: AsyncMongoMockClient,
    ) -> None:

    """
    Non-empty allowlists reject unknown clients for schedule; the lifecycle allowlist further
    restricts lifecycle verbs.
    """

    restricted = settings.model_copy(
        update={
            "client_allowlist": ["ops-portal"],
            "lifecycle_client_allowlist": ["noc-only"],
        },
    )
    events = await get_events_repository(mongo_client)
    idempotency = await get_idempotency_repository(mongo_client)
    audit = await get_audit_repository(mongo_client)
    zoms = _StubZoms()
    service = EventService(restricted, events, idempotency, audit, zoms)  # type: ignore[arg-type]
    with pytest.raises(ForbiddenActionError):
        await service.schedule(
            _request(),
            client_id="stranger",
            idempotency_key=None,
            correlation_id="c-1",
        )
    # endWith
    with pytest.raises(ForbiddenActionError):
        await service.lifecycle(
            "irrelevant",
            LifecycleAction.START,
            client_id="ops-portal",
            confirm_ticket=TICKET,
            correlation_id="c-2",
        )
    # endWith
# endDef


async def test_get_event_not_found(harness: SimpleNamespace) -> None:

    """
    Reading an unknown event id raises NotFoundError.
    """

    with pytest.raises(NotFoundError):
        await harness.service.get_event("missing", correlation_id="c-1")
    # endWith
# endDef


async def test_upstream_status_happy_path(harness: SimpleNamespace) -> None:

    """
    A live status check returns local + upstream statuses side by side, calls the ZOMS status
    read with the stored EWS id, and writes NO audit documents (a read is not a state change).
    """

    scheduled = await harness.service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
    )
    audit_before = await harness.audit._collection.count_documents({})
    view = await harness.service.upstream_status(
        scheduled.response.event_id,
        client_id=CLIENT_ID,
        correlation_id="c-2",
    )
    assert view.event_id == scheduled.response.event_id
    assert view.local_status is EventStatus.SCHEDULED
    assert view.upstream_status == "SCHEDULED"
    assert view.checked_at.tzinfo is not None
    assert view.correlation_id == "c-2"
    assert ("status", EWS_EVENT_ID) in harness.zoms.calls
    audit_after = await harness.audit._collection.count_documents({})
    assert audit_after == audit_before
# endDef


async def test_upstream_status_normalizes_and_tolerates_absent(
    harness: SimpleNamespace,
    ) -> None:

    """
    The upstream status string is upper-cased/stripped; a blank or absent status surfaces as
    None rather than crashing (the vendor response schema is unconfirmed).
    """

    scheduled = await harness.service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
    )
    harness.zoms.status_response = EwsEventStatusResponse(status=" in_progress ")
    view = await harness.service.upstream_status(
        scheduled.response.event_id,
        client_id=CLIENT_ID,
        correlation_id="c-2",
    )
    assert view.upstream_status == "IN_PROGRESS"
    harness.zoms.status_response = EwsEventStatusResponse()
    view = await harness.service.upstream_status(
        scheduled.response.event_id,
        client_id=CLIENT_ID,
        correlation_id="c-3",
    )
    assert view.upstream_status is None
# endDef


async def test_upstream_status_without_upstream_id_conflicts(
    harness: SimpleNamespace,
    ) -> None:

    """
    An event still waiting for its upstream id (PENDING_UPSTREAM_ID) cannot be looked up live —
    409 CONFLICT, and the ZOMS status read is never called.
    """

    harness.zoms.schedule_response = EwsScheduleResponse()
    scheduled = await harness.service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
    )
    assert scheduled.response.status is EventStatus.PENDING_UPSTREAM_ID
    with pytest.raises(ConflictError):
        await harness.service.upstream_status(
            scheduled.response.event_id,
            client_id=CLIENT_ID,
            correlation_id="c-2",
        )
    # endWith
    assert ("status", EWS_EVENT_ID) not in harness.zoms.calls
# endDef


async def test_upstream_status_unknown_event_not_found(harness: SimpleNamespace) -> None:

    """
    An unknown facade event id raises NotFoundError.
    """

    with pytest.raises(NotFoundError):
        await harness.service.upstream_status(
            "missing",
            client_id=CLIENT_ID,
            correlation_id="c-1",
        )
    # endWith
# endDef


class _FakeEmployeeLookup:

    """
    Duck-typed EmployeeLookup: returns a configured record (or None) and records requests.
    """

    def __init__(self, record: EmployeeRecord | None) -> None:

        """
        Store the configured result.
        """

        self.record = record
        self.requested: list[str] = []
    # endDef

    async def get_employee(self, username: str) -> EmployeeRecord | None:

        """
        Record the request and return the configured result.
        """

        self.requested.append(username)
        return self.record
    # endDef
# endClass


class _RecordingSender:

    """
    Duck-typed EmailSender: records sends, or raises when configured to fail.
    """

    def __init__(self, fail: bool = False) -> None:

        """
        Store the failure switch.
        """

        self.fail = fail
        self.sent: list[tuple[str, str, str]] = []
    # endDef

    async def send_email(self, to: str, subject: str, html_body: str) -> None:

        """
        Record the send or raise the configured failure.
        """

        if self.fail:
            raise RuntimeError("smtp down")
        # endIf
        self.sent.append((to, subject, html_body))
    # endDef
# endClass


def _enriched_service(
    harness: SimpleNamespace,
    lookup: _FakeEmployeeLookup | None,
    sender: _RecordingSender | None,
    settings: ZelleSettings | None = None,
    ) -> EventService:

    """
    Build an EventService with directory enrichment and/or notifications wired.

    :param harness: The repository/zoms harness fixture value.
    :type harness: SimpleNamespace
    :param lookup: The fake directory lookup, or None.
    :type lookup: _FakeEmployeeLookup | None
    :param sender: The recording email sender, or None to disable notifications.
    :type sender: _RecordingSender | None
    :param settings: Settings override; None uses the harness settings.
    :type settings: ZelleSettings | None
    :return: The wired service.
    :rtype: EventService
    """

    effective = settings if settings is not None else harness.settings
    notifications = (
        NotificationService(sender, effective) if sender is not None else None
    )
    return EventService(
        effective,
        harness.events,
        harness.idempotency,
        harness.audit,
        harness.zoms,
        employee_lookup=lookup,
        notifications=notifications,
    )
# endDef


async def test_schedule_enriches_contact_block_from_directory(
    harness: SimpleNamespace,
    ) -> None:

    """
    With Sm-User and a directory hit, the southbound contact block carries the employee's
    name/phone/email (phone normalized to digits) and the notification goes to the employee.
    """

    lookup = _FakeEmployeeLookup(
        EmployeeRecord(
            name="Shane Reddy",
            emailAddress="sreddy@bank.com",
            phone="+1 (555) 123-4567",
        ),
    )
    sender = _RecordingSender()
    service = _enriched_service(harness, lookup, sender)
    result = await service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
        sm_user="sreddy",
    )
    assert result.status_code == 201
    assert lookup.requested == ["sreddy"]
    stored = await harness.events.get(result.response.event_id)
    assert stored is not None
    snapshot = stored.payload_snapshot
    assert snapshot["submittedName"] == "Shane Reddy"
    assert snapshot["contactName"] == "Shane Reddy"
    assert snapshot["contactPhone"] == "15551234567"
    assert snapshot["contactEmail"] == "sreddy@bank.com"
    assert len(sender.sent) == 1
    to, subject, html = sender.sent[0]
    assert to == "sreddy@bank.com"
    assert "SCHEDULE" in subject
    assert "SCHEDULED" in subject
    assert "sreddy" in html
# endDef


async def test_schedule_falls_back_when_user_not_found(harness: SimpleNamespace) -> None:

    """
    A directory miss falls back to the configured defaults, notes it in the audit trail and the
    email, and still schedules successfully — the directory must never block a schedule.
    """

    lookup = _FakeEmployeeLookup(None)
    sender = _RecordingSender()
    service = _enriched_service(harness, lookup, sender)
    result = await service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
        sm_user="ghost01",
    )
    assert result.status_code == 201
    stored = await harness.events.get(result.response.event_id)
    assert stored is not None
    assert stored.payload_snapshot["contactEmail"] == harness.settings.contact_email
    assert stored.payload_snapshot["submittedName"] == harness.settings.submitted_name
    to, _subject, html = sender.sent[0]
    assert to == harness.settings.contact_email
    assert "not found in GlobalDirectory" in html
    audit_docs = await harness.audit._collection.find({}).to_list(10)
    assert any(
        "not found in GlobalDirectory" in (doc.get("detail_redacted") or "")
        for doc in audit_docs
    )
# endDef


async def test_schedule_minimum_lead_days_rejected(harness: SimpleNamespace) -> None:

    """
    With min_schedule_lead_days=1, a window starting in an hour is rejected 422 before any
    ledger write or southbound call.
    """

    strict = harness.settings.model_copy(update={"min_schedule_lead_days": 1})
    service = _enriched_service(harness, None, None, settings=strict)
    with pytest.raises(ValidationFailedError):
        await service.schedule(
            _request(hours_from_now=1.0),
            client_id=CLIENT_ID,
            idempotency_key=None,
            correlation_id="c-1",
        )
    # endWith
    assert harness.zoms.calls == []
# endDef


async def test_schedule_minimum_lead_days_allows_far_window(
    harness: SimpleNamespace,
    ) -> None:

    """
    The same rule admits a window beyond the minimum lead.
    """

    strict = harness.settings.model_copy(update={"min_schedule_lead_days": 1})
    service = _enriched_service(harness, None, None, settings=strict)
    result = await service.schedule(
        _request(hours_from_now=48.0),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
    )
    assert result.status_code == 201
# endDef


async def test_email_failure_never_breaks_the_api_call(harness: SimpleNamespace) -> None:

    """
    A failing email egress is logged and swallowed; the schedule still succeeds.
    """

    sender = _RecordingSender(fail=True)
    service = _enriched_service(harness, None, sender)
    result = await service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
        sm_user="sreddy",
    )
    assert result.status_code == 201
# endDef


async def test_failed_attempt_still_sends_notification(harness: SimpleNamespace) -> None:

    """
    An EWS rejection re-raises to the consumer AND produces a notification email stating the
    REJECTED outcome — every attempt gets an email.
    """

    harness.zoms.schedule_error = UpstreamRejectedError("EWS rejected the schedule request.")
    sender = _RecordingSender()
    service = _enriched_service(harness, None, sender)
    with pytest.raises(UpstreamRejectedError):
        await service.schedule(
            _request(),
            client_id=CLIENT_ID,
            idempotency_key=None,
            correlation_id="c-1",
            sm_user="sreddy",
        )
    # endWith
    assert len(sender.sent) == 1
    _to, subject, _html = sender.sent[0]
    assert "REJECTED" in subject
# endDef


def _window_request(
    *,
    start_hour_utc: int,
    duration_hours: float = 2.0,
    emergency_immediate_start: bool | None = None,
    ) -> ScheduleEventRequest:

    """
    Build a schedule request at a fixed UTC hour three days out, for the window-gate tests.

    :param start_hour_utc: The UTC hour of the window start.
    :type start_hour_utc: int
    :param duration_hours: Window length in hours.
    :type duration_hours: float
    :param emergency_immediate_start: Optional EMERGENCY_IMMEDIATE indicator.
    :type emergency_immediate_start: bool | None
    :return: The schedule request.
    :rtype: ScheduleEventRequest
    """

    start = (datetime.now(timezone.utc) + timedelta(days=3)).replace(
        hour=start_hour_utc,
        minute=0,
        second=0,
        microsecond=0,
    )
    return ScheduleEventRequest(
        start_time=start,
        end_time=start + timedelta(hours=duration_hours),
        ticket_number=TICKET,
        reason="window gate test",
        emergency_immediate_start=emergency_immediate_start,
    )
# endDef


async def test_schedule_off_window_rejected_before_ews(harness: SimpleNamespace) -> None:

    """
    With the window gate enabled, a mid-day window is rejected 422 with the allowed hours in
    the message, before any southbound call.
    """

    strict = harness.settings.model_copy(update={"enforce_ews_window": True})
    service = _enriched_service(harness, None, None, settings=strict)
    with pytest.raises(ValidationFailedError) as excinfo:
        await service.schedule(
            _window_request(start_hour_utc=15),
            client_id=CLIENT_ID,
            idempotency_key=None,
            correlation_id="c-1",
        )

    # endWith

    assert "allowed maintenance hours" in str(excinfo.value)
    assert harness.zoms.calls == []
# endDef


async def test_schedule_inside_window_allowed(harness: SimpleNamespace) -> None:

    """
    A window fully inside the 05:00 to 11:00 UTC band passes the gate and schedules.
    """

    strict = harness.settings.model_copy(update={"enforce_ews_window": True})
    service = _enriched_service(harness, None, None, settings=strict)
    result = await service.schedule(
        _window_request(start_hour_utc=6),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
    )
    assert result.status_code == 201
# endDef


async def test_emergency_immediate_bypasses_gates_and_reaches_wire(
    harness: SimpleNamespace,
    ) -> None:

    """
    emergencyImmediateStart=true skips both the window gate and the lead-time rule, and the
    indicator reaches the southbound payload verbatim.
    """

    strict = harness.settings.model_copy(
        update={"enforce_ews_window": True, "min_schedule_lead_days": 1},
    )
    service = _enriched_service(harness, None, None, settings=strict)
    start = datetime.now(timezone.utc) + timedelta(minutes=10)
    request = ScheduleEventRequest(
        start_time=start,
        end_time=start + timedelta(hours=1),
        ticket_number=TICKET,
        reason="incident window",
        emergency_immediate_start=True,
    )
    result = await service.schedule(
        request,
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
    )
    assert result.status_code == 201
    stored = await harness.events.get(result.response.event_id)
    assert stored is not None
    assert stored.payload_snapshot["emergencyImmediateStart"] is True
# endDef


async def test_lifecycle_attempt_sends_notification(harness: SimpleNamespace) -> None:

    """
    A successful start emails the acting user with the resulting IN_PROGRESS status.
    """

    lookup = _FakeEmployeeLookup(
        EmployeeRecord(name="Shane Reddy", emailAddress="sreddy@bank.com", phone="5551234567"),
    )
    sender = _RecordingSender()
    service = _enriched_service(harness, lookup, sender)
    scheduled = await service.schedule(
        _request(),
        client_id=CLIENT_ID,
        idempotency_key=None,
        correlation_id="c-1",
        sm_user="sreddy",
    )
    sender.sent.clear()
    await service.lifecycle(
        scheduled.response.event_id,
        LifecycleAction.START,
        client_id=CLIENT_ID,
        confirm_ticket=TICKET,
        correlation_id="c-2",
        sm_user="sreddy",
    )
    assert len(sender.sent) == 1
    to, subject, _html = sender.sent[0]
    assert to == "sreddy@bank.com"
    assert "START" in subject
    assert "IN_PROGRESS" in subject
# endDef


# end_tests/unit/zelle/test_event_service.py
