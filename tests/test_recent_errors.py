from collections.abc import Iterator
from typing import Any

import pytest

from aws_infra_ops_mcp.tools.recent_errors import inspect_recent_errors


class FakeLogs:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses: Iterator[dict[str, Any]] = iter(responses)
        self.start_request: dict[str, Any] | None = None
        self.result_requests: list[dict[str, Any]] = []
        self.stop_request: dict[str, Any] | None = None

    def start_query(self, **kwargs: Any) -> dict[str, str]:
        self.start_request = kwargs
        return {"queryId": "test-query-id"}

    def get_query_results(self, **kwargs: Any) -> dict[str, Any]:
        self.result_requests.append(kwargs)
        return next(self.responses)

    def stop_query(self, **kwargs: Any) -> dict[str, bool]:
        self.stop_request = kwargs
        return {"success": True}


def ticking_clock(values: list[float]):
    times = iter(values)
    return lambda: next(times)


def test_completed_query_with_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_LOG_GROUP_PREFIX", raising=False)
    client = FakeLogs(
        [
            {"status": "Running", "results": []},
            {
                "status": "Complete",
                "results": [
                    [
                        {"field": "@timestamp", "value": "2026-07-30 03:00:00.000"},
                        {"field": "@message", "value": "nginx failed to start"},
                        {
                            "field": "@log",
                            "value": "123456789012:/aws/mcp-lab/web01/nginx",
                        },
                        {"field": "@logStream", "value": "i-123"},
                        {"field": "@ptr", "value": "not-returned"},
                    ]
                ],
            },
        ]
    )

    result = inspect_recent_errors(
        " WEB01 ",
        10,
        60,
        client,
        monotonic=ticking_clock([0.0, 0.1, 0.2, 0.3]),
        wall_clock=lambda: 10_000.0,
        sleep=lambda _: None,
    )

    assert result == {
        "instance_name": "web01",
        "lookback_minutes": 60,
        "result_count": 1,
        "errors": [
            {
                "timestamp": "2026-07-30 03:00:00.000",
                "message": "nginx failed to start",
                "log_group": "/aws/mcp-lab/web01/nginx",
                "log_stream": "i-123",
            }
        ],
        "data_source": "aws-cloudwatch",
    }
    assert "123456789012" not in repr(result)
    assert "test-query-id" not in repr(result)


def test_completed_query_with_no_results() -> None:
    result = inspect_recent_errors(
        "web01",
        10,
        60,
        FakeLogs([{"status": "Complete", "results": []}]),
        monotonic=lambda: 0.0,
        wall_clock=lambda: 10_000.0,
    )

    assert result["result_count"] == 0
    assert result["errors"] == []
    assert result["data_source"] == "aws-cloudwatch"


def test_returned_message_content_is_bounded() -> None:
    client = FakeLogs(
        [
            {
                "status": "Complete",
                "results": [
                    [
                        {"field": "@message", "value": "x" * 5000},
                        {"field": "@logStream", "value": "s" * 1000},
                    ]
                ],
            }
        ]
    )

    result = inspect_recent_errors(
        "web01",
        10,
        60,
        client,
        monotonic=lambda: 0.0,
        wall_clock=lambda: 10_000.0,
    )

    assert len(result["errors"][0]["message"]) == 4000
    assert len(result["errors"][0]["log_stream"]) == 512


def test_queries_only_both_approved_log_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_LOG_GROUP_PREFIX", "/company/lab/")
    client = FakeLogs([{"status": "Complete", "results": []}])

    inspect_recent_errors(
        "web01",
        7,
        15,
        client,
        monotonic=lambda: 0.0,
        wall_clock=lambda: 10_000.9,
    )

    assert client.start_request == {
        "logGroupNames": [
            "/company/lab/web01/system",
            "/company/lab/web01/nginx",
        ],
        "startTime": 9_100,
        "endTime": 10_000,
        "queryString": (
            "fields @timestamp, @message, @log, @logStream\n"
            "| filter @message like "
            "/(?i)(error|failed|failure|critical|timeout|denied)/\n"
            "| sort @timestamp desc\n"
            "| limit 7"
        ),
        "limit": 7,
    }


@pytest.mark.parametrize("status", ["Failed", "Cancelled", "Timeout", "Unknown"])
def test_terminal_query_error(status: str) -> None:
    client = FakeLogs([{"status": status}])

    with pytest.raises(RuntimeError, match=f"status {status}"):
        inspect_recent_errors(
            "web01",
            10,
            60,
            client,
            monotonic=lambda: 0.0,
            wall_clock=lambda: 10_000.0,
        )

    assert client.stop_request is None


def test_local_polling_timeout_stops_query() -> None:
    client = FakeLogs([{"status": "Running"}])

    with pytest.raises(RuntimeError, match="local polling timeout"):
        inspect_recent_errors(
            "web01",
            10,
            60,
            client,
            monotonic=ticking_clock([0.0, 10.0]),
            wall_clock=lambda: 10_000.0,
            sleep=lambda _: None,
        )

    assert client.stop_request == {"queryId": "test-query-id"}


@pytest.mark.parametrize("maximum_results", [0, 51])
def test_invalid_result_limit_prevents_aws_call(maximum_results: int) -> None:
    client = FakeLogs([])

    with pytest.raises(ValueError, match="between 1 and 50"):
        inspect_recent_errors("web01", maximum_results, 60, client)

    assert client.start_request is None


@pytest.mark.parametrize("minutes", [4, 1441])
def test_invalid_lookback_prevents_aws_call(minutes: int) -> None:
    client = FakeLogs([])

    with pytest.raises(ValueError, match="between 5 and 1440"):
        inspect_recent_errors("web01", 10, minutes, client)

    assert client.start_request is None


def test_instance_allowlist_rejection_prevents_aws_call() -> None:
    client = FakeLogs([])

    with pytest.raises(ValueError, match="not approved"):
        inspect_recent_errors("database01", 10, 60, client)

    assert client.start_request is None
