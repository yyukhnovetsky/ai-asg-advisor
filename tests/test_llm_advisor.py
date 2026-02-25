"""Tests for LLM advisor module."""

from __future__ import annotations

import json
import unittest.mock as mock
from datetime import datetime, timezone

import pytest

from ai_asg_advisor.asg_collector import ASGConfig, ScalingPolicy, StepAdjustment
from ai_asg_advisor.cloudwatch_collector import MetricTimeSeries
from ai_asg_advisor.config import LLMConfig
from ai_asg_advisor.instance_collector import InstanceRecommendations, InstanceTypeInfo
from ai_asg_advisor.llm_advisor import (
    LLMAdvisorResponse,
    LLMInstanceRecommendation,
    LLMRationale,
    LLMScalingDirection,
    LLMStepAdjustment,
    LLMStepScalingPolicy,
    STRATEGY_PROMPTS,
    SYSTEM_PROMPT,
    build_llm_input,
    call_llm_advisor,
    parse_llm_response,
    _validate_response,
)
from ai_asg_advisor.preprocessor import (
    AnalysisContext,
    ASGCapacitySummary,
    HourlyPattern,
    QueueDepthSummary,
    TimeSeriesStats,
)


# -- Fixtures --

def _make_analysis_context() -> AnalysisContext:
    return AnalysisContext(
        queue_summary=QueueDepthSummary(
            queue_name="email_sync_v2",
            period_start="2025-02-11T00:00:00+00:00",
            period_end="2025-02-25T00:00:00+00:00",
            stats=TimeSeriesStats(
                count=2016, avg=142.5, min=0.0, max=3500.0,
                p50=85.0, p95=520.0, p99=1200.0, zero_pct=12.5,
            ),
            hourly_pattern=HourlyPattern(
                hourly_avg={8: 300.0, 14: 500.0, 22: 50.0},
                peak_hour=14, trough_hour=22,
            ),
            raw_point_count=2016,
        ),
        asg_config=ASGConfig(
            name="celery-email-sync-py39",
            min_size=2, max_size=20, desired_capacity=5,
            instance_types=["m5.xlarge"],
            launch_template_id="lt-abc123",
        ),
        capacity_summary=ASGCapacitySummary(
            asg_name="celery-email-sync-py39",
            period_start="2025-02-11T00:00:00+00:00",
            period_end="2025-02-25T00:00:00+00:00",
            desired_stats=TimeSeriesStats(
                count=2016, avg=6.2, min=2.0, max=18.0,
                p50=5.0, p95=14.0, p99=17.0, zero_pct=0.0,
            ),
            max_size_stats=TimeSeriesStats(
                count=2016, avg=20.0, min=20.0, max=20.0,
                p50=20.0, p95=20.0, p99=20.0, zero_pct=0.0,
            ),
            desired_hourly_pattern=HourlyPattern(
                hourly_avg={8: 8.0, 14: 12.0, 22: 3.0},
                peak_hour=14, trough_hour=22,
            ),
        ),
        current_policies=[
            ScalingPolicy(
                policy_name="scale-up",
                policy_type="StepScaling",
                adjustment_type="ChangeInCapacity",
                cooldown=120,
                step_adjustments=[
                    StepAdjustment(metric_interval_lower_bound=0, metric_interval_upper_bound=100, scaling_adjustment=1),
                    StepAdjustment(metric_interval_lower_bound=100, scaling_adjustment=2),
                ],
            ),
        ],
    )


def _make_instance_recs() -> InstanceRecommendations:
    return InstanceRecommendations(
        current=InstanceTypeInfo(
            instance_type="m5.xlarge", vcpu=4, memory_gib=16.0,
            architecture="x86_64", price_per_hour=0.192, monthly_cost_estimate=140.16,
        ),
        alternatives=[
            InstanceTypeInfo(
                instance_type="m6g.xlarge", vcpu=4, memory_gib=16.0,
                architecture="arm64", price_per_hour=0.154, monthly_cost_estimate=112.42,
            ),
            InstanceTypeInfo(
                instance_type="m5a.xlarge", vcpu=4, memory_gib=16.0,
                architecture="x86_64", price_per_hour=0.172, monthly_cost_estimate=125.56,
            ),
        ],
    )


VALID_LLM_RESPONSE = {
    "step_scaling_policy": {
        "scale_up": {
            "adjustment_type": "ChangeInCapacity",
            "cooldown": 120,
            "step_adjustments": [
                {"metric_interval_lower_bound": 0, "metric_interval_upper_bound": 200, "scaling_adjustment": 1},
                {"metric_interval_lower_bound": 200, "metric_interval_upper_bound": 500, "scaling_adjustment": 2},
                {"metric_interval_lower_bound": 500, "scaling_adjustment": 4},
            ],
        },
        "scale_down": {
            "adjustment_type": "ChangeInCapacity",
            "cooldown": 300,
            "step_adjustments": [
                {"metric_interval_lower_bound": -100, "metric_interval_upper_bound": 0, "scaling_adjustment": -1},
            ],
        },
    },
    "instance_recommendations": [
        {
            "instance_type": "m6g.xlarge",
            "vcpu": 4,
            "memory_gb": 16.0,
            "price_per_hour": 0.154,
            "monthly_cost_estimate": 112.42,
            "savings_vs_current_pct": 19.8,
            "rationale": "Graviton-based equivalent.",
        },
    ],
    "rationale": {
        "scaling_policy": "Queue depth p95 is 520 messages.",
        "strategy_impact": "Balanced strategy applied.",
        "risk_notes": "Peak capacity reached 18/20 max.",
    },
}


