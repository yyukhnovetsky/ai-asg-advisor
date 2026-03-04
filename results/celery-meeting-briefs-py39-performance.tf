  step_adjustments = [
    {
      scaling_adjustment          = -3
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 10
    },
    {
      scaling_adjustment          = -1
      metric_interval_lower_bound = 10
      metric_interval_upper_bound = 50
    },
    {
      scaling_adjustment          = 2
      metric_interval_lower_bound = 50
      metric_interval_upper_bound = 150
    },
    {
      scaling_adjustment          = 5
      metric_interval_lower_bound = 150
      metric_interval_upper_bound = 400
    },
    {
      scaling_adjustment          = 8
      metric_interval_lower_bound = 400
      metric_interval_upper_bound = 800
    },
    {
      scaling_adjustment          = 12
      metric_interval_lower_bound = 800
      metric_interval_upper_bound = 5000
    },
    {
      scaling_adjustment          = 18
      metric_interval_lower_bound = 5000
      metric_interval_upper_bound = 50000
    },
    {
      scaling_adjustment          = 25
      metric_interval_lower_bound = 50000
      metric_interval_upper_bound = null
    },
  ]
