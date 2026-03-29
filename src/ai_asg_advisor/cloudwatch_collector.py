"""CloudWatch metrics collector — RabbitMQ queue depth and ASG capacity metrics."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

PERIOD_MAP = {
    "1w": timedelta(weeks=1),
    "2w": timedelta(weeks=2),
    "3w": timedelta(weeks=3),
    "1m": timedelta(days=30),
    "2m": timedelta(days=60),
}

# CloudWatch resolution: use 5-minute periods for <=2w, 1-hour for longer
_SHORT_PERIOD_THRESHOLD = timedelta(weeks=2)
_SHORT_PERIOD_SECONDS = 300      # 5 minutes
_LONG_PERIOD_SECONDS = 3600      # 1 hour

OTEL_LIB_DIMENSION = "github.com/open-telemetry/opentelemetry-collector-contrib/receiver/prometheusreceiver"

DEFAULT_QUEUE_NAMESPACE = "cloudamqp/fat-plum-goat"
VALID_QUEUE_NAMESPACES = ("cloudamqp/fat-plum-goat", "CloudAMQP/metrics")


@dataclass
class MetricTimeSeries:
    """Raw time-series data from CloudWatch."""
    label: str
    timestamps: list[datetime] = field(default_factory=list)
    values: list[float] = field(default_factory=list)


def parse_period(period_str: str) -> timedelta:
    """Convert a period string like '2w' to a timedelta."""
    if period_str not in PERIOD_MAP:
        raise ValueError(f"Invalid period: {period_str}. Valid: {list(PERIOD_MAP.keys())}")
    return PERIOD_MAP[period_str]


def _metric_period_seconds(duration: timedelta) -> int:
    """Choose CloudWatch metric period based on analysis duration."""
    if duration <= _SHORT_PERIOD_THRESHOLD:
        return _SHORT_PERIOD_SECONDS
    return _LONG_PERIOD_SECONDS


def get_queue_depth_metrics(
    cw_client,
    queue_name: str,
    period: str,
    namespace: str = DEFAULT_QUEUE_NAMESPACE,
) -> MetricTimeSeries:
    """Fetch rabbitmq_detailed_queue_messages_ready for a specific queue.

    Uses GetMetricData with a MetricStat query to handle the OTel dimensions.
    """
    duration = parse_period(period)
    end_time = datetime.now(timezone.utc)
    start_time = end_time - duration
    metric_period = _metric_period_seconds(duration)

    logger.info(
        "Fetching queue depth for '%s' from %s to %s (period=%ds)",
        queue_name, start_time.isoformat(), end_time.isoformat(), metric_period,
    )

    response = cw_client.get_metric_data(
        MetricDataQueries=[
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
                    "Period": metric_period,
                    "Stat": "Average",
                },
                "ReturnData": True,
            }
        ],
        StartTime=start_time,
        EndTime=end_time,
        ScanBy="TimestampAscending",
    )

    result = response["MetricDataResults"][0]

    # Handle pagination — CloudWatch may return partial results
    all_timestamps = list(result["Timestamps"])
    all_values = list(result["Values"])

    while response.get("NextToken"):
        response = cw_client.get_metric_data(
            MetricDataQueries=[
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
                        "Period": metric_period,
                        "Stat": "Average",
                    },
                    "ReturnData": True,
                }
            ],
            StartTime=start_time,
            EndTime=end_time,
            ScanBy="TimestampAscending",
            NextToken=response["NextToken"],
        )
        page_result = response["MetricDataResults"][0]
        all_timestamps.extend(page_result["Timestamps"])
        all_values.extend(page_result["Values"])

    logger.info("Retrieved %d data points for queue '%s'", len(all_values), queue_name)

    return MetricTimeSeries(
        label=f"queue_depth:{queue_name}",
        timestamps=all_timestamps,
        values=all_values,
    )


def get_asg_capacity_metrics(
    cw_client,
    asg_name: str,
    period: str,
) -> dict[str, MetricTimeSeries]:
    """Fetch GroupDesiredCapacity and GroupMaxSize metrics for an ASG.

    Returns a dict with keys 'desired_capacity' and 'max_size'.
    """
    duration = parse_period(period)
    end_time = datetime.now(timezone.utc)
    start_time = end_time - duration
    metric_period = _metric_period_seconds(duration)

    logger.info(
        "Fetching ASG capacity metrics for '%s' from %s to %s",
        asg_name, start_time.isoformat(), end_time.isoformat(),
    )

    metrics_to_fetch = [
        ("desired_capacity", "GroupDesiredCapacity", "Average"),
        ("max_size", "GroupMaxSize", "Maximum"),
    ]

    queries = [
        {
            "Id": metric_id,
            "MetricStat": {
                "Metric": {
                    "Namespace": "AWS/AutoScaling",
                    "MetricName": metric_name,
                    "Dimensions": [
                        {"Name": "AutoScalingGroupName", "Value": asg_name},
                    ],
                },
                "Period": metric_period,
                "Stat": stat,
            },
            "ReturnData": True,
        }
        for metric_id, metric_name, stat in metrics_to_fetch
    ]

    response = cw_client.get_metric_data(
        MetricDataQueries=queries,
        StartTime=start_time,
        EndTime=end_time,
        ScanBy="TimestampAscending",
    )

    results = {}
    for metric_result in response["MetricDataResults"]:
        metric_id = metric_result["Id"]
        results[metric_id] = MetricTimeSeries(
            label=f"{asg_name}:{metric_id}",
            timestamps=list(metric_result["Timestamps"]),
            values=list(metric_result["Values"]),
        )

    # Handle pagination
    while response.get("NextToken"):
        response = cw_client.get_metric_data(
            MetricDataQueries=queries,
            StartTime=start_time,
            EndTime=end_time,
            ScanBy="TimestampAscending",
            NextToken=response["NextToken"],
        )
        for metric_result in response["MetricDataResults"]:
            metric_id = metric_result["Id"]
            results[metric_id].timestamps.extend(metric_result["Timestamps"])
            results[metric_id].values.extend(metric_result["Values"])

    for metric_id, ts in results.items():
        logger.info("Retrieved %d data points for %s:%s", len(ts.values), asg_name, metric_id)

    return results
