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

from apis.config.zelle import ZelleSettings
from apis.models.zelle.enums import EventStatus, LifecycleAction
from apis.models.zelle.errors import (
    ConflictError,
    ForbiddenActionError,
    NotFoundError,
    UpstreamUnavailableError,
    UpstreamUncertainError,
)
from apis.models.zelle.northbound import ResolveRequest, ScheduleEventRequest
from apis.models.zelle.southbound import EwsScheduleRequest, EwsScheduleResponse
from apis.repositories.zelle.audit import AuditRepository
from apis.repositories.zelle.events import EventsRepository
from apis.repositories.zelle.idempotency import IdempotencyRepository
from apis.services.zelle.event_service import EventService

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
# endClass


@pytest.fixture
async def harness(
    settings: ZelleSettings,
    database: AsyncMongoMockClient,
    ) -> SimpleNamespace:

    """
    Real repositories over mongomock, a stub ZOMS client, and the service under test.
    """

    events = EventsRepository(database, settings.mongo_collection_prefix)
    idempotency = IdempotencyRepository(database, settings.mongo_collection_prefix)
    audit = AuditRepository(database, settings.mongo_collection_prefix)
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
        database=database,
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
    audit_count = await harness.database["zelle_audit"].count_documents({})
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
    database: AsyncMongoMockClient,
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
    events = EventsRepository(database, restricted.mongo_collection_prefix)
    idempotency = IdempotencyRepository(database, restricted.mongo_collection_prefix)
    audit = AuditRepository(database, restricted.mongo_collection_prefix)
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


# end_tests/unit/zelle/test_event_service.py
