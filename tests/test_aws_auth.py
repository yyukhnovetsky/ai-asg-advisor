"""Tests for AWS authentication module."""

from __future__ import annotations

import unittest.mock as mock

import boto3
from botocore.stub import Stubber

from ai_asg_advisor.aws_auth import (
    create_session,
    get_client,
    get_pricing_client,
    verify_credentials,
)
from ai_asg_advisor.config import AWSConfig


class TestCreateSessionProfile:
    """Test session creation via named profile."""

    def test_profile_session(self):
        config = AWSConfig(profile="test-profile", region="us-west-2")
        with mock.patch("boto3.Session") as mock_session_cls:
            mock_session = mock.MagicMock()
            mock_session.profile_name = "test-profile"
            mock_session.region_name = "us-west-2"
            mock_session_cls.return_value = mock_session

            session = create_session(config)
            mock_session_cls.assert_called_once_with(
                profile_name="test-profile",
                region_name="us-west-2",
            )
            assert session.profile_name == "test-profile"
            assert session.region_name == "us-west-2"

    def test_default_credential_chain(self):
        config = AWSConfig(region="eu-west-1")
        with mock.patch("boto3.Session") as mock_session_cls:
            mock_session = mock.MagicMock()
            mock_session.region_name = "eu-west-1"
            mock_session_cls.return_value = mock_session

            session = create_session(config)
            mock_session_cls.assert_called_once_with(region_name="eu-west-1")
            assert session.region_name == "eu-west-1"


class TestCreateSessionRole:
    """Test session creation via IAM role assumption."""

    def test_assume_role_with_full_arn(self):
        config = AWSConfig(role_arn="arn:aws:iam::123456789012:role/test-role", region="us-east-1")

        # We need to stub the STS call. Create a base session and stub it.
        base_session = boto3.Session(region_name="us-east-1")
        sts = base_session.client("sts", region_name="us-east-1")

        with Stubber(sts) as stubber:
            stubber.add_response(
                "assume_role",
                {
                    "Credentials": {
                        "AccessKeyId": "AKIAIOSFODNN7EXAMPLE",
                        "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                        "SessionToken": "FwoGZX...",
                        "Expiration": "2025-03-01T00:00:00Z",
                    },
                    "AssumedRoleUser": {
                        "AssumedRoleId": "AROA3XFRBF23:ai-asg-advisor",
                        "Arn": "arn:aws:sts::123456789012:assumed-role/test-role/ai-asg-advisor",
                    },
                },
                expected_params={
                    "RoleArn": "arn:aws:iam::123456789012:role/test-role",
                    "RoleSessionName": "ai-asg-advisor",
                    "DurationSeconds": 3600,
                },
            )

            with mock.patch("boto3.client", return_value=sts):
                session = create_session(config)
                assert session.region_name == "us-east-1"
                # Session should have temp credentials (not None)
                creds = session.get_credentials()
                assert creds is not None

    def test_assume_role_with_short_name(self):
        """When role_arn is a short name, it should resolve account ID first."""
        config = AWSConfig(role_arn="ai-advisor-role", region="us-east-1")

        base_session = boto3.Session(region_name="us-east-1")
        sts = base_session.client("sts", region_name="us-east-1")

        with Stubber(sts) as stubber:
            # First call: get_caller_identity to resolve account ID
            stubber.add_response(
                "get_caller_identity",
                {
                    "UserId": "AIDACKCEVSQ6C2EXAMPLE",
                    "Account": "123456789012",
                    "Arn": "arn:aws:iam::123456789012:user/testuser",
                },
            )
            # Second call: assume_role with the built ARN
            stubber.add_response(
                "assume_role",
                {
                    "Credentials": {
                        "AccessKeyId": "AKIAIOSFODNN7EXAMPLE",
                        "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                        "SessionToken": "FwoGZX...",
                        "Expiration": "2025-03-01T00:00:00Z",
                    },
                    "AssumedRoleUser": {
                        "AssumedRoleId": "AROA3XFRBF23:ai-asg-advisor",
                        "Arn": "arn:aws:sts::123456789012:assumed-role/ai-advisor-role/ai-asg-advisor",
                    },
                },
                expected_params={
                    "RoleArn": "arn:aws:iam::123456789012:role/ai-advisor-role",
                    "RoleSessionName": "ai-asg-advisor",
                    "DurationSeconds": 3600,
                },
            )

            with mock.patch("boto3.client", return_value=sts):
                session = create_session(config)
                assert session.region_name == "us-east-1"


class TestGetClient:
    """Test client factory helpers."""

    def test_get_client_returns_client(self):
        session = boto3.Session(region_name="us-east-1")
        client = get_client(session, "autoscaling")
        assert client.meta.service_model.service_name == "autoscaling"

    def test_get_pricing_client_uses_us_east_1(self):
        session = boto3.Session(region_name="eu-west-1")
        client = get_pricing_client(session)
        assert client.meta.service_model.service_name == "pricing"
        assert client.meta.region_name == "us-east-1"


class TestVerifyCredentials:
    """Test credential verification."""

    def test_verify_returns_identity(self):
        session = boto3.Session(region_name="us-east-1")
        sts = session.client("sts", region_name="us-east-1")

        with Stubber(sts) as stubber:
            stubber.add_response(
                "get_caller_identity",
                {
                    "UserId": "AIDACKCEVSQ6C2EXAMPLE",
                    "Account": "123456789012",
                    "Arn": "arn:aws:iam::123456789012:user/testuser",
                },
            )

            with mock.patch.object(session, "client", return_value=sts):
                identity = verify_credentials(session)
                assert identity["Account"] == "123456789012"
                assert "Arn" in identity


class TestResolutionOrder:
    """Test that role takes precedence over profile."""

    def test_role_takes_precedence_over_profile(self):
        config = AWSConfig(
            profile="should-be-ignored",
            role_arn="arn:aws:iam::123456789012:role/test-role",
            region="us-east-1",
        )

        base_session = boto3.Session(region_name="us-east-1")
        sts = base_session.client("sts", region_name="us-east-1")

        with Stubber(sts) as stubber:
            stubber.add_response(
                "assume_role",
                {
                    "Credentials": {
                        "AccessKeyId": "AKIAIOSFODNN7EXAMPLE",
                        "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                        "SessionToken": "FwoGZX...",
                        "Expiration": "2025-03-01T00:00:00Z",
                    },
                    "AssumedRoleUser": {
                        "AssumedRoleId": "AROA3XFRBF23:ai-asg-advisor",
                        "Arn": "arn:aws:sts::123456789012:assumed-role/test-role/ai-asg-advisor",
                    },
                },
                expected_params={
                    "RoleArn": "arn:aws:iam::123456789012:role/test-role",
                    "RoleSessionName": "ai-asg-advisor",
                    "DurationSeconds": 3600,
                },
            )

            with mock.patch("boto3.client", return_value=sts):
                session = create_session(config)
                # Session was created with temp credentials, not the profile
                creds = session.get_credentials()
                assert creds is not None
                frozen = creds.get_frozen_credentials()
                assert frozen.access_key == "AKIAIOSFODNN7EXAMPLE"
                assert frozen.token == "FwoGZX..."
