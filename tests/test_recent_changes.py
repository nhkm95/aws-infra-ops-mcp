from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from botocore.exceptions import ClientError
import pytest

import aws_infra_ops_mcp.tools.recent_changes as changes_module
from aws_infra_ops_mcp.policy import (
    ALLOWED_CHANGE_LOOKBACK_HOURS,
    ALLOWED_CHANGE_RESULT_LIMITS,
)
from aws_infra_ops_mcp.tools.recent_changes import (
    ALLOWED_EVENT_NAMES,
    inspect_recent_changes,
)

NOW = datetime(2026, 7, 31, 6, 0, tzinfo=timezone.utc)
TERRAFORM_POLICY = (
    Path(__file__).parents[1]
    / "infrastructure"
    / "modules"
    / "mcp_readonly"
    / "main.tf"
)


class FakeEc2:
    def __init__(
        self,
        region: str = "ap-southeast-1",
        instance_id: str = "i-approved",
    ) -> None:
        self.meta = SimpleNamespace(region_name=region)
        self.instance_id = instance_id
        self.request: dict[str, Any] | None = None

    def describe_instances(self, **kwargs: Any) -> dict[str, Any]:
        self.request = kwargs
        return {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": self.instance_id,
                            "State": {"Name": "running"},
                        }
                    ]
                }
            ]
        }


