  step_adjustments = [
    {
      scaling_adjustment          = -3
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 10
    },
    {
      scaling_adjustment          = -1
      metric_interval_lower_bound = 10
      metric_interval_upper_bound = 500
    },
    {
      scaling_adjustment          = 2
      metric_interval_lower_bound = 500
      metric_interval_upper_bound = 10000
    },
    {
      scaling_adjustment          = 4
      metric_interval_lower_bound = 10000
      metric_interval_upper_bound = 100000
    },
    {
      scaling_adjustment          = 8
      metric_interval_lower_bound = 100000
      metric_interval_upper_bound = 1000000
    },
    {
      scaling_adjustment          = 12
      metric_interval_lower_bound = 1000000
      metric_interval_upper_bound = null
    },
  ]
