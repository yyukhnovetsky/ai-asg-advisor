# AI ASG Advisor — Original Design Notes

> **This document contains the original design requirements that guided initial development.
> For current documentation, see [`README.md`](README.md).**

## Original Requirements

### Inputs

1. **RabbitMQ Metrics** — `rabbitmq_detailed_queue_messages_ready` from CloudWatch
2. **Analysis Period** — 1 week to 2 months
3. **ASG Capacity** — `desired_capacity` and `max_size` for `celery-*` ASGs
4. **Existing Autoscaling Policies** — current step scaling policies for each ASG
5. **Python 3.13+** — minimum runtime version
6. **CLI Interface** — command-line tool (no UI)
7. **AWS Authorization** — named profile (`ai-advisor`) or IAM role assumption
8. **Output** — Terraform `step_adjustment` blocks

### Initial Focus

- **ASG:** `celery-email-sync-py39`
- **RabbitMQ queue:** `email_sync_v2`

### CloudWatch Dimensions

```hcl
dimensions = {
  "OTelLib" = "github.com/open-telemetry/opentelemetry-collector-contrib/receiver/prometheusreceiver"
  "queue"   = "some-queue"
}
```
