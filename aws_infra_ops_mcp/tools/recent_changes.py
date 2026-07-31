"""Bounded CloudTrail control-plane activity for an approved EC2 instance."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import json
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, NoRegionError

from aws_infra_ops_mcp.aws import create_cloudtrail_client, create_ec2_client
from aws_infra_ops_mcp.policy import (
    validate_change_lookback_hours,
    validate_change_maximum_results,
    validate_instance,
)
from aws_infra_ops_mcp.tools.instance_resolution import resolve_approved_instance
from aws_infra_ops_mcp.tools.service_status import _aws_error_message

ALLOWED_EVENT_NAMES = frozenset(
    {
        "AssociateIamInstanceProfile",
        "CreateAssociation",
        "DeleteAssociation",
        "ModifyInstanceAttribute",
        "RebootInstances",
        "ReplaceIamInstanceProfileAssociation",
        "SendCommand",
        "StartInstances",
        "StartSession",
        "StopInstances",
        "TerminateSession",
        "UpdateAssociation",
    }
)
ALLOWED_EVENT_SOURCES = {
    "AssociateIamInstanceProfile": "ec2.amazonaws.com",
    "CreateAssociation": "ssm.amazonaws.com",
    "DeleteAssociation": "ssm.amazonaws.com",
    "ModifyInstanceAttribute": "ec2.amazonaws.com",
    "RebootInstances": "ec2.amazonaws.com",
    "ReplaceIamInstanceProfileAssociation": "ec2.amazonaws.com",
    "SendCommand": "ssm.amazonaws.com",
    "StartInstances": "ec2.amazonaws.com",
    "StartSession": "ssm.amazonaws.com",
    "StopInstances": "ec2.amazonaws.com",
    "TerminateSession": "ssm.amazonaws.com",
    "UpdateAssociation": "ssm.amazonaws.com",
}
LOOKUP_PAGE_SIZE = 50
MAX_SCANNED_EVENTS = 500
MAX_LOOKUP_PAGES = 20
MAX_RAW_EVENT_CHARS = 64_000
MAX_FIELD_CHARS = 512
MAX_SOURCE_CHARS = 128
MAX_TIMESTAMP_CHARS = 64
MAX_TOTAL_RESPONSE_CHARS = 64_000
MAX_NODES_INSPECTED = 2_000
LIMITATIONS = [
    (
        "CloudTrail records AWS API activity, not commands entered through SSH "
        "or inside an SSM session"
    ),
    (
        "Actor values are CloudTrail attribution and do not prove which human "
        "intended an action"
    ),
    (
        "Results are limited to the fixed event allowlist and events that "
        "reference the approved instance ID"
    ),
]

Clock = Callable[[], datetime]


def _bounded(value: Any, maximum_chars: int = MAX_FIELD_CHARS) -> str:
    """Return a compact, single-line, bounded string."""
    return " ".join(str(value or "").split())[:maximum_chars]


def _region(client: Any) -> str:
    region = getattr(getattr(client, "meta", None), "region_name", None)
    if not isinstance(region, str) or not region:
        raise RuntimeError(
            "AWS Region is not configured; configure it through the standard AWS chain."
        )
    return region


def _references_instance(value: Any, instance_id: str) -> bool:
    """Defensively find an exact instance reference in approved event sections."""
    pending = [value]
    inspected = 0
    while pending and inspected < MAX_NODES_INSPECTED:
        current = pending.pop()
        inspected += 1
        if isinstance(current, str):
            if current == instance_id:
                return True
            if current.startswith("arn:") and instance_id in current.split("/"):
                return True
        elif isinstance(current, dict):
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return False


def _actor(event: dict[str, Any]) -> str | None:
    """Extract only a compact CloudTrail-attributed identity."""
    identity = event.get("userIdentity")
    if not isinstance(identity, dict):
        return None

    candidates = [identity.get("arn"), identity.get("userName")]
    session_context = identity.get("sessionContext")
    if isinstance(session_context, dict):
        issuer = session_context.get("sessionIssuer")
        if isinstance(issuer, dict):
            candidates.extend([issuer.get("arn"), issuer.get("userName")])

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return _bounded(candidate)
    return None


def _read_only(value: Any) -> bool | None:
    if type(value) is bool:
        return value
    if isinstance(value, str):
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
    return None


def _event_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _parse_event(
    lookup_event: Any,
    instance_id: str,
) -> tuple[datetime, dict[str, Any]] | None:
    """Parse and reduce one lookup result without exposing its raw event."""
    if not isinstance(lookup_event, dict):
        return None
    raw_event = lookup_event.get("CloudTrailEvent")
    if not isinstance(raw_event, str) or len(raw_event) > MAX_RAW_EVENT_CHARS:
        return None
    try:
        event = json.loads(raw_event)
    except (json.JSONDecodeError, TypeError, RecursionError):
        return None
    if not isinstance(event, dict):
        return None

    event_name = event.get("eventName")
    event_source = event.get("eventSource")
    outer_name = lookup_event.get("EventName")
    if (
        not isinstance(event_name, str)
        or event_name not in ALLOWED_EVENT_NAMES
        or event_source != ALLOWED_EVENT_SOURCES[event_name]
        or (isinstance(outer_name, str) and outer_name != event_name)
    ):
        return None

    matched_on = []
    if _references_instance(lookup_event.get("Resources"), instance_id) or (
        _references_instance(event.get("resources"), instance_id)
    ):
        matched_on.append("instance_id")
    elif _references_instance(event.get("requestParameters"), instance_id):
        matched_on.append("instance_id")
    if not matched_on:
        return None

    timestamp = _event_time(lookup_event.get("EventTime"))
    if timestamp is None:
        timestamp = _event_time(event.get("eventTime"))
    if timestamp is None:
        return None

    return timestamp, {
        "event_time": timestamp.isoformat()[:MAX_TIMESTAMP_CHARS],
        "event_name": event_name,
        "event_source": _bounded(event_source, MAX_SOURCE_CHARS),
        "actor": _actor(event),
        "read_only": _read_only(event.get("readOnly")),
        "matched_on": matched_on,
    }


def inspect_recent_changes(
    instance_name: str,
    hours: int,
    maximum_results: int,
    ec2_client: Any,
    cloudtrail_client: Any,
    *,
    clock: Clock = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Resolve an approved instance and query bounded CloudTrail Event History."""
    normalized_instance = validate_instance(instance_name)
    validated_hours = validate_change_lookback_hours(hours)
    validated_maximum = validate_change_maximum_results(maximum_results)
    region = _region(ec2_client)
    cloudtrail_region = _region(cloudtrail_client)
    if cloudtrail_region != region:
        raise RuntimeError(
            "EC2 and CloudTrail clients must use the same configured AWS Region."
        )

    checked_at = clock().astimezone(timezone.utc)
    end_time = checked_at
    start_time = end_time - timedelta(hours=validated_hours)
    parsed_events: list[tuple[datetime, dict[str, Any]]] = []
    scanned = 0
    pages = 0
    next_token: str | None = None
    truncated = False

    try:
        normalized_instance, instance = resolve_approved_instance(
            normalized_instance, ec2_client
        )
        instance_id = instance["InstanceId"]

        while True:
            if pages >= MAX_LOOKUP_PAGES:
                truncated = True
                break
            remaining_scan = MAX_SCANNED_EVENTS - scanned
            if remaining_scan <= 0:
                truncated = True
                break
            request: dict[str, Any] = {
                "LookupAttributes": [
                    {
                        "AttributeKey": "ResourceName",
                        "AttributeValue": instance_id,
                    }
                ],
                "StartTime": start_time,
                "EndTime": end_time,
                "MaxResults": min(LOOKUP_PAGE_SIZE, remaining_scan),
            }
            if next_token:
                request["NextToken"] = next_token

            response = cloudtrail_client.lookup_events(**request)
            pages += 1
            lookup_events = response.get("Events", [])
            if not isinstance(lookup_events, list):
                lookup_events = []

            for index, lookup_event in enumerate(lookup_events):
                scanned += 1
                parsed = _parse_event(lookup_event, instance_id)
                if parsed is not None:
                    parsed_events.append(parsed)
                    if len(parsed_events) >= validated_maximum:
                        truncated = (
                            index < len(lookup_events) - 1
                            or bool(response.get("NextToken"))
                        )
                        break
                if scanned >= MAX_SCANNED_EVENTS:
                    truncated = (
                        index < len(lookup_events) - 1
                        or bool(response.get("NextToken"))
                    )
                    break

            if len(parsed_events) >= validated_maximum:
                break
            if scanned >= MAX_SCANNED_EVENTS:
                break
            token = response.get("NextToken")
            if not isinstance(token, str) or not token:
                break
            next_token = token
    except (NoRegionError, NoCredentialsError, ClientError, BotoCoreError) as error:
        raise RuntimeError(_aws_error_message("EC2 or CloudTrail", error)) from error

    parsed_events.sort(key=lambda item: item[0], reverse=True)
    events: list[dict[str, Any]] = []
    response_chars = 0
    for _, event in parsed_events[:validated_maximum]:
        event_size = len(json.dumps(event, separators=(",", ":")))
        if response_chars + event_size > MAX_TOTAL_RESPONSE_CHARS:
            truncated = True
            break
        events.append(event)
        response_chars += event_size

    return {
        "instance_name": normalized_instance,
        "instance_id": instance_id,
        "region": region,
        "lookback_hours": validated_hours,
        "maximum_results": validated_maximum,
        "result_count": len(events),
        "events": events,
        "truncated": truncated,
        "data_source": "aws-cloudtrail-event-history",
        "checked_at": checked_at.isoformat(),
        "limitations": LIMITATIONS.copy(),
    }


def get_recent_changes(
    instance_name: str,
    hours: int = 24,
    maximum_results: int = 25,
) -> dict[str, Any]:
    """Return bounded CloudTrail activity for an approved EC2 instance."""
    normalized_instance = validate_instance(instance_name)
    validated_hours = validate_change_lookback_hours(hours)
    validated_maximum = validate_change_maximum_results(maximum_results)
    try:
        ec2_client = create_ec2_client()
        cloudtrail_client = create_cloudtrail_client()
    except (NoRegionError, NoCredentialsError, ClientError, BotoCoreError) as error:
        raise RuntimeError(_aws_error_message("client creation", error)) from error
    return inspect_recent_changes(
        normalized_instance,
        validated_hours,
        validated_maximum,
        ec2_client,
        cloudtrail_client,
    )