class FakeCloudTrail:
    def __init__(
        self,
        responses: list[dict[str, Any]] | None = None,
        *,
        error: Exception | None = None,
        region: str = "ap-southeast-1",
    ) -> None:
        self.meta = SimpleNamespace(region_name=region)
        self.responses = iter(responses or [])
        self.error = error
        self.requests: list[dict[str, Any]] = []

    def lookup_events(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return next(self.responses, {"Events": []})


def lookup_event(
    event_name: str = "StartSession",
    *,
    instance_id: str = "i-approved",
    event_time: datetime = NOW,
    event_source: str = "ssm.amazonaws.com",
    request_parameters: dict[str, Any] | None = None,
    resources: list[dict[str, str]] | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    detail = {
        "eventTime": event_time.isoformat(),
        "eventName": event_name,
        "eventSource": event_source,
        "readOnly": False,
        "requestParameters": (
            {"target": instance_id}
            if request_parameters is None
            else request_parameters
        ),
        "userIdentity": {
            "arn": "arn:aws:sts::123456789012:assumed-role/Ops/alice",
            "accessKeyId": "AKIA-SECRET",
            "sessionContext": {
                "sessionIssuer": {
                    "arn": "arn:aws:iam::123456789012:role/Ops",
                    "userName": "Ops",
                },
                "sessionToken": "secret-token",
            },
        },
        "sourceIPAddress": "198.51.100.1",
        "userAgent": "sensitive-agent",
        "requestHeaders": {"authorization": "secret"},
    }
    result = {
        "EventName": event_name,
        "EventTime": event_time,
        "Resources": resources or [],
        "CloudTrailEvent": json.dumps(detail),
    }
    if event_id is not None:
        result["EventId"] = event_id
    return result


def inspect(
    cloudtrail: FakeCloudTrail,
    *,
    hours: int = 24,
    maximum_results: int = 25,
    ec2: FakeEc2 | None = None,
) -> dict[str, Any]:
    return inspect_recent_changes(
        "web01",
        hours,
        maximum_results,
        ec2 or FakeEc2(),
        cloudtrail,
        clock=lambda: NOW,
    )


def test_successful_matching_event_and_fixed_lookup() -> None:
    cloudtrail = FakeCloudTrail([{"Events": [lookup_event()]}])

    result = inspect(cloudtrail)

    assert result == {
        "instance_name": "web01",
        "instance_id": "i-approved",
        "region": "ap-southeast-1",
        "lookback_hours": 24,
        "maximum_results": 25,
        "result_count": 1,
        "events": [
            {
                "event_time": "2026-07-31T06:00:00+00:00",
                "event_name": "StartSession",
                "event_source": "ssm.amazonaws.com",
                "actor": "arn:aws:sts::123456789012:assumed-role/Ops/alice",
                "read_only": False,
                "matched_on": "request_parameters.target",
            }
        ],
        "truncated": False,
        "data_source": "aws-cloudtrail-event-history",
        "checked_at": "2026-07-31T06:00:00+00:00",
        "limitations": [
            (
                "CloudTrail records AWS API activity, not commands entered "
                "through SSH or inside an SSM session"
            ),
            (
                "Actor values are CloudTrail attribution and do not prove "
                "which human intended an action"
            ),
            (
                "Results are limited to the fixed event allowlist and events "
                "that reference the approved instance ID"
            ),
        ],
    }
    assert len(cloudtrail.requests) == len(ALLOWED_EVENT_NAMES)
    assert {
        request["LookupAttributes"][0]["AttributeValue"]
        for request in cloudtrail.requests
    } == ALLOWED_EVENT_NAMES
    for request in cloudtrail.requests:
        assert request == {
            "LookupAttributes": [
                {
                    "AttributeKey": "EventName",
                    "AttributeValue": request["LookupAttributes"][0][
                        "AttributeValue"
                    ],
                }
            ],
            "StartTime": NOW - timedelta(hours=24),
            "EndTime": NOW,
            "MaxResults": 50,
        }


def test_resource_reference_matches_and_events_are_sorted_newest_first() -> None:
    older = lookup_event(
        "StopInstances",
        event_time=NOW - timedelta(hours=2),
        event_source="ec2.amazonaws.com",
        request_parameters={},
        resources=[{"ResourceName": "i-approved"}],
    )
    newer = lookup_event(event_time=NOW - timedelta(hours=1))
    result = inspect(FakeCloudTrail([{"Events": [older, newer]}]))

    assert [event["event_name"] for event in result["events"]] == [
        "StartSession",
        "StopInstances",
    ]
    assert result["events"][1]["matched_on"] == (
        "lookup_resources.resource_name"
    )


def test_parsed_cloudtrail_resource_arn_matches_exact_instance() -> None:
    event = lookup_event(
        "StopInstances",
        event_source="ec2.amazonaws.com",
        request_parameters={},
    )
    detail = json.loads(event["CloudTrailEvent"])
    detail["resources"] = [
        {
            "ARN": (
                "arn:aws:ec2:ap-southeast-1:123456789012:"
                "instance/i-approved"
            )
        }
    ]
    event["CloudTrailEvent"] = json.dumps(detail)

    result = inspect(FakeCloudTrail([{"Events": [event]}]))

    assert result["events"][0]["matched_on"] == "cloudtrail_event.resources"


def test_unrelated_instance_event_is_excluded() -> None:
    result = inspect(
        FakeCloudTrail([{"Events": [lookup_event(instance_id="i-unrelated")]}])
    )
    assert result["events"] == []
    assert result["result_count"] == 0


@pytest.mark.parametrize(
    ("request_parameters", "matched_on"),
    [
        (
            {"instanceIds": ["i-approved"]},
            "request_parameters.instance_ids",
        ),
        (
            {
                "targets": [
                    {"Key": "InstanceIds", "Values": ["i-approved"]}
                ]
            },
            "request_parameters.targets.instance_ids",
        ),
    ],
)
def test_send_command_matches_explicit_instance_request_shapes(
    request_parameters: dict[str, Any], matched_on: str
) -> None:
    event = lookup_event(
        "SendCommand",
        request_parameters=request_parameters,
    )

    result = inspect(FakeCloudTrail([{"Events": [event]}]))

    assert result["result_count"] == 1
    assert result["events"][0]["matched_on"] == matched_on


def test_start_session_matches_target_with_empty_lookup_resources() -> None:
    event = lookup_event(
        "StartSession",
        request_parameters={"target": "i-approved"},
    )

    result = inspect(FakeCloudTrail([{"Events": [event]}]))

    assert event["Resources"] == []
    assert result["events"][0]["matched_on"] == "request_parameters.target"


def test_real_instance_ids_are_matched_exactly_for_web01() -> None:
    approved_id = "i-027c58992375ac593"
    unrelated_id = "i-08a6bbb9f1ebeeb7c"
    events = [
        lookup_event(
            "SendCommand",
            instance_id=unrelated_id,
            request_parameters={"instanceIds": [unrelated_id]},
        ),
        lookup_event(
            "StartSession",
            instance_id=approved_id,
            request_parameters={"target": approved_id},
        ),
    ]

    result = inspect(
        FakeCloudTrail([{"Events": events}]),
        ec2=FakeEc2(instance_id=approved_id),
    )

    assert result["instance_id"] == approved_id
    assert [event["event_name"] for event in result["events"]] == [
        "StartSession"
    ]


@pytest.mark.parametrize(
    ("event_name", "request_parameters"),
    [
        ("SendCommand", {"instanceIds": ["i-other"]}),
        (
            "SendCommand",
            {"targets": [{"Key": "InstanceIds", "Values": ["i-other"]}]},
        ),
        ("StartSession", {"target": "i-other"}),
    ],
)
def test_request_shapes_for_other_instance_are_rejected(
    event_name: str, request_parameters: dict[str, Any]
) -> None:
    event = lookup_event(event_name, request_parameters=request_parameters)

    result = inspect(FakeCloudTrail([{"Events": [event]}]))

    assert result["events"] == []


@pytest.mark.parametrize("request_parameters", [None, "malformed", [], 42])
def test_malformed_request_parameters_are_rejected(
    request_parameters: Any,
) -> None:
    event = lookup_event(request_parameters={})
    detail = json.loads(event["CloudTrailEvent"])
    detail["requestParameters"] = request_parameters
    event["CloudTrailEvent"] = json.dumps(detail)

    result = inspect(FakeCloudTrail([{"Events": [event]}]))

    assert result["events"] == []


@pytest.mark.parametrize(
    "key",
    ["tag:Name", "tag-key", "resource-groups:Name"],
)
def test_send_command_tag_targets_are_not_instance_matches(key: str) -> None:
    event = lookup_event(
        "SendCommand",
        request_parameters={
            "targets": [{"Key": key, "Values": ["i-approved"]}]
        },
    )

    result = inspect(FakeCloudTrail([{"Events": [event]}]))

    assert result["events"] == []


def test_terminate_session_id_is_not_attributed_to_instance() -> None:
    event = lookup_event(
        "TerminateSession",
        request_parameters={"sessionId": "alice-i-approved"},
    )

    result = inspect(FakeCloudTrail([{"Events": [event]}]))

    assert result["events"] == []


@pytest.mark.parametrize(
    ("event_name", "event_source"),
    [
        ("DeleteTrail", "cloudtrail.amazonaws.com"),
        ("AuthorizeSecurityGroupIngress", "ec2.amazonaws.com"),
        ("StartSession", "unapproved.amazonaws.com"),
    ],
)
def test_fixed_event_and_source_allowlist_is_enforced(
    event_name: str, event_source: str
) -> None:
    result = inspect(
        FakeCloudTrail(
            [
                {
                    "Events": [
                        lookup_event(event_name, event_source=event_source)
                    ]
                }
            ]
        )
    )
    assert result["events"] == []


def test_allowlist_contains_exact_fixed_scope() -> None:
    assert ALLOWED_EVENT_NAMES == {
        "StartInstances",
        "StopInstances",
        "RebootInstances",
        "ModifyInstanceAttribute",
        "AssociateIamInstanceProfile",
        "ReplaceIamInstanceProfileAssociation",
        "SendCommand",
        "StartSession",
        "TerminateSession",
        "CreateAssociation",
        "UpdateAssociation",
        "DeleteAssociation",
    }


def test_sensitive_cloudtrail_fields_are_not_returned() -> None:
    result = inspect(FakeCloudTrail([{"Events": [lookup_event()]}]))
    serialized = json.dumps(result)

    for sensitive in [
        "AKIA-SECRET",
        "secret-token",
        "198.51.100.1",
        "sensitive-agent",
        "authorization",
        "requestHeaders",
        "userIdentity",
        "CloudTrailEvent",
    ]:
        assert sensitive not in serialized


@pytest.mark.parametrize("hours", sorted(ALLOWED_CHANGE_LOOKBACK_HOURS))
def test_each_approved_lookback(hours: int) -> None:
    result = inspect(FakeCloudTrail([{"Events": []}]), hours=hours)
    assert result["lookback_hours"] == hours


@pytest.mark.parametrize("maximum", sorted(ALLOWED_CHANGE_RESULT_LIMITS))
def test_each_approved_result_limit(maximum: int) -> None:
    result = inspect(
        FakeCloudTrail([{"Events": []}]),
        maximum_results=maximum,
    )
    assert result["maximum_results"] == maximum


@pytest.mark.parametrize("hours", [0, 2, 25, 169])
def test_rejected_lookbacks_make_no_aws_calls(hours: int) -> None:
    ec2 = FakeEc2()
    cloudtrail = FakeCloudTrail()
    with pytest.raises(ValueError, match="hours must be one of"):
        inspect_recent_changes("web01", hours, 25, ec2, cloudtrail)
    assert ec2.request is None
    assert cloudtrail.requests == []


@pytest.mark.parametrize("maximum", [0, 9, 11, 26, 100])
def test_rejected_result_limits_make_no_aws_calls(maximum: int) -> None:
    ec2 = FakeEc2()
    cloudtrail = FakeCloudTrail()
    with pytest.raises(ValueError, match="maximum_results must be one of"):
        inspect_recent_changes("web01", 24, maximum, ec2, cloudtrail)
    assert ec2.request is None
    assert cloudtrail.requests == []


def test_pagination_uses_next_token() -> None:
    cloudtrail = FakeCloudTrail(
        [
            {"Events": [], "NextToken": "opaque-token"},
            {"Events": [lookup_event()]},
        ]
    )
    result = inspect(cloudtrail)

    assert result["result_count"] == 1
    assert len(cloudtrail.requests) == len(ALLOWED_EVENT_NAMES) + 1
    assert cloudtrail.requests[1]["NextToken"] == "opaque-token"


def test_pagination_is_bounded_per_fixed_event_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(changes_module, "MAX_LOOKUP_PAGES_PER_EVENT", 2)
    cloudtrail = FakeCloudTrail(
        [
            {"Events": [], "NextToken": "page-2"},
            {"Events": [], "NextToken": "page-3"},
        ]
    )

    result = inspect(cloudtrail)

    assert len(cloudtrail.requests) == len(ALLOWED_EVENT_NAMES) + 1
    assert cloudtrail.requests[1]["NextToken"] == "page-2"
    assert all(
        request.get("NextToken") != "page-3"
        for request in cloudtrail.requests
    )
    assert result["truncated"] is True


def test_duplicate_event_ids_are_returned_once() -> None:
    duplicate = lookup_event(event_id="event-123")

    result = inspect(
        FakeCloudTrail([{"Events": [duplicate, duplicate.copy()]}])
    )

    assert result["result_count"] == 1


def test_maximum_results_is_applied_after_matching_dedup_and_sorting() -> None:
    older = [
        lookup_event(
            event_time=NOW - timedelta(minutes=20 + index),
            event_id=f"older-{index}",
        )
        for index in range(10)
    ]
    newer = lookup_event(
        event_time=NOW - timedelta(minutes=1),
        event_id="newer",
    )
    cloudtrail = FakeCloudTrail(
        [
            {"Events": older, "NextToken": "next-page"},
            {"Events": [newer]},
        ]
    )

    result = inspect(cloudtrail, maximum_results=10)

    assert result["result_count"] == 10
    assert result["events"][0]["event_time"] == (
        NOW - timedelta(minutes=1)
    ).isoformat()
    assert result["truncated"] is True


def test_result_limit_marks_truncation() -> None:
    events = [
        lookup_event(event_time=NOW - timedelta(minutes=index))
        for index in range(11)
    ]
    result = inspect(
        FakeCloudTrail([{"Events": events}]),
        maximum_results=10,
    )
    assert result["result_count"] == 10
    assert result["truncated"] is True


def test_empty_results_are_distinct_from_failure() -> None:
    result = inspect(FakeCloudTrail([{"Events": []}]))
    assert result["events"] == []
    assert result["result_count"] == 0
    assert result["truncated"] is False


@pytest.mark.parametrize("raw", ["{not-json", "[]", None])
def test_malformed_cloudtrail_event_is_skipped(raw: Any) -> None:
    malformed = lookup_event()
    malformed["CloudTrailEvent"] = raw
    result = inspect(FakeCloudTrail([{"Events": [malformed]}]))
    assert result["events"] == []


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("AccessDeniedException", "AccessDeniedException"),
        ("ThrottlingException", "ThrottlingException"),
    ],
)
def test_access_denied_and_throttling_are_controlled(
    code: str, message: str
) -> None:
    error = ClientError(
        {"Error": {"Code": code, "Message": "request rejected"}},
        "LookupEvents",
    )
    with pytest.raises(RuntimeError, match=message):
        inspect(FakeCloudTrail(error=error))


