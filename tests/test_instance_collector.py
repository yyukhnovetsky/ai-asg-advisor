"""Tests for EC2 instance type and pricing collector."""

from __future__ import annotations

import json
import unittest.mock as mock

import boto3
import pytest
from botocore.stub import Stubber

from ai_asg_advisor.instance_collector import (
    InstanceRecommendations,
    InstanceTypeInfo,
    _is_relevant_alternative,
    _parse_instance_type,
    enrich_with_pricing,
    find_alternative_types,
    get_instance_recommendations,
    get_instance_type_info,
)


def _make_ec2_client():
    session = boto3.Session(region_name="us-east-1")
    return session.client("ec2", region_name="us-east-1")


def _instance_type_response(instance_type="m5.xlarge", vcpu=4, memory_mib=16384, arch="x86_64"):
    return {
        "InstanceTypes": [
            {
                "InstanceType": instance_type,
                "CurrentGeneration": True,
                "BurstablePerformanceSupported": False,
                "VCpuInfo": {"DefaultVCpus": vcpu, "DefaultCores": vcpu // 2, "DefaultThreadsPerCore": 2},
                "MemoryInfo": {"SizeInMiB": memory_mib},
                "ProcessorInfo": {"SupportedArchitectures": [arch], "SustainedClockSpeedInGhz": 3.1},
                "NetworkInfo": {"NetworkPerformance": "Up to 10 Gigabit"},
            }
        ],
    }


class TestGetInstanceTypeInfo:
    def test_returns_info(self):
        ec2 = _make_ec2_client()

        with Stubber(ec2) as stub:
            stub.add_response(
                "describe_instance_types",
                _instance_type_response("m5.xlarge", 4, 16384),
                {"InstanceTypes": ["m5.xlarge"]},
            )

            info = get_instance_type_info(ec2, "m5.xlarge")

            assert info.instance_type == "m5.xlarge"
            assert info.vcpu == 4
            assert info.memory_gib == 16.0
            assert info.architecture == "x86_64"
            assert info.burstable is False

    def test_not_found(self):
        ec2 = _make_ec2_client()

        with Stubber(ec2) as stub:
            stub.add_response(
                "describe_instance_types",
                {"InstanceTypes": []},
                {"InstanceTypes": ["nonexistent.type"]},
            )

            with pytest.raises(ValueError, match="not found"):
                get_instance_type_info(ec2, "nonexistent.type")


class TestParseInstanceType:
    def test_basic_parse(self):
        raw = {
            "InstanceType": "m6g.xlarge",
            "CurrentGeneration": True,
            "BurstablePerformanceSupported": False,
            "VCpuInfo": {"DefaultVCpus": 4},
            "MemoryInfo": {"SizeInMiB": 16384},
            "ProcessorInfo": {"SupportedArchitectures": ["arm64"]},
            "NetworkInfo": {"NetworkPerformance": "Up to 10 Gigabit"},
        }
        info = _parse_instance_type(raw)
        assert info.instance_type == "m6g.xlarge"
        assert info.architecture == "arm64"
        assert info.memory_gib == 16.0

    def test_burstable(self):
        raw = {
            "InstanceType": "t3.xlarge",
            "CurrentGeneration": True,
            "BurstablePerformanceSupported": True,
            "VCpuInfo": {"DefaultVCpus": 4},
            "MemoryInfo": {"SizeInMiB": 16384},
            "ProcessorInfo": {"SupportedArchitectures": ["x86_64"]},
            "NetworkInfo": {"NetworkPerformance": "Up to 5 Gigabit"},
        }
        info = _parse_instance_type(raw)
        assert info.burstable is True


class TestIsRelevantAlternative:
    def test_same_family_different_gen(self):
        assert _is_relevant_alternative("m5.xlarge", "m6i.xlarge") is True
        assert _is_relevant_alternative("m5.xlarge", "m7i.xlarge") is True

    def test_graviton_variant(self):
        assert _is_relevant_alternative("m5.xlarge", "m6g.xlarge") is True

    def test_amd_variant(self):
        assert _is_relevant_alternative("m5.xlarge", "m5a.xlarge") is True

    def test_burstable_from_general(self):
        assert _is_relevant_alternative("m5.xlarge", "t3.xlarge") is True

    def test_unrelated_family(self):
        assert _is_relevant_alternative("m5.xlarge", "r5.xlarge") is False

    def test_compute_from_general(self):
        assert _is_relevant_alternative("m5.xlarge", "c6i.xlarge") is True


class TestEnrichWithPricing:
    def test_adds_pricing(self):
        instances = [
            InstanceTypeInfo(instance_type="m5.xlarge", vcpu=4, memory_gib=16.0),
            InstanceTypeInfo(instance_type="m6g.xlarge", vcpu=4, memory_gib=16.0),
        ]

        prices = {"m5.xlarge": 0.192, "m6g.xlarge": 0.154}

        with mock.patch(
            "ai_asg_advisor.instance_collector._get_ondemand_price",
            side_effect=lambda _client, it, _loc: prices.get(it),
        ):
            result = enrich_with_pricing(mock.MagicMock(), instances, "us-east-1")

            assert result[0].price_per_hour == 0.192
            assert result[0].monthly_cost_estimate == 140.16
            assert result[1].price_per_hour == 0.154

    def test_unknown_region_skips(self):
        instances = [InstanceTypeInfo(instance_type="m5.xlarge", vcpu=4, memory_gib=16.0)]

        result = enrich_with_pricing(mock.MagicMock(), instances, "mars-north-1")
        assert result[0].price_per_hour is None


class TestGetInstanceRecommendations:
    def test_full_pipeline(self):
        ec2 = _make_ec2_client()

        with Stubber(ec2) as ec2_stub:
            # get_instance_type_info for current type
            ec2_stub.add_response(
                "describe_instance_types",
                _instance_type_response("m5.xlarge", 4, 16384),
                {"InstanceTypes": ["m5.xlarge"]},
            )
            # find_alternative_types: first call to get current specs
            ec2_stub.add_response(
                "describe_instance_types",
                _instance_type_response("m5.xlarge", 4, 16384),
                {"InstanceTypes": ["m5.xlarge"]},
            )
            # find_alternative_types: paginated search
            ec2_stub.add_response(
                "describe_instance_types",
                {
                    "InstanceTypes": [
                        {
                            "InstanceType": "m6g.xlarge",
                            "CurrentGeneration": True,
                            "BurstablePerformanceSupported": False,
                            "VCpuInfo": {"DefaultVCpus": 4},
                            "MemoryInfo": {"SizeInMiB": 16384},
                            "ProcessorInfo": {"SupportedArchitectures": ["arm64"]},
                            "NetworkInfo": {"NetworkPerformance": "Up to 10 Gigabit"},
                        },
                    ],
                },
            )

            prices = {"m5.xlarge": 0.192, "m6g.xlarge": 0.154}
            with mock.patch(
                "ai_asg_advisor.instance_collector._get_ondemand_price",
                side_effect=lambda _client, it, _loc: prices.get(it),
            ):
                recs = get_instance_recommendations(ec2, mock.MagicMock(), "m5.xlarge", "us-east-1")

            assert isinstance(recs, InstanceRecommendations)
            assert recs.current.instance_type == "m5.xlarge"
            assert recs.current.price_per_hour == 0.192
            assert len(recs.alternatives) == 1
            assert recs.alternatives[0].instance_type == "m6g.xlarge"
            assert recs.alternatives[0].price_per_hour == 0.154
