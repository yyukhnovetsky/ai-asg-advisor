# AI ASG Advisor — Agent Guide

## Project Overview

CLI tool that analyzes AWS Auto Scaling Group behavior and uses Claude LLM to produce optimized step scaling policies, instance type recommendations, and cost/performance analysis.

Python 3.13+ / src layout / Click CLI / boto3 / Anthropic SDK.

## Build & Install

```bash
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest tests/ -v
```

All AWS and LLM calls are mocked in tests (no credentials needed). 97 tests expected to pass.

## Project Structure

```
src/ai_asg_advisor/
  cli.py                 # Click CLI entry point
  config.py              # TOML config + env var loading
  aws_auth.py            # AWS session/profile/role auth
  cloudwatch_collector.py # CloudWatch queue depth + ASG capacity metrics
  asg_collector.py       # ASG config + scaling policies
  instance_collector.py  # EC2 instance types + pricing
  preprocessor.py        # Statistical analysis of collected data
  llm_advisor.py         # Claude API call with guardrails + response parsing
  output_formatter.py    # Terraform HCL, JSON, plain-text output

tests/                   # One test file per module, pytest
results/                 # Generated advisor output files (tf/json)
config.toml.example      # Example config file
```

## Key Conventions

- **Single step scaling policy**: Both scale-up (positive) and scale-down (negative) adjustments in one `step_adjustments` list. Never separate up/down policies.
- **Both bounds required**: Every step must have `metric_interval_lower_bound` and `metric_interval_upper_bound`. Use `0` for the lowest lower bound, `null` for unbounded upper.
- **Non-negative bounds**: All metric bounds must be >= 0 (queue depth cannot be negative).
- **Cooldown**: Must be >= `max(warmup_seconds, drain_seconds)`. Default warmup=120s, drain=300s.
- **Estimated instance warmup**: `estimated_instance_warmup` must be >= `warmup_seconds`. Prevents premature scaling during instance boot. Both `cooldown` and `estimated_instance_warmup` are included in the policy output.
- **Terraform output**: Generates a `step_adjustments = [...]` list for use as a Terraform module variable, not a full resource block.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY` | Claude API key (AUTH_TOKEN takes precedence) |
| `ANTHROPIC_MODEL` / `AI_ASG_ADVISOR_MODEL` | Claude model name |
| `ANTHROPIC_BASE_URL` | Custom API base URL |
| `AWS_DEFAULT_REGION` | AWS region override |
| `AWS_PROFILE` | AWS profile (default: `ai-advisor`) |

## Code Style

- Ruff for linting (E, F, W, I rules)
- Type hints throughout (Python 3.13+ syntax: `X | None` not `Optional[X]`)
- Dataclasses for structured data
- No docstrings on code you didn't change; keep changes minimal

## Commit Messages

Format: `type: short description`

Types: `feat`, `fix`, `refactor`, `docs`, `chore`, `test`

Always append: `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>`
