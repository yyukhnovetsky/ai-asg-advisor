# AI ASG Advisor

A CLI tool that analyzes AWS Auto Scaling Group behavior and uses an LLM to produce optimized step scaling policies, instance type recommendations, and cost/performance trade-off analysis.

## Overview

AI ASG Advisor collects historical metrics (RabbitMQ queue depth, ASG capacity) and current ASG configuration from AWS, feeds the data into an LLM (Claude) with domain-specific guardrails, and outputs:

- Optimized Terraform `step_adjustment` blocks
- Instance type recommendations (cost-optimized, performance-optimized, or balanced)
- A human-readable rationale for every recommendation

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     CLI (Click)                              │
│  --asg celery-email-sync-py39                                │
│  --queue email_sync_v2                                       │
│  --period 2w                                                 │
│  --strategy balanced|cost|performance                        │
│  --profile ai-adviser | --role ai-advisor-role               │
└──────────────────────┬───────────────────────────────────────┘
                       │
           ┌───────────▼───────────┐
           │    AWS Auth Module    │
           │  (profile or IAM role)│
           └───────────┬───────────┘
                       │
     ┌─────────────────┼─────────────────┐
     │                 │                 │
┌────▼─────┐   ┌──────▼──────┐   ┌──────▼──────┐
│CloudWatch│   │ AutoScaling │   │EC2 / Pricing│
│Collector │   │  Collector  │   │  Collector  │
│          │   │             │   │             │
│- queue   │   │- ASG config │   │- current    │
│  depth   │   │- capacity   │   │  instance   │
│  metrics │   │  history    │   │  type specs │
│          │   │- current    │   │- alternative│
│          │   │  policies   │   │  types +    │
│          │   │- launch     │   │  pricing    │
│          │   │  template   │   │             │
└────┬─────┘   └──────┬──────┘   └──────┬──────┘
     │                │                 │
     └────────────────┼─────────────────┘
                      │
              ┌───────▼───────┐
              │  Data Context │
              │   Builder     │
              │               │
              │ Assembles all │
              │ collected data│
              │ into a struct │
              │ for the LLM   │
              └───────┬───────┘
                      │
              ┌───────▼───────┐
              │  LLM Advisor  │
              │  (Claude API) │
              │               │
              │ System prompt │
              │ + guardrails  │
              │ + data context│
              │               │
              │ Returns:      │
              │ - step policy │
              │ - instance    │
              │   types       │
              │ - rationale   │
              └───────┬───────┘
                      │
              ┌───────▼───────┐
              │   Output      │
              │   Formatter   │
              │               │
              │ - Terraform   │
              │   HCL blocks  │
              │ - JSON report │
              │ - Human-      │
              │   readable    │
              │   summary     │
              └───────────────┘
```

## Features

### Data Collection

| Source | Data | API |
|--------|------|-----|
| CloudWatch | `rabbitmq_detailed_queue_messages_ready` time-series | `cloudwatch:GetMetricData` |
| CloudWatch | ASG `GroupDesiredCapacity`, `GroupMaxSize` history | `cloudwatch:GetMetricData` |
| Auto Scaling | Current step scaling policies | `autoscaling:DescribePolicies` |
| Auto Scaling | ASG configuration (desired, min, max) | `autoscaling:DescribeAutoScalingGroups` |
| Auto Scaling | Launch template / launch configuration | `autoscaling:DescribeAutoScalingGroups` |
| EC2 | Current instance type specs (vCPU, memory, network) | `ec2:DescribeInstanceTypes` |
| EC2 | Alternative instance type candidates | `ec2:DescribeInstanceTypes` |
| Pricing | On-demand and spot pricing for instance types | `pricing:GetProducts` |

### Cost/Performance Optimization Strategy

The `--strategy` flag controls how the LLM weighs trade-offs:

| Strategy | Behavior |
|----------|----------|
| `cost` | Minimize spend: prefer smaller/fewer instances, aggressive scale-down, suggest cheaper instance families (e.g., `t3` → `t3a`, `m5` → `m5a`, Graviton) |
| `performance` | Minimize queue latency: prefer faster scale-up, higher headroom, suggest compute-optimized or larger instance types |
| `balanced` (default) | Optimize the cost-to-latency ratio: find the sweet spot between spend and queue processing time |

### Instance Type Recommendations

Based on the current launch template's instance type, the tool recommends alternatives:

- **Same family, different size** — e.g., `m5.xlarge` → `m5.large` or `m5.2xlarge`
- **ARM/Graviton equivalents** — e.g., `m5.xlarge` → `m6g.xlarge` (typically ~20% cheaper)
- **AMD equivalents** — e.g., `m5.xlarge` → `m5a.xlarge` (typically ~10% cheaper)
- **Newer generation** — e.g., `m5.xlarge` → `m7i.xlarge`
- **Burstable options** — e.g., `m5.large` → `t3.xlarge` (for bursty workloads)

Each recommendation includes estimated monthly cost difference.

### LLM-Powered Analysis (Claude API)

The tool uses Claude as an analysis engine with strict guardrails. The LLM receives structured data (metrics, current config, pricing) and returns structured recommendations.

#### LLM System Prompt and Guardrails

The system prompt constrains the LLM to act as an **AWS autoscaling specialist** with these guardrails:

```
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
   - Always include a scale-down policy alongside scale-up.
   - Minimum cooldown period: 60 seconds.

