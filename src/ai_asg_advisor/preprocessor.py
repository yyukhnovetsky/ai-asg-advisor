"""Data pre-processing — raw time-series to statistical summaries for LLM context."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime

from ai_asg_advisor.asg_collector import ASGConfig, ScalingPolicy
from ai_asg_advisor.cloudwatch_collector import MetricTimeSeries

logger = logging.getLogger(__name__)


@dataclass
class TimeSeriesStats:
    """Statistical summary of a time-series."""
    count: int = 0
    avg: float = 0.0
    min: float = 0.0
    max: float = 0.0
    p50: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    zero_pct: float = 0.0


@dataclass
class HourlyPattern:
    """Average values by hour-of-day (0-23), useful for identifying daily patterns."""
    hourly_avg: dict[int, float] = field(default_factory=dict)
    peak_hour: int = 0
    trough_hour: int = 0


@dataclass
class QueueDepthSummary:
    """Processed queue depth data ready for LLM context."""
    queue_name: str
    period_start: str
    period_end: str
    stats: TimeSeriesStats
    hourly_pattern: HourlyPattern
    raw_point_count: int


@dataclass
class ASGCapacitySummary:
    """Processed ASG capacity data ready for LLM context."""
    asg_name: str
    period_start: str
    period_end: str
    desired_stats: TimeSeriesStats
    max_size_stats: TimeSeriesStats
    desired_hourly_pattern: HourlyPattern


@dataclass
class AnalysisContext:
    """Complete pre-processed data bundle for the LLM advisor."""
    queue_summary: QueueDepthSummary
    asg_config: ASGConfig
    capacity_summary: ASGCapacitySummary
    current_policies: list[ScalingPolicy]


def compute_stats(values: list[float]) -> TimeSeriesStats:
    """Compute statistical summary from a list of values."""
    if not values:
        return TimeSeriesStats()

    sorted_vals = sorted(values)
    n = len(sorted_vals)

    return TimeSeriesStats(
        count=n,
        avg=sum(sorted_vals) / n,
        min=sorted_vals[0],
        max=sorted_vals[-1],
        p50=_percentile(sorted_vals, 50),
        p95=_percentile(sorted_vals, 95),
        p99=_percentile(sorted_vals, 99),
        zero_pct=round((sum(1 for v in sorted_vals if v == 0.0) / n) * 100, 2),
    )


def compute_hourly_pattern(timestamps: list[datetime], values: list[float]) -> HourlyPattern:
    """Compute average value per hour-of-day across the time range."""
    if not timestamps or not values:
        return HourlyPattern()

    hourly_sums: dict[int, float] = {}
    hourly_counts: dict[int, int] = {}

    for ts, val in zip(timestamps, values):
        hour = ts.hour
        hourly_sums[hour] = hourly_sums.get(hour, 0.0) + val
        hourly_counts[hour] = hourly_counts.get(hour, 0) + 1

    hourly_avg = {
        h: round(hourly_sums[h] / hourly_counts[h], 2)
        for h in sorted(hourly_sums)
    }

    if hourly_avg:
        peak_hour = max(hourly_avg, key=hourly_avg.get)
        trough_hour = min(hourly_avg, key=hourly_avg.get)
    else:
        peak_hour = 0
        trough_hour = 0

    return HourlyPattern(
        hourly_avg=hourly_avg,
        peak_hour=peak_hour,
        trough_hour=trough_hour,
    )


def summarize_queue_depth(
    queue_name: str,
    metrics: MetricTimeSeries,
) -> QueueDepthSummary:
    """Pre-process raw queue depth time-series into an LLM-ready summary."""
    stats = compute_stats(metrics.values)
    hourly = compute_hourly_pattern(metrics.timestamps, metrics.values)

    period_start = min(metrics.timestamps).isoformat() if metrics.timestamps else ""
    period_end = max(metrics.timestamps).isoformat() if metrics.timestamps else ""

    logger.info(
        "Queue '%s' summary: %d points, avg=%.1f, p95=%.1f, max=%.1f, zero=%.1f%%",
        queue_name, stats.count, stats.avg, stats.p95, stats.max, stats.zero_pct,
    )

    return QueueDepthSummary(
        queue_name=queue_name,
        period_start=period_start,
        period_end=period_end,
        stats=stats,
        hourly_pattern=hourly,
        raw_point_count=len(metrics.values),
    )


def summarize_asg_capacity(
    asg_name: str,
    capacity_metrics: dict[str, MetricTimeSeries],
) -> ASGCapacitySummary:
    """Pre-process raw ASG capacity time-series into an LLM-ready summary."""
    desired = capacity_metrics.get("desired_capacity")
    max_size = capacity_metrics.get("max_size")

    desired_stats = compute_stats(desired.values) if desired else TimeSeriesStats()
    max_size_stats = compute_stats(max_size.values) if max_size else TimeSeriesStats()

    desired_hourly = (
        compute_hourly_pattern(desired.timestamps, desired.values)
        if desired
        else HourlyPattern()
    )

    period_start = ""
    period_end = ""
    if desired and desired.timestamps:
        period_start = min(desired.timestamps).isoformat()
        period_end = max(desired.timestamps).isoformat()

    logger.info(
        "ASG '%s' capacity summary: desired avg=%.1f, max_observed=%.0f, max_size avg=%.1f",
        asg_name, desired_stats.avg, desired_stats.max, max_size_stats.avg,
    )

    return ASGCapacitySummary(
        asg_name=asg_name,
        period_start=period_start,
        period_end=period_end,
        desired_stats=desired_stats,
        max_size_stats=max_size_stats,
        desired_hourly_pattern=desired_hourly,
    )


def build_analysis_context(
    queue_name: str,
    queue_metrics: MetricTimeSeries,
    asg_config: ASGConfig,
    capacity_metrics: dict[str, MetricTimeSeries],
    policies: list[ScalingPolicy],
) -> AnalysisContext:
    """Build the complete pre-processed data bundle for the LLM advisor."""
    return AnalysisContext(
        queue_summary=summarize_queue_depth(queue_name, queue_metrics),
        asg_config=asg_config,
        capacity_summary=summarize_asg_capacity(asg_config.name, capacity_metrics),
        current_policies=policies,
    )


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Compute percentile from a pre-sorted list using linear interpolation."""
    n = len(sorted_values)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_values[0]

    rank = (pct / 100) * (n - 1)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))

    if low == high:
        return sorted_values[low]

    fraction = rank - low
    return sorted_values[low] + fraction * (sorted_values[high] - sorted_values[low])
