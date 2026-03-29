"""LLM advisor — Claude API integration with domain-specific guardrails."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import anthropic

from ai_asg_advisor.config import LLMConfig
from ai_asg_advisor.instance_collector import InstanceRecommendations
from ai_asg_advisor.preprocessor import AnalysisContext

logger = logging.getLogger(__name__)

# -- System prompt with guardrails --

SYSTEM_PROMPT = """\
ROLE:
You are an AWS Auto Scaling optimization advisor. Your ONLY purpose is to
analyze provided ASG metrics and configuration data and recommend step
scaling policies and instance types.

GUARDRAILS:

1. SCOPE: Only respond to autoscaling optimization queries. Refuse any
   request that is not related to ASG policy analysis, instance type
   selection, or cost/performance trade-offs for autoscaling.

2. DATA-ONLY: Base all recommendations exclusively on the provided metrics
   and configuration data. Do not fabricate metrics, assume traffic
   patterns not visible in the data, or hallucinate AWS service features.

3. SAFETY:
   - Never recommend scaling to zero unless the data shows sustained zero
     queue depth for >80% of the analysis period.
   - Never recommend a max_size lower than the observed peak
     desired_capacity.
   - Always include both scale-down (negative adjustment) and scale-up
     (positive adjustment) steps within the single policy.
   - Minimum cooldown period: 60 seconds.
   - INSTANCE WARMUP: New EC2 instances require a warmup period before they
     can process tasks (boot, pull code, register workers). The cooldown
     MUST be >= the provided instance_warmup_seconds to avoid launching
     more instances before previous ones are ready.
   - INSTANCE DRAIN: Instances have lifecycle hooks for graceful shutdown
     (drain in-flight tasks). The cooldown MUST also be >= the provided
     instance_drain_seconds to avoid terminating additional instances
     before previous ones have fully stopped. Use the maximum of
     instance_warmup_seconds and instance_drain_seconds as the minimum
     cooldown.
   - METRIC BOUNDS: The metric_interval_lower_bound and
     metric_interval_upper_bound values represent absolute queue depth
     thresholds (number of messages). Since queue depth cannot be negative,
     ALL bounds MUST be >= 0. Every step MUST have both
     metric_interval_lower_bound and metric_interval_upper_bound. Use 0
     for the lowest lower_bound and null for the highest upper_bound
     (unbounded). The first step should have a low threshold
     (e.g., 0 to some small value) with a negative scaling_adjustment to
     trigger scale-down when the queue is nearly empty. Higher thresholds
     should have positive scaling_adjustments for scale-up.

4. OUTPUT FORMAT: Return your response strictly as a single JSON object
   matching the output schema below. Do not include any text, markdown
   fences, or commentary outside the JSON structure.

5. INSTANCE TYPES:
   - Only recommend instance types from the provided alternatives list.
   - Always include the current instance type as a baseline option.
   - Include price-per-hour for every recommended type (from provided data).

6. COST ESTIMATES:
   - Use the provided pricing data only. Do not guess prices.
   - Express savings as both percentage and absolute monthly estimate
     relative to the current instance type.

7. PROHIBITED:
   - No infrastructure provisioning or modification suggestions beyond
     scaling policy and instance type.
   - No security, IAM, or networking recommendations.
   - No opinions on application architecture or code changes.

