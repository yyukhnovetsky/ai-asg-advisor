  step_adjustments = [
    {
      scaling_adjustment          = -15
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 20
    },
    {
      scaling_adjustment          = -5
      metric_interval_lower_bound = 20
      metric_interval_upper_bound = 50
    },
    {
      scaling_adjustment          = 5
      metric_interval_lower_bound = 50
      metric_interval_upper_bound = 80
    },
    {
      scaling_adjustment          = 15
      metric_interval_lower_bound = 80
      metric_interval_upper_bound = 200
    },
    {
      scaling_adjustment          = 30
      metric_interval_lower_bound = 200
      metric_interval_upper_bound = 500
    },
    {
      scaling_adjustment          = 60
      metric_interval_lower_bound = 500
      metric_interval_upper_bound = 1500
    },
    {
      scaling_adjustment          = 100
      metric_interval_lower_bound = 1500
      metric_interval_upper_bound = 5000
    },
    {
      scaling_adjustment          = 200
      metric_interval_lower_bound = 5000
      metric_interval_upper_bound = 15000
    },
    {
      scaling_adjustment          = 350
      metric_interval_lower_bound = 15000
      metric_interval_upper_bound = null
    },
  ]
