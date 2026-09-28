#!/usr/bin/env python3
"""
EC2 Instance Scanner & Pricing Engine
Fetches EC2 instance details across one or all regions and calculates estimated
hourly and monthly costs based on region, instance type, platform, and state.
"""

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("ec2_scanner")

# ─────────────────────────────────────────────────────────────
# Base On-Demand Hourly Rates (USD) for Linux (us-east-1 reference)
# Windows pricing is calculated with ~1.5 - 1.8x multiplier where appropriate.
# ─────────────────────────────────────────────────────────────
BASE_EC2_PRICES = {
    # T2 family
    "t2.nano":      0.0058,
    "t2.micro":     0.0116,
    "t2.small":     0.023,
    "t2.medium":    0.0464,
    "t2.large":     0.0928,
    "t2.xlarge":    0.1856,
    "t2.2xlarge":   0.3712,

    # T3 family
    "t3.nano":      0.0052,
    "t3.micro":     0.0104,
    "t3.small":     0.0208,
    "t3.medium":    0.0416,
    "t3.large":     0.0832,
    "t3.xlarge":    0.1664,
    "t3.2xlarge":   0.3328,

    # T3a family
    "t3a.nano":     0.0047,
    "t3a.micro":    0.0094,
    "t3a.small":    0.0188,
    "t3a.medium":   0.0376,
    "t3a.large":    0.0752,
    "t3a.xlarge":   0.1504,
    "t3a.2xlarge":  0.3008,

    # T4g family (ARM Graviton2)
    "t4g.nano":     0.0042,
    "t4g.micro":    0.0084,
    "t4g.small":    0.0168,
    "t4g.medium":   0.0336,
    "t4g.large":    0.0672,
    "t4g.xlarge":   0.1344,
    "t4g.2xlarge":  0.2688,

    # M5 family
    "m5.large":     0.096,
    "m5.xlarge":    0.192,
    "m5.2xlarge":   0.384,
    "m5.4xlarge":   0.768,
    "m5.8xlarge":   1.536,
    "m5.12xlarge":  2.304,
    "m5.16xlarge":  3.072,
    "m5.24xlarge":  4.608,

    # M6i family
    "m6i.large":    0.096,
    "m6i.xlarge":   0.192,
    "m6i.2xlarge":  0.384,
    "m6i.4xlarge":  0.768,

    # C5 family
    "c5.large":     0.085,
    "c5.xlarge":    0.170,
    "c5.2xlarge":   0.340,
    "c5.4xlarge":   0.680,
    "c5.9xlarge":   1.530,
    "c5.18xlarge":  3.060,

    # C6i family
    "c6i.large":    0.085,
    "c6i.xlarge":   0.170,
    "c6i.2xlarge":  0.340,
    "c6i.4xlarge":  0.680,

    # R5 family
    "r5.large":     0.126,
    "r5.xlarge":    0.252,
    "r5.2xlarge":   0.504,
    "r5.4xlarge":   1.008,
    "r5.8xlarge":   2.016,
    "r5.12xlarge":  3.024,

    # R6i family
    "r6i.large":    0.126,
    "r6i.xlarge":   0.252,
    "r6i.2xlarge":  0.504,
    "r6i.4xlarge":  1.008,

    # GPU / Specialized
    "g4dn.xlarge":  0.526,
    "g4dn.2xlarge": 0.752,
    "p3.2xlarge":   3.06,
}

# Regional price multipliers relative to us-east-1 base
REGION_MULTIPLIERS = {
    "us-east-1":      1.00,
    "us-east-2":      1.00,
    "us-west-1":      1.10,
    "us-west-2":      1.00,
    "eu-west-1":      1.08,
    "eu-west-2":      1.12,
    "eu-central-1":   1.12,
    "ap-southeast-1": 1.15,
    "ap-southeast-2": 1.15,
    "ap-northeast-1": 1.14,
    "ap-south-1":     1.05,
    "sa-east-1":      1.40,
}


