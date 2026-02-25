"""Tests for ASG collector module."""

from __future__ import annotations

import unittest.mock as mock

import boto3
import pytest
from botocore.stub import Stubber

from ai_asg_advisor.asg_collector import (
    ASGConfig,
    ScalingPolicy,
    StepAdjustment,
    get_asg_config,
    get_scaling_policies,
    list_celery_asgs,
)


# -- Fixtures / helpers --

def _make_clients():
    session = boto3.Session(region_name="us-east-1")
    asg_client = session.client("autoscaling", region_name="us-east-1")
    ec2_client = session.client("ec2", region_name="us-east-1")
    return asg_client, ec2_client


def _asg_response_with_launch_template(asg_name="celery-email-sync-py39"):
    return {
        "AutoScalingGroups": [
            {
                "AutoScalingGroupName": asg_name,
                "AutoScalingGroupARN": f"arn:aws:autoscaling:us-east-1:123456789012:autoScalingGroup:xxx:autoScalingGroupName/{asg_name}",
                "MinSize": 2,
                "MaxSize": 20,
                "DesiredCapacity": 5,
                "DefaultCooldown": 300,
                "AvailabilityZones": ["us-east-1a"],
                "HealthCheckType": "EC2",
                "HealthCheckGracePeriod": 300,
                "CreatedTime": "2025-01-01T00:00:00Z",
                "LaunchTemplate": {
                    "LaunchTemplateId": "lt-0abcdef1234567890",
                    "LaunchTemplateName": "celery-email-sync-template",
                    "Version": "3",
                },
                "NewInstancesProtectedFromScaleIn": False,
                "ServiceLinkedRoleARN": "arn:aws:iam::123456789012:role/aws-service-role/autoscaling.amazonaws.com/AWSServiceRoleForAutoScaling",
                "VPCZoneIdentifier": "subnet-abc123",
            }
        ],
    }


def _launch_template_versions_response():
    return {
        "LaunchTemplateVersions": [
            {
                "LaunchTemplateId": "lt-0abcdef1234567890",
                "LaunchTemplateName": "celery-email-sync-template",
                "VersionNumber": 3,
                "CreateTime": "2025-01-01T00:00:00Z",
                "CreatedBy": "arn:aws:iam::123456789012:user/admin",
                "DefaultVersion": False,
                "LaunchTemplateData": {
                    "InstanceType": "m5.xlarge",
                    "ImageId": "ami-0abc123",
                },
            }
        ],
    }