4. OUTPUT FORMAT: Return responses strictly as JSON matching the defined
   output schema. Do not include commentary outside the JSON structure.

5. INSTANCE TYPES:
   - Only recommend instance types available in the target region.
   - Only recommend instance types from the same or adjacent compute
     category (general-purpose, compute-optimized).
   - Always include the current instance type as a baseline option.
   - Include price-per-hour for every recommended type.

6. COST ESTIMATES:
   - Use the provided pricing data only. Do not guess prices.
   - Express savings as both percentage and absolute monthly estimate.

7. PROHIBITED:
   - No infrastructure provisioning or modification suggestions beyond
     scaling policy and instance type.
   - No security, IAM, or networking recommendations.
   - No opinions on application architecture or code changes.
```

#### LLM Input Schema

The data context sent to the LLM follows this structure:

```json
{
  "analysis_request": {
    "asg_name": "celery-email-sync-py39",
    "queue_name": "email_sync_v2",
    "period": "2w",
    "strategy": "balanced"
  },
  "queue_metrics": {
    "metric_name": "rabbitmq_detailed_queue_messages_ready",
    "period_start": "2025-02-11T00:00:00Z",
    "period_end": "2025-02-25T00:00:00Z",
    "statistics": {
      "avg": 142.5,
      "p50": 85.0,
      "p95": 520.0,
      "p99": 1200.0,
      "max": 3500.0
    },
    "time_series_summary": {
      "total_points": 2016,
      "zero_depth_pct": 12.5,
      "hourly_pattern": "..."
    }
  },
  "asg_data": {
    "current_config": {
      "min_size": 2,
      "max_size": 20,
      "desired_capacity": 5
    },
    "capacity_history": {
      "avg_desired": 6.2,
      "max_desired": 18,
      "min_desired": 2
    },
    "current_policies": [ "..." ],
    "launch_template": {
      "instance_type": "m5.xlarge"
    }
  },
  "instance_pricing": {
    "current": { "type": "m5.xlarge", "vcpu": 4, "memory_gb": 16, "price_per_hour": 0.192 },
    "alternatives": [
      { "type": "m5a.xlarge", "vcpu": 4, "memory_gb": 16, "price_per_hour": 0.172 },
      { "type": "m6g.xlarge", "vcpu": 4, "memory_gb": 16, "price_per_hour": 0.154 },
      { "type": "t3.xlarge",  "vcpu": 4, "memory_gb": 16, "price_per_hour": 0.166 }
    ]
  }
}
```

#### LLM Output Schema

```json
{
  "step_scaling_policy": {
    "scale_up": {
      "adjustment_type": "ChangeInCapacity",
      "cooldown": 120,
      "step_adjustments": [
        { "metric_interval_lower_bound": 0,    "metric_interval_upper_bound": 200,  "scaling_adjustment": 1 },
        { "metric_interval_lower_bound": 200,  "metric_interval_upper_bound": 500,  "scaling_adjustment": 2 },
        { "metric_interval_lower_bound": 500,  "metric_interval_upper_bound": 1000, "scaling_adjustment": 4 },
        { "metric_interval_lower_bound": 1000, "scaling_adjustment": 6 }
      ]
    },
    "scale_down": {
      "adjustment_type": "ChangeInCapacity",
      "cooldown": 300,
      "step_adjustments": [
        { "metric_interval_lower_bound": -100, "metric_interval_upper_bound": 0,  "scaling_adjustment": -1 },
        { "metric_interval_lower_bound": -500, "metric_interval_upper_bound": -100, "scaling_adjustment": -2 }
      ]
    }
  },
  "instance_recommendations": [
    {
      "instance_type": "m6g.xlarge",
      "vcpu": 4,
      "memory_gb": 16,
      "price_per_hour": 0.154,
      "monthly_cost_estimate": 110.88,
      "savings_vs_current_pct": 19.8,
      "rationale": "Graviton-based equivalent; compatible with Python workloads."
    }
  ],
  "rationale": {
    "scaling_policy": "Queue depth p95 is 520 messages. ...",
    "strategy_impact": "Balanced strategy applied: ...",
    "risk_notes": "Peak capacity reached 18/20 max. Consider raising max_size to 25."
  }
}
```

### Terraform Output

The tool generates ready-to-use Terraform HCL:

```hcl
resource "aws_autoscaling_policy" "celery_email_sync_scale_up" {
  name                   = "celery-email-sync-py39-scale-up"
  autoscaling_group_name = "celery-email-sync-py39"
  adjustment_type        = "ChangeInCapacity"
  policy_type            = "StepScaling"
  estimated_instance_warmup = 120

  step_adjustment {
    metric_interval_lower_bound = 0
    metric_interval_upper_bound = 200
    scaling_adjustment          = 1
  }

  step_adjustment {
    metric_interval_lower_bound = 200
    metric_interval_upper_bound = 500
    scaling_adjustment          = 2
  }

  step_adjustment {
    metric_interval_lower_bound = 500
    metric_interval_upper_bound = 1000
    scaling_adjustment          = 4
  }

  step_adjustment {
    metric_interval_lower_bound = 1000
    scaling_adjustment          = 6
  }
}
```

## Installation

```bash
pip install -e .

