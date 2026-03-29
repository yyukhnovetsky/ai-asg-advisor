"""Tests for data pre-processing module."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ai_asg_advisor.asg_collector import ASGConfig, ScalingPolicy
from ai_asg_advisor.cloudwatch_collector import MetricTimeSeries
from ai_asg_advisor.preprocessor import (
    AnalysisContext,
    build_analysis_context,
    compute_hourly_pattern,
    compute_stats,
    summarize_asg_capacity,
    summarize_queue_depth,
)


class TestComputeStats:
    def test_basic_stats(self):
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        stats = compute_stats(values)
        assert stats.count == 5
        assert stats.avg == 30.0
        assert stats.min == 10.0
        assert stats.max == 50.0
        assert stats.p50 == 30.0
        assert stats.zero_pct == 0.0

    def test_with_zeros(self):
        values = [0.0, 0.0, 0.0, 100.0, 200.0]
        stats = compute_stats(values)
        assert stats.count == 5
        assert stats.zero_pct == 60.0
        assert stats.min == 0.0

    def test_single_value(self):
        stats = compute_stats([42.0])
        assert stats.count == 1
        assert stats.avg == 42.0
        assert stats.p50 == 42.0
        assert stats.p95 == 42.0

    def test_empty(self):
        stats = compute_stats([])
        assert stats.count == 0
        assert stats.avg == 0.0

    def test_percentiles(self):
        # 100 evenly spaced values: 1..100
        values = [float(i) for i in range(1, 101)]
        stats = compute_stats(values)
        assert stats.p50 == pytest.approx(50.5, abs=0.5)
        assert stats.p95 == pytest.approx(95.05, abs=1.0)
        assert stats.p99 == pytest.approx(99.01, abs=1.0)


class TestComputeHourlyPattern:
    def test_basic_pattern(self):
        timestamps = [
            datetime(2025, 2, 20, 8, 0, tzinfo=timezone.utc),
            datetime(2025, 2, 20, 8, 5, tzinfo=timezone.utc),
            datetime(2025, 2, 20, 14, 0, tzinfo=timezone.utc),
            datetime(2025, 2, 20, 22, 0, tzinfo=timezone.utc),
        ]
        values = [100.0, 200.0, 500.0, 50.0]

        pattern = compute_hourly_pattern(timestamps, values)

        assert pattern.hourly_avg[8] == 150.0  # avg of 100 + 200
        assert pattern.hourly_avg[14] == 500.0
        assert pattern.hourly_avg[22] == 50.0
        assert pattern.peak_hour == 14
        assert pattern.trough_hour == 22

    def test_empty(self):
        pattern = compute_hourly_pattern([], [])
        assert pattern.hourly_avg == {}


class TestSummarizeQueueDepth:
    def test_produces_summary(self):
        ts = MetricTimeSeries(
            label="queue_depth:email_sync_v2",
            timestamps=[
                datetime(2025, 2, 18, 10, 0, tzinfo=timezone.utc),
                datetime(2025, 2, 18, 10, 5, tzinfo=timezone.utc),
                datetime(2025, 2, 18, 14, 0, tzinfo=timezone.utc),
            ],
            values=[100.0, 0.0, 300.0],
        )

        summary = summarize_queue_depth("email_sync_v2", ts)

        assert summary.queue_name == "email_sync_v2"
        assert summary.raw_point_count == 3
        assert summary.stats.count == 3
        assert summary.stats.max == 300.0
        assert summary.stats.zero_pct == pytest.approx(33.33, abs=0.01)
        assert summary.period_start != ""
        assert summary.period_end != ""

    def test_empty_metrics(self):
        ts = MetricTimeSeries(label="queue_depth:empty", timestamps=[], values=[])
        summary = summarize_queue_depth("empty", ts)
        assert summary.stats.count == 0
        assert summary.period_start == ""


class TestSummarizeASGCapacity:
    def test_produces_summary(self):
        ts1 = datetime(2025, 2, 18, 10, 0, tzinfo=timezone.utc)
        ts2 = datetime(2025, 2, 18, 14, 0, tzinfo=timezone.utc)

        metrics = {
            "desired_capacity": MetricTimeSeries(
                label="asg:desired", timestamps=[ts1, ts2], values=[5.0, 12.0],
            ),
            "max_size": MetricTimeSeries(
                label="asg:max", timestamps=[ts1, ts2], values=[20.0, 20.0],
            ),
        }

        summary = summarize_asg_capacity("celery-email-sync-py39", metrics)

        assert summary.asg_name == "celery-email-sync-py39"
        assert summary.desired_stats.max == 12.0
        assert summary.desired_stats.avg == 8.5
        assert summary.max_size_stats.avg == 20.0


class TestBuildAnalysisContext:
    def test_assembles_context(self):
        ts = MetricTimeSeries(
            label="q", timestamps=[datetime(2025, 2, 20, 10, 0, tzinfo=timezone.utc)], values=[100.0],
        )
        asg_config = ASGConfig(name="celery-test", min_size=1, max_size=10, desired_capacity=3)
        capacity = {
            "desired_capacity": MetricTimeSeries(
                label="d", timestamps=[datetime(2025, 2, 20, 10, 0, tzinfo=timezone.utc)], values=[3.0],
            ),
            "max_size": MetricTimeSeries(
                label="m", timestamps=[datetime(2025, 2, 20, 10, 0, tzinfo=timezone.utc)], values=[10.0],
            ),
        }
        policies = [
            ScalingPolicy(policy_name="up", policy_type="StepScaling"),
        ]

        ctx = build_analysis_context("test_queue", ts, asg_config, capacity, policies)

        assert isinstance(ctx, AnalysisContext)
        assert ctx.queue_summary.queue_name == "test_queue"
        assert ctx.asg_config.name == "celery-test"
        assert ctx.capacity_summary.asg_name == "celery-test"
        assert len(ctx.current_policies) == 1
