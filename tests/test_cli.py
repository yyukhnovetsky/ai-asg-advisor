"""Tests for CLI entry point."""

from __future__ import annotations

import json
import unittest.mock as mock
from contextlib import ExitStack
from datetime import datetime, timezone

import pytest
from click.testing import CliRunner

from ai_asg_advisor.cli import main


# A valid LLM response dict for mocking
MOCK_LLM_RESPONSE_DICT = {
    "step_scaling_policy": {
        "adjustment_type": "ChangeInCapacity",
        "cooldown": 300,
        "step_adjustments": [
            {"metric_interval_lower_bound": 0, "metric_interval_upper_bound": 100, "scaling_adjustment": -1},
            {"metric_interval_lower_bound": 100, "metric_interval_upper_bound": 200, "scaling_adjustment": 1},
            {"metric_interval_lower_bound": 200, "metric_interval_upper_bound": None, "scaling_adjustment": 2},
        ],
    },
    "instance_recommendations": [],
    "rationale": {
        "scaling_policy": "Test rationale.",
        "strategy_impact": "Balanced.",
        "risk_notes": "None.",
    },
}


class TestCLIHelp:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Analyze ASG scaling behavior" in result.output

    def test_version(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output


class TestCLIValidation:
    def test_missing_asg(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--queue", "test"])
        assert result.exit_code != 0

    def test_missing_queue(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--asg", "test"])
        assert result.exit_code != 0

    def test_invalid_period(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--asg", "test", "--queue", "test", "--period", "5y"])
        assert result.exit_code != 0

    def test_invalid_strategy(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--asg", "test", "--queue", "test", "--strategy", "yolo"])
        assert result.exit_code != 0

    def test_missing_api_key(self):
        runner = CliRunner()
        strip_keys = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}
        env = {k: v for k, v in __import__("os").environ.items() if k not in strip_keys}
        with mock.patch.dict("os.environ", env, clear=True):
            result = runner.invoke(main, ["--asg", "test", "--queue", "test"])
            assert result.exit_code != 0
            assert "API key" in result.output


def _setup_pipeline_mocks(stack: ExitStack):
    """Set up all mocks for the full pipeline and return the mock objects."""
    from ai_asg_advisor.cloudwatch_collector import MetricTimeSeries
    from ai_asg_advisor.asg_collector import ASGConfig
    from ai_asg_advisor.instance_collector import InstanceRecommendations, InstanceTypeInfo
    from ai_asg_advisor.llm_advisor import parse_llm_response

    mock_ts = MetricTimeSeries(
        label="test",
        timestamps=[datetime(2025, 2, 20, 10, 0, tzinfo=timezone.utc)],
        values=[100.0],
    )
    mock_cap = {
        "desired_capacity": MetricTimeSeries(
            label="d", timestamps=[datetime(2025, 2, 20, 10, 0, tzinfo=timezone.utc)], values=[5.0],
        ),
        "max_size": MetricTimeSeries(
            label="m", timestamps=[datetime(2025, 2, 20, 10, 0, tzinfo=timezone.utc)], values=[20.0],
        ),
    }
    mock_asg_config = ASGConfig(
        name="celery-test", min_size=1, max_size=20, desired_capacity=5,
        instance_types=["m5.xlarge"],
    )
    mock_instance_recs = InstanceRecommendations(
        current=InstanceTypeInfo(instance_type="m5.xlarge", vcpu=4, memory_gib=16.0, price_per_hour=0.192),
        alternatives=[],
    )
    mock_llm_response = parse_llm_response(json.dumps(MOCK_LLM_RESPONSE_DICT))

    targets = [
        ("ai_asg_advisor.aws_auth.create_session", mock.MagicMock()),
        ("ai_asg_advisor.aws_auth.verify_credentials", mock.MagicMock(return_value={"Account": "123", "Arn": "arn:test"})),
        ("ai_asg_advisor.aws_auth.get_client", mock.MagicMock()),
        ("ai_asg_advisor.aws_auth.get_pricing_client", mock.MagicMock()),
        ("ai_asg_advisor.cloudwatch_collector.get_queue_depth_metrics", mock.MagicMock(return_value=mock_ts)),
        ("ai_asg_advisor.cloudwatch_collector.get_asg_capacity_metrics", mock.MagicMock(return_value=mock_cap)),
        ("ai_asg_advisor.asg_collector.get_asg_config", mock.MagicMock(return_value=mock_asg_config)),
        ("ai_asg_advisor.asg_collector.get_scaling_policies", mock.MagicMock(return_value=[])),
        ("ai_asg_advisor.instance_collector.get_instance_recommendations", mock.MagicMock(return_value=mock_instance_recs)),
        ("ai_asg_advisor.llm_advisor.call_llm_advisor", mock.MagicMock(return_value=mock_llm_response)),
    ]

    for target, mock_obj in targets:
        stack.enter_context(mock.patch(target, mock_obj))

    stack.enter_context(mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}))


class TestCLIPipeline:
    """Test the full CLI pipeline with all AWS/LLM calls mocked."""

    def _run(self, output_format="text", extra_args=None):
        runner = CliRunner()
        with ExitStack() as stack:
            _setup_pipeline_mocks(stack)
            args = ["--asg", "celery-test", "--queue", "email_sync_v2", "--format", output_format]
            if extra_args:
                args.extend(extra_args)
            return runner.invoke(main, args, catch_exceptions=False)

    def test_terraform_output(self):
        result = self._run(output_format="terraform")
        assert result.exit_code == 0
        assert 'resource "aws_autoscaling_policy"' in result.output

    def test_json_output(self):
        result = self._run(output_format="json")
        assert result.exit_code == 0
        assert "step_scaling_policy" in result.output

    def test_text_output(self):
        result = self._run(output_format="text")
        assert result.exit_code == 0
        assert "STEP SCALING POLICY" in result.output

    def test_file_output(self, tmp_path):
        outfile = tmp_path / "out.tf"
        result = self._run(output_format="terraform", extra_args=["--output", str(outfile)])
        assert result.exit_code == 0
        assert outfile.exists()
        content = outfile.read_text()
        assert 'resource "aws_autoscaling_policy"' in content

    def test_verbose_flag(self):
        result = self._run(output_format="text", extra_args=["-v"])
        assert result.exit_code == 0

    def test_strategy_cost(self):
        result = self._run(output_format="text", extra_args=["--strategy", "cost"])
        assert result.exit_code == 0

    def test_strategy_performance(self):
        result = self._run(output_format="text", extra_args=["--strategy", "performance"])
        assert result.exit_code == 0