# -- Tests --

class TestBuildLLMInput:
    def test_assembles_all_sections(self):
        ctx = _make_analysis_context()
        recs = _make_instance_recs()

        result = build_llm_input(ctx, recs, "balanced", "2w")

        assert result["analysis_request"]["asg_name"] == "celery-email-sync-py39"
        assert result["analysis_request"]["strategy"] == "balanced"
        assert result["queue_metrics"]["statistics"]["p95"] == 520.0
        assert result["asg_data"]["current_config"]["max_size"] == 20
        assert result["asg_data"]["capacity_history"]["max_desired"] == 18.0
        assert len(result["asg_data"]["current_policies"]) == 1
        assert result["instance_pricing"]["current"]["type"] == "m5.xlarge"
        assert len(result["instance_pricing"]["alternatives"]) == 2

    def test_serializes_to_json(self):
        ctx = _make_analysis_context()
        recs = _make_instance_recs()
        result = build_llm_input(ctx, recs, "cost", "1m")
        # Must be JSON-serializable
        json_str = json.dumps(result)
        assert len(json_str) > 100


class TestParseLLMResponse:
    def test_parses_valid_json(self):
        raw = json.dumps(VALID_LLM_RESPONSE)
        result = parse_llm_response(raw)

        assert isinstance(result, LLMAdvisorResponse)
        assert len(result.step_scaling_policy.scale_up.step_adjustments) == 3
        assert len(result.step_scaling_policy.scale_down.step_adjustments) == 1
        assert result.step_scaling_policy.scale_up.cooldown == 120
        assert result.step_scaling_policy.scale_down.cooldown == 300
        assert result.instance_recommendations[0].instance_type == "m6g.xlarge"
        assert result.rationale.scaling_policy == "Queue depth p95 is 520 messages."

    def test_strips_markdown_fences(self):
        raw = f"```json\n{json.dumps(VALID_LLM_RESPONSE)}\n```"
        result = parse_llm_response(raw)
        assert len(result.step_scaling_policy.scale_up.step_adjustments) == 3

    def test_enforces_min_cooldown(self):
        """Cooldowns below 60s should be raised to 60s."""
        data = json.loads(json.dumps(VALID_LLM_RESPONSE))
        data["step_scaling_policy"]["scale_up"]["cooldown"] = 30
        raw = json.dumps(data)
        result = parse_llm_response(raw)
        assert result.step_scaling_policy.scale_up.cooldown == 60

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_llm_response("not json at all")


class TestValidateResponse:
    def test_valid_response_passes(self):
        raw = json.dumps(VALID_LLM_RESPONSE)
        result = parse_llm_response(raw)
        # Should not raise
        _validate_response(result)

    def test_missing_scale_up_raises(self):
        response = LLMAdvisorResponse(
            step_scaling_policy=LLMStepScalingPolicy(
                scale_up=LLMScalingDirection(step_adjustments=[]),
                scale_down=LLMScalingDirection(step_adjustments=[
                    LLMStepAdjustment(scaling_adjustment=-1),
                ]),
            ),
        )
        with pytest.raises(ValueError, match="scale-up"):
            _validate_response(response)

    def test_missing_scale_down_raises(self):
        response = LLMAdvisorResponse(
            step_scaling_policy=LLMStepScalingPolicy(
                scale_up=LLMScalingDirection(step_adjustments=[
                    LLMStepAdjustment(scaling_adjustment=1),
                ]),
                scale_down=LLMScalingDirection(step_adjustments=[]),
            ),
        )
        with pytest.raises(ValueError, match="scale-down"):
            _validate_response(response)

    def test_positive_scale_down_raises(self):
        response = LLMAdvisorResponse(
            step_scaling_policy=LLMStepScalingPolicy(
                scale_up=LLMScalingDirection(step_adjustments=[
                    LLMStepAdjustment(scaling_adjustment=1),
                ]),
                scale_down=LLMScalingDirection(step_adjustments=[
                    LLMStepAdjustment(scaling_adjustment=1),  # wrong: should be negative
                ]),
            ),
        )
        with pytest.raises(ValueError, match="negative"):
            _validate_response(response)

    def test_negative_scale_up_raises(self):
        response = LLMAdvisorResponse(
            step_scaling_policy=LLMStepScalingPolicy(
                scale_up=LLMScalingDirection(step_adjustments=[
                    LLMStepAdjustment(scaling_adjustment=-1),  # wrong: should be positive
                ]),
                scale_down=LLMScalingDirection(step_adjustments=[
                    LLMStepAdjustment(scaling_adjustment=-1),
                ]),
            ),
        )
        with pytest.raises(ValueError, match="positive"):
            _validate_response(response)

    def test_cooldown_below_60_raises(self):
        response = LLMAdvisorResponse(
            step_scaling_policy=LLMStepScalingPolicy(
                scale_up=LLMScalingDirection(
                    cooldown=30,
                    step_adjustments=[LLMStepAdjustment(scaling_adjustment=1)],
                ),
                scale_down=LLMScalingDirection(
                    cooldown=300,
                    step_adjustments=[LLMStepAdjustment(scaling_adjustment=-1)],
                ),
            ),
        )
        with pytest.raises(ValueError, match="cooldown"):
            _validate_response(response)


