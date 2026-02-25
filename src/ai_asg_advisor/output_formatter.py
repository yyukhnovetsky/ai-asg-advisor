"""Output formatter — Terraform HCL, JSON report, and plain-text summary."""

from __future__ import annotations

import json
import re
from dataclasses import asdict

from ai_asg_advisor.llm_advisor import LLMAdvisorResponse, LLMStepAdjustment


def format_terraform(response: LLMAdvisorResponse, asg_name: str) -> str:
    """Generate Terraform HCL for step scaling policies."""
    blocks = []

    # Sanitize ASG name for Terraform resource naming
    resource_name = re.sub(r"[^a-zA-Z0-9_]", "_", asg_name)

    # Scale-up policy
    up = response.step_scaling_policy.scale_up
    blocks.append(_terraform_policy_block(
        resource_name=f"{resource_name}_scale_up",
        policy_name=f"{asg_name}-scale-up",
        asg_name=asg_name,
        adjustment_type=up.adjustment_type,
        cooldown=up.cooldown,
        step_adjustments=up.step_adjustments,
    ))

    # Scale-down policy
    down = response.step_scaling_policy.scale_down
    blocks.append(_terraform_policy_block(
        resource_name=f"{resource_name}_scale_down",
        policy_name=f"{asg_name}-scale-down",
        asg_name=asg_name,
        adjustment_type=down.adjustment_type,
        cooldown=down.cooldown,
        step_adjustments=down.step_adjustments,
    ))

    return "\n\n".join(blocks) + "\n"


def format_json(response: LLMAdvisorResponse) -> str:
    """Generate a full JSON report from the LLM response."""
    data = _response_to_dict(response)
    return json.dumps(data, indent=2) + "\n"


def format_text(response: LLMAdvisorResponse, asg_name: str) -> str:
    """Generate a human-readable plain-text summary."""
    lines = []
    lines.append(f"=== Scaling Policy Recommendations for {asg_name} ===")
    lines.append("")

    # Scale-up
    up = response.step_scaling_policy.scale_up
    lines.append(f"SCALE UP ({up.adjustment_type}, cooldown={up.cooldown}s):")
    for sa in up.step_adjustments:
        lower = sa.metric_interval_lower_bound
        upper = sa.metric_interval_upper_bound
        bound_str = _bound_str(lower, upper)
        lines.append(f"  {bound_str} -> +{sa.scaling_adjustment} instances")
    lines.append("")

    # Scale-down
    down = response.step_scaling_policy.scale_down
    lines.append(f"SCALE DOWN ({down.adjustment_type}, cooldown={down.cooldown}s):")
    for sa in down.step_adjustments:
        lower = sa.metric_interval_lower_bound
        upper = sa.metric_interval_upper_bound
        bound_str = _bound_str(lower, upper)
        lines.append(f"  {bound_str} -> {sa.scaling_adjustment} instances")
    lines.append("")

    # Instance recommendations
    if response.instance_recommendations:
        lines.append("INSTANCE RECOMMENDATIONS:")
        for rec in response.instance_recommendations:
            savings = f" ({rec.savings_vs_current_pct:+.1f}%)" if rec.savings_vs_current_pct else ""
            lines.append(
                f"  {rec.instance_type}: {rec.vcpu}vCPU / {rec.memory_gb}GiB "
                f"@ ${rec.price_per_hour:.4f}/hr (~${rec.monthly_cost_estimate:.0f}/mo{savings})"
            )
            if rec.rationale:
                lines.append(f"    {rec.rationale}")
        lines.append("")

    # Rationale
    if response.rationale.scaling_policy:
        lines.append("ANALYSIS:")
        lines.append(f"  Policy:   {response.rationale.scaling_policy}")
    if response.rationale.strategy_impact:
        lines.append(f"  Strategy: {response.rationale.strategy_impact}")
    if response.rationale.risk_notes:
        lines.append(f"  Risks:    {response.rationale.risk_notes}")
    lines.append("")

    return "\n".join(lines)


