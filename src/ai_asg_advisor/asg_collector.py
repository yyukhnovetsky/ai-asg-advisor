"""ASG collector — Auto Scaling Group configuration, capacity history, and policies."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ASGConfig:
    """Current ASG configuration snapshot."""
    name: str
    min_size: int
    max_size: int
    desired_capacity: int
    launch_template_id: str | None = None
    launch_template_name: str | None = None
    launch_template_version: str | None = None
    launch_configuration_name: str | None = None
    instance_types: list[str] = field(default_factory=list)


@dataclass
class StepAdjustment:
    """A single step adjustment from an existing scaling policy."""
    metric_interval_lower_bound: float | None = None
    metric_interval_upper_bound: float | None = None
    scaling_adjustment: int = 0


@dataclass
class ScalingPolicy:
    """An existing ASG scaling policy."""
    policy_name: str
    policy_type: str
    adjustment_type: str | None = None
    cooldown: int | None = None
    estimated_instance_warmup: int | None = None
    step_adjustments: list[StepAdjustment] = field(default_factory=list)
    metric_aggregation_type: str | None = None


def get_asg_config(asg_client, ec2_client, asg_name: str) -> ASGConfig:
    """Fetch ASG configuration including instance type resolution from launch template."""
    response = asg_client.describe_auto_scaling_groups(
        AutoScalingGroupNames=[asg_name]
    )

    if not response["AutoScalingGroups"]:
        raise ValueError(f"ASG '{asg_name}' not found")

    asg = response["AutoScalingGroups"][0]

    config = ASGConfig(
        name=asg["AutoScalingGroupName"],
        min_size=asg["MinSize"],
        max_size=asg["MaxSize"],
        desired_capacity=asg["DesiredCapacity"],
    )

    # Resolve instance types from the three possible sources
    instance_types = _resolve_instance_types(asg_client, ec2_client, asg)
    config.instance_types = instance_types

    # Store launch template info if present
    if lt := asg.get("LaunchTemplate"):
        config.launch_template_id = lt.get("LaunchTemplateId")
        config.launch_template_name = lt.get("LaunchTemplateName")
        config.launch_template_version = lt.get("Version")
    elif mip := asg.get("MixedInstancesPolicy"):
        lt_spec = mip["LaunchTemplate"]["LaunchTemplateSpecification"]
        config.launch_template_id = lt_spec.get("LaunchTemplateId")
        config.launch_template_name = lt_spec.get("LaunchTemplateName")
        config.launch_template_version = lt_spec.get("Version")
    elif lc_name := asg.get("LaunchConfigurationName"):
        config.launch_configuration_name = lc_name

    logger.info(
        "ASG '%s': min=%d, max=%d, desired=%d, instance_types=%s",
        config.name, config.min_size, config.max_size,
        config.desired_capacity, config.instance_types,
    )

    return config


def get_scaling_policies(asg_client, asg_name: str) -> list[ScalingPolicy]:
    """Fetch all scaling policies for an ASG."""
    policies = []
    paginator = asg_client.get_paginator("describe_policies")

    for page in paginator.paginate(AutoScalingGroupName=asg_name):
        for p in page["ScalingPolicies"]:
            policy = ScalingPolicy(
                policy_name=p["PolicyName"],
                policy_type=p["PolicyType"],
                adjustment_type=p.get("AdjustmentType"),
                cooldown=p.get("Cooldown"),
                estimated_instance_warmup=p.get("EstimatedInstanceWarmup"),
                metric_aggregation_type=p.get("MetricAggregationType"),
            )

            for sa in p.get("StepAdjustments", []):
                policy.step_adjustments.append(
                    StepAdjustment(
                        metric_interval_lower_bound=sa.get("MetricIntervalLowerBound"),
                        metric_interval_upper_bound=sa.get("MetricIntervalUpperBound"),
                        scaling_adjustment=sa["ScalingAdjustment"],
                    )
                )

            policies.append(policy)

    logger.info("Found %d scaling policies for ASG '%s'", len(policies), asg_name)
    return policies


def list_celery_asgs(asg_client) -> list[str]:
    """List all ASG names matching the 'celery-*' pattern."""
    names = []
    paginator = asg_client.get_paginator("describe_auto_scaling_groups")

    for page in paginator.paginate():
        for asg in page["AutoScalingGroups"]:
            if asg["AutoScalingGroupName"].startswith("celery-"):
                names.append(asg["AutoScalingGroupName"])

    logger.info("Found %d celery-* ASGs", len(names))
    return sorted(names)


def _resolve_instance_types(asg_client, ec2_client, asg: dict) -> list[str]:
    """Resolve instance types from an ASG definition.

    Handles three code paths:
    1. MixedInstancesPolicy with Overrides
    2. Direct LaunchTemplate reference
    3. Legacy LaunchConfiguration
    """
    # Case 1: MixedInstancesPolicy
    if mip := asg.get("MixedInstancesPolicy"):
        overrides = mip["LaunchTemplate"].get("Overrides", [])
        types_from_overrides = [
            o["InstanceType"] for o in overrides if "InstanceType" in o
        ]
        if types_from_overrides:
            return types_from_overrides

        # No overrides — fall through to the launch template
        lt_spec = mip["LaunchTemplate"]["LaunchTemplateSpecification"]
        if it := _resolve_from_launch_template(ec2_client, lt_spec):
            return [it]

    # Case 2: Direct LaunchTemplate
    elif lt := asg.get("LaunchTemplate"):
        if it := _resolve_from_launch_template(ec2_client, lt):
            return [it]

    # Case 3: Legacy LaunchConfiguration
    elif lc_name := asg.get("LaunchConfigurationName"):
        response = asg_client.describe_launch_configurations(
            LaunchConfigurationNames=[lc_name]
        )
        if response["LaunchConfigurations"]:
            return [response["LaunchConfigurations"][0]["InstanceType"]]

    logger.warning("Could not resolve instance types for ASG '%s'", asg.get("AutoScalingGroupName"))
    return []


def _resolve_from_launch_template(ec2_client, lt_spec: dict) -> str | None:
    """Resolve instance type from a launch template specification."""
    kwargs = {}
    if lt_id := lt_spec.get("LaunchTemplateId"):
        kwargs["LaunchTemplateId"] = lt_id
    elif lt_name := lt_spec.get("LaunchTemplateName"):
        kwargs["LaunchTemplateName"] = lt_name
    else:
        return None

    version = lt_spec.get("Version", "$Default")
    kwargs["Versions"] = [version]

    response = ec2_client.describe_launch_template_versions(**kwargs)
    versions = response.get("LaunchTemplateVersions", [])
    if versions:
        return versions[0]["LaunchTemplateData"].get("InstanceType")
    return None
