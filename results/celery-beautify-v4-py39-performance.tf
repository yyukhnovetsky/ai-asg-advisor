  step_adjustments = [
    {
      scaling_adjustment          = -50
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 50
    },
    {
      scaling_adjustment          = -10
      metric_interval_lower_bound = 50
      metric_interval_upper_bound = 150
    },
    {
      scaling_adjustment          = 5
      metric_interval_lower_bound = 150
      metric_interval_upper_bound = 300
    },
    {
      scaling_adjustment          = 15
      metric_interval_lower_bound = 300
      metric_interval_upper_bound = 700
    },
    {
      scaling_adjustment          = 25
      metric_interval_lower_bound = 700
      metric_interval_upper_bound = 2000
    },
    {
      scaling_adjustment          = 40
      metric_interval_lower_bound = 2000
      metric_interval_upper_bound = 5000
    },
    {
      scaling_adjustment          = 60
      metric_interval_lower_bound = 5000
      metric_interval_upper_bound = 15000
    },
    {
      scaling_adjustment          = 80
      metric_interval_lower_bound = 15000
      metric_interval_upper_bound = 50000
    },
    {
      scaling_adjustment          = 100
      metric_interval_lower_bound = 50000
      metric_interval_upper_bound = null
    },
  ]
