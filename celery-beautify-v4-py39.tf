resource "aws_autoscaling_policy" "celery_beautify_v4_py39_step_scaling" {
  name                      = "celery-beautify-v4-py39-step-scaling"
  autoscaling_group_name    = "celery-beautify-v4-py39"
  adjustment_type           = "ChangeInCapacity"
  policy_type               = "StepScaling"
  estimated_instance_warmup = 120
  cooldown                  = 300

  step_adjustment {
    metric_interval_lower_bound = 0
    metric_interval_upper_bound = 50
    scaling_adjustment          = -10
  }

  step_adjustment {
    metric_interval_lower_bound = 50
    metric_interval_upper_bound = 200
    scaling_adjustment          = -5
  }

  step_adjustment {
    metric_interval_lower_bound = 200
    metric_interval_upper_bound = 350
    scaling_adjustment          = 0
  }

  step_adjustment {
    metric_interval_lower_bound = 350
    metric_interval_upper_bound = 1000
    scaling_adjustment          = 8
  }

  step_adjustment {
    metric_interval_lower_bound = 1000
    metric_interval_upper_bound = 5000
    scaling_adjustment          = 20
  }

  step_adjustment {
    metric_interval_lower_bound = 5000
    metric_interval_upper_bound = 11200
    scaling_adjustment          = 40
  }

  step_adjustment {
    metric_interval_lower_bound = 11200
    metric_interval_upper_bound = 35000
    scaling_adjustment          = 60
  }

  step_adjustment {
    metric_interval_lower_bound = 35000
    metric_interval_upper_bound = null
    scaling_adjustment          = 80
  }
}