class TestGetASGConfig:
    def test_with_launch_template(self):
        asg_client, ec2_client = _make_clients()

        with Stubber(asg_client) as asg_stub, Stubber(ec2_client) as ec2_stub:
            asg_stub.add_response(
                "describe_auto_scaling_groups",
                _asg_response_with_launch_template(),
                {"AutoScalingGroupNames": ["celery-email-sync-py39"]},
            )
            ec2_stub.add_response(
                "describe_launch_template_versions",
                _launch_template_versions_response(),
                {"LaunchTemplateId": "lt-0abcdef1234567890", "Versions": ["3"]},
            )

            config = get_asg_config(asg_client, ec2_client, "celery-email-sync-py39")

            assert config.name == "celery-email-sync-py39"
            assert config.min_size == 2
            assert config.max_size == 20
            assert config.desired_capacity == 5
            assert config.instance_types == ["m5.xlarge"]
            assert config.launch_template_id == "lt-0abcdef1234567890"

    def test_with_mixed_instances_policy(self):
        asg_client, ec2_client = _make_clients()

        mip_response = {
            "AutoScalingGroups": [
                {
                    "AutoScalingGroupName": "celery-worker",
                    "AutoScalingGroupARN": "arn:aws:autoscaling:us-east-1:123456789012:autoScalingGroup:xxx:autoScalingGroupName/celery-worker",
                    "MinSize": 1,
                    "MaxSize": 10,
                    "DesiredCapacity": 3,
                    "DefaultCooldown": 300,
                    "AvailabilityZones": ["us-east-1a"],
                    "HealthCheckType": "EC2",
                    "HealthCheckGracePeriod": 300,
                    "CreatedTime": "2025-01-01T00:00:00Z",
                    "MixedInstancesPolicy": {
                        "LaunchTemplate": {
                            "LaunchTemplateSpecification": {
                                "LaunchTemplateId": "lt-mixed123",
                                "LaunchTemplateName": "celery-mixed",
                                "Version": "$Latest",
                            },
                            "Overrides": [
                                {"InstanceType": "m5.xlarge", "WeightedCapacity": "1"},
                                {"InstanceType": "m5a.xlarge", "WeightedCapacity": "1"},
                                {"InstanceType": "m6g.xlarge", "WeightedCapacity": "1"},
                            ],
                        },
                        "InstancesDistribution": {
                            "OnDemandPercentageAboveBaseCapacity": 50,
                            "SpotAllocationStrategy": "capacity-optimized",
                        },
                    },
                    "NewInstancesProtectedFromScaleIn": False,
                    "ServiceLinkedRoleARN": "arn:aws:iam::123456789012:role/aws-service-role/autoscaling.amazonaws.com/AWSServiceRoleForAutoScaling",
                    "VPCZoneIdentifier": "subnet-abc123",
                }
            ],
        }

        with Stubber(asg_client) as asg_stub, Stubber(ec2_client) as ec2_stub:
            asg_stub.add_response(
                "describe_auto_scaling_groups",
                mip_response,
                {"AutoScalingGroupNames": ["celery-worker"]},
            )

            config = get_asg_config(asg_client, ec2_client, "celery-worker")

            assert config.instance_types == ["m5.xlarge", "m5a.xlarge", "m6g.xlarge"]
            assert config.launch_template_id == "lt-mixed123"

    def test_with_launch_configuration(self):
        asg_client, ec2_client = _make_clients()

        lc_response = {
            "AutoScalingGroups": [
                {
                    "AutoScalingGroupName": "celery-legacy",
                    "AutoScalingGroupARN": "arn:aws:autoscaling:us-east-1:123456789012:autoScalingGroup:xxx:autoScalingGroupName/celery-legacy",
                    "MinSize": 1,
                    "MaxSize": 5,
                    "DesiredCapacity": 2,
                    "DefaultCooldown": 300,
                    "AvailabilityZones": ["us-east-1a"],
                    "HealthCheckType": "EC2",
                    "HealthCheckGracePeriod": 300,
                    "CreatedTime": "2025-01-01T00:00:00Z",
                    "LaunchConfigurationName": "celery-legacy-lc",
                    "NewInstancesProtectedFromScaleIn": False,
                    "ServiceLinkedRoleARN": "arn:aws:iam::123456789012:role/aws-service-role/autoscaling.amazonaws.com/AWSServiceRoleForAutoScaling",
                    "VPCZoneIdentifier": "subnet-abc123",
                }
            ],
        }

        with Stubber(asg_client) as asg_stub, Stubber(ec2_client) as ec2_stub:
            asg_stub.add_response(
                "describe_auto_scaling_groups",
                lc_response,
                {"AutoScalingGroupNames": ["celery-legacy"]},
            )
            asg_stub.add_response(
                "describe_launch_configurations",
                {
                    "LaunchConfigurations": [
                        {
                            "LaunchConfigurationName": "celery-legacy-lc",
                            "LaunchConfigurationARN": "arn:...",
                            "ImageId": "ami-0abc123",
                            "InstanceType": "t3.large",
                            "CreatedTime": "2025-01-01T00:00:00Z",
                        }
                    ]
                },
                {"LaunchConfigurationNames": ["celery-legacy-lc"]},
            )

            config = get_asg_config(asg_client, ec2_client, "celery-legacy")

            assert config.instance_types == ["t3.large"]
            assert config.launch_configuration_name == "celery-legacy-lc"

    def test_asg_not_found(self):
        asg_client, ec2_client = _make_clients()

        with Stubber(asg_client) as asg_stub, Stubber(ec2_client) as ec2_stub:
            asg_stub.add_response(
                "describe_auto_scaling_groups",
                {"AutoScalingGroups": []},
                {"AutoScalingGroupNames": ["nonexistent"]},
            )

            with pytest.raises(ValueError, match="not found"):
                get_asg_config(asg_client, ec2_client, "nonexistent")


