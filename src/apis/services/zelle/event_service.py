#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/services/zelle/event_service.py.                                               #
# Date of birth : 2026-07-16.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : EventService — northbound orchestration for zelle maintenance events: the           #
#                 facade state machine (STATE_TRANSITIONS), the ledger-first schedule                 #
#                 idempotency flow, overlap/allowlist/ticket-confirmation guardrails, dry-run,        #
#                 operator resolve, the startup PENDING sweep, and the append-only                    #
#                 INTENT/OUTCOME audit pair written around every southbound call.                     #
# Dependencies  : apis.config.zelle, apis.models.zelle.*, apis.repositories.zelle.*,                  #
#                 apis.services.zelle.zoms_client.                                                    #
# Modifications : 2026-07-16 Shane Reddy — Initial version.                                           #
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

import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

# Internal imports

from src.apis.config.zelle import ZelleSettings
from src.apis.models.zelle.enums import (
    AuditKind,
    AuditOutcome,
    EventStatus,
    HoldMode,
    LifecycleAction,
)
from src.apis.models.zelle.errors import (
    AuthConfigError,
    ConflictError,
    ForbiddenActionError,
    NotFoundError,
    RateLimitedError,
    UpstreamRejectedError,
    UpstreamUnavailableError,
    UpstreamUncertainError,
)
from src.apis.models.zelle.northbound import (
    EventListResponse,
    MaintenanceEventResponse,
    ResolveRequest,
    ScheduleEventRequest,
)
from src.apis.models.zelle.records import AuditRecord, EventRecord, IdempotencyRecord
from src.apis.models.zelle.southbound import EwsScheduleRequest, format_ews_datetime
from src.apis.repositories.zelle.audit import AuditRepository
from src.apis.repositories.zelle.events import EventsRepository
from src.apis.repositories.zelle.idempotency import IdempotencyRepository
from src.apis.services.zelle.zoms_client import ZomsClient

# Local variables

LOGGER = logging.getLogger(__name__)
SCHEDULE_ACTION = "schedule"
RESOLVE_ACTION = "resolve"
# EWS success codes per docs/zoms-api-reference.md: schedule 201, lifecycle verbs 200.
EWS_SCHEDULE_SUCCESS_STATUS = 201
EWS_LIFECYCLE_SUCCESS_STATUS = 200
# Northbound status when EWS omits maintenanceEventId synchronously (open question #2).
PENDING_UPSTREAM_STATUS_CODE = 202
IN_FLIGHT_MESSAGE = "A schedule with this Idempotency-Key is already in flight."
# The ONLY legal state-machine edges (architecture §5). The service checks legality here and
# events.transition() enforces each edge atomically via its expected-status filter.
STATE_TRANSITIONS: dict[EventStatus, frozenset[EventStatus]] = {
    EventStatus.PENDING: frozenset(
        {
            EventStatus.SCHEDULED,
            EventStatus.PENDING_UPSTREAM_ID,
            EventStatus.UNCERTAIN,
            EventStatus.FAILED,
        },
    ),
    EventStatus.PENDING_UPSTREAM_ID: frozenset({EventStatus.SCHEDULED}),
    EventStatus.SCHEDULED: frozenset(
        {EventStatus.IN_PROGRESS, EventStatus.CANCELLED, EventStatus.UNCERTAIN},
    ),
    EventStatus.IN_PROGRESS: frozenset({EventStatus.COMPLETE, EventStatus.UNCERTAIN}),
    EventStatus.UNCERTAIN: frozenset(
        {
            EventStatus.SCHEDULED,
            EventStatus.IN_PROGRESS,
            EventStatus.COMPLETE,
            EventStatus.CANCELLED,
            EventStatus.FAILED,
        },
    ),
    EventStatus.COMPLETE: frozenset(),
    EventStatus.CANCELLED: frozenset(),
    EventStatus.FAILED: frozenset(),
}
# Lifecycle verbs: the required current status and the status a successful call produces.
LIFECYCLE_PRECONDITIONS: dict[LifecycleAction, EventStatus] = {
    LifecycleAction.START: EventStatus.SCHEDULED,
    LifecycleAction.COMPLETE: EventStatus.IN_PROGRESS,
    LifecycleAction.CANCEL: EventStatus.SCHEDULED,
}
LIFECYCLE_TARGETS: dict[LifecycleAction, EventStatus] = {
    LifecycleAction.START: EventStatus.IN_PROGRESS,
    LifecycleAction.COMPLETE: EventStatus.COMPLETE,
    LifecycleAction.CANCEL: EventStatus.CANCELLED,
}


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


