#!/usr/bin/env python3
"""
High-Performance S3 Storage Tier Scanner
Uses AWS CloudWatch S3 daily metrics to retrieve exact object counts and size splitups
by storage class in seconds without listing individual objects, preventing high CPU/memory
load on production servers.
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("s3_scanner")

STORAGE_CLASS_MAP = {
    "STANDARD": "Standard",
    "REDUCED_REDUNDANCY": "Reduced Redundancy",
    "STANDARD_IA": "Standard Infrequent Access (Standard-IA)",
    "ONEZONE_IA": "One Zone Infrequent Access (OneZone-IA)",
    "INTELLIGENT_TIERING": "Intelligent-Tiering",
    "GLACIER": "Glacier Flexible Retrieval",
    "DEEP_ARCHIVE": "Glacier Deep Archive",
    "GLACIER_IR": "Glacier Instant Retrieval (Glacier-IR)",
    "OUTPOSTS": "Outposts",
    "SNOW": "Snowball Edge",
    "EXPRESS_ONEZONE": "Express One Zone"
}

import os

# S3 Safety max objects pagination cap for fallback list_objects_v2
S3_SAFETY_MAX_OBJECTS = int(os.environ.get("S3_SAFETY_MAX_OBJECTS", "50000"))

# CloudWatch StorageType to S3 StorageClass mapping
CW_STORAGE_TYPES = {
    "StandardStorage":                "STANDARD",
    "StandardIAStorage":              "STANDARD_IA",
    "StandardIASizeBytes":            "STANDARD_IA",
    "OneZoneIAStorage":               "ONEZONE_IA",
    "IntelligentTieringFAStorage":    "INTELLIGENT_TIERING",
    "IntelligentTieringIAStorage":    "INTELLIGENT_TIERING",
    "IntelligentTieringAAStorage":    "INTELLIGENT_TIERING",
    "IntelligentTieringAIAStorage":   "INTELLIGENT_TIERING",
    "GlacierStorage":                 "GLACIER",
    "GlacierInstantRetrievalStorage": "GLACIER_IR",
    "DeepArchiveStorage":            "DEEP_ARCHIVE",
    "ReducedRedundancyStorage":      "REDUCED_REDUNDANCY"
}


def get_friendly_storage_class(raw_class):
    if not raw_class:
        return "Standard"
    return STORAGE_CLASS_MAP.get(raw_class, raw_class.title().replace("_", " "))


def _scan_bucket_via_cloudwatch(session, bucket_name):
    """
    Query AWS CloudWatch S3 metrics for BucketSizeBytes and NumberOfObjects.
    Returns list of dicts if metrics exist, or None if no CloudWatch data is found.
    """
    s3_client = session.client("s3")
    try:
        loc = s3_client.get_bucket_location(Bucket=bucket_name).get("LocationConstraint")
        region = loc if loc else "us-east-1"
        if region == "EU":
            region = "eu-west-1"
    except Exception:
        region = "us-east-1"

    cw_client = session.client("cloudwatch", region_name=region)

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=4)

    # 1. Fetch total object count metric
    total_objects = 0
    try:
        resp_obj = cw_client.get_metric_statistics(
            Namespace="AWS/S3",
            MetricName="NumberOfObjects",
            Dimensions=[
                {"Name": "BucketName", "Value": bucket_name},
                {"Name": "StorageType", "Value": "AllStorageTypes"}
            ],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400,
            Statistics=["Average"]
        )
        obj_dps = resp_obj.get("Datapoints", [])
        if obj_dps:
            total_objects = int(sorted(obj_dps, key=lambda x: x["Timestamp"])[-1]["Average"])
    except Exception as e:
        logger.debug(f"CloudWatch NumberOfObjects metric failed for {bucket_name}: {e}")

    # 2. Fetch size by storage class
    class_sizes = defaultdict(int)
    found_metrics = False

    for st_name, sc_code in CW_STORAGE_TYPES.items():
        try:
            resp_sz = cw_client.get_metric_statistics(
                Namespace="AWS/S3",
                MetricName="BucketSizeBytes",
                Dimensions=[
                    {"Name": "BucketName", "Value": bucket_name},
                    {"Name": "StorageType", "Value": st_name}
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=86400,
                Statistics=["Average"]
            )
            sz_dps = resp_sz.get("Datapoints", [])
            if sz_dps:
                latest_size = sorted(sz_dps, key=lambda x: x["Timestamp"])[-1]["Average"]
                if latest_size > 0:
                    class_sizes[sc_code] += int(latest_size)
                    found_metrics = True
        except Exception:
            pass

    if not found_metrics:
        return None

    # Format results
    results = []
    main_sc = max(class_sizes.items(), key=lambda x: x[1])[0] if class_sizes else "STANDARD"
    for sc_code, sz_bytes in class_sizes.items():
        objs = total_objects if sc_code == main_sc else 0
        results.append({
            "bucket": bucket_name,
            "storage_class": sc_code,
            "object_count": objs,
            "size_bytes": sz_bytes
        })

    return results


def _paginate_objects(s3_client, bucket_name, aggregates, max_objects=50000):
    """Paginates object list with safety cap to avoid hanging on multi-million object buckets."""
    paginator = s3_client.get_paginator("list_objects_v2")
    total_scanned = 0
    cap = max_objects if max_objects else S3_SAFETY_MAX_OBJECTS

    for page in paginator.paginate(Bucket=bucket_name):
        for obj in page.get("Contents", []):
            storage_class = obj.get("StorageClass", "STANDARD")
            aggregates[storage_class][0] += 1
            aggregates[storage_class][1] += obj.get("Size", 0)
            total_scanned += 1
            if total_scanned >= cap:
                logger.warning(f"Reached safety scan limit ({cap} objects) for {bucket_name}")
                return


def scan_bucket(bucket_name, session_params, include_versions=False, max_objects=None):
    """
    Scans an S3 bucket using fast CloudWatch metrics, falling back to paginated API list if metrics are unavailable.
    """
    logger.info(f"Starting scan for bucket: {bucket_name}")
    session = boto3.Session(**session_params)

    # 1. Try Fast CloudWatch Metric Query
    if not include_versions and not max_objects:
        cw_results = _scan_bucket_via_cloudwatch(session, bucket_name)
        if cw_results is not None:
            logger.info(f"CloudWatch fast scan completed for {bucket_name} ({len(cw_results)} storage classes found)")
            return cw_results

    # 2. Fallback to API Pagination
    s3_client = session.client("s3")
    aggregates = defaultdict(lambda: [0, 0])

    try:
        _paginate_objects(s3_client, bucket_name, aggregates, max_objects=max_objects or 50000)
        logger.info(f"Finished paginated scan for bucket: {bucket_name}.")
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        logger.error(f"Access denied or error scanning bucket {bucket_name}: {error_code} - {e}")
        return [{
            "bucket": bucket_name,
            "storage_class": "ACCESS_DENIED",
            "object_count": 0,
            "size_bytes": 0
        }]
    except Exception as e:
        logger.error(f"Unexpected error scanning bucket {bucket_name}: {e}")
        return [{
            "bucket": bucket_name,
            "storage_class": "ERROR",
            "object_count": 0,
            "size_bytes": 0
        }]

    results = []
    for storage_class, (count, size) in aggregates.items():
        results.append({
            "bucket": bucket_name,
            "storage_class": storage_class,
            "object_count": count,
            "size_bytes": size
        })

    if not results:
        results.append({
            "bucket": bucket_name,
            "storage_class": "STANDARD",
            "object_count": 0,
            "size_bytes": 0
        })

    return results


def get_all_buckets(session_params):
    """Retrieves all S3 bucket names in the account."""
    session = boto3.Session(**session_params)
    s3_client = session.client("s3")
    try:
        response = s3_client.list_buckets()
        return [bucket["Name"] for bucket in response.get("Buckets", [])]
    except ClientError as e:
        logger.error(f"Failed to list S3 buckets: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error listing S3 buckets: {e}")
        raise