class TestGetScalingPolicies:
    def test_returns_step_policies(self):
        asg_client, _ = _make_clients()

        with Stubber(asg_client) as asg_stub:
            asg_stub.add_response(
                "describe_policies",
                {
                    "ScalingPolicies": [
                        {
                            "AutoScalingGroupName": "celery-email-sync-py39",
                            "PolicyName": "scale-up",
                            "PolicyARN": "arn:...",
                            "PolicyType": "StepScaling",
                            "AdjustmentType": "ChangeInCapacity",
                            "Cooldown": 120,
                            "StepAdjustments": [
                                {
                                    "MetricIntervalLowerBound": 0.0,
                                    "MetricIntervalUpperBound": 200.0,
                                    "ScalingAdjustment": 1,
                                },
                                {
                                    "MetricIntervalLowerBound": 200.0,
                                    "ScalingAdjustment": 3,
                                },
                            ],
                            "MetricAggregationType": "Average",
                        },
                        {
                            "AutoScalingGroupName": "celery-email-sync-py39",
                            "PolicyName": "scale-down",
                            "PolicyARN": "arn:...",
                            "PolicyType": "StepScaling",
                            "AdjustmentType": "ChangeInCapacity",
                            "Cooldown": 300,
                            "StepAdjustments": [
                                {
                                    "MetricIntervalUpperBound": 0.0,
                                    "ScalingAdjustment": -1,
                                },
                            ],
                            "MetricAggregationType": "Average",
                        },
                    ],
                },
                {"AutoScalingGroupName": "celery-email-sync-py39"},
            )

            policies = get_scaling_policies(asg_client, "celery-email-sync-py39")

            assert len(policies) == 2

            up = policies[0]
            assert up.policy_name == "scale-up"
            assert up.policy_type == "StepScaling"
            assert up.adjustment_type == "ChangeInCapacity"
            assert len(up.step_adjustments) == 2
            assert up.step_adjustments[0].scaling_adjustment == 1
            assert up.step_adjustments[1].metric_interval_upper_bound is None

            down = policies[1]
            assert down.policy_name == "scale-down"
            assert len(down.step_adjustments) == 1
            assert down.step_adjustments[0].scaling_adjustment == -1


class TestListCeleryASGs:
    def test_filters_celery_prefix(self):
        asg_client, _ = _make_clients()

        with Stubber(asg_client) as asg_stub:
            asg_stub.add_response(
                "describe_auto_scaling_groups",
                {
                    "AutoScalingGroups": [
                        _minimal_asg("celery-email-sync-py39"),
                        _minimal_asg("celery-worker-py39"),
                        _minimal_asg("web-server-prod"),
                        _minimal_asg("celery-beat"),
                    ],
                },
            )

            names = list_celery_asgs(asg_client)

            assert names == ["celery-beat", "celery-email-sync-py39", "celery-worker-py39"]
            assert "web-server-prod" not in names


def _minimal_asg(name: str) -> dict:
    """Minimal ASG dict for list tests."""
    return {
        "AutoScalingGroupName": name,
        "AutoScalingGroupARN": f"arn:.../{name}",
        "MinSize": 1,
        "MaxSize": 10,
        "DesiredCapacity": 2,
        "DefaultCooldown": 300,
        "AvailabilityZones": ["us-east-1a"],
        "HealthCheckType": "EC2",
        "HealthCheckGracePeriod": 300,
        "CreatedTime": "2025-01-01T00:00:00Z",
        "NewInstancesProtectedFromScaleIn": False,
        "ServiceLinkedRoleARN": "arn:...",
        "VPCZoneIdentifier": "subnet-abc123",
    }