def _canonical_body_hash(request: ScheduleEventRequest) -> str:

    """
    Hash the northbound schedule body canonically for idempotency-key comparison.

    :param request: The validated northbound schedule request.
    :type request: ScheduleEventRequest
    :return: sha256 hex digest of the canonical JSON (camelCase aliases, sorted keys, compact
        separators) — the exact recipe pinned by the module contract.
    :rtype: str
    """

    canonical = json.dumps(
        request.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
# endDef


@dataclass
class ScheduleResult:

    """
    Outcome of a schedule call: the northbound response body, the HTTP status the route should
    return (201 normal, 202 PENDING_UPSTREAM_ID, the stored code on replay), and whether the
    response was replayed from the idempotency ledger.
    """

    response: MaintenanceEventResponse
    status_code: int
    replayed: bool
# endClass


class EventService:

    """
    Orchestration service for zelle maintenance events. Owns the facade state machine, the
    idempotency ledger flow, guardrails (allowlists, overlap, ticket confirmation, UNCERTAIN
    lock-out), dry-run, operator resolve, and the INTENT/OUTCOME audit pair around every
    southbound EWS call. Never touches httpx or FastAPI types directly.
    """

    def __init__(
        self,
        settings: ZelleSettings,
        events: EventsRepository,
        idempotency: IdempotencyRepository,
        audit: AuditRepository,
        zoms: ZomsClient,
        ) -> None:

        """
        Wire the service with its collaborators (constructor dependency injection).

        :param settings: Zelle facade settings (org constants, allowlists, defaults).
        :type settings: ZelleSettings
        :param events: Event persistence and atomic state-machine transitions.
        :type events: EventsRepository
        :param idempotency: Ledger closing the schedule idempotency race.
        :type idempotency: IdempotencyRepository
        :param audit: Append-only INTENT/OUTCOME audit trail.
        :type audit: AuditRepository
        :param zoms: Southbound EWS client adapter.
        :type zoms: ZomsClient
        """

        self._settings = settings
        self._events = events
        self._idempotency = idempotency
        self._audit = audit
        self._zoms = zoms
    # endDef

    async def schedule(
        self,
        request: ScheduleEventRequest,
        *,
        client_id: str,
        idempotency_key: str | None,
        correlation_id: str,
        ) -> ScheduleResult:

        """
        Schedule a maintenance window with EWS, closing the idempotency race ledger-first.

        :param request: Validated northbound schedule request.
        :type request: ScheduleEventRequest
        :param client_id: Attributed caller identity from ``X-Client-Id``.
        :type client_id: str
        :param idempotency_key: Consumer ``Idempotency-Key`` value; None disables replay.
        :type idempotency_key: str | None
        :param correlation_id: Correlation id bound to this request.
        :type correlation_id: str
        :return: The northbound response, route HTTP status, and replay flag.
        :rtype: ScheduleResult
        :raises ForbiddenActionError: If the caller is not in the client allowlist.
        :raises ConflictError: On window overlap, key reuse with a different body, or an
            in-flight key.
        :raises UpstreamUncertainError: If the EWS outcome is unknown — the event is locked
            UNCERTAIN and the idempotency key stays pending.
        :raises UpstreamUnavailableError: If EWS was unreachable; event marked FAILED,
            retryable.
        :raises RateLimitedError: If EWS throttled the call; event marked FAILED, retryable.
        :raises UpstreamRejectedError: If EWS definitively rejected the request; event FAILED.
        :raises AuthConfigError: On a signing-key / client-registration incident; event FAILED.
        """

        self._require_schedule_allowed(client_id)
        body_hash = _canonical_body_hash(request)
        reclaimed = False
        if idempotency_key is not None:
            existing = await self._idempotency.get(client_id, idempotency_key)
            if existing is not None:
                replay = await self._replay_or_reclaim(
                    existing,
                    body_hash,
                    client_id=client_id,
                    correlation_id=correlation_id,
                )
                if replay is not None:
                    return replay
                # endIf
                reclaimed = True
            # endIf
        # endIf
        if not request.allow_overlap:
            overlapping = await self._events.find_overlapping(request.start_time, request.end_time)
            if overlapping:
                overlap_ids = ", ".join(record.event_id for record in overlapping)
                raise ConflictError(
                    f"Window overlaps active event(s) {overlap_ids}; "
                    "pass allowOverlap=true to override.",
                )
            # endIf
        # endIf
        hold_mode: HoldMode = (
            request.hold_mode if request.hold_mode is not None
            else self._settings.default_hold_mode
        )
        payload = self._build_ews_payload(request, hold_mode)
        now = datetime.now(timezone.utc)
        record = EventRecord(
            event_id=str(uuid.uuid4()),
            ews_event_id=None,
            status=EventStatus.PENDING,
            idempotency_id=str(uuid.uuid4()),
            client_id=client_id,
            ticket_number=request.ticket_number,
            reason=request.reason,
            hold_mode=hold_mode,
            scheduled_start=request.start_time,
            scheduled_end=request.end_time,
            payload_snapshot=payload.model_dump(mode="json", by_alias=True),
            last_confirmed_upstream_at=None,
            created_at=now,
            updated_at=now,
        )
        if idempotency_key is not None and not reclaimed:
            ledger = IdempotencyRecord(
                client_id=client_id,
                key=idempotency_key,
                body_hash=body_hash,
                event_id=record.event_id,
                status="pending",
                response_snapshot=None,
                response_status_code=None,
                created_at=now,
            )
            # Ledger-first insert: the unique (client_id, key) index makes a concurrent
            # duplicate lose deterministically before any event document or southbound call.
            if not await self._idempotency.try_insert(ledger):
                winner = await self._idempotency.get(client_id, idempotency_key)
                if winner is None:
                    raise ConflictError(IN_FLIGHT_MESSAGE)
                # endIf
                replay = await self._replay_or_reclaim(
                    winner,
                    body_hash,
                    client_id=client_id,
                    correlation_id=correlation_id,
                )
                if replay is not None:
                    return replay
                # endIf
                # The winner failed cleanly and we reclaimed its ledger row — re-drive below.
            # endIf
        # endIf
        await self._events.create(record)
        attempt_id = await self._record_intent(
            client_id=client_id,
            correlation_id=correlation_id,
            event_id=record.event_id,
            action=SCHEDULE_ACTION,
            detail=(
                f"schedule ticket={record.ticket_number} "
                f"window={record.scheduled_start.isoformat()}"
                f"..{record.scheduled_end.isoformat()} hold={record.hold_mode.value}"
            ),
        )
        try:
            ews_response, request_ids = await self._zoms.schedule(payload, record.idempotency_id)
        except UpstreamUncertainError as exc:
            # Ambiguous outcome: lock the event UNCERTAIN; the idempotency key STAYS pending,
            # so consumer retries 409 until an operator resolves the event.
            await self._record_outcome(
                attempt_id=attempt_id,
                client_id=client_id,
                correlation_id=correlation_id,
                event_id=record.event_id,
                action=SCHEDULE_ACTION,
                outcome=AuditOutcome.UNCERTAIN,
                http_status=None,
                request_ids=[],
                detail=f"{type(exc).__name__}: {exc.message}",
            )
            await self._transition_or_warn(
                record.event_id,
                EventStatus.PENDING,
                EventStatus.UNCERTAIN,
            )
            raise
        except (UpstreamUnavailableError, RateLimitedError) as exc:
            # Clean failure before execution: safe to mark FAILED and free the key for retry.
            await self._fail_schedule(
                attempt_id=attempt_id,
                client_id=client_id,
                correlation_id=correlation_id,
                event_id=record.event_id,
                idempotency_key=idempotency_key,
                outcome=AuditOutcome.UNAVAILABLE,
                detail=f"{type(exc).__name__}: {exc.message}",
            )
            raise
        except (UpstreamRejectedError, AuthConfigError) as exc:
            # Definite rejection: EWS answered without executing — FAILED, key freed.
            await self._fail_schedule(
                attempt_id=attempt_id,
                client_id=client_id,
                correlation_id=correlation_id,
                event_id=record.event_id,
                idempotency_key=idempotency_key,
                outcome=AuditOutcome.REJECTED,
                detail=f"{type(exc).__name__}: {exc.message}",
            )
            raise
        # endTryExcept
        if ews_response.maintenance_event_id is not None:
            new_status = EventStatus.SCHEDULED
            status_code = EWS_SCHEDULE_SUCCESS_STATUS
            outcome_detail = "maintenanceEventId returned"
        else:
            new_status = EventStatus.PENDING_UPSTREAM_ID
            status_code = PENDING_UPSTREAM_STATUS_CODE
            outcome_detail = "maintenanceEventId absent; awaiting operator resolve"
        # endIfElse
        await self._record_outcome(
            attempt_id=attempt_id,
            client_id=client_id,
            correlation_id=correlation_id,
            event_id=record.event_id,
            action=SCHEDULE_ACTION,
            outcome=AuditOutcome.SUCCESS,
            http_status=EWS_SCHEDULE_SUCCESS_STATUS,
            request_ids=request_ids,
            detail=outcome_detail,
        )
        updated = await self._events.transition(
            record.event_id,
            expected=(EventStatus.PENDING,),
            new_status=new_status,
            ews_event_id=ews_response.maintenance_event_id,
            confirmed_upstream=True,
        )
        if updated is None:
            raise ConflictError(
                "Event changed state concurrently; the EWS outcome is recorded in audit.",
            )
        # endIf
        response = self._to_response(updated, correlation_id)
        if idempotency_key is not None:
            await self._idempotency.mark_succeeded(
                client_id,
                idempotency_key,
                response.model_dump(mode="json", by_alias=True),
                status_code,
            )
        # endIf
        return ScheduleResult(response=response, status_code=status_code, replayed=False)
    # endDef

    async def lifecycle(
        self,
        event_id: str,
        action: LifecycleAction,
        *,
        client_id: str,
        confirm_ticket: str,
        correlation_id: str,
        dry_run: bool = False,
        ) -> MaintenanceEventResponse:

        """
        Drive a lifecycle verb (start/complete/cancel) against EWS for a stored event.

        :param event_id: Facade event id from the route path.
        :type event_id: str
        :param action: The lifecycle verb to perform.
        :type action: LifecycleAction
        :param client_id: Attributed caller identity from ``X-Client-Id``.
        :type client_id: str
        :param confirm_ticket: ``X-Confirm-Ticket`` value; must equal the stored ticket number.
        :type confirm_ticket: str
        :param correlation_id: Correlation id bound to this request.
        :type correlation_id: str
        :param dry_run: When True, audit the attempt (outcome DRY_RUN) and return the current
            state without calling EWS or transitioning.
        :type dry_run: bool
        :return: The (possibly transitioned) consumer view of the event.
        :rtype: MaintenanceEventResponse
        :raises ForbiddenActionError: If the caller is not in the lifecycle allowlist.
        :raises NotFoundError: If no event with ``event_id`` exists.
        :raises ConflictError: On ticket mismatch, UNCERTAIN lock-out, state-machine
            precondition violation, or a missing EWS event id (PENDING_UPSTREAM_ID).
        :raises UpstreamUncertainError: If the EWS outcome is unknown; event locked UNCERTAIN.
        :raises UpstreamUnavailableError: If EWS was unreachable pre-send; state untouched.
        :raises RateLimitedError: If EWS throttled the call; state untouched.
        :raises UpstreamRejectedError: If EWS definitively rejected the call; state untouched.
        :raises AuthConfigError: On a signing-key / client-registration incident.
        """

        self._require_lifecycle_allowed(client_id)
        event = await self._events.get(event_id)
        if event is None:
            raise NotFoundError(f"No maintenance event with id {event_id}.")
        # endIf
        if confirm_ticket != event.ticket_number:
            raise ConflictError(
                "X-Confirm-Ticket does not match the ticket number of this event.",
            )
        # endIf
        if event.status is EventStatus.UNCERTAIN:
            raise ConflictError(
                "Event is UNCERTAIN; lifecycle actions are blocked until an operator resolves "
                "it via the admin resolve endpoint.",
            )
        # endIf
        required = LIFECYCLE_PRECONDITIONS[action]
        if event.status is not required:
            raise ConflictError(
                f"Cannot {action.value} an event in status {event.status.value}; "
                f"requires {required.value}.",
            )
        # endIf
        if dry_run:
            # Dry run is audited (paired INTENT/OUTCOME) but never calls EWS or transitions.
            attempt_id = await self._record_intent(
                client_id=client_id,
                correlation_id=correlation_id,
                event_id=event.event_id,
                action=action.value,
                detail=f"dry run: {action.value} ticket={event.ticket_number}",
            )
            await self._record_outcome(
                attempt_id=attempt_id,
                client_id=client_id,
                correlation_id=correlation_id,
                event_id=event.event_id,
                action=action.value,
                outcome=AuditOutcome.DRY_RUN,
                http_status=None,
                request_ids=[],
                detail=f"dry run: {action.value} ticket={event.ticket_number}",
            )
            return self._to_response(event, correlation_id)
        # endIf
        ews_event_id = event.ews_event_id
        if ews_event_id is None:
            raise ConflictError(
                "Event has no EWS maintenance event id yet; resolve it before lifecycle calls.",
            )
        # endIf
        # Dispatch table keeps a single audited southbound call site for all three verbs.
        operations: dict[LifecycleAction, Callable[[str], Awaitable[list[str]]]] = {
            LifecycleAction.START: self._zoms.start,
            LifecycleAction.COMPLETE: self._zoms.complete,
            LifecycleAction.CANCEL: self._zoms.cancel,
        }
        attempt_id = await self._record_intent(
            client_id=client_id,
            correlation_id=correlation_id,
            event_id=event.event_id,
            action=action.value,
            detail=f"{action.value} ticket={event.ticket_number}",
        )
        try:
            request_ids = await operations[action](ews_event_id)
        except UpstreamUncertainError as exc:
            # The verb may have executed upstream: lock the event UNCERTAIN and page.
            await self._record_outcome(
                attempt_id=attempt_id,
                client_id=client_id,
                correlation_id=correlation_id,
                event_id=event.event_id,
                action=action.value,
                outcome=AuditOutcome.UNCERTAIN,
                http_status=None,
                request_ids=[],
                detail=f"{type(exc).__name__}: {exc.message}",
            )
            await self._transition_or_warn(event.event_id, required, EventStatus.UNCERTAIN)
            raise
        except (UpstreamUnavailableError, RateLimitedError) as exc:
            # Clean pre-send failure: the call never executed, so the state stays untouched.
            await self._record_outcome(
                attempt_id=attempt_id,
                client_id=client_id,
                correlation_id=correlation_id,
                event_id=event.event_id,
                action=action.value,
                outcome=AuditOutcome.UNAVAILABLE,
                http_status=None,
                request_ids=[],
                detail=f"{type(exc).__name__}: {exc.message}",
            )
            raise
        except (UpstreamRejectedError, AuthConfigError) as exc:
            # Definite rejection without execution: audit the refusal; state stays untouched.
            await self._record_outcome(
                attempt_id=attempt_id,
                client_id=client_id,
                correlation_id=correlation_id,
                event_id=event.event_id,
                action=action.value,
                outcome=AuditOutcome.REJECTED,
                http_status=None,
                request_ids=[],
                detail=f"{type(exc).__name__}: {exc.message}",
            )
            raise
        # endTryExcept
        await self._record_outcome(
            attempt_id=attempt_id,
            client_id=client_id,
            correlation_id=correlation_id,
            event_id=event.event_id,
            action=action.value,
            outcome=AuditOutcome.SUCCESS,
            http_status=EWS_LIFECYCLE_SUCCESS_STATUS,
            request_ids=request_ids,
            detail=None,
        )
        updated = await self._events.transition(
            event.event_id,
            expected=(required,),
            new_status=LIFECYCLE_TARGETS[action],
            confirmed_upstream=True,
        )
        if updated is None:
            raise ConflictError(
                "Event changed state concurrently; the EWS outcome is recorded in audit.",
            )
        # endIf
        return self._to_response(updated, correlation_id)
    # endDef

    async def get_event(
        self,
        event_id: str,
        *,
        correlation_id: str,
        ) -> MaintenanceEventResponse:

        """
        Read one event from local state (last known intent, never upstream authority).

        :param event_id: Facade event id.
        :type event_id: str
        :param correlation_id: Correlation id bound to this request.
        :type correlation_id: str
        :return: Consumer view of the event.
        :rtype: MaintenanceEventResponse
        :raises NotFoundError: If no event with ``event_id`` exists.
        """

        event = await self._events.get(event_id)
        if event is None:
            raise NotFoundError(f"No maintenance event with id {event_id}.")
        # endIf
        return self._to_response(event, correlation_id)
    # endDef

    async def list_events(
        self,
        status: EventStatus | None,
        *,
        correlation_id: str,
        ) -> EventListResponse:

        """
        List events from local state, optionally filtered by status.

        :param status: Optional status filter; None returns all.
        :type status: EventStatus | None
        :param correlation_id: Correlation id bound to this request.
        :type correlation_id: str
        :return: Envelope of consumer event views.
        :rtype: EventListResponse
        """

        records = await self._events.list_events(status=status)
        return EventListResponse(
            events=[self._to_response(record, correlation_id) for record in records],
        )
    # endDef

    async def resolve(
        self,
        event_id: str,
        request: ResolveRequest,
        *,
        client_id: str,
        correlation_id: str,
        ) -> MaintenanceEventResponse:

        """
        Operator resolution of an UNCERTAIN or PENDING_UPSTREAM_ID event after manual
        reconciliation with EWS; fully audited with the attestation preserved.

        :param event_id: Facade event id.
        :type event_id: str
        :param request: The resolve request (target status, attestation, optional EWS id).
        :type request: ResolveRequest
        :param client_id: Attributed operator identity from ``X-Client-Id``.
        :type client_id: str
        :param correlation_id: Correlation id bound to this request.
        :type correlation_id: str
        :return: Consumer view of the resolved event.
        :rtype: MaintenanceEventResponse
        :raises NotFoundError: If no event with ``event_id`` exists.
        :raises ConflictError: If the event is not UNCERTAIN/PENDING_UPSTREAM_ID, the target
            status is not a legal edge, an EWS id is missing for PENDING_UPSTREAM_ID, or the
            transition lost a concurrent race.
        """

        event = await self._events.get(event_id)
        if event is None:
            raise NotFoundError(f"No maintenance event with id {event_id}.")
        # endIf
        if event.status not in (EventStatus.UNCERTAIN, EventStatus.PENDING_UPSTREAM_ID):
            raise ConflictError(
                "Only UNCERTAIN or PENDING_UPSTREAM_ID events can be resolved; "
                f"event is {event.status.value}.",
            )
        # endIf
        if event.status is EventStatus.PENDING_UPSTREAM_ID and request.ews_event_id is None:
            raise ConflictError(
                "Resolving a PENDING_UPSTREAM_ID event requires ewsEventId in the request.",
            )
        # endIf
        allowed = STATE_TRANSITIONS[event.status]
        if request.actual_status not in allowed:
            allowed_values = ", ".join(sorted(status.value for status in allowed))
            raise ConflictError(
                f"Cannot resolve {event.status.value} to {request.actual_status.value}; "
                f"allowed: {allowed_values}.",
            )
        # endIf
        attempt_id = await self._record_intent(
            client_id=client_id,
            correlation_id=correlation_id,
            event_id=event.event_id,
            action=RESOLVE_ACTION,
            detail=request.attestation,
        )
        updated = await self._events.transition(
            event.event_id,
            expected=(event.status,),
            new_status=request.actual_status,
            ews_event_id=request.ews_event_id,
            confirmed_upstream=True,
        )
        if updated is None:
            raise ConflictError("Event changed state concurrently during resolve.")
        # endIf
        await self._record_outcome(
            attempt_id=attempt_id,
            client_id=client_id,
            correlation_id=correlation_id,
            event_id=event.event_id,
            action=RESOLVE_ACTION,
            outcome=AuditOutcome.SUCCESS,
            http_status=None,
            request_ids=[],
            detail=request.attestation,
        )
        return self._to_response(updated, correlation_id)
    # endDef

    async def startup_sweep(self) -> int:

        """
        Sweep every PENDING event into UNCERTAIN at startup — a schedule idempotency-id must
        never be blind-replayed after a crash — and alert per swept event.

        :return: Number of events swept.
        :rtype: int
        """

        swept = await self._events.sweep_pending()
        for record in swept:
            # CRITICAL is the alert channel: host monitoring pages on critical log lines.
            LOGGER.critical(
                "startup sweep: event %s (ticket %s) was PENDING at startup -> UNCERTAIN; "
                "manual reconciliation with EWS required",
                record.event_id,
                record.ticket_number,
            )
        # endFor
        return len(swept)
    # endDef

    def _require_schedule_allowed(self, client_id: str) -> None:

        """
        Enforce the schedule allowlist; an empty allowlist allows any caller (dev only).

        :param client_id: Attributed caller identity.
        :type client_id: str
        :raises ForbiddenActionError: If the caller is not in the non-empty allowlist.
        """

        allowlist = self._settings.client_allowlist
        if allowlist and client_id not in allowlist:
            raise ForbiddenActionError("Client is not allowed to schedule maintenance events.")
        # endIf
    # endDef

    def _require_lifecycle_allowed(self, client_id: str) -> None:

        """
        Enforce the lifecycle allowlist; empty falls back to the general client allowlist.

        :param client_id: Attributed caller identity.
        :type client_id: str
        :raises ForbiddenActionError: If the caller is not in the applicable allowlist.
        """

        allowlist = self._settings.lifecycle_client_allowlist or self._settings.client_allowlist
        if allowlist and client_id not in allowlist:
            raise ForbiddenActionError("Client is not allowed to run lifecycle actions.")
        # endIf
    # endDef

    async def _replay_or_reclaim(
        self,
        existing: IdempotencyRecord,
        body_hash: str,
        *,
        client_id: str,
        correlation_id: str,
        ) -> ScheduleResult | None:

        """
        Settle an existing idempotency ledger row: replay a stored success, 409 an in-flight
        or body-mismatched key, or reclaim a cleanly-failed row for a re-drive.

        :param existing: The ledger row found for (client_id, key).
        :type existing: IdempotencyRecord
        :param body_hash: Canonical hash of the incoming request body.
        :type body_hash: str
        :param client_id: Attributed caller identity.
        :type client_id: str
        :param correlation_id: Correlation id bound to this request.
        :type correlation_id: str
        :return: A replayed ScheduleResult, or None when the row was reclaimed and the caller
            should re-drive the schedule.
        :rtype: ScheduleResult | None
        :raises ConflictError: On body-hash mismatch or an in-flight (pending/lost-reclaim) key.
        """

        if existing.body_hash != body_hash:
            raise ConflictError(
                "Idempotency-Key was already used with a different request body.",
            )
        # endIf
        if existing.status == "pending":
            raise ConflictError(IN_FLIGHT_MESSAGE)
        # endIf
        if existing.status == "succeeded":
            if existing.response_snapshot is None or existing.response_status_code is None:
                raise ConflictError(
                    "Stored idempotent response is incomplete; contact the operator.",
                )
            # endIf
            replayed = MaintenanceEventResponse.model_validate(existing.response_snapshot)
            replayed = replayed.model_copy(update={"correlation_id": correlation_id})
            attempt_id = await self._record_intent(
                client_id=client_id,
                correlation_id=correlation_id,
                event_id=existing.event_id,
                action=SCHEDULE_ACTION,
                detail="idempotent replay",
            )
            await self._record_outcome(
                attempt_id=attempt_id,
                client_id=client_id,
                correlation_id=correlation_id,
                event_id=existing.event_id,
                action=SCHEDULE_ACTION,
                outcome=AuditOutcome.REPLAYED,
                http_status=existing.response_status_code,
                request_ids=[],
                detail="idempotent replay",
            )
            return ScheduleResult(
                response=replayed,
                status_code=existing.response_status_code,
                replayed=True,
            )
        # endIf
        # Status "failed": a clean pre-send failure — reclaim the row and re-drive safely.
        if await self._idempotency.reclaim_failed(client_id, existing.key):
            return None
        # endIf
        raise ConflictError(IN_FLIGHT_MESSAGE)
    # endDef

    async def _fail_schedule(
        self,
        *,
        attempt_id: str,
        client_id: str,
        correlation_id: str,
        event_id: str,
        idempotency_key: str | None,
        outcome: AuditOutcome,
        detail: str,
        ) -> None:

        """
        Bookkeep a clean schedule failure: OUTCOME audit, PENDING -> FAILED, and mark the
        idempotency key failed so a consumer retry can reclaim and re-drive it.

        :param attempt_id: Attempt id shared with the INTENT document.
        :type attempt_id: str
        :param client_id: Attributed caller identity.
        :type client_id: str
        :param correlation_id: Correlation id bound to this request.
        :type correlation_id: str
        :param event_id: Facade event id of the failed schedule.
        :type event_id: str
        :param idempotency_key: Consumer key to mark failed; None when absent.
        :type idempotency_key: str | None
        :param outcome: Audit classification (UNAVAILABLE or REJECTED).
        :type outcome: AuditOutcome
        :param detail: Redacted, facade-authored failure summary.
        :type detail: str
        """

        await self._record_outcome(
            attempt_id=attempt_id,
            client_id=client_id,
            correlation_id=correlation_id,
            event_id=event_id,
            action=SCHEDULE_ACTION,
            outcome=outcome,
            http_status=None,
            request_ids=[],
            detail=detail,
        )
        await self._transition_or_warn(event_id, EventStatus.PENDING, EventStatus.FAILED)
        if idempotency_key is not None:
            await self._idempotency.mark_failed(client_id, idempotency_key)
        # endIf
    # endDef

    async def _transition_or_warn(
        self,
        event_id: str,
        expected: EventStatus,
        new_status: EventStatus,
        ) -> None:

        """
        Attempt a failure-path transition; a lost precondition only warns because the original
        upstream error is about to be re-raised and must not be masked.

        :param event_id: Facade event id.
        :type event_id: str
        :param expected: The status the event must still be in.
        :type expected: EventStatus
        :param new_status: The failure status to record.
        :type new_status: EventStatus
        """

        updated = await self._events.transition(
            event_id,
            expected=(expected,),
            new_status=new_status,
        )
        if updated is None:
            LOGGER.warning(
                "event %s changed state concurrently; could not mark %s",
                event_id,
                new_status.value,
            )
        # endIf
    # endDef

    def _build_ews_payload(
        self,
        request: ScheduleEventRequest,
        hold_mode: HoldMode,
        ) -> EwsScheduleRequest:

        """
        Enrich the northbound request into the southbound EWS schedule body: org constants and
        the contact block come from config; datetimes pass through format_ews_datetime.

        :param request: Validated northbound schedule request.
        :type request: ScheduleEventRequest
        :param hold_mode: Effective hold mode (request value or configured default).
        :type hold_mode: HoldMode
        :return: The EWS wire model, ready for ``model_dump(mode="json", by_alias=True)``.
        :rtype: EwsScheduleRequest
        """

        settings = self._settings
        return EwsScheduleRequest(
            org_id=settings.org_id,
            participant_name=settings.participant_name,
            submitted_name=settings.submitted_name,
            contact_name=settings.contact_name,
            contact_phone=settings.contact_phone,
            contact_email=settings.contact_email,
            scheduled_start_date=format_ews_datetime(request.start_time),
            scheduled_end_date=format_ews_datetime(request.end_time),
            ews_hold=hold_mode,
            suppress_duplicate_payments=request.suppress_duplicate_payments,
            ticket_number=request.ticket_number,
            network_notification_id=request.network_notification_id,
        )
    # endDef

    def _to_response(
        self,
        record: EventRecord,
        correlation_id: str,
        ) -> MaintenanceEventResponse:

        """
        Map a persistence record to the consumer view.

        :param record: The stored event record.
        :type record: EventRecord
        :param correlation_id: Correlation id bound to this request.
        :type correlation_id: str
        :return: Consumer view of the event.
        :rtype: MaintenanceEventResponse
        """

        return MaintenanceEventResponse(
            event_id=record.event_id,
            status=record.status,
            start_time=record.scheduled_start,
            end_time=record.scheduled_end,
            ticket_number=record.ticket_number,
            reason=record.reason,
            hold_mode=record.hold_mode,
            correlation_id=correlation_id,
            created_at=record.created_at,
            last_confirmed_upstream_at=record.last_confirmed_upstream_at,
        )
    # endDef

    async def _record_intent(
        self,
        *,
        client_id: str,
        correlation_id: str,
        event_id: str,
        action: str,
        detail: str | None = None,
        ) -> str:

        """
        Insert an INTENT audit document (before every southbound call) and return the attempt
        id that the paired OUTCOME document must share.

        :param client_id: Attributed caller identity.
        :type client_id: str
        :param correlation_id: Correlation id bound to this request.
        :type correlation_id: str
        :param event_id: Facade event id the attempt concerns.
        :type event_id: str
        :param action: Audit action string (schedule/start/complete/cancel/resolve).
        :type action: str
        :param detail: Redacted, PII-free detail; None when not applicable.
        :type detail: str | None
        :return: The attempt id linking INTENT and OUTCOME.
        :rtype: str
        """

        record = AuditRecord(
            attempt_id=str(uuid.uuid4()),
            kind=AuditKind.INTENT,
            ts=datetime.now(timezone.utc),
            actor_client_id=client_id,
            correlation_id=correlation_id,
            event_id=event_id,
            action=action,
            ews_request_ids=[],
            outcome=None,
            http_status=None,
            detail_redacted=detail,
        )
        return await self._audit.record_intent(record)
    # endDef

    async def _record_outcome(
        self,
        *,
        attempt_id: str,
        client_id: str,
        correlation_id: str,
        event_id: str,
        action: str,
        outcome: AuditOutcome,
        http_status: int | None,
        request_ids: list[str],
        detail: str | None = None,
        ) -> None:

        """
        Insert an OUTCOME audit document (never an update) sharing the INTENT's attempt id.

        :param attempt_id: Attempt id returned by :meth:`_record_intent`.
        :type attempt_id: str
        :param client_id: Attributed caller identity.
        :type client_id: str
        :param correlation_id: Correlation id bound to this request.
        :type correlation_id: str
        :param event_id: Facade event id the attempt concerns.
        :type event_id: str
        :param action: Audit action string (schedule/start/complete/cancel/resolve).
        :type action: str
        :param outcome: Terminal classification of the attempt.
        :type outcome: AuditOutcome
        :param http_status: Upstream HTTP status when known; None otherwise.
        :type http_status: int | None
        :param request_ids: EWS request-ids used (one per attempt); empty when none were made.
        :type request_ids: list[str]
        :param detail: Redacted, PII-free detail; None when not applicable.
        :type detail: str | None
        """

        record = AuditRecord(
            attempt_id=attempt_id,
            kind=AuditKind.OUTCOME,
            ts=datetime.now(timezone.utc),
            actor_client_id=client_id,
            correlation_id=correlation_id,
            event_id=event_id,
            action=action,
            ews_request_ids=request_ids,
            outcome=outcome,
            http_status=http_status,
            detail_redacted=detail,
        )
        await self._audit.record_outcome(record)
    # endDef
# endClass


# end_apis/services/zelle/event_service.py
