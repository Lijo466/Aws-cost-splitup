#!/usr/bin/env python3
"""
AWS Data Transfer Scanner
Queries CloudWatch metrics and Lightsail API to collect inbound and outbound network data transfer
for EC2 instances, Lightsail instances, and S3 buckets across regions over the past 30 days.
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.exceptions import ClientError

from ec2_scanner import scan_ec2
from lightsail_scanner import scan_lightsail

logger = logging.getLogger("datatransfer_scanner")

# AWS Outbound Internet Data Transfer Egress Rate ($/GB) after allowances
STANDARD_EGRESS_PER_GB = float(os.environ.get("EGRESS_COST_PER_GB", "0.09"))


def get_ec2_network_metrics(session_params: dict, region: str, instance_id: str, days: int = 30) -> tuple[float, float]:
    """Retrieve NetworkIn and NetworkOut total GB for an EC2 instance over the last N days."""
    try:
        session = boto3.Session(**session_params, region_name=region)
        cw = session.client("cloudwatch")

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days)

        # Query NetworkIn
        in_resp = cw.get_metric_statistics(
            Namespace="AWS/EC2",
            MetricName="NetworkIn",
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start_time,
            EndTime=end_time,
            Period=days * 86400,
            Statistics=["Sum"],
        )
        in_bytes = sum(dp.get("Sum", 0) for dp in in_resp.get("Datapoints", []))

        # Query NetworkOut
        out_resp = cw.get_metric_statistics(
            Namespace="AWS/EC2",
            MetricName="NetworkOut",
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start_time,
            EndTime=end_time,
            Period=days * 86400,
            Statistics=["Sum"],
        )
        out_bytes = sum(dp.get("Sum", 0) for dp in out_resp.get("Datapoints", []))

        in_gb = round(in_bytes / (1024 ** 3), 3)
        out_gb = round(out_bytes / (1024 ** 3), 3)
        return in_gb, out_gb
    except Exception as e:
        logger.warning(f"Failed to fetch EC2 CloudWatch network metrics for {instance_id} in {region}: {e}")
        return 0.0, 0.0


def get_lightsail_network_metrics(session_params: dict, region: str, instance_name: str, days: int = 30) -> tuple[float, float]:
    """Retrieve NetworkIn and NetworkOut total GB for a Lightsail instance over the last N days."""
    try:
        session = boto3.Session(**session_params, region_name=region)
        ls = session.client("lightsail")

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days)

        in_resp = ls.get_instance_metric_data(
            instanceName=instance_name,
            metricName="NetworkIn",
            period=days * 86400,
            startTime=start_time,
            endTime=end_time,
            unit="Bytes",
            statistics=["Sum"],
        )
        in_bytes = sum(dp.get("sum", 0) for dp in in_resp.get("metricData", []))

        out_resp = ls.get_instance_metric_data(
            instanceName=instance_name,
            metricName="NetworkOut",
            period=days * 86400,
            startTime=start_time,
            endTime=end_time,
            unit="Bytes",
            statistics=["Sum"],
        )
        out_bytes = sum(dp.get("sum", 0) for dp in out_resp.get("metricData", []))

        in_gb = round(in_bytes / (1024 ** 3), 3)
        out_gb = round(out_bytes / (1024 ** 3), 3)
        return in_gb, out_gb
    except Exception as e:
        logger.warning(f"Failed to fetch Lightsail metric data for {instance_name} in {region}: {e}")
        return 0.0, 0.0


def get_s3_network_metrics(session_params: dict, region: str, bucket_name: str, days: int = 30) -> tuple[float, float]:
    """Retrieve BytesUploaded (inbound) and BytesDownloaded (outbound) for an S3 bucket over the last N days."""
    try:
        session = boto3.Session(**session_params, region_name=region)
        cw = session.client("cloudwatch")

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days)

        # Query BytesUploaded (Inbound)
        in_bytes = 0.0
        try:
            in_resp = cw.get_metric_statistics(
                Namespace="AWS/S3",
                MetricName="BytesUploaded",
                Dimensions=[{"Name": "BucketName", "Value": bucket_name}],
                StartTime=start_time,
                EndTime=end_time,
                Period=days * 86400,
                Statistics=["Sum"],
            )
            in_bytes = sum(dp.get("Sum", 0) for dp in in_resp.get("Datapoints", []))
        except Exception:
            pass

        # Query BytesDownloaded (Outbound Egress)
        out_bytes = 0.0
        try:
            out_resp = cw.get_metric_statistics(
                Namespace="AWS/S3",
                MetricName="BytesDownloaded",
                Dimensions=[{"Name": "BucketName", "Value": bucket_name}],
                StartTime=start_time,
                EndTime=end_time,
                Period=days * 86400,
                Statistics=["Sum"],
            )
            out_bytes = sum(dp.get("Sum", 0) for dp in out_resp.get("Datapoints", []))
        except Exception:
            pass

        in_gb = round(in_bytes / (1024 ** 3), 3)
        out_gb = round(out_bytes / (1024 ** 3), 3)
        return in_gb, out_gb
    except Exception as e:
        logger.warning(f"Failed to fetch S3 CloudWatch network metrics for {bucket_name} in {region}: {e}")
        return 0.0, 0.0


def scan_datatransfer(session_params: dict, regions: list[str] | None = None, days: int = 30, threads: int = 8) -> list[dict]:
    """
    Scan data transfer metrics across EC2, Lightsail instances, and S3 buckets over the past N days.
    Returns structured data transfer rows with included allowances and estimated egress costs.
    """
    rows = []

    # 1. Scan EC2 Instances Data Transfer
    logger.info("Scanning EC2 instances for data transfer...")
    try:
        ec2_instances = scan_ec2(session_params, regions=regions, threads=threads)

        def process_ec2(inst):
            r_region = inst["region"]
            inst_id = inst["instance_id"]
            inst_name = inst.get("name") or inst_id
            in_gb, out_gb = get_ec2_network_metrics(session_params, r_region, inst_id, days=days)

            # EC2 standard egress pricing (first 100GB/mo free across account, $0.09/GB after)
            billable_out_gb = max(0.0, out_gb - 100.0)
            egress_cost = round(billable_out_gb * STANDARD_EGRESS_PER_GB, 2)

            return {
                "service":              "EC2",
                "resource_id":          inst_id,
                "resource_name":        inst_name,
                "region":               r_region,
                "state":                inst.get("state", "unknown"),
                "inbound_gb":           in_gb,
                "outbound_gb":          out_gb,
                "allowance_gb":         100.0,
                "allowance_used_pct":   round(min(100.0, (out_gb / 100.0) * 100.0), 1) if out_gb > 0 else 0.0,
                "overage_gb":           billable_out_gb,
                "egress_cost":          egress_cost,
            }

        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = [executor.submit(process_ec2, inst) for inst in ec2_instances]
            for future in as_completed(futures):
                try:
                    rows.append(future.result())
                except Exception as e:
                    logger.error(f"Failed processing EC2 data transfer row: {e}")
    except Exception as e:
        logger.error(f"Error scanning EC2 data transfer: {e}")

    # 2. Scan Lightsail Instances Data Transfer
    logger.info("Scanning Lightsail instances for data transfer...")
    try:
        lightsail_instances = scan_lightsail(session_params, regions=regions, threads=threads)

        def process_lightsail(inst):
            r_region = inst["region"]
            inst_name = inst["name"]
            allowance_gb = float(inst.get("monthly_transfer_gb") or 1000.0)

            in_gb, out_gb = get_lightsail_network_metrics(session_params, r_region, inst_name, days=days)

            overage_gb = max(0.0, out_gb - allowance_gb)
            egress_cost = round(overage_gb * STANDARD_EGRESS_PER_GB, 2)
            used_pct = round((out_gb / allowance_gb) * 100.0, 1) if allowance_gb > 0 else 0.0

            return {
                "service":              "Lightsail",
                "resource_id":          inst_name,
                "resource_name":        inst_name,
                "region":               r_region,
                "state":                inst.get("state", "unknown"),
                "inbound_gb":           in_gb,
                "outbound_gb":          out_gb,
                "allowance_gb":         allowance_gb,
                "allowance_used_pct":   min(100.0, used_pct),
                "overage_gb":           overage_gb,
                "egress_cost":          egress_cost,
            }

        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = [executor.submit(process_lightsail, inst) for inst in lightsail_instances]
            for future in as_completed(futures):
                try:
                    rows.append(future.result())
                except Exception as e:
                    logger.error(f"Failed processing Lightsail data transfer row: {e}")
    except Exception as e:
        logger.error(f"Error scanning Lightsail data transfer: {e}")

    # 3. Scan S3 Buckets Data Transfer
    logger.info("Scanning S3 buckets for data transfer...")
    try:
        session = boto3.Session(**session_params)
        s3 = session.client("s3")
        buckets = s3.list_buckets().get("Buckets", [])

        def process_s3_bucket(bucket):
            b_name = bucket["Name"]
            try:
                loc = s3.get_bucket_location(Bucket=b_name).get("LocationConstraint")
                b_region = loc if loc and loc != "EU" else ("eu-west-1" if loc == "EU" else "us-east-1")
            except Exception:
                b_region = "us-east-1"

            in_gb, out_gb = get_s3_network_metrics(session_params, b_region, b_name, days=days)
            billable_out_gb = max(0.0, out_gb - 100.0)
            egress_cost = round(billable_out_gb * STANDARD_EGRESS_PER_GB, 2)

            return {
                "service":              "S3",
                "resource_id":          b_name,
                "resource_name":        b_name,
                "region":               b_region,
                "state":                "active",
                "inbound_gb":           in_gb,
                "outbound_gb":          out_gb,
                "allowance_gb":         100.0, # Account baseline free tier
                "allowance_used_pct":   round(min(100.0, (out_gb / 100.0) * 100.0), 1) if out_gb > 0 else 0.0,
                "overage_gb":           billable_out_gb,
                "egress_cost":          egress_cost,
            }

        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = [executor.submit(process_s3_bucket, b) for b in buckets]
            for future in as_completed(futures):
                try:
                    rows.append(future.result())
                except Exception as e:
                    logger.error(f"Failed processing S3 bucket data transfer row: {e}")
    except Exception as e:
        logger.error(f"Error listing S3 buckets for data transfer: {e}")

    return rows
