"""Configuration loader — config file and environment variable support."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "ai-asg-advisor" / "config.toml"
DEFAULT_MODEL = "claude-sonnet-4-20250514"


@dataclass
class LLMConfig:
    api_key: str = ""
    model: str = DEFAULT_MODEL
    base_url: str | None = None


@dataclass
class AWSConfig:
    profile: str | None = None
    role_arn: str | None = None
    region: str = "us-west-1"


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    aws: AWSConfig = field(default_factory=AWSConfig)


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load configuration from TOML file, with environment variable overrides."""
    config = AppConfig()
    path = config_path or DEFAULT_CONFIG_PATH

    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)

        if "llm" in data:
            config.llm.api_key = data["llm"].get("api_key", "")
            config.llm.model = data["llm"].get("model", DEFAULT_MODEL)
            config.llm.base_url = data["llm"].get("base_url")

        if "aws" in data:
            config.aws.profile = data["aws"].get("profile")
            config.aws.role_arn = data["aws"].get("role_arn")
            config.aws.region = data["aws"].get("region", "us-west-1")

    # Environment variables override config file
    if env_key := os.environ.get("ANTHROPIC_AUTH_TOKEN", os.environ.get("ANTHROPIC_API_KEY")):
        config.llm.api_key = env_key
    if env_model := os.environ.get("ANTHROPIC_MODEL", os.environ.get("AI_ASG_ADVISOR_MODEL")):
        config.llm.model = env_model
    if env_base_url := os.environ.get("ANTHROPIC_BASE_URL"):
        config.llm.base_url = env_base_url
    if env_region := os.environ.get("AWS_DEFAULT_REGION"):
        config.aws.region = env_region

    return config