# With development dependencies
pip install -e ".[dev]"
```

## Configuration

### Config File

Copy the example config and edit:

```bash
mkdir -p ~/.config/ai-asg-advisor
cp config.toml.example ~/.config/ai-asg-advisor/config.toml
```

See [`config.toml.example`](config.toml.example) for all available settings.

### AWS Authorization

| Environment | Method | CLI Flag |
|-------------|--------|----------|
| Local | Named profile | `--profile ai-adviser` (default) |
| EC2 | IAM role assumption | `--role ai-advisor-role` or `--role arn:aws:iam::123456789012:role/...` |
| Default chain | Environment / instance metadata | omit `--profile` and `--role` |

### Claude API Key

Set via environment variable (recommended):

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Or in the config file (`~/.config/ai-asg-advisor/config.toml`):

```toml
[llm]
api_key = "sk-ant-..."
model = "claude-sonnet-4-20250514"
```

## Usage

```bash
# Basic: analyze with balanced strategy, output Terraform HCL
ai-asg-advisor \
  --asg celery-email-sync-py39 \
  --queue email_sync_v2 \
  --period 2w

# Cost-optimized analysis, JSON output
ai-asg-advisor \
  --asg celery-email-sync-py39 \
  --queue email_sync_v2 \
  --period 1m \
  --strategy cost \
  --format json

# Performance-optimized, write to file
ai-asg-advisor \
  --asg celery-email-sync-py39 \
  --queue email_sync_v2 \
  --period 2w \
  --strategy performance \
  --format terraform \
  --output scaling_policy.tf

# Human-readable text summary
ai-asg-advisor \
  --asg celery-email-sync-py39 \
  --queue email_sync_v2 \
  --period 2w \
  --format text

# Use IAM role (EC2 environment)
ai-asg-advisor \
  --asg celery-email-sync-py39 \
  --queue email_sync_v2 \
  --period 2w \
  --role ai-advisor-role

# Specify region and custom config file
ai-asg-advisor \
  --asg celery-email-sync-py39 \
  --queue email_sync_v2 \
  --region eu-west-1 \
  --config ./my-config.toml

# Verbose logging (debug output to stderr)
ai-asg-advisor \
  --asg celery-email-sync-py39 \
  --queue email_sync_v2 \
  -v
```

### CLI Options

| Option | Description | Default |
|--------|-------------|---------|
| `--asg` | Auto Scaling Group name (required) | — |
| `--queue` | RabbitMQ queue name (required) | — |
| `--period` | Analysis time period (`1w`, `2w`, `3w`, `1m`, `2m`) | `2w` |
| `--strategy` | Optimization strategy (`cost`, `balanced`, `performance`) | `balanced` |
| `--format` | Output format (`terraform`, `json`, `text`) | `terraform` |
| `--output` | Write output to file instead of stdout | — |
| `--profile` | AWS CLI profile name | `ai-adviser` |
| `--role` | IAM role ARN or short name to assume | — |
| `--region` | AWS region | `us-east-1` |
| `--config` | Path to config file | `~/.config/ai-asg-advisor/config.toml` |
| `-v, --verbose` | Enable debug logging to stderr | off |
| `--version` | Show version and exit | — |

### Analysis Period

| Flag | Duration | CloudWatch Resolution |
|------|----------|-----------------------|
| `1w` | 1 week | 5-minute |
| `2w` | 2 weeks | 5-minute |
| `3w` | 3 weeks | 1-hour |
| `1m` | 1 month | 1-hour |
| `2m` | 2 months | 1-hour |

## Requirements

- Python >= 3.13
- AWS credentials with read access to CloudWatch, Auto Scaling, EC2, and Pricing APIs
- Claude API key (Anthropic)

## Required AWS IAM Permissions

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "cloudwatch:GetMetricData",
        "cloudwatch:ListMetrics",
        "autoscaling:DescribeAutoScalingGroups",
        "autoscaling:DescribePolicies",
        "autoscaling:DescribeLaunchConfigurations",
        "ec2:DescribeInstanceTypes",
        "ec2:DescribeLaunchTemplates",
        "ec2:DescribeLaunchTemplateVersions",
        "pricing:GetProducts",
        "sts:GetCallerIdentity",
        "sts:AssumeRole"
      ],
      "Resource": "*"
    }
  ]
}
```

## Development

```bash
# Create a virtual environment and install dev dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest

# Run tests with coverage
pytest --cov=ai_asg_advisor

# Lint
ruff check src/ tests/
```