def get_ec2_pricing(region: str, instance_type: str, platform: str = "linux", state: str = "running") -> tuple[float, float]:
    """
    Calculate estimated hourly_cost ($/hr) and monthly_cost ($/mo) for an EC2 instance.
    - hourly_cost: base hourly rate when running.
    - monthly_cost: 730 * hourly_cost if running, or 0.0 if stopped (compute charges paused).
    """
    base_rate = BASE_EC2_PRICES.get(instance_type)
    if base_rate is None:
        # Generic heuristic based on instance class for unknown types
        if "nano" in instance_type: base_rate = 0.006
        elif "micro" in instance_type: base_rate = 0.012
        elif "small" in instance_type: base_rate = 0.024
        elif "medium" in instance_type: base_rate = 0.048
        elif "2xlarge" in instance_type: base_rate = 0.40
        elif "xlarge" in instance_type: base_rate = 0.20
        elif "large" in instance_type: base_rate = 0.10
        else: base_rate = 0.10

    multiplier = REGION_MULTIPLIERS.get(region, 1.05)
    
    # Platform modifier (Windows incurs additional license cost)
    is_windows = "win" in (platform or "").lower()
    if is_windows:
        multiplier *= 1.6

    hourly_cost = round(base_rate * multiplier, 4)

    # If stopped or terminated, compute hourly charge active usage is 0
    if state and state.lower() not in ("running", "pending"):
        monthly_cost = 0.0
    else:
        monthly_cost = round(hourly_cost * 730, 2)

    return hourly_cost, monthly_cost


def _get_instance_name(tags: list) -> str:
    """Extract the Name tag value from a tag list, or return empty string."""
    for tag in tags or []:
        if tag.get("Key") == "Name":
            return tag.get("Value", "")
    return ""


def scan_region(region: str, session_params: dict) -> list[dict]:
    """Scan all EC2 instances in a single region and return a list of row dicts."""
    logger.info(f"Scanning EC2 instances in region: {region}")
    session = boto3.Session(**session_params, region_name=region)
    ec2 = session.client("ec2")

    rows = []
    try:
        paginator = ec2.get_paginator("describe_instances")
        for page in paginator.paginate():
            for reservation in page.get("Reservations", []):
                for inst in reservation.get("Instances", []):
                    cpu = inst.get("CpuOptions", {})
                    instance_type = inst.get("InstanceType", "")
                    state = inst.get("State", {}).get("Name", "")
                    platform = inst.get("Platform", "linux")

                    hourly_cost, monthly_cost = get_ec2_pricing(region, instance_type, platform, state)

                    rows.append({
                        "region":            region,
                        "instance_id":       inst.get("InstanceId", ""),
                        "instance_type":     instance_type,
                        "state":             state,
                        "name":              _get_instance_name(inst.get("Tags", [])),
                        "public_ip":         inst.get("PublicIpAddress", ""),
                        "private_ip":        inst.get("PrivateIpAddress", ""),
                        "launch_time":       inst.get("LaunchTime", "").isoformat()
                                             if hasattr(inst.get("LaunchTime", ""), "isoformat")
                                             else str(inst.get("LaunchTime", "")),
                        "platform":          platform,
                        "vpc_id":            inst.get("VpcId", ""),
                        "subnet_id":         inst.get("SubnetId", ""),
                        "key_name":          inst.get("KeyName", ""),
                        "monitoring":        inst.get("Monitoring", {}).get("State", ""),
                        "availability_zone": inst.get("Placement", {}).get("AvailabilityZone", ""),
                        "ami_id":            inst.get("ImageId", ""),
                        "architecture":      inst.get("Architecture", ""),
                        "core_count":        cpu.get("CoreCount", 0),
                        "thread_per_core":   cpu.get("ThreadsPerCore", 0),
                        "hourly_cost":       hourly_cost,
                        "monthly_cost":      monthly_cost,
                    })
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        logger.error(f"ClientError scanning EC2 in {region}: {error_code} — {e}")
    except Exception as e:
        logger.error(f"Unexpected error scanning EC2 in {region}: {e}")

    logger.info(f"Found {len(rows)} EC2 instances in {region}")
    return rows


def get_all_regions(session_params: dict) -> list[str]:
    """Return all EC2-enabled region names for the account."""
    session = boto3.Session(**session_params)
    ec2 = session.client("ec2", region_name="us-east-1")
    try:
        response = ec2.describe_regions(Filters=[{"Name": "opt-in-status", "Values": ["opt-in-not-required", "opted-in"]}])
        return [r["RegionName"] for r in response.get("Regions", [])]
    except ClientError as e:
        logger.error(f"Failed to list EC2 regions: {e}")
        raise


def scan_ec2(session_params: dict, regions: list[str] | None = None, threads: int = 8) -> list[dict]:
    """
    Scan EC2 instances across the given regions (or all regions if None).
    Returns a flat list of instance row dicts with pricing.
    """
    if not regions:
        logger.info("Fetching all available EC2 regions...")
        regions = get_all_regions(session_params)
        logger.info(f"Scanning {len(regions)} regions with {threads} threads")

    all_rows: list[dict] = []

    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = {
            executor.submit(scan_region, region, session_params): region
            for region in regions
        }
        for future in as_completed(futures):
            region = futures[future]
            try:
                all_rows.extend(future.result())
            except Exception as e:
                logger.error(f"Thread failed for region {region}: {e}")

    return all_rows