OUTPUT SCHEMA:
{
  "step_scaling_policy": {
    "adjustment_type": "ChangeInCapacity",
    "cooldown": <int, seconds, >= max(instance_warmup_seconds, instance_drain_seconds)>,
    "step_adjustments": [
      {
        "metric_interval_lower_bound": <number >= 0, required>,
        "metric_interval_upper_bound": <number >= 0 or null for unbounded, required>,
        "scaling_adjustment": <int, negative for scale-down, positive for scale-up>
      }
    ]
  },
  "instance_recommendations": [
    {
      "instance_type": "<string>",
      "vcpu": <int>,
      "memory_gb": <float>,
      "price_per_hour": <float>,
      "monthly_cost_estimate": <float>,
      "savings_vs_current_pct": <float>,
      "rationale": "<string>"
    }
  ],
  "rationale": {
    "scaling_policy": "<string explaining the step thresholds chosen>",
    "strategy_impact": "<string explaining how the strategy influenced decisions>",
    "risk_notes": "<string with warnings or risks>"
  }
}
"""

# -- Strategy-specific prompt additions --

STRATEGY_PROMPTS = {
    "cost": (
        "STRATEGY: COST OPTIMIZATION\n"
        "Prioritize minimizing spend. Prefer:\n"
        "- Aggressive scale-down (shorter cooldown for scale-down, longer for scale-up)\n"
        "- Fewer, higher-threshold scale-up steps\n"
        "- Cheaper instance types (Graviton/ARM, AMD variants, burstable if workload is bursty)\n"
        "- Accept slightly higher queue latency in exchange for cost savings\n"
    ),
    "performance": (
        "STRATEGY: PERFORMANCE OPTIMIZATION\n"
        "Prioritize minimizing queue latency. Prefer:\n"
        "- Aggressive scale-up (shorter cooldown for scale-up, more granular steps)\n"
        "- Higher headroom (scale up earlier at lower queue depth thresholds)\n"
        "- Compute-optimized or larger instance types if they process faster\n"
        "- Accept higher cost in exchange for faster queue processing\n"
    ),
    "balanced": (
        "STRATEGY: BALANCED (cost-to-latency ratio)\n"
        "Optimize the trade-off between cost and queue latency. Prefer:\n"
        "- Moderate cooldowns for both scale-up and scale-down\n"
        "- Step thresholds that match observed queue depth percentiles\n"
        "- Instance types that offer the best price-performance ratio\n"
        "- Maintain reasonable headroom without over-provisioning\n"
    ),
}


# -- Response dataclasses --

@dataclass
class LLMStepAdjustment:
    metric_interval_lower_bound: float | None = None
    metric_interval_upper_bound: float | None = None
    scaling_adjustment: int = 0


@dataclass
class LLMStepScalingPolicy:
    adjustment_type: str = "ChangeInCapacity"
    cooldown: int = 300
    step_adjustments: list[LLMStepAdjustment] = field(default_factory=list)


@dataclass
class LLMInstanceRecommendation:
    instance_type: str = ""
    vcpu: int = 0
    memory_gb: float = 0.0
    price_per_hour: float = 0.0
    monthly_cost_estimate: float = 0.0
    savings_vs_current_pct: float = 0.0
    rationale: str = ""


@dataclass
class LLMRationale:
    scaling_policy: str = ""
    strategy_impact: str = ""
    risk_notes: str = ""


@dataclass
class LLMAdvisorResponse:
    step_scaling_policy: LLMStepScalingPolicy = field(default_factory=LLMStepScalingPolicy)
    instance_recommendations: list[LLMInstanceRecommendation] = field(default_factory=list)
    rationale: LLMRationale = field(default_factory=LLMRationale)


# -- Context builder --

def build_llm_input(
    analysis_context: AnalysisContext,
    instance_recs: InstanceRecommendations,
    strategy: str,
    period: str,
    warmup_seconds: int = 120,
    drain_seconds: int = 300,
) -> dict:
    """Assemble the structured data context for the LLM."""
    queue = analysis_context.queue_summary
    asg = analysis_context.asg_config
    cap = analysis_context.capacity_summary

    # Serialize current policies
    current_policies = []
    for p in analysis_context.current_policies:
        policy_dict = {
            "policy_name": p.policy_name,
            "policy_type": p.policy_type,
            "adjustment_type": p.adjustment_type,
            "cooldown": p.cooldown,
        }
        if p.step_adjustments:
            policy_dict["step_adjustments"] = [
                {
                    "metric_interval_lower_bound": sa.metric_interval_lower_bound,
                    "metric_interval_upper_bound": sa.metric_interval_upper_bound,
                    "scaling_adjustment": sa.scaling_adjustment,
                }
                for sa in p.step_adjustments
            ]
        current_policies.append(policy_dict)

    # Serialize instance pricing
    def _instance_to_dict(info):
        return {
            "type": info.instance_type,
            "vcpu": info.vcpu,
            "memory_gb": info.memory_gib,
            "architecture": info.architecture,
            "burstable": info.burstable,
            "price_per_hour": info.price_per_hour,
            "monthly_cost_estimate": info.monthly_cost_estimate,
        }

    return {
        "analysis_request": {
            "asg_name": asg.name,
            "queue_name": queue.queue_name,
            "period": period,
            "strategy": strategy,
            "instance_warmup_seconds": warmup_seconds,
            "instance_drain_seconds": drain_seconds,
        },
        "queue_metrics": {
            "metric_name": "rabbitmq_detailed_queue_messages_ready",
            "period_start": queue.period_start,
            "period_end": queue.period_end,
            "statistics": {
                "avg": round(queue.stats.avg, 2),
                "min": round(queue.stats.min, 2),
                "max": round(queue.stats.max, 2),
                "p50": round(queue.stats.p50, 2),
                "p95": round(queue.stats.p95, 2),
                "p99": round(queue.stats.p99, 2),
            },
            "time_series_summary": {
                "total_points": queue.raw_point_count,
                "zero_depth_pct": round(queue.stats.zero_pct, 2),
                "peak_hour_utc": queue.hourly_pattern.peak_hour,
                "trough_hour_utc": queue.hourly_pattern.trough_hour,
            },
        },
        "asg_data": {
            "current_config": {
                "min_size": asg.min_size,
                "max_size": asg.max_size,
                "desired_capacity": asg.desired_capacity,
            },
            "capacity_history": {
                "avg_desired": round(cap.desired_stats.avg, 2),
                "max_desired": round(cap.desired_stats.max, 2),
                "min_desired": round(cap.desired_stats.min, 2),
                "avg_max_size": round(cap.max_size_stats.avg, 2),
            },
            "current_policies": current_policies,
            "launch_template": {
                "instance_type": asg.instance_types[0] if asg.instance_types else "unknown",
            },
        },
        "instance_pricing": {
            "current": _instance_to_dict(instance_recs.current),
            "alternatives": [_instance_to_dict(alt) for alt in instance_recs.alternatives],
        },
    }


# -- LLM call --

def call_llm_advisor(
    llm_config: LLMConfig,
    analysis_context: AnalysisContext,
    instance_recs: InstanceRecommendations,
    strategy: str,
    period: str,
    warmup_seconds: int = 120,
    drain_seconds: int = 300,
) -> LLMAdvisorResponse:
    """Call Claude API with the analysis data and return structured recommendations."""
    llm_input = build_llm_input(
        analysis_context, instance_recs, strategy, period,
        warmup_seconds=warmup_seconds, drain_seconds=drain_seconds,
    )

    strategy_prompt = STRATEGY_PROMPTS.get(strategy, STRATEGY_PROMPTS["balanced"])
    full_system = SYSTEM_PROMPT + "\n" + strategy_prompt

    user_message = (
        "Analyze the following ASG and queue data, then return your recommendations "
        "as a single JSON object matching the output schema.\n\n"
        f"```json\n{json.dumps(llm_input, indent=2)}\n```"
    )

    logger.info(
        "Calling Claude API (model=%s, strategy=%s, asg=%s)",
        llm_config.model, strategy, analysis_context.asg_config.name,
    )

    client_kwargs: dict = {"api_key": llm_config.api_key}
    if llm_config.base_url:
        client_kwargs["base_url"] = llm_config.base_url
    client = anthropic.Anthropic(**client_kwargs)

    message = client.messages.create(
        model=llm_config.model,
        max_tokens=4096,
        system=full_system,
        messages=[{"role": "user", "content": user_message}],
    )

    raw_text = ""
    for block in message.content:
        if hasattr(block, "text"):
            raw_text += block.text

    if not raw_text.strip():
        logger.error(
            "LLM returned empty response. stop_reason=%s, content=%s",
            message.stop_reason, message.content,
        )
        raise ValueError(
            f"LLM returned empty response (stop_reason={message.stop_reason}). "
            "Check your API key, model name, and base URL."
        )

    logger.debug("Raw LLM response: %s", raw_text)

    return parse_llm_response(raw_text, warmup_seconds=warmup_seconds, drain_seconds=drain_seconds)


# -- Response parsing and validation --

def parse_llm_response(raw_text: str, warmup_seconds: int = 120, drain_seconds: int = 300) -> LLMAdvisorResponse:
    """Parse and validate the LLM's JSON response into typed dataclasses."""
    # Strip markdown fences if present
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json) and last line (```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse LLM response as JSON: %s\nRaw text: %s", e, text[:500])
        raise ValueError(f"LLM response is not valid JSON: {e}\nResponse preview: {text[:200]}") from e
    return _parse_response_dict(data, warmup_seconds=warmup_seconds, drain_seconds=drain_seconds)


def _parse_response_dict(data: dict, warmup_seconds: int = 120, drain_seconds: int = 300) -> LLMAdvisorResponse:
    """Convert a raw dict into a validated LLMAdvisorResponse."""
    response = LLMAdvisorResponse()

    # Parse step scaling policy
    if policy_data := data.get("step_scaling_policy"):
        response.step_scaling_policy = _parse_scaling_policy(
            policy_data, warmup_seconds=warmup_seconds, drain_seconds=drain_seconds,
        )

    # Parse instance recommendations
    for rec_data in data.get("instance_recommendations", []):
        response.instance_recommendations.append(
            LLMInstanceRecommendation(
                instance_type=rec_data.get("instance_type", ""),
                vcpu=rec_data.get("vcpu", 0),
                memory_gb=rec_data.get("memory_gb", 0.0),
                price_per_hour=rec_data.get("price_per_hour", 0.0),
                monthly_cost_estimate=rec_data.get("monthly_cost_estimate", 0.0),
                savings_vs_current_pct=rec_data.get("savings_vs_current_pct", 0.0),
                rationale=rec_data.get("rationale", ""),
            )
        )

    # Parse rationale
    if rationale_data := data.get("rationale"):
        response.rationale = LLMRationale(
            scaling_policy=rationale_data.get("scaling_policy", ""),
            strategy_impact=rationale_data.get("strategy_impact", ""),
            risk_notes=rationale_data.get("risk_notes", ""),
        )

    # Validate guardrails
    _validate_response(response, warmup_seconds=warmup_seconds, drain_seconds=drain_seconds)

    return response


def _parse_scaling_policy(data: dict, warmup_seconds: int = 120, drain_seconds: int = 300) -> LLMStepScalingPolicy:
    """Parse the single combined step scaling policy."""
    min_cooldown = max(warmup_seconds, drain_seconds)
    return LLMStepScalingPolicy(
        adjustment_type=data.get("adjustment_type", "ChangeInCapacity"),
        cooldown=max(data.get("cooldown", min_cooldown), min_cooldown),
        step_adjustments=[
            LLMStepAdjustment(
                metric_interval_lower_bound=sa.get("metric_interval_lower_bound"),
                metric_interval_upper_bound=sa.get("metric_interval_upper_bound"),
                scaling_adjustment=sa.get("scaling_adjustment", 0),
            )
            for sa in data.get("step_adjustments", [])
        ],
    )


def _validate_response(response: LLMAdvisorResponse, warmup_seconds: int = 120, drain_seconds: int = 300) -> None:
    """Enforce guardrails on the parsed response. Raises ValueError on violations."""
    policy = response.step_scaling_policy
    min_cooldown = max(warmup_seconds, drain_seconds)

    # Must have step adjustments
    if not policy.step_adjustments:
        raise ValueError("LLM response missing step adjustments")

    # Cooldown must be >= max(warmup, drain)
    if policy.cooldown < min_cooldown:
        raise ValueError(
            f"Cooldown {policy.cooldown}s is below minimum {min_cooldown}s "
            f"(warmup={warmup_seconds}s, drain={drain_seconds}s)"
        )

    # Must have at least one negative (scale-down) and one positive (scale-up) adjustment
    has_scale_down = any(sa.scaling_adjustment < 0 for sa in policy.step_adjustments)
    has_scale_up = any(sa.scaling_adjustment > 0 for sa in policy.step_adjustments)
    if not has_scale_down:
        raise ValueError("Policy must include at least one scale-down step (negative adjustment)")
    if not has_scale_up:
        raise ValueError("Policy must include at least one scale-up step (positive adjustment)")

    # Clamp negative bounds to 0 — queue depth cannot be negative
    # Also ensure every step has both bounds (lower defaults to 0, upper to null)
    for sa in policy.step_adjustments:
        if sa.metric_interval_lower_bound is None:
            sa.metric_interval_lower_bound = 0
        if sa.metric_interval_lower_bound < 0:
            logger.warning(
                "Clamping negative lower_bound %.0f to 0 (queue depth cannot be negative)",
                sa.metric_interval_lower_bound,
            )
            sa.metric_interval_lower_bound = 0
        if sa.metric_interval_upper_bound is not None and sa.metric_interval_upper_bound < 0:
            logger.warning(
                "Clamping negative upper_bound %.0f to 0 (queue depth cannot be negative)",
                sa.metric_interval_upper_bound,
            )
            sa.metric_interval_upper_bound = 0
