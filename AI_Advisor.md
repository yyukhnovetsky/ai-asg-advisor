# AI ASG Advisor

Analyzing tool for optimizing the dynamic autoscaling step policies.

## Inputs

The optimization should be performed based on the following inputs:

### 1. RabbitMQ Metrics

Metric: `rabbitmq_detailed_queue_messages_ready` for all queues.

Dimensions block:

```hcl
dimensions = {
  "OTelLib" = "github.com/open-telemetry/opentelemetry-collector-contrib/receiver/prometheusreceiver"
  "queue"   = "some-queue"
}
```

### 2. Analysis Period

The analyzed period can vary:
- 1 week
- 2 weeks
- 3 weeks
- 1 month
- 2 months

### 3. ASG Capacity

For all the ASGs named `celery-*`, analyze the ASG capacity (`desired_capacity` and `max_size`) for the same period of time as for messages in RabbitMQ queues.

**Initial focus:**
- **ASG:** `celery-email-sync-py39`
- **RabbitMQ queue:** `email_sync_v2`

### 4. Existing Autoscaling Policies

Existing autoscaling policies for every ASG named `celery-*`.

### 5. Python Version

Python 3.13 or higher should be used for the analyzing tool.

### 6. Interface

For the beginning, only a backend (command-line tool) is required.

### 7. AWS Authorization

The authorization against AWS can be done by:

- **Local execution:** Using a dedicated profile `[ai-adviser]`, defined in `~/.aws/credentials`.
- **EC2 execution:** Assuming IAM role `ai-advisor-role`, when the tool is being executed within an EC2 that assumes the appropriate profile.

### 8. Required Output

Generate a `step_adjustment` block suitable for Terraform code.
