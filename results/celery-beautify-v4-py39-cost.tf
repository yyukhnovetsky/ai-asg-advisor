  step_adjustments = [
    {
      scaling_adjustment          = -40
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 50
    },
    {
      scaling_adjustment          = -15
      metric_interval_lower_bound = 50
      metric_interval_upper_bound = 200
    },
    {
      scaling_adjustment          = -5
      metric_interval_lower_bound = 200
      metric_interval_upper_bound = 500
    },
    {
      scaling_adjustment          = 5
      metric_interval_lower_bound = 500
      metric_interval_upper_bound = 1000
    },
    {
      scaling_adjustment          = 15
      metric_interval_lower_bound = 1000
      metric_interval_upper_bound = 3000
    },
    {
      scaling_adjustment          = 30
      metric_interval_lower_bound = 3000
      metric_interval_upper_bound = 6000
    },
    {
      scaling_adjustment          = 50
      metric_interval_lower_bound = 6000
      metric_interval_upper_bound = 15000
    },
    {
      scaling_adjustment          = 70
      metric_interval_lower_bound = 15000
      metric_interval_upper_bound = 40000
    },
    {
      scaling_adjustment          = 90
      metric_interval_lower_bound = 40000
      metric_interval_upper_bound = null
    },
  ]
