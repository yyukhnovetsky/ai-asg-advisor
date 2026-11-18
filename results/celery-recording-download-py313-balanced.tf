  step_adjustments = [
    {
      scaling_adjustment          = -5
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 5
    },
    {
      scaling_adjustment          = -2
      metric_interval_lower_bound = 5
      metric_interval_upper_bound = 50
    },
    {
      scaling_adjustment          = 1
      metric_interval_lower_bound = 50
      metric_interval_upper_bound = 200
    },
    {
      scaling_adjustment          = 3
      metric_interval_lower_bound = 200
      metric_interval_upper_bound = 1000
    },
    {
      scaling_adjustment          = 8
      metric_interval_lower_bound = 1000
      metric_interval_upper_bound = 5000
    },
    {
      scaling_adjustment          = 15
      metric_interval_lower_bound = 5000
      metric_interval_upper_bound = null
    },
  ]
  estimated_instance_warmup = 120
