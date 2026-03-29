"""Tests for CloudWatch metrics collector."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import boto3
import pytest
from botocore.stub import Stubber

from ai_asg_advisor.cloudwatch_collector import (
    DEFAULT_QUEUE_NAMESPACE,
    OTEL_LIB_DIMENSION,
    MetricTimeSeries,
    get_asg_capacity_metrics,
    get_queue_depth_metrics,
    parse_period,
)


class TestParsePeriod:
    def test_valid_periods(self):
        assert parse_period("1w") == timedelta(weeks=1)
        assert parse_period("2w") == timedelta(weeks=2)
        assert parse_period("3w") == timedelta(weeks=3)
        assert parse_period("1m") == timedelta(days=30)
        assert parse_period("2m") == timedelta(days=60)

    def test_invalid_period(self):
        with pytest.raises(ValueError, match="Invalid period"):
            parse_period("5y")


class TestGetQueueDepthMetrics:
    def _make_cw_client(self):
        session = boto3.Session(region_name="us-east-1")
        return session.client("cloudwatch", region_name="us-east-1")

    def test_returns_time_series(self):
        cw = self._make_cw_client()
        ts1 = datetime(2025, 2, 20, 12, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2025, 2, 20, 12, 5, 0, tzinfo=timezone.utc)

        with Stubber(cw) as stubber:
            stubber.add_response(
                "get_metric_data",
                {
                    "MetricDataResults": [
                        {
                            "Id": "queue_depth",
                            "Label": "queue_depth",
                            "Timestamps": [ts1, ts2],
                            "Values": [100.0, 250.0],
                            "StatusCode": "Complete",
                        }
                    ],
                },
                expected_params=_queue_depth_expected_params("email_sync_v2", 300),
            )

            result = get_queue_depth_metrics(cw, "email_sync_v2", "1w")

            assert isinstance(result, MetricTimeSeries)
            assert result.label == "queue_depth:email_sync_v2"
            assert len(result.timestamps) == 2
            assert result.values == [100.0, 250.0]

    def test_uses_longer_period_for_1m(self):
        """Periods longer than 2w should use 3600s CloudWatch resolution."""
        cw = self._make_cw_client()

        with Stubber(cw) as stubber:
            stubber.add_response(
                "get_metric_data",
                {
                    "MetricDataResults": [
                        {
                            "Id": "queue_depth",
                            "Label": "queue_depth",
                            "Timestamps": [],
                            "Values": [],
                            "StatusCode": "Complete",
                        }
                    ],
                },
                expected_params=_queue_depth_expected_params("test_queue", 3600),
            )

            result = get_queue_depth_metrics(cw, "test_queue", "1m")
            assert result.values == []

    def test_handles_pagination(self):
        cw = self._make_cw_client()
        ts1 = datetime(2025, 2, 20, 12, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2025, 2, 20, 12, 5, 0, tzinfo=timezone.utc)

        with Stubber(cw) as stubber:
            # First page with NextToken
            stubber.add_response(
                "get_metric_data",
                {
                    "MetricDataResults": [
                        {
                            "Id": "queue_depth",
                            "Label": "queue_depth",
                            "Timestamps": [ts1],
                            "Values": [100.0],
                            "StatusCode": "Partial",
                        }
                    ],
                    "NextToken": "token123",
                },
                expected_params=_queue_depth_expected_params("my_queue", 300),
            )
            # Second page (no NextToken)
            stubber.add_response(
                "get_metric_data",
                {
                    "MetricDataResults": [
                        {
                            "Id": "queue_depth",
                            "Label": "queue_depth",
                            "Timestamps": [ts2],
                            "Values": [200.0],
                            "StatusCode": "Complete",
                        }
                    ],
                },
            )

            result = get_queue_depth_metrics(cw, "my_queue", "1w")
            assert len(result.values) == 2
            assert result.values == [100.0, 200.0]


class TestGetASGCapacityMetrics:
    def _make_cw_client(self):
        session = boto3.Session(region_name="us-east-1")
        return session.client("cloudwatch", region_name="us-east-1")

    def test_returns_desired_and_max(self):
        cw = self._make_cw_client()
        ts1 = datetime(2025, 2, 20, 12, 0, 0, tzinfo=timezone.utc)

        with Stubber(cw) as stubber:
            stubber.add_response(
                "get_metric_data",
                {
                    "MetricDataResults": [
                        {
                            "Id": "desired_capacity",
                            "Label": "desired_capacity",
                            "Timestamps": [ts1],
                            "Values": [5.0],
                            "StatusCode": "Complete",
                        },
                        {
                            "Id": "max_size",
                            "Label": "max_size",
                            "Timestamps": [ts1],
                            "Values": [20.0],
                            "StatusCode": "Complete",
                        },
                    ],
                },
                expected_params=_asg_capacity_expected_params("celery-email-sync-py39", 300),
            )

            result = get_asg_capacity_metrics(cw, "celery-email-sync-py39", "1w")

            assert "desired_capacity" in result
            assert "max_size" in result
            assert result["desired_capacity"].values == [5.0]
            assert result["max_size"].values == [20.0]


def _queue_depth_expected_params(queue_name: str, period: int, namespace: str = DEFAULT_QUEUE_NAMESPACE) -> dict:
    """Build expected params for queue depth stubber (ignoring timestamps)."""
    return {
        "MetricDataQueries": [
            {
                "Id": "queue_depth",
                "MetricStat": {
                    "Metric": {
                        "Namespace": namespace,
                        "MetricName": "rabbitmq_detailed_queue_messages_ready",
                        "Dimensions": [
                            {"Name": "OTelLib", "Value": OTEL_LIB_DIMENSION},
                            {"Name": "queue", "Value": queue_name},
                        ],
                    },
                    "Period": period,
                    "Stat": "Average",
                },
                "ReturnData": True,
            }
        ],
        "StartTime": stubber_any(),
        "EndTime": stubber_any(),
        "ScanBy": "TimestampAscending",
    }


def _asg_capacity_expected_params(asg_name: str, period: int) -> dict:
    """Build expected params for ASG capacity stubber."""
    return {
        "MetricDataQueries": [
            {
                "Id": "desired_capacity",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/AutoScaling",
                        "MetricName": "GroupDesiredCapacity",
                        "Dimensions": [
                            {"Name": "AutoScalingGroupName", "Value": asg_name},
                        ],
                    },
                    "Period": period,
                    "Stat": "Average",
                },
                "ReturnData": True,
            },
            {
                "Id": "max_size",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/AutoScaling",
                        "MetricName": "GroupMaxSize",
                        "Dimensions": [
                            {"Name": "AutoScalingGroupName", "Value": asg_name},
                        ],
                    },
                    "Period": period,
                    "Stat": "Maximum",
                },
                "ReturnData": True,
            },
        ],
        "StartTime": stubber_any(),
        "EndTime": stubber_any(),
        "ScanBy": "TimestampAscending",
    }


class _StubberAny:
    """Matches any value in botocore Stubber expected params."""
    def __eq__(self, other):
        return True


def stubber_any():
    return _StubberAny()
