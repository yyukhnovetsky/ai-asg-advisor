  step_adjustments = [
    {
      scaling_adjustment          = -20
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 10
    },
    {
      scaling_adjustment          = -10
      metric_interval_lower_bound = 10
      metric_interval_upper_bound = 50
    },
    {
      scaling_adjustment          = -3
      metric_interval_lower_bound = 50
      metric_interval_upper_bound = 150
    },
    {
      scaling_adjustment          = 5
      metric_interval_lower_bound = 150
      metric_interval_upper_bound = 300
    },
    {
      scaling_adjustment          = 20
      metric_interval_lower_bound = 300
      metric_interval_upper_bound = 800
    },
    {
      scaling_adjustment          = 40
      metric_interval_lower_bound = 800
      metric_interval_upper_bound = 1500
    },
    {
      scaling_adjustment          = 80
      metric_interval_lower_bound = 1500
      metric_interval_upper_bound = null
    },
  ]
  estimated_instance_warmup = 120
