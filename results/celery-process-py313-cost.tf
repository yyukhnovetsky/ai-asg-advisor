  step_adjustments = [
    {
      scaling_adjustment          = -4
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 5
    },
    {
      scaling_adjustment          = 3
      metric_interval_lower_bound = 5
      metric_interval_upper_bound = 100
    },
    {
      scaling_adjustment          = 8
      metric_interval_lower_bound = 100
      metric_interval_upper_bound = 500
    },
    {
      scaling_adjustment          = 15
      metric_interval_lower_bound = 500
      metric_interval_upper_bound = 5000
    },
    {
      scaling_adjustment          = 25
      metric_interval_lower_bound = 5000
      metric_interval_upper_bound = null
    },
  ]
  estimated_instance_warmup = 180
