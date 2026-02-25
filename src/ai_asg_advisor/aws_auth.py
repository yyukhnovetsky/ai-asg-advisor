"""AWS authentication module — profile-based or IAM role assumption."""

from __future__ import annotations

import logging

import boto3
from botocore.config import Config as BotoConfig

from ai_asg_advisor.config import AWSConfig

logger = logging.getLogger(__name__)

DEFAULT_PROFILE = "ai-adviser"
DEFAULT_ROLE_NAME = "ai-advisor-role"

# Retry config for transient API errors
_BOTO_CONFIG = BotoConfig(
    retries={"max_attempts": 3, "mode": "adaptive"},
)


def create_session(aws_config: AWSConfig) -> boto3.Session:
    """Create a boto3 session using either a named profile or IAM role assumption.

    Resolution order:
    1. If role_arn is set → assume that role (via STS)
    2. If profile is set → use that named profile
    3. Fall back to default credential chain (env vars, instance profile, etc.)
    """
    if aws_config.role_arn:
        return _session_from_role(aws_config.role_arn, aws_config.region)

    if aws_config.profile:
        logger.info("Using AWS profile: %s", aws_config.profile)
        return boto3.Session(
            profile_name=aws_config.profile,
            region_name=aws_config.region,
        )

    logger.info("Using default AWS credential chain")
    return boto3.Session(region_name=aws_config.region)


def _session_from_role(role_arn: str, region: str) -> boto3.Session:
    """Assume an IAM role and return a session with temporary credentials."""
    # If the user passed a short name instead of a full ARN, build the ARN
    if not role_arn.startswith("arn:"):
        sts = boto3.client("sts", region_name=region, config=_BOTO_CONFIG)
        account_id = sts.get_caller_identity()["Account"]
        role_arn = f"arn:aws:iam::{account_id}:role/{role_arn}"

    logger.info("Assuming IAM role: %s", role_arn)
    sts = boto3.client("sts", region_name=region, config=_BOTO_CONFIG)
    response = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName="ai-asg-advisor",
        DurationSeconds=3600,
    )
    creds = response["Credentials"]

    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region,
    )


def get_client(session: boto3.Session, service: str) -> boto3.client:
    """Create a boto3 client from a session with standard retry config."""
    return session.client(service, config=_BOTO_CONFIG)


def get_pricing_client(session: boto3.Session) -> boto3.client:
    """Create a pricing client — must be in us-east-1 regardless of target region."""
    return session.client(
        "pricing",
        region_name="us-east-1",
        config=_BOTO_CONFIG,
    )


def verify_credentials(session: boto3.Session) -> dict:
    """Verify that the session has valid credentials. Returns caller identity."""
    sts = get_client(session, "sts")
    identity = sts.get_caller_identity()
    logger.info(
        "Authenticated as %s (account: %s)",
        identity["Arn"],
        identity["Account"],
    )
    return identity
