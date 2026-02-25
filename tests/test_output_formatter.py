"""Tests for output formatter module."""

from __future__ import annotations

import json

import pytest

from ai_asg_advisor.llm_advisor import (
    LLMAdvisorResponse,
    LLMInstanceRecommendation,
    LLMRationale,
    LLMScalingDirection,
    LLMStepAdjustment,
    LLMStepScalingPolicy,
)
from ai_asg_advisor.output_formatter import (
    format_json,
    format_output,
    format_terraform,
    format_text,
)


def _make_response() -> LLMAdvisorResponse:
    return LLMAdvisorResponse(
        step_scaling_policy=LLMStepScalingPolicy(
            scale_up=LLMScalingDirection(
                adjustment_type="ChangeInCapacity",
                cooldown=120,
                step_adjustments=[
                    LLMStepAdjustment(metric_interval_lower_bound=0, metric_interval_upper_bound=200, scaling_adjustment=1),
                    LLMStepAdjustment(metric_interval_lower_bound=200, metric_interval_upper_bound=500, scaling_adjustment=2),
                    LLMStepAdjustment(metric_interval_lower_bound=500, scaling_adjustment=4),
                ],
            ),
            scale_down=LLMScalingDirection(
                adjustment_type="ChangeInCapacity",
                cooldown=300,
                step_adjustments=[
                    LLMStepAdjustment(metric_interval_lower_bound=-100, metric_interval_upper_bound=0, scaling_adjustment=-1),
                ],
            ),
        ),
        instance_recommendations=[
            LLMInstanceRecommendation(
                instance_type="m6g.xlarge",
                vcpu=4,
                memory_gb=16.0,
                price_per_hour=0.154,
                monthly_cost_estimate=112.42,
                savings_vs_current_pct=19.8,
                rationale="Graviton-based equivalent.",
            ),
        ],
        rationale=LLMRationale(
            scaling_policy="Queue depth p95 is 520 messages.",
            strategy_impact="Balanced strategy applied.",
            risk_notes="Peak capacity reached 18/20 max.",
        ),
    )


class TestFormatTerraform:
    def test_generates_scale_up_block(self):
        result = format_terraform(_make_response(), "celery-email-sync-py39")

        assert 'resource "aws_autoscaling_policy" "celery_email_sync_py39_scale_up"' in result
        assert '"celery-email-sync-py39-scale-up"' in result
        assert 'policy_type               = "StepScaling"' in result
        assert "estimated_instance_warmup = 120" in result

    def test_generates_scale_down_block(self):
        result = format_terraform(_make_response(), "celery-email-sync-py39")

        assert 'resource "aws_autoscaling_policy" "celery_email_sync_py39_scale_down"' in result
        assert '"celery-email-sync-py39-scale-down"' in result
        assert "estimated_instance_warmup = 300" in result

    def test_step_adjustments_present(self):
        result = format_terraform(_make_response(), "celery-email-sync-py39")

        assert "step_adjustment {" in result
        assert "metric_interval_lower_bound = 0" in result
        assert "metric_interval_upper_bound = 200" in result
        assert "scaling_adjustment          = 1" in result
        assert "scaling_adjustment          = 4" in result
        assert "scaling_adjustment          = -1" in result

    def test_unbounded_upper_omitted(self):
        """The last scale-up step has no upper bound — it should be omitted in TF."""
        result = format_terraform(_make_response(), "celery-email-sync-py39")

        # Find the step with adjustment=4 (unbounded upper)
        # It should have lower_bound=500 but no upper_bound line
        lines = result.split("\n")
        found_adj_4 = False
        for i, line in enumerate(lines):
            if "scaling_adjustment          = 4" in line:
                found_adj_4 = True
                # Look at the preceding lines in this step_adjustment block
                block_start = max(0, i - 3)
                block = "\n".join(lines[block_start:i + 1])
                assert "metric_interval_lower_bound = 500" in block
                assert "metric_interval_upper_bound" not in block
                break
        assert found_adj_4

    def test_sanitizes_resource_name(self):
        result = format_terraform(_make_response(), "celery-worker-py39")
        assert "celery_worker_py39_scale_up" in result


class TestFormatJSON:
    def test_valid_json(self):
        result = format_json(_make_response())
        data = json.loads(result)

        assert "step_scaling_policy" in data
        assert "instance_recommendations" in data
        assert "rationale" in data

    def test_scale_up_steps(self):
        result = format_json(_make_response())
        data = json.loads(result)

        steps = data["step_scaling_policy"]["scale_up"]["step_adjustments"]
        assert len(steps) == 3
        assert steps[0]["scaling_adjustment"] == 1
        assert steps[2]["scaling_adjustment"] == 4

    def test_instance_recommendations(self):
        result = format_json(_make_response())
        data = json.loads(result)

        recs = data["instance_recommendations"]
        assert len(recs) == 1
        assert recs[0]["instance_type"] == "m6g.xlarge"
        assert recs[0]["savings_vs_current_pct"] == 19.8

    def test_null_bounds_omitted(self):
        """None bounds should not appear as null in JSON."""
        result = format_json(_make_response())
        data = json.loads(result)

        last_step = data["step_scaling_policy"]["scale_up"]["step_adjustments"][2]
        assert "metric_interval_upper_bound" not in last_step


class TestFormatText:
    def test_contains_header(self):
        result = format_text(_make_response(), "celery-email-sync-py39")
        assert "celery-email-sync-py39" in result

    def test_contains_scale_up(self):
        result = format_text(_make_response(), "celery-email-sync-py39")
        assert "SCALE UP" in result
        assert "+1 instances" in result
        assert "+4 instances" in result

    def test_contains_scale_down(self):
        result = format_text(_make_response(), "celery-email-sync-py39")
        assert "SCALE DOWN" in result
        assert "-1 instances" in result

    def test_contains_instance_recs(self):
        result = format_text(_make_response(), "celery-email-sync-py39")
        assert "m6g.xlarge" in result
        assert "$0.1540/hr" in result
        assert "Graviton" in result

    def test_contains_rationale(self):
        result = format_text(_make_response(), "celery-email-sync-py39")
        assert "Queue depth p95" in result
        assert "Peak capacity" in result


class TestFormatOutput:
    def test_routes_terraform(self):
        result = format_output(_make_response(), "test-asg", "terraform")
        assert 'resource "aws_autoscaling_policy"' in result

    def test_routes_json(self):
        result = format_output(_make_response(), "test-asg", "json")
        json.loads(result)  # must be valid JSON

    def test_routes_text(self):
        result = format_output(_make_response(), "test-asg", "text")
        assert "SCALE UP" in result

    def test_unknown_format_raises(self):
        with pytest.raises(ValueError, match="Unknown"):
            format_output(_make_response(), "test-asg", "xml")
