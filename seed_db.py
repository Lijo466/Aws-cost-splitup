#!/usr/bin/env python3
"""
Seed Database with Mock S3, EC2, Lightsail, Data Transfer, and RDS Scan Data
This is used to populate SQLite database so that the dashboard works locally
even without active AWS credentials.
"""

import sys
from datetime import datetime, timezone
import db
from ec2_scanner import get_ec2_pricing
from rds_scanner import get_rds_pricing

def seed():
    print("Initialising database schema...")
    db.init_db()
    
    # ─────────────────────────────────────────────────────────────
    # Seed S3 scans
    # ─────────────────────────────────────────────────────────────
    s3_ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    s3_mock_rows = [
        {
            "bucket": "bucket-prod-assets",
            "storage_class": "STANDARD",
            "object_count": 120400,
            "size_bytes": 579820584960,
            "status": "OK"
        },
        {
            "bucket": "bucket-prod-assets",
            "storage_class": "GLACIER_IR",
            "object_count": 45000,
            "size_bytes": 343597383680,
            "status": "OK"
        },
        {
            "bucket": "bucket-prod-assets",
            "storage_class": "INTELLIGENT_TIERING",
            "object_count": 20000,
            "size_bytes": 161061273600,
            "status": "OK"
        },
        {
            "bucket": "bucket-logs-archive",
            "storage_class": "STANDARD_IA",
            "object_count": 85000,
            "size_bytes": 1319413952512,
            "status": "OK"
        },
        {
            "bucket": "bucket-logs-archive",
            "storage_class": "DEEP_ARCHIVE",
            "object_count": 230000,
            "size_bytes": 4947802324992,
            "status": "OK"
        },
        {
            "bucket": "bucket-user-backups",
            "storage_class": "STANDARD",
            "object_count": 15000,
            "size_bytes": 85899345920,
            "status": "OK"
        },
        {
            "bucket": "bucket-user-backups",
            "storage_class": "GLACIER",
            "object_count": 48000,
            "size_bytes": 1020054732800,
            "status": "OK"
        },
        {
            "bucket": "bucket-test-temp",
            "storage_class": "STANDARD",
            "object_count": 420,
            "size_bytes": 2576980377,
            "status": "OK"
        },
        {
            "bucket": "bucket-public-website",
            "storage_class": "STANDARD",
            "object_count": 8900,
            "size_bytes": 12884901888,
            "status": "OK"
        },
        {
            "bucket": "bucket-public-website",
            "storage_class": "ONEZONE_IA",
            "object_count": 1200,
            "size_bytes": 4294967296,
            "status": "OK"
        },
        {
            "bucket": "bucket-confidential-finance",
            "storage_class": "STANDARD",
            "object_count": 0,
            "size_bytes": 0,
            "status": "ACCESS_DENIED"
        }
    ]
    
    print(f"Seeding S3 mock data ({len(s3_mock_rows)} rows) at timestamp {s3_ts}...")
    db.insert_s3_scan(s3_mock_rows, s3_ts)
    
    # ─────────────────────────────────────────────────────────────
    # Seed EC2 scans
    # ─────────────────────────────────────────────────────────────
    ec2_ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    raw_ec2_rows = [
        {
            "region": "us-east-1",
            "instance_id": "i-0123456789abcdef0",
            "instance_type": "t3.medium",
            "state": "running",
            "name": "prod-web-server-1",
            "public_ip": "54.210.12.34",
            "private_ip": "172.31.10.12",
            "launch_time": "2026-06-15T08:30:00Z",
            "platform": "linux",
            "vpc_id": "vpc-0a1b2c3d",
            "subnet_id": "subnet-0a1b2c3d4e5f6g7h8",
            "key_name": "prod-ssh-key",
            "monitoring": "disabled",
            "availability_zone": "us-east-1a",
            "ami_id": "ami-0c55b159cbfafe1f0",
            "architecture": "x86_64",
            "core_count": 2,
            "thread_per_core": 1
        },
        {
            "region": "us-east-1",
            "instance_id": "i-0123456789abcdef1",
            "instance_type": "r5.xlarge",
            "state": "running",
            "name": "prod-db-server",
            "public_ip": "",
            "private_ip": "172.31.20.45",
            "launch_time": "2026-06-12T04:15:00Z",
            "platform": "linux",
            "vpc_id": "vpc-0a1b2c3d",
            "subnet_id": "subnet-0a1b2c3d4e5f6g7h9",
            "key_name": "db-ssh-key",
            "monitoring": "enabled",
            "availability_zone": "us-east-1b",
            "ami_id": "ami-0c55b159cbfafe1f0",
            "architecture": "x86_64",
            "core_count": 4,
            "thread_per_core": 2
        },
        {
            "region": "us-west-2",
            "instance_id": "i-0123456789abcdef2",
            "instance_type": "t3.large",
            "state": "stopped",
            "name": "dev-test-windows",
            "public_ip": "",
            "private_ip": "10.0.1.15",
            "launch_time": "2026-06-20T14:00:00Z",
            "platform": "windows",
            "vpc_id": "vpc-1a2b3c4d",
            "subnet_id": "subnet-1a2b3c4d5e6f7g8h9",
            "key_name": "dev-win-key",
            "monitoring": "disabled",
            "availability_zone": "us-west-2a",
            "ami_id": "ami-03cf127a2342345ef",
            "architecture": "x86_64",
            "core_count": 2,
            "thread_per_core": 2
        },
        {
            "region": "eu-central-1",
            "instance_id": "i-0123456789abcdef3",
            "instance_type": "t3.micro",
            "state": "running",
            "name": "stage-api-server",
            "public_ip": "3.120.45.67",
            "private_ip": "192.168.1.10",
            "launch_time": "2026-06-25T11:00:00Z",
            "platform": "linux",
            "vpc_id": "vpc-2a3b4c5d",
            "subnet_id": "subnet-2a3b4c5d6e7f8g9h0",
            "key_name": "stage-ssh-key",
            "monitoring": "disabled",
            "availability_zone": "eu-central-1a",
            "ami_id": "ami-0d5d9d301c853a04a",
            "architecture": "x86_64",
            "core_count": 1,
            "thread_per_core": 2
        },
        {
            "region": "ap-southeast-1",
            "instance_id": "i-0123456789abcdef4",
            "instance_type": "t3.nano",
            "state": "stopped",
            "name": "backup-utility",
            "public_ip": "",
            "private_ip": "10.200.1.4",
            "launch_time": "2026-06-22T23:50:00Z",
            "platform": "linux",
            "vpc_id": "vpc-3a4b5c6d",
            "subnet_id": "subnet-3a4b5c6d7e8f9g0h1",
            "key_name": "backup-key",
            "monitoring": "disabled",
            "availability_zone": "ap-southeast-1a",
            "ami_id": "ami-0fed63ea358539e44",
            "architecture": "x86_64",
            "core_count": 1,
            "thread_per_core": 1
        }
    ]

    ec2_mock_rows = []
    for r in raw_ec2_rows:
        hr, mo = get_ec2_pricing(r["region"], r["instance_type"], r["platform"], r["state"])
        r["hourly_cost"] = hr
        r["monthly_cost"] = mo
        ec2_mock_rows.append(r)
    
    print(f"Seeding EC2 mock data ({len(ec2_mock_rows)} rows) at timestamp {ec2_ts}...")
    db.insert_ec2_scan(ec2_mock_rows, ec2_ts)

    # ─────────────────────────────────────────────────────────────
    # Seed Lightsail scans
    # ─────────────────────────────────────────────────────────────
    lightsail_ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lightsail_mock_rows = [
        {
            "region": "us-east-1",
            "name": "ls-frontend-wp",
            "arn": "arn:aws:lightsail:us-east-1:123456789012:Instance/110022-ls-frontend-wp",
            "bundle_id": "micro_3_0",
            "blueprint_id": "wordpress",
            "state": "running",
            "public_ip": "44.201.88.12",
            "private_ip": "172.26.12.5",
            "created_at": "2026-05-10T10:00:00Z",
            "availability_zone": "us-east-1a",
            "cpu_count": 1,
            "ram_gb": 1.0,
            "disk_gb": 40,
            "monthly_transfer_gb": 2000,
            "hourly_cost": round(5.00 / 730.0, 4),
            "monthly_cost": 5.00
        },
        {
            "region": "us-east-1",
            "name": "ls-api-gateway",
            "arn": "arn:aws:lightsail:us-east-1:123456789012:Instance/220033-ls-api-gateway",
            "bundle_id": "small_3_0",
            "blueprint_id": "nodejs",
            "state": "running",
            "public_ip": "52.90.14.99",
            "private_ip": "172.26.18.20",
            "created_at": "2026-05-15T14:20:00Z",
            "availability_zone": "us-east-1b",
            "cpu_count": 1,
            "ram_gb": 2.0,
            "disk_gb": 60,
            "monthly_transfer_gb": 3000,
            "hourly_cost": round(10.00 / 730.0, 4),
            "monthly_cost": 10.00
        },
        {
            "region": "eu-central-1",
            "name": "ls-redis-cache",
            "arn": "arn:aws:lightsail:eu-central-1:123456789012:Instance/330044-ls-redis-cache",
            "bundle_id": "medium_3_0",
            "blueprint_id": "ubuntu_22_04",
            "state": "running",
            "public_ip": "18.192.30.45",
            "private_ip": "172.26.40.11",
            "created_at": "2026-06-01T09:12:00Z",
            "availability_zone": "eu-central-1a",
            "cpu_count": 2,
            "ram_gb": 4.0,
            "disk_gb": 80,
            "monthly_transfer_gb": 4000,
            "hourly_cost": round(20.00 / 730.0, 4),
            "monthly_cost": 20.00
        },
        {
            "region": "us-west-2",
            "name": "ls-dev-sandbox",
            "arn": "arn:aws:lightsail:us-west-2:123456789012:Instance/440055-ls-dev-sandbox",
            "bundle_id": "nano_3_0",
            "blueprint_id": "debian_11",
            "state": "stopped",
            "public_ip": "",
            "private_ip": "172.26.5.90",
            "created_at": "2026-06-18T16:00:00Z",
            "availability_zone": "us-west-2a",
            "cpu_count": 1,
            "ram_gb": 0.5,
            "disk_gb": 20,
            "monthly_transfer_gb": 1000,
            "hourly_cost": round(3.50 / 730.0, 4),
            "monthly_cost": 3.50
        },
        {
            "region": "ap-south-1",
            "name": "ls-analytics-node",
            "arn": "arn:aws:lightsail:ap-south-1:123456789012:Instance/550066-ls-analytics-node",
            "bundle_id": "large_3_0",
            "blueprint_id": "amazon_linux_2",
            "state": "running",
            "public_ip": "13.235.12.80",
            "private_ip": "172.26.70.3",
            "created_at": "2026-06-20T07:45:00Z",
            "availability_zone": "ap-south-1a",
            "cpu_count": 2,
            "ram_gb": 8.0,
            "disk_gb": 160,
            "monthly_transfer_gb": 5000,
            "hourly_cost": round(40.00 / 730.0, 4),
            "monthly_cost": 40.00
        }
    ]

    print(f"Seeding Lightsail mock data ({len(lightsail_mock_rows)} rows) at timestamp {lightsail_ts}...")
    db.insert_lightsail_scan(lightsail_mock_rows, lightsail_ts)

    # ─────────────────────────────────────────────────────────────
    # Seed Data Transfer scans
    # ─────────────────────────────────────────────────────────────
    datatransfer_ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    datatransfer_mock_rows = [
        {
            "service": "EC2",
            "resource_id": "i-0123456789abcdef0",
            "resource_name": "prod-web-server-1",
            "region": "us-east-1",
            "state": "running",
            "inbound_gb": 45.2,
            "outbound_gb": 182.5,
            "allowance_gb": 100.0,
            "allowance_used_pct": 100.0,
            "overage_gb": 82.5,
            "egress_cost": round(82.5 * 0.09, 2)
        },
        {
            "service": "EC2",
            "resource_id": "i-0123456789abcdef1",
            "resource_name": "prod-db-server",
            "region": "us-east-1",
            "state": "running",
            "inbound_gb": 12.8,
            "outbound_gb": 14.1,
            "allowance_gb": 100.0,
            "allowance_used_pct": 14.1,
            "overage_gb": 0.0,
            "egress_cost": 0.0
        },
        {
            "service": "EC2",
            "resource_id": "i-0123456789abcdef3",
            "resource_name": "stage-api-server",
            "region": "eu-central-1",
            "state": "running",
            "inbound_gb": 8.4,
            "outbound_gb": 42.0,
            "allowance_gb": 100.0,
            "allowance_used_pct": 42.0,
            "overage_gb": 0.0,
            "egress_cost": 0.0
        },
        {
            "service": "Lightsail",
            "resource_id": "ls-frontend-wp",
            "resource_name": "ls-frontend-wp",
            "region": "us-east-1",
            "state": "running",
            "inbound_gb": 110.5,
            "outbound_gb": 840.0,
            "allowance_gb": 2000.0,
            "allowance_used_pct": round((840.0 / 2000.0) * 100.0, 1),
            "overage_gb": 0.0,
            "egress_cost": 0.0
        },
        {
            "service": "Lightsail",
            "resource_id": "ls-api-gateway",
            "resource_name": "ls-api-gateway",
            "region": "us-east-1",
            "state": "running",
            "inbound_gb": 320.0,
            "outbound_gb": 3250.0,
            "allowance_gb": 3000.0,
            "allowance_used_pct": 100.0,
            "overage_gb": 250.0,
            "egress_cost": round(250.0 * 0.09, 2)
        },
        {
            "service": "Lightsail",
            "resource_id": "ls-redis-cache",
            "resource_name": "ls-redis-cache",
            "region": "eu-central-1",
            "state": "running",
            "inbound_gb": 85.0,
            "outbound_gb": 410.0,
            "allowance_gb": 4000.0,
            "allowance_used_pct": round((410.0 / 4000.0) * 100.0, 1),
            "overage_gb": 0.0,
            "egress_cost": 0.0
        },
        {
            "service": "Lightsail",
            "resource_id": "ls-analytics-node",
            "resource_name": "ls-analytics-node",
            "region": "ap-south-1",
            "state": "running",
            "inbound_gb": 520.0,
            "outbound_gb": 2100.0,
            "allowance_gb": 5000.0,
            "allowance_used_pct": round((2100.0 / 5000.0) * 100.0, 1),
            "overage_gb": 0.0,
            "egress_cost": 0.0
        },
        {
            "service": "S3",
            "resource_id": "bucket-prod-assets",
            "resource_name": "bucket-prod-assets",
            "region": "us-east-1",
            "state": "active",
            "inbound_gb": 850.4,
            "outbound_gb": 3420.0,
            "allowance_gb": 100.0,
            "allowance_used_pct": 100.0,
            "overage_gb": 3320.0,
            "egress_cost": round(3320.0 * 0.09, 2)
        },
        {
            "service": "S3",
            "resource_id": "bucket-public-website",
            "resource_name": "bucket-public-website",
            "region": "us-east-1",
            "state": "active",
            "inbound_gb": 12.0,
            "outbound_gb": 450.0,
            "allowance_gb": 100.0,
            "allowance_used_pct": 100.0,
            "overage_gb": 350.0,
            "egress_cost": round(350.0 * 0.09, 2)
        }
    ]

    print(f"Seeding Data Transfer mock data ({len(datatransfer_mock_rows)} rows) at timestamp {datatransfer_ts}...")
    db.insert_datatransfer_scan(datatransfer_mock_rows, datatransfer_ts)

    # ─────────────────────────────────────────────────────────────
    # Seed RDS scans
    # ─────────────────────────────────────────────────────────────
    rds_ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    raw_rds_rows = [
        {
            "region": "us-east-1",
            "db_instance_identifier": "prod-postgres-main",
            "db_instance_class": "db.r5.xlarge",
            "engine": "postgres",
            "engine_version": "14.7",
            "status": "available",
            "allocated_storage_gb": 250,
            "storage_type": "gp3",
            "multi_az": True,
            "endpoint": "prod-postgres-main.c123456789.us-east-1.rds.amazonaws.com:5432",
            "vpc_id": "vpc-0a1b2c3d",
            "availability_zone": "us-east-1a",
            "created_at": "2026-04-10T12:00:00Z",
            "storage_encrypted": True,
        },
        {
            "region": "us-east-1",
            "db_instance_identifier": "stage-mysql-db",
            "db_instance_class": "db.t3.medium",
            "engine": "mysql",
            "engine_version": "8.0.32",
            "status": "available",
            "allocated_storage_gb": 50,
            "storage_type": "gp2",
            "multi_az": False,
            "endpoint": "stage-mysql-db.c123456789.us-east-1.rds.amazonaws.com:3306",
            "vpc_id": "vpc-0a1b2c3d",
            "availability_zone": "us-east-1b",
            "created_at": "2026-05-01T08:30:00Z",
            "storage_encrypted": True,
        },
        {
            "region": "eu-central-1",
            "db_instance_identifier": "dev-aurora-pg",
            "db_instance_class": "db.t4g.medium",
            "engine": "aurora-postgresql",
            "engine_version": "15.2",
            "status": "available",
            "allocated_storage_gb": 100,
            "storage_type": "aurora",
            "multi_az": False,
            "endpoint": "dev-aurora-pg.c987654321.eu-central-1.rds.amazonaws.com:5432",
            "vpc_id": "vpc-2a3b4c5d",
            "availability_zone": "eu-central-1a",
            "created_at": "2026-05-20T14:15:00Z",
            "storage_encrypted": True,
        },
        {
            "region": "ap-south-1",
            "db_instance_identifier": "analytics-mariadb",
            "db_instance_class": "db.m5.large",
            "engine": "mariadb",
            "engine_version": "10.6.12",
            "status": "stopped",
            "allocated_storage_gb": 500,
            "storage_type": "gp3",
            "multi_az": True,
            "endpoint": "-",
            "vpc_id": "vpc-3a4b5c6d",
            "availability_zone": "ap-south-1a",
            "created_at": "2026-06-05T10:00:00Z",
            "storage_encrypted": True,
        },
        {
            "region": "us-west-2",
            "db_instance_identifier": "backup-test-db",
            "db_instance_class": "db.t3.micro",
            "engine": "postgres",
            "engine_version": "13.10",
            "status": "available",
            "allocated_storage_gb": 20,
            "storage_type": "gp2",
            "multi_az": False,
            "endpoint": "backup-test-db.c555666777.us-west-2.rds.amazonaws.com:5432",
            "vpc_id": "vpc-1a2b3c4d",
            "availability_zone": "us-west-2a",
            "created_at": "2026-06-15T18:40:00Z",
            "storage_encrypted": False,
        }
    ]

    rds_mock_rows = []
    for r in raw_rds_rows:
        hr, mo = get_rds_pricing(r["region"], r["db_instance_class"], r["engine"], r["multi_az"], r["allocated_storage_gb"], r["status"])
        r["hourly_cost"] = hr
        r["monthly_cost"] = mo
        rds_mock_rows.append(r)

    print(f"Seeding RDS mock data ({len(rds_mock_rows)} rows) at timestamp {rds_ts}...")
    db.insert_rds_scan(rds_mock_rows, rds_ts)

    print("Database seeding completed successfully!")

if __name__ == "__main__":
    seed()
