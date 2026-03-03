resource "aws_autoscaling_policy" "celery_beautify_v4_py39_step_scaling" {
  name                      = "celery-beautify-v4-py39-step-scaling"
  autoscaling_group_name    = "celery-beautify-v4-py39"
  adjustment_type           = "ChangeInCapacity"
  policy_type               = "StepScaling"
  estimated_instance_warmup = 300

  step_adjustment {
    metric_interval_lower_bound = 0
    metric_interval_upper_bound = 50
    scaling_adjustment          = -10
  }

  step_adjustment {
    metric_interval_lower_bound = 50
    metric_interval_upper_bound = 300
    scaling_adjustment          = -3
  }

  step_adjustment {
    metric_interval_lower_bound = 300
    metric_interval_upper_bound = 1000
    scaling_adjustment          = 5
  }

  step_adjustment {
    metric_interval_lower_bound = 1000
    metric_interval_upper_bound = 5000
    scaling_adjustment          = 15
  }

  step_adjustment {
    metric_interval_lower_bound = 5000
    metric_interval_upper_bound = 12000
    scaling_adjustment          = 30
  }

  step_adjustment {
    metric_interval_lower_bound = 12000
    metric_interval_upper_bound = 40000
    scaling_adjustment          = 50
  }

  step_adjustment {
    metric_interval_lower_bound = 40000
    metric_interval_upper_bound = null
    scaling_adjustment          = 80
  }
}
