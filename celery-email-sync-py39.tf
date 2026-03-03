resource "aws_autoscaling_policy" "celery_email_sync_py39_step_scaling" {
  name                      = "celery-email-sync-py39-step-scaling"
  autoscaling_group_name    = "celery-email-sync-py39"
  adjustment_type           = "ChangeInCapacity"
  policy_type               = "StepScaling"
  estimated_instance_warmup = 120
  cooldown                  = 300

  step_adjustment {
    metric_interval_lower_bound = 0
    metric_interval_upper_bound = 500
    scaling_adjustment          = -10
  }

  step_adjustment {
    metric_interval_lower_bound = 500
    metric_interval_upper_bound = 50000
    scaling_adjustment          = -3
  }

  step_adjustment {
    metric_interval_lower_bound = 50000
    metric_interval_upper_bound = 108000
    scaling_adjustment          = 0
  }

  step_adjustment {
    metric_interval_lower_bound = 108000
    metric_interval_upper_bound = 500000
    scaling_adjustment          = 3
  }

  step_adjustment {
    metric_interval_lower_bound = 500000
    metric_interval_upper_bound = 2000000
    scaling_adjustment          = 5
  }

  step_adjustment {
    metric_interval_lower_bound = 2000000
    metric_interval_upper_bound = 10000000
    scaling_adjustment          = 10
  }

  step_adjustment {
    metric_interval_lower_bound = 10000000
    metric_interval_upper_bound = null
    scaling_adjustment          = 15
  }
}