class TestCallLLMAdvisor:
    def test_calls_claude_api(self):
        ctx = _make_analysis_context()
        recs = _make_instance_recs()
        llm_config = LLMConfig(api_key="sk-ant-test", model="claude-sonnet-4-20250514")

        mock_message = mock.MagicMock()
        mock_message.content = [mock.MagicMock(text=json.dumps(VALID_LLM_RESPONSE))]

        with mock.patch("ai_asg_advisor.llm_advisor.anthropic.Anthropic") as mock_cls:
            mock_client = mock.MagicMock()
            mock_client.messages.create.return_value = mock_message
            mock_cls.return_value = mock_client

            result = call_llm_advisor(llm_config, ctx, recs, "balanced", "2w")

            # Verify the API was called correctly
            mock_cls.assert_called_once_with(api_key="sk-ant-test")
            call_kwargs = mock_client.messages.create.call_args
            assert call_kwargs.kwargs["model"] == "claude-sonnet-4-20250514"
            assert call_kwargs.kwargs["max_tokens"] == 4096
            assert "GUARDRAILS" in call_kwargs.kwargs["system"]
            assert "BALANCED" in call_kwargs.kwargs["system"]

            # Verify the response was parsed
            assert isinstance(result, LLMAdvisorResponse)
            assert len(result.step_scaling_policy.scale_up.step_adjustments) == 3

    def test_strategy_prompt_included(self):
        """Each strategy should include its specific prompt section."""
        ctx = _make_analysis_context()
        recs = _make_instance_recs()
        llm_config = LLMConfig(api_key="sk-ant-test", model="test-model")

        mock_message = mock.MagicMock()
        mock_message.content = [mock.MagicMock(text=json.dumps(VALID_LLM_RESPONSE))]

        for strategy, expected_keyword in [
            ("cost", "COST OPTIMIZATION"),
            ("performance", "PERFORMANCE OPTIMIZATION"),
            ("balanced", "BALANCED"),
        ]:
            with mock.patch("ai_asg_advisor.llm_advisor.anthropic.Anthropic") as mock_cls:
                mock_client = mock.MagicMock()
                mock_client.messages.create.return_value = mock_message
                mock_cls.return_value = mock_client

                call_llm_advisor(llm_config, ctx, recs, strategy, "2w")

                system_prompt = mock_client.messages.create.call_args.kwargs["system"]
                assert expected_keyword in system_prompt


class TestSystemPromptGuardrails:
    """Verify the system prompt contains all required guardrail sections."""

    def test_contains_role(self):
        assert "Auto Scaling optimization advisor" in SYSTEM_PROMPT

    def test_contains_scope_guardrail(self):
        assert "SCOPE" in SYSTEM_PROMPT
        assert "Refuse any" in SYSTEM_PROMPT

    def test_contains_data_only_guardrail(self):
        assert "DATA-ONLY" in SYSTEM_PROMPT
        assert "Do not fabricate" in SYSTEM_PROMPT

    def test_contains_safety_guardrail(self):
        assert "SAFETY" in SYSTEM_PROMPT
        assert "scaling to zero" in SYSTEM_PROMPT
        assert "scale-down policy" in SYSTEM_PROMPT
        assert "60 seconds" in SYSTEM_PROMPT

    def test_contains_output_format(self):
        assert "OUTPUT FORMAT" in SYSTEM_PROMPT
        assert "JSON" in SYSTEM_PROMPT

    def test_contains_instance_type_rules(self):
        assert "INSTANCE TYPES" in SYSTEM_PROMPT
        assert "current instance type as a baseline" in SYSTEM_PROMPT

    def test_contains_cost_rules(self):
        assert "COST ESTIMATES" in SYSTEM_PROMPT
        assert "Do not guess prices" in SYSTEM_PROMPT

    def test_contains_prohibited_section(self):
        assert "PROHIBITED" in SYSTEM_PROMPT
        assert "No security" in SYSTEM_PROMPT
        assert "No opinions on application architecture" in SYSTEM_PROMPT

    def test_all_strategies_defined(self):
        assert "cost" in STRATEGY_PROMPTS
        assert "performance" in STRATEGY_PROMPTS
        assert "balanced" in STRATEGY_PROMPTS
