#!/usr/bin/env python3
"""
RDS Instance Scanner & Pricing Engine
Fetches AWS RDS database instances across regions, calculating estimated hourly and monthly costs
including Multi-AZ multipliers, provisioned storage fees, and engine types.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("rds_scanner")

# Base On-Demand Hourly Rates (USD) for Single-AZ RDS PostgreSQL/MySQL (us-east-1 reference)
BASE_RDS_PRICES = {
    # T3 DB family
    "db.t3.micro":     0.017,
    "db.t3.small":     0.034,
    "db.t3.medium":    0.068,
    "db.t3.large":     0.136,
    "db.t3.xlarge":    0.272,
    "db.t3.2xlarge":   0.544,

    # T4g DB family (Graviton2)
    "db.t4g.micro":    0.016,
    "db.t4g.small":    0.032,
    "db.t4g.medium":   0.064,
    "db.t4g.large":    0.128,
    "db.t4g.xlarge":   0.256,
    "db.t4g.2xlarge":  0.512,

    # M5 / M6i DB family
    "db.m5.large":     0.176,
    "db.m5.xlarge":    0.352,
    "db.m5.2xlarge":   0.704,
    "db.m5.4xlarge":   1.408,
    "db.m6i.large":    0.176,
    "db.m6i.xlarge":   0.352,
    "db.m6i.2xlarge":  0.704,

    # R5 / R6i DB family (Memory Optimized)
    "db.r5.large":     0.240,
    "db.r5.xlarge":    0.480,
    "db.r5.2xlarge":   0.960,
    "db.r5.4xlarge":   1.920,
    "db.r6i.large":    0.240,
    "db.r6i.xlarge":   0.480,
    "db.r6i.2xlarge":  0.960,

    # T2 legacy family
    "db.t2.micro":     0.018,
    "db.t2.small":     0.036,
    "db.t2.medium":    0.072,
    "db.t2.large":     0.144,
}

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

STORAGE_PER_GB_MONTH = 0.115  # General Purpose SSD (gp2/gp3) per GB-month


def get_rds_pricing(region: str, instance_class: str, engine: str, multi_az: bool, storage_gb: int, status: str) -> tuple[float, float]:
    """Calculate estimated hourly_cost ($/hr) and total monthly_cost ($/mo) for an RDS DB instance."""
    base_rate = BASE_RDS_PRICES.get(instance_class)
    if base_rate is None:
        if "micro" in instance_class: base_rate = 0.018
        elif "small" in instance_class: base_rate = 0.035
        elif "medium" in instance_class: base_rate = 0.070
        elif "2xlarge" in instance_class: base_rate = 0.800
        elif "xlarge" in instance_class: base_rate = 0.400
        elif "large" in instance_class: base_rate = 0.200
        else: base_rate = 0.200

    multiplier = REGION_MULTIPLIERS.get(region, 1.05)

    # Multi-AZ doubles compute & storage costs
    if multi_az:
        multiplier *= 2.0

    # Engine licensing modifier
    eng_lower = (engine or "").lower()
    if "oracle" in eng_lower or "sqlserver" in eng_lower:
        multiplier *= 1.8

    hourly_compute = round(base_rate * multiplier, 4)

    # Compute charges pause when DB is stopped, but provisioned storage costs persist
    is_available = status and status.lower() in ("available", "backing-up", "modifying", "configuring-log-exports")
    monthly_compute = round(hourly_compute * 730, 2) if is_available else 0.0

    storage_multi = 2.0 if multi_az else 1.0
    monthly_storage = round((storage_gb or 20) * STORAGE_PER_GB_MONTH * storage_multi, 2)

    total_monthly = round(monthly_compute + monthly_storage, 2)

    return hourly_compute, total_monthly


def scan_region_rds(region: str, session_params: dict) -> list[dict]:
    """Scan RDS DB instances in a single region."""
    logger.info(f"Scanning RDS instances in region: {region}")
    session = boto3.Session(**session_params, region_name=region)
    rds = session.client("rds")

    rows = []
    try:
        paginator = rds.get_paginator("describe_db_instances")
        for page in paginator.paginate():
            for db_inst in page.get("DBInstances", []):
                db_id = db_inst.get("DBInstanceIdentifier", "")
                db_class = db_inst.get("DBInstanceClass", "")
                engine = db_inst.get("Engine", "")
                engine_ver = db_inst.get("EngineVersion", "")
                status = db_inst.get("DBInstanceStatus", "available")
                storage_gb = db_inst.get("AllocatedStorage", 20)
                storage_type = db_inst.get("StorageType", "gp2")
                multi_az = db_inst.get("MultiAZ", False)

                endpoint_data = db_inst.get("Endpoint", {})
                endpoint_str = f"{endpoint_data.get('Address', '-')}:{endpoint_data.get('Port', '')}" if endpoint_data else "-"

                create_time = db_inst.get("InstanceCreateTime")
                create_str = create_time.isoformat() if hasattr(create_time, "isoformat") else str(create_time or "")

                hourly_cost, monthly_cost = get_rds_pricing(region, db_class, engine, multi_az, storage_gb, status)

                rows.append({
                    "region":                   region,
                    "db_instance_identifier":   db_id,
                    "db_instance_class":        db_class,
                    "engine":                   engine,
                    "engine_version":           engine_ver,
                    "status":                   status,
                    "allocated_storage_gb":     storage_gb,
                    "storage_type":             storage_type,
                    "multi_az":                 multi_az,
                    "endpoint":                 endpoint_str,
                    "vpc_id":                   db_inst.get("DBSubnetGroup", {}).get("VpcId", "-"),
                    "availability_zone":        db_inst.get("AvailabilityZone", ""),
                    "created_at":               create_str,
                    "storage_encrypted":        db_inst.get("StorageEncrypted", False),
                    "hourly_cost":              hourly_cost,
                    "monthly_cost":             monthly_cost,
                })
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code != "AccessDeniedException":
            logger.error(f"ClientError scanning RDS in {region}: {error_code} — {e}")
    except Exception as e:
        logger.error(f"Unexpected error scanning RDS in {region}: {e}")

    logger.info(f"Found {len(rows)} RDS instances in {region}")
    return rows


def scan_rds(session_params: dict, regions: list[str] | None = None, threads: int = 8) -> list[dict]:
    """Scan RDS DB instances across regions."""
    if not regions:
        # Standard AWS RDS regions
        regions = [
            "us-east-1", "us-east-2", "us-west-1", "us-west-2", "ca-central-1",
            "eu-west-1", "eu-west-2", "eu-west-3", "eu-central-1", "eu-north-1",
            "ap-south-1", "ap-southeast-1", "ap-southeast-2", "ap-northeast-1", "sa-east-1"
        ]

    logger.info(f"Scanning RDS across {len(regions)} regions with {threads} threads")
    all_rows = []

    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = {
            executor.submit(scan_region_rds, region, session_params): region
            for region in regions
        }
        for future in as_completed(futures):
            region = futures[future]
            try:
                all_rows.extend(future.result())
            except Exception as e:
                logger.error(f"Thread failed for RDS in region {region}: {e}")

    return all_rows
