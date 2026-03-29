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
│  --profile ai-advisor | --role ai-advisor-role               │
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
   - Always include both scale-down (negative adjustment) and scale-up
     (positive adjustment) steps within the single policy.
   - Minimum cooldown period: 60 seconds.
   - Cooldown MUST be >= max(instance_warmup_seconds, instance_drain_seconds).
   - All metric bounds MUST be >= 0 (queue depth cannot be negative).
   - Every step MUST have both metric_interval_lower_bound and
     metric_interval_upper_bound (use 0 for lowest, null for unbounded).

4. OUTPUT FORMAT: Return responses strictly as JSON matching the defined
   output schema. Do not include commentary outside the JSON structure.

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
```

#### LLM Input Schema

The data context sent to the LLM follows this structure:

```json
{
  "analysis_request": {
    "asg_name": "celery-email-sync-py39",
    "queue_name": "email_sync_v2",
    "period": "2w",
    "strategy": "balanced",
    "instance_warmup_seconds": 120,
    "instance_drain_seconds": 300
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
    "adjustment_type": "ChangeInCapacity",
    "cooldown": 300,
    "step_adjustments": [
      { "metric_interval_lower_bound": 0,    "metric_interval_upper_bound": 50,   "scaling_adjustment": -10 },
      { "metric_interval_lower_bound": 50,   "metric_interval_upper_bound": 200,  "scaling_adjustment": -5 },
      { "metric_interval_lower_bound": 200,  "metric_interval_upper_bound": 500,  "scaling_adjustment": 3 },
      { "metric_interval_lower_bound": 500,  "metric_interval_upper_bound": 1500, "scaling_adjustment": 10 },
      { "metric_interval_lower_bound": 1500, "metric_interval_upper_bound": 5000, "scaling_adjustment": 20 },
      { "metric_interval_lower_bound": 5000, "metric_interval_upper_bound": null,  "scaling_adjustment": 40 }
    ]
  },
  "instance_recommendations": [
    {
      "instance_type": "c5a.2xlarge",
      "vcpu": 8,
      "memory_gb": 16,
      "price_per_hour": 0.38,
      "monthly_cost_estimate": 277.40,
      "savings_vs_current_pct": 10.37,
      "rationale": "AMD EPYC-based equivalent with identical specs at ~10% lower cost."
    }
  ],
  "rationale": {
    "scaling_policy": "Steps aligned with observed queue depth percentiles. ...",
    "strategy_impact": "Balanced strategy: moderate cooldowns, graduated steps. ...",
    "risk_notes": "Peak capacity reached 163/250 max. Queue zero 26.5% of time."
  }
}
```

### Terraform Output

The tool generates a `step_adjustments` list ready to pass into a Terraform module:

```hcl
  step_adjustments = [
    {
      scaling_adjustment          = -10
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 50
    },
    {
      scaling_adjustment          = -5
      metric_interval_lower_bound = 50
      metric_interval_upper_bound = 200
    },
    {
      scaling_adjustment          = 3
      metric_interval_lower_bound = 200
      metric_interval_upper_bound = 500
    },
    {
      scaling_adjustment          = 10
      metric_interval_lower_bound = 500
      metric_interval_upper_bound = 1500
    },
    {
      scaling_adjustment          = 20
      metric_interval_lower_bound = 1500
      metric_interval_upper_bound = 5000
    },
    {
      scaling_adjustment          = 40
      metric_interval_lower_bound = 5000
      metric_interval_upper_bound = null
    },
  ]
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
| Local | Named profile | `--profile ai-advisor` (default) |
| EC2 | IAM role assumption | `--role ai-advisor-role` or `--role arn:aws:iam::123456789012:role/...` |
| Default chain | Environment / instance metadata | omit `--profile` and `--role` |

### Claude API Key

Set via environment variables (recommended):

```bash
# API key (either variable works; ANTHROPIC_AUTH_TOKEN takes precedence)
export ANTHROPIC_AUTH_TOKEN="sk-ant-..."
# or
export ANTHROPIC_API_KEY="sk-ant-..."

# Optional: custom model and base URL
export ANTHROPIC_MODEL="claude-sonnet-4-20250514"
export ANTHROPIC_BASE_URL="https://api.anthropic.com"
```

Or in the config file (`~/.config/ai-asg-advisor/config.toml`):

```toml
[llm]
api_key = "sk-ant-..."
model = "claude-sonnet-4-20250514"
# base_url = "https://api.anthropic.com"
```

## Usage

```bash
# Basic: analyze with balanced strategy, output Terraform to stdout
ai-asg-advisor \
  --asg celery-email-sync-py39 \
  --queue email_sync_v2 \
  --period 2w

# Generate both Terraform and JSON outputs to files
ai-asg-advisor \
  --asg celery-email-sync-py39 \
  --queue email_sync_v2 \
  --period 2w \
  --format terraform \
  --output results/celery-email-sync-py39.tf

ai-asg-advisor \
  --asg celery-email-sync-py39 \
  --queue email_sync_v2 \
  --period 2w \
  --format json \
  --output results/celery-email-sync-py39.json

# Performance strategy with 1-month lookback, writing to files
ai-asg-advisor \
  --asg celery-beautify-v4-py39 \
  --queue beautify_v4 \
  --period 1m \
  --strategy performance \
  --format terraform \
  --output results/celery-beautify-v4-py39-performance.tf

# ASG in a different region (default is us-west-1)
ai-asg-advisor \
  --asg celery-transcription-v3-usw2-py39 \
  --queue transcription_v3 \
  --period 1m \
  --region us-west-2 \
  --format terraform \
  --output results/celery-transcription-v3-usw2-py39.tf

# Cost-optimized analysis, JSON output
ai-asg-advisor \
  --asg celery-email-sync-py39 \
  --queue email_sync_v2 \
  --period 1m \
  --strategy cost \
  --format json

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

# Include Graviton (ARM64) instance types in recommendations
ai-asg-advisor \
  --asg celery-email-sync-py39 \
  --queue email_sync_v2 \
  --period 2w \
  --use-graviton yes

# Custom warmup/drain times and config file
ai-asg-advisor \
  --asg celery-email-sync-py39 \
  --queue email_sync_v2 \
  --period 2w \
  --warmup 180 \
  --drain 600 \
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
| `--profile` | AWS CLI profile name | `ai-advisor` |
| `--role` | IAM role ARN or short name to assume | — |
| `--region` | AWS region | `us-west-1` |
| `--namespace` | CloudWatch metrics namespace (`cloudamqp/fat-plum-goat`, `CloudAMQP/metrics`) | `cloudamqp/fat-plum-goat` |
| `--use-graviton` | Include Graviton (ARM64) instance types (`yes`, `no`) | `no` |
| `--warmup` | Instance warmup time in seconds (time for new EC2 to be ready) | `120` |
| `--drain` | Instance drain time in seconds (lifecycle hook for graceful shutdown) | `300` |
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