def test_region_mismatch_is_rejected_before_lookup() -> None:
    cloudtrail = FakeCloudTrail(region="us-east-1")
    with pytest.raises(RuntimeError, match="same configured AWS Region"):
        inspect(cloudtrail)
    assert cloudtrail.requests == []


def test_public_wrapper_uses_injected_factories_without_live_aws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ec2 = FakeEc2()
    cloudtrail = FakeCloudTrail([{"Events": []}])
    monkeypatch.setattr(changes_module, "create_ec2_client", lambda: ec2)
    monkeypatch.setattr(
        changes_module,
        "create_cloudtrail_client",
        lambda: cloudtrail,
    )
    monkeypatch.setattr(changes_module, "create_sts_client", object)
    monkeypatch.setattr(changes_module, "validate_runtime_identity", lambda _: {})

    result = changes_module.get_recent_changes("web01")

    assert result["lookback_hours"] == 24
    assert result["maximum_results"] == 25
    assert len(cloudtrail.requests) == len(ALLOWED_EVENT_NAMES)


def test_iam_adds_only_cloudtrail_lookup_events() -> None:
    terraform = TERRAFORM_POLICY.read_text(encoding="utf-8")

    assert 'sid     = "ReadCloudTrailEventHistory"' in terraform
    assert 'actions = ["cloudtrail:LookupEvents"]' in terraform
    assert terraform.count("cloudtrail:") == 1
    assert "resources = [\"*\"]" in terraform


def test_iam_policy_identity_remains_stable_when_capabilities_change() -> None:
    terraform = TERRAFORM_POLICY.read_text(encoding="utf-8")
    policy_resource = terraform.split(
        'resource "aws_iam_policy" "this" {',
        maxsplit=1,
    )[1].split("\n}", maxsplit=1)[0]

    assert (
        'name        = "aws-infra-ops-mcp-lab-diagnostics-readonly"'
        in policy_resource
    )
    assert (
        'description = "Read-only EC2, CloudWatch metrics and Logs lookup '
        'for the Infrastructure Operations MCP"'
    ) in policy_resource
    assert "name_prefix" not in policy_resource
    assert 'actions = ["cloudtrail:LookupEvents"]' in terraform
