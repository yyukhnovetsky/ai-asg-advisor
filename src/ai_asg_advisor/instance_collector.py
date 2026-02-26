"""EC2 instance type specs and pricing collector."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Region code → Pricing API location name mapping (common regions)
REGION_LOCATION_MAP = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "eu-west-1": "EU (Ireland)",
    "eu-west-2": "EU (London)",
    "eu-west-3": "EU (Paris)",
    "eu-central-1": "EU (Frankfurt)",
    "eu-north-1": "EU (Stockholm)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "ca-central-1": "Canada (Central)",
    "sa-east-1": "South America (Sao Paulo)",
}

# Instance family categories for alternative suggestions
_GENERAL_PURPOSE_FAMILIES = {"m5", "m5a", "m5n", "m6i", "m6a", "m6g", "m6gn", "m7i", "m7a", "m7g"}
_COMPUTE_OPTIMIZED_FAMILIES = {"c5", "c5a", "c5n", "c6i", "c6a", "c6g", "c6gn", "c7i", "c7a", "c7g"}
_BURSTABLE_FAMILIES = {"t3", "t3a", "t4g"}

# Pattern to parse instance type: family + generation + modifiers . size
_INSTANCE_TYPE_RE = re.compile(r"^([a-z]+)(\d+)([a-z]*)\.(.+)$")


@dataclass
class InstanceTypeInfo:
    """Specs and pricing for an EC2 instance type."""
    instance_type: str
    vcpu: int = 0
    memory_gib: float = 0.0
    architecture: str = "x86_64"
    network_performance: str = ""
    burstable: bool = False
    current_generation: bool = True
    price_per_hour: float | None = None
    monthly_cost_estimate: float | None = None


@dataclass
class InstanceRecommendations:
    """Current instance info plus alternatives."""
    current: InstanceTypeInfo
    alternatives: list[InstanceTypeInfo] = field(default_factory=list)


def get_instance_type_info(ec2_client, instance_type: str) -> InstanceTypeInfo:
    """Fetch specs for a single instance type."""
    response = ec2_client.describe_instance_types(InstanceTypes=[instance_type])

    if not response["InstanceTypes"]:
        raise ValueError(f"Instance type '{instance_type}' not found")

    return _parse_instance_type(response["InstanceTypes"][0])


def find_alternative_types(ec2_client, current_type: str, include_graviton: bool = False) -> list[InstanceTypeInfo]:
    """Find alternative instance types with the same vCPU and memory."""
    current_info = get_instance_type_info(ec2_client, current_type)

    memory_mib = int(current_info.memory_gib * 1024)
    paginator = ec2_client.get_paginator("describe_instance_types")

    alternatives = []
    for page in paginator.paginate(
        Filters=[
            {"Name": "vcpu-info.default-vcpus", "Values": [str(current_info.vcpu)]},
            {"Name": "memory-info.size-in-mib", "Values": [str(memory_mib)]},
            {"Name": "current-generation", "Values": ["true"]},
        ]
    ):
        for it in page["InstanceTypes"]:
            if it["InstanceType"] == current_type:
                continue
            parsed = _parse_instance_type(it)
            if not include_graviton and parsed.architecture == "arm64":
                continue
            if _is_relevant_alternative(current_type, parsed.instance_type):
                alternatives.append(parsed)

    logger.info(
        "Found %d alternatives for '%s' (vcpu=%d, mem=%.1fGiB)",
        len(alternatives), current_type, current_info.vcpu, current_info.memory_gib,
    )

    return alternatives


def enrich_with_pricing(
    pricing_client,
    instances: list[InstanceTypeInfo],
    region: str,
) -> list[InstanceTypeInfo]:
    """Add on-demand pricing to a list of instance type infos."""
    location = REGION_LOCATION_MAP.get(region)
    if not location:
        logger.warning("No pricing location mapping for region '%s', skipping pricing", region)
        return instances

    for info in instances:
        price = _get_ondemand_price(pricing_client, info.instance_type, location)
        if price is not None:
            info.price_per_hour = round(price, 4)
            info.monthly_cost_estimate = round(price * 730, 2)  # ~730 hours/month

    return instances


def get_instance_recommendations(
    ec2_client,
    pricing_client,
    instance_type: str,
    region: str,
    include_graviton: bool = False,
) -> InstanceRecommendations:
    """Build complete instance recommendations: current specs + priced alternatives."""
    current = get_instance_type_info(ec2_client, instance_type)
    alternatives = find_alternative_types(ec2_client, instance_type, include_graviton)

    # Price everything
    all_instances = [current] + alternatives
    enrich_with_pricing(pricing_client, all_instances, region)

    # Sort alternatives by price (cheapest first), unknown-price last
    alternatives.sort(key=lambda x: x.price_per_hour if x.price_per_hour is not None else float("inf"))

    logger.info(
        "Instance recommendations for '%s': %d alternatives, current=$%.4f/hr",
        instance_type, len(alternatives), current.price_per_hour or 0,
    )

    return InstanceRecommendations(current=current, alternatives=alternatives)


def _parse_instance_type(it: dict) -> InstanceTypeInfo:
    """Parse a describe_instance_types response item into our dataclass."""
    architectures = it.get("ProcessorInfo", {}).get("SupportedArchitectures", ["x86_64"])
    memory_mib = it.get("MemoryInfo", {}).get("SizeInMiB", 0)

    return InstanceTypeInfo(
        instance_type=it["InstanceType"],
        vcpu=it.get("VCpuInfo", {}).get("DefaultVCpus", 0),
        memory_gib=round(memory_mib / 1024, 1),
        architecture=architectures[0] if architectures else "x86_64",
        network_performance=it.get("NetworkInfo", {}).get("NetworkPerformance", ""),
        burstable=it.get("BurstablePerformanceSupported", False),
        current_generation=it.get("CurrentGeneration", True),
    )


def _is_relevant_alternative(current_type: str, candidate_type: str) -> bool:
    """Filter candidates to only relevant alternatives (same category or adjacent)."""
    current_match = _INSTANCE_TYPE_RE.match(current_type)
    candidate_match = _INSTANCE_TYPE_RE.match(candidate_type)

    if not current_match or not candidate_match:
        return False

    current_family = current_match.group(1)
    candidate_family = candidate_match.group(1)

    # Same base family letter (m, c, t, r, etc.)
    if current_family[0] == candidate_family[0]:
        return True

    # Build full family key including modifier: e.g. "m5", "m5a", "c6i", "t3"
    current_full = current_family + current_match.group(2) + current_match.group(3)
    candidate_full = candidate_family + candidate_match.group(2) + candidate_match.group(3)

    def _in_category(full: str, categories: set[str]) -> bool:
        """Check if a full family key belongs to a category set.

        Handles both exact matches (m5 in {m5}) and base matches (m5n -> m5 base).
        """
        if full in categories:
            return True
        # Strip trailing modifier letters and check base: e.g. m5a -> m5, c6gn -> c6g
        base = re.match(r"^[a-z]+\d+", full)
        return base is not None and base.group(0) in categories

    gp_burstable = _GENERAL_PURPOSE_FAMILIES | _BURSTABLE_FAMILIES
    if _in_category(current_full, _GENERAL_PURPOSE_FAMILIES) or _in_category(current_full, _BURSTABLE_FAMILIES):
        if _in_category(candidate_full, gp_burstable):
            return True

    # Compute-optimized ↔ general-purpose is acceptable
    gp_compute = _COMPUTE_OPTIMIZED_FAMILIES | _GENERAL_PURPOSE_FAMILIES
    if _in_category(current_full, _COMPUTE_OPTIMIZED_FAMILIES) or _in_category(current_full, _GENERAL_PURPOSE_FAMILIES):
        if _in_category(candidate_full, gp_compute):
            return True

    return False


def _get_ondemand_price(pricing_client, instance_type: str, location: str) -> float | None:
    """Fetch on-demand hourly price for an instance type in a given location."""
    try:
        response = pricing_client.get_products(
            ServiceCode="AmazonEC2",
            Filters=[
                {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
                {"Type": "TERM_MATCH", "Field": "location", "Value": location},
                {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
                {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
                {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
                {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
            ],
            MaxResults=1,
        )

        if not response.get("PriceList"):
            logger.debug("No pricing found for %s in %s", instance_type, location)
            return None

        price_data = json.loads(response["PriceList"][0])
        on_demand = price_data.get("terms", {}).get("OnDemand", {})

        for offer_term in on_demand.values():
            for price_dim in offer_term.get("priceDimensions", {}).values():
                usd = price_dim.get("pricePerUnit", {}).get("USD")
                if usd:
                    return float(usd)

    except Exception:
        logger.debug("Failed to fetch pricing for %s", instance_type, exc_info=True)

    return None
