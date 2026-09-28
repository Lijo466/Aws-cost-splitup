#!/usr/bin/env python3
"""
Lightsail Instance Scanner
Fetches Lightsail instance details and bundle pricing across regions, returning structured rows
ready to be persisted to SQLite via db.insert_lightsail_scan().
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("lightsail_scanner")

# Default fallback pricing lookup table (USD / Month)
DEFAULT_BUNDLE_PRICES = {
    # Revision 3_0 / 3_1
    "nano_3_0": 3.50, "nano_3_1": 5.00,
    "micro_3_0": 5.00, "micro_3_1": 7.00,
    "small_3_0": 10.00, "small_3_1": 12.00,
    "medium_3_0": 20.00, "medium_3_1": 24.00,
    "large_3_0": 40.00, "large_3_1": 44.00,
    "xlarge_3_0": 80.00, "xlarge_3_1": 84.00,
    "2xlarge_3_0": 160.00, "2xlarge_3_1": 164.00,
    "4xlarge_3_1": 384.00, "8xlarge_3_1": 884.00,

    # Revision 2_0 / 2_1 / 1_0
    "nano_2_0": 3.50, "nano_2_1": 5.00,
    "micro_2_0": 5.00, "micro_2_1": 7.00,
    "small_2_0": 10.00, "small_2_1": 12.00,
    "medium_2_0": 20.00, "medium_2_1": 24.00,
    "large_2_0": 40.00, "large_2_1": 44.00,
    "xlarge_2_0": 80.00, "xlarge_2_1": 84.00,
    "2xlarge_2_0": 160.00, "2xlarge_2_1": 164.00,

    # Windows bundles
    "nano_win_3_0": 9.50, "micro_win_3_0": 15.00, "small_win_3_0": 20.00,
    "medium_win_3_0": 34.00, "large_win_3_0": 70.00, "xlarge_win_3_0": 120.00,
}

LIGHTSAIL_REGIONS = [
    "us-east-1", "us-east-2", "us-west-2", "ca-central-1",
    "eu-west-1", "eu-west-2", "eu-west-3", "eu-central-1", "eu-north-1",
    "ap-south-1", "ap-southeast-1", "ap-southeast-2", "ap-northeast-1"
]


def resolve_bundle_cost(bundle_id: str, regional_bundles: dict) -> float:
    """Resolve exact or estimated monthly cost for a given Lightsail bundle_id."""
    if not bundle_id:
        return 0.0

    # 1. Exact match from regional API response
    if bundle_id in regional_bundles:
        return float(regional_bundles[bundle_id].get("price", 0.0))

    # 2. Match from static table
    if bundle_id in DEFAULT_BUNDLE_PRICES:
        return DEFAULT_BUNDLE_PRICES[bundle_id]

    # 3. Dynamic pattern heuristic
    b_lower = bundle_id.lower()
    is_win = "win" in b_lower

    if "16xlarge" in b_lower: base = 1760.0
    elif "12xlarge" in b_lower: base = 1320.0
    elif "8xlarge" in b_lower: base = 880.0
    elif "4xlarge" in b_lower: base = 380.0
    elif "2xlarge" in b_lower: base = 160.0
    elif "xlarge" in b_lower: base = 80.0
    elif "large" in b_lower: base = 44.0
    elif "medium" in b_lower: base = 24.0
    elif "small" in b_lower: base = 12.0
    elif "micro" in b_lower: base = 7.0
    elif "nano" in b_lower: base = 5.0
    else: base = 10.0

    if is_win:
        base *= 1.5

    return round(base, 2)


def fetch_regional_bundles(session_params: dict, region: str) -> dict:
    """Fetch live bundle details from Lightsail API for a specific region."""
    bundles_map = {}
    try:
        session = boto3.Session(**session_params, region_name=region)
        client = session.client("lightsail")

        # Paginate get_bundles with includeInactive=True
        paginator = client.get_paginator("get_bundles")
        for page in paginator.paginate(includeInactive=True):
            for b in page.get("bundles", []):
                b_id = b.get("bundleId")
                if b_id:
                    bundles_map[b_id] = {
                        "price": float(b.get("price", 0.0)),
                        "ram_gb": float(b.get("ramSizeInGb", 0.0)),
                        "cpu": int(b.get("cpuCount", 1)),
                        "disk_gb": int(b.get("diskSizeInGb", 0)),
                        "transfer_gb": int(b.get("transferPerMonthInGb", 0)),
                    }
    except Exception as e:
        logger.warning(f"Could not fetch Lightsail bundle prices for {region}: {e}")
    return bundles_map


def scan_region_lightsail(region: str, session_params: dict, global_bundle_cache: dict) -> list[dict]:
    """Scan Lightsail instances in a single region."""
    logger.info(f"Scanning Lightsail instances in region: {region}")
    session = boto3.Session(**session_params, region_name=region)
    client = session.client("lightsail")

    # Regional bundle price lookup
    regional_bundles = fetch_regional_bundles(session_params, region)
    if not regional_bundles:
        regional_bundles = global_bundle_cache

    rows = []
    try:
        paginator = client.get_paginator("get_instances")
        for page in paginator.paginate():
            for inst in page.get("instances", []):
                bundle_id = inst.get("bundleId", "")
                blueprint_id = inst.get("blueprintId", "")
                state_name = inst.get("state", {}).get("name", "running")

                b_info = regional_bundles.get(bundle_id, {})
                monthly_cost = resolve_bundle_cost(bundle_id, regional_bundles)
                hourly_cost = round(monthly_cost / 730.0, 4)

                hardware = inst.get("hardware", {})
                location = inst.get("location", {})

                created_at = inst.get("createdAt")
                created_str = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or "")

                rows.append({
                    "region":               region,
                    "name":                 inst.get("name", ""),
                    "arn":                  inst.get("arn", ""),
                    "bundle_id":            bundle_id,
                    "blueprint_id":         blueprint_id,
                    "state":                state_name,
                    "public_ip":            inst.get("publicIpAddress", ""),
                    "private_ip":           inst.get("privateIpAddress", ""),
                    "created_at":           created_str,
                    "availability_zone":    location.get("availabilityZone", ""),
                    "cpu_count":            hardware.get("cpuCount", b_info.get("cpu", 1)),
                    "ram_gb":               hardware.get("ramSizeInGb", b_info.get("ram_gb", 0.0)),
                    "disk_gb":              b_info.get("disk_gb", 0),
                    "monthly_transfer_gb":  b_info.get("transfer_gb", 0),
                    "hourly_cost":          hourly_cost,
                    "monthly_cost":         monthly_cost,
                })
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code != "AccessDeniedException":
            logger.error(f"ClientError scanning Lightsail in {region}: {error_code} — {e}")
    except Exception as e:
        logger.error(f"Unexpected error scanning Lightsail in {region}: {e}")

    logger.info(f"Found {len(rows)} Lightsail instances in {region}")
    return rows


def scan_lightsail(session_params: dict, regions: list[str] | None = None, threads: int = 6) -> list[dict]:
    """Scan Lightsail instances across regions."""
    if not regions:
        regions = LIGHTSAIL_REGIONS

    logger.info(f"Scanning Lightsail across {len(regions)} regions with {threads} threads")
    global_bundle_cache = fetch_regional_bundles(session_params, "us-east-1")

    all_rows = []
    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = {
            executor.submit(scan_region_lightsail, region, session_params, global_bundle_cache): region
            for region in regions
        }
        for future in as_completed(futures):
            region = futures[future]
            try:
                all_rows.extend(future.result())
            except Exception as e:
                logger.error(f"Thread failed for Lightsail in region {region}: {e}")

    return all_rows
