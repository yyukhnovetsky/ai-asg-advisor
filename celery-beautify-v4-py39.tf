  step_adjustments = [
    {
      scaling_adjustment          = -10
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 50
    },
    {
      scaling_adjustment          = -3
      metric_interval_lower_bound = 50
      metric_interval_upper_bound = 300
    },
    {
      scaling_adjustment          = 5
      metric_interval_lower_bound = 300
      metric_interval_upper_bound = 1000
    },
    {
      scaling_adjustment          = 15
      metric_interval_lower_bound = 1000
      metric_interval_upper_bound = 5000
    },
    {
      scaling_adjustment          = 30
      metric_interval_lower_bound = 5000
      metric_interval_upper_bound = 12000
    },
    {
      scaling_adjustment          = 50
      metric_interval_lower_bound = 12000
      metric_interval_upper_bound = 40000
    },
    {
      scaling_adjustment          = 70
      metric_interval_lower_bound = 40000
      metric_interval_upper_bound = null
    },
  ]
