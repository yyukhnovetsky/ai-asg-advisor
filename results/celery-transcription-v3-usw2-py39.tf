  step_adjustments = [
    {
      scaling_adjustment          = -15
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 10
    },
    {
      scaling_adjustment          = -5
      metric_interval_lower_bound = 10
      metric_interval_upper_bound = 80
    },
    {
      scaling_adjustment          = 10
      metric_interval_lower_bound = 80
      metric_interval_upper_bound = 225
    },
    {
      scaling_adjustment          = 30
      metric_interval_lower_bound = 225
      metric_interval_upper_bound = 1500
    },
    {
      scaling_adjustment          = 60
      metric_interval_lower_bound = 1500
      metric_interval_upper_bound = 5000
    },
    {
      scaling_adjustment          = 120
      metric_interval_lower_bound = 5000
      metric_interval_upper_bound = null
    },
  ]
