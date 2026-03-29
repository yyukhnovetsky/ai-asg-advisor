  step_adjustments = [
    {
      scaling_adjustment          = -20
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 50
    },
    {
      scaling_adjustment          = -5
      metric_interval_lower_bound = 50
      metric_interval_upper_bound = 100
    },
    {
      scaling_adjustment          = 10
      metric_interval_lower_bound = 100
      metric_interval_upper_bound = 500
    },
    {
      scaling_adjustment          = 25
      metric_interval_lower_bound = 500
      metric_interval_upper_bound = 1500
    },
    {
      scaling_adjustment          = 60
      metric_interval_lower_bound = 1500
      metric_interval_upper_bound = 7000
    },
    {
      scaling_adjustment          = 120
      metric_interval_lower_bound = 7000
      metric_interval_upper_bound = 18000
    },
    {
      scaling_adjustment          = 200
      metric_interval_lower_bound = 18000
      metric_interval_upper_bound = null
    },
  ]