def format_output(response: LLMAdvisorResponse, asg_name: str, output_format: str) -> str:
    """Route to the appropriate formatter based on output_format."""
    if output_format == "terraform":
        return format_terraform(response, asg_name)
    elif output_format == "json":
        return format_json(response)
    elif output_format == "text":
        return format_text(response, asg_name)
    else:
        raise ValueError(f"Unknown output format: {output_format}")


# -- Internal helpers --

def _terraform_policy_block(
    resource_name: str,
    policy_name: str,
    asg_name: str,
    adjustment_type: str,
    cooldown: int,
    step_adjustments: list[LLMStepAdjustment],
) -> str:
    """Generate a single Terraform aws_autoscaling_policy resource block."""
    lines = [
        f'resource "aws_autoscaling_policy" "{resource_name}" {{',
        f'  name                      = "{policy_name}"',
        f'  autoscaling_group_name    = "{asg_name}"',
        f'  adjustment_type           = "{adjustment_type}"',
        f'  policy_type               = "StepScaling"',
        f'  estimated_instance_warmup = {cooldown}',
    ]

    for sa in step_adjustments:
        lines.append("")
        lines.append("  step_adjustment {")
        if sa.metric_interval_lower_bound is not None:
            lines.append(f"    metric_interval_lower_bound = {_tf_number(sa.metric_interval_lower_bound)}")
        if sa.metric_interval_upper_bound is not None:
            lines.append(f"    metric_interval_upper_bound = {_tf_number(sa.metric_interval_upper_bound)}")
        lines.append(f"    scaling_adjustment          = {sa.scaling_adjustment}")
        lines.append("  }")

    lines.append("}")
    return "\n".join(lines)


def _tf_number(value: float) -> str:
    """Format a number for Terraform — integers as ints, floats as floats."""
    if value == int(value):
        return str(int(value))
    return str(value)


def _bound_str(lower: float | None, upper: float | None) -> str:
    """Format a metric interval bound range for display."""
    if lower is not None and upper is not None:
        return f"[{lower:.0f}, {upper:.0f})"
    elif lower is not None:
        return f"[{lower:.0f}, +inf)"
    elif upper is not None:
        return f"(-inf, {upper:.0f})"
    return "[any]"


def _response_to_dict(response: LLMAdvisorResponse) -> dict:
    """Convert LLMAdvisorResponse to a clean dict for JSON serialization."""
    policy = response.step_scaling_policy

    def _steps_to_list(steps):
        result = []
        for sa in steps:
            d = {"scaling_adjustment": sa.scaling_adjustment}
            if sa.metric_interval_lower_bound is not None:
                d["metric_interval_lower_bound"] = sa.metric_interval_lower_bound
            if sa.metric_interval_upper_bound is not None:
                d["metric_interval_upper_bound"] = sa.metric_interval_upper_bound
            result.append(d)
        return result

    return {
        "step_scaling_policy": {
            "scale_up": {
                "adjustment_type": policy.scale_up.adjustment_type,
                "cooldown": policy.scale_up.cooldown,
                "step_adjustments": _steps_to_list(policy.scale_up.step_adjustments),
            },
            "scale_down": {
                "adjustment_type": policy.scale_down.adjustment_type,
                "cooldown": policy.scale_down.cooldown,
                "step_adjustments": _steps_to_list(policy.scale_down.step_adjustments),
            },
        },
        "instance_recommendations": [
            {
                "instance_type": rec.instance_type,
                "vcpu": rec.vcpu,
                "memory_gb": rec.memory_gb,
                "price_per_hour": rec.price_per_hour,
                "monthly_cost_estimate": rec.monthly_cost_estimate,
                "savings_vs_current_pct": rec.savings_vs_current_pct,
                "rationale": rec.rationale,
            }
            for rec in response.instance_recommendations
        ],
        "rationale": {
            "scaling_policy": response.rationale.scaling_policy,
            "strategy_impact": response.rationale.strategy_impact,
            "risk_notes": response.rationale.risk_notes,
        },
    }
