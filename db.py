#!/usr/bin/env python3
"""
Database module — SQLite schema and helpers for S3, EC2, Lightsail, Data Transfer, and RDS scan data.
Every scan appends a new snapshot; history is preserved across runs.
Cross-process scan status is persisted in scan_status table.
"""

import os
import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timezone

logger = logging.getLogger("db")

DB_PATH = os.environ.get("DB_PATH", "data/dashboard.db")


def _ensure_dir():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@contextmanager
def get_conn():
    """Context manager that yields a SQLite connection with WAL mode enabled."""
    _ensure_dir()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# Schema bootstrap
# ─────────────────────────────────────────────────────────────

def init_db():
    """Create all tables if they do not already exist, and perform safe migrations."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS scan_status (
                service     TEXT PRIMARY KEY,
                is_running  INTEGER NOT NULL DEFAULT 0,
                last_run    TEXT,
                error       TEXT,
                message     TEXT,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS s3_scans (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                scanned_at    TEXT    NOT NULL,
                bucket        TEXT    NOT NULL,
                storage_class TEXT    NOT NULL,
                object_count  INTEGER NOT NULL DEFAULT 0,
                size_bytes    INTEGER NOT NULL DEFAULT 0,
                status        TEXT    NOT NULL DEFAULT 'OK'
            );

            CREATE INDEX IF NOT EXISTS idx_s3_scans_bucket
                ON s3_scans (bucket);
            CREATE INDEX IF NOT EXISTS idx_s3_scans_scanned_at
                ON s3_scans (scanned_at);

            CREATE TABLE IF NOT EXISTS ec2_scans (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                scanned_at        TEXT    NOT NULL,
                region            TEXT    NOT NULL,
                instance_id       TEXT    NOT NULL,
                instance_type     TEXT,
                state             TEXT,
                name              TEXT,
                public_ip         TEXT,
                private_ip        TEXT,
                launch_time       TEXT,
                platform          TEXT,
                vpc_id            TEXT,
                subnet_id         TEXT,
                key_name          TEXT,
                monitoring        TEXT,
                availability_zone TEXT,
                ami_id            TEXT,
                architecture      TEXT,
                core_count        INTEGER,
                thread_per_core   INTEGER,
                hourly_cost       REAL,
                monthly_cost      REAL
            );

            CREATE INDEX IF NOT EXISTS idx_ec2_scans_instance_id
                ON ec2_scans (instance_id);
            CREATE INDEX IF NOT EXISTS idx_ec2_scans_scanned_at
                ON ec2_scans (scanned_at);

            CREATE TABLE IF NOT EXISTS lightsail_scans (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                scanned_at          TEXT    NOT NULL,
                region              TEXT    NOT NULL,
                name                TEXT    NOT NULL,
                arn                 TEXT,
                bundle_id           TEXT,
                blueprint_id        TEXT,
                state               TEXT,
                public_ip           TEXT,
                private_ip          TEXT,
                created_at          TEXT,
                availability_zone   TEXT,
                cpu_count           INTEGER,
                ram_gb              REAL,
                disk_gb             INTEGER,
                monthly_transfer_gb INTEGER,
                hourly_cost         REAL,
                monthly_cost        REAL
            );

            CREATE INDEX IF NOT EXISTS idx_lightsail_scans_name
                ON lightsail_scans (name);
            CREATE INDEX IF NOT EXISTS idx_lightsail_scans_scanned_at
                ON lightsail_scans (scanned_at);

            CREATE TABLE IF NOT EXISTS datatransfer_scans (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                scanned_at          TEXT    NOT NULL,
                service             TEXT    NOT NULL,
                resource_id         TEXT    NOT NULL,
                resource_name       TEXT,
                region              TEXT,
                state               TEXT,
                inbound_gb          REAL    DEFAULT 0.0,
                outbound_gb         REAL    DEFAULT 0.0,
                allowance_gb        REAL    DEFAULT 0.0,
                allowance_used_pct  REAL    DEFAULT 0.0,
                overage_gb          REAL    DEFAULT 0.0,
                egress_cost         REAL    DEFAULT 0.0
            );

            CREATE INDEX IF NOT EXISTS idx_datatransfer_scans_resource
                ON datatransfer_scans (resource_id);
            CREATE INDEX IF NOT EXISTS idx_datatransfer_scans_scanned_at
                ON datatransfer_scans (scanned_at);

            CREATE TABLE IF NOT EXISTS rds_scans (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                scanned_at              TEXT    NOT NULL,
                region                  TEXT    NOT NULL,
                db_instance_identifier  TEXT    NOT NULL,
                db_instance_class       TEXT,
                engine                  TEXT,
                engine_version          TEXT,
                status                  TEXT,
                allocated_storage_gb    INTEGER,
                storage_type            TEXT,
                multi_az                BOOLEAN,
                endpoint                TEXT,
                vpc_id                  TEXT,
                availability_zone       TEXT,
                created_at              TEXT,
                storage_encrypted       BOOLEAN,
                hourly_cost             REAL,
                monthly_cost            REAL
            );

            CREATE INDEX IF NOT EXISTS idx_rds_scans_db_id
                ON rds_scans (db_instance_identifier);
            CREATE INDEX IF NOT EXISTS idx_rds_scans_scanned_at
                ON rds_scans (scanned_at);

            CREATE TABLE IF NOT EXISTS projects (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT UNIQUE NOT NULL,
                description TEXT,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS resource_project_mappings (
                resource_id   TEXT PRIMARY KEY,
                resource_type TEXT NOT NULL,
                project_name  TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS system_settings (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS project_monthly_costs (
                year_month     TEXT NOT NULL,
                project_name   TEXT NOT NULL,
                total_cost     REAL DEFAULT 0.0,
                s3_cost        REAL DEFAULT 0.0,
                ec2_cost       REAL DEFAULT 0.0,
                rds_cost       REAL DEFAULT 0.0,
                lightsail_cost REAL DEFAULT 0.0,
                updated_at     TEXT NOT NULL,
                PRIMARY KEY (year_month, project_name)
            );

            INSERT INTO system_settings (key, value, updated_at)
            VALUES ('s3_monthly_bill', '3981.23', datetime('now')),
                   ('total_monthly_bill', '8393.73', datetime('now'))
            ON CONFLICT(key) DO NOTHING;
        """)

        # Migration helper for existing ec2_scans table if hourly_cost column missing
        cursor = conn.execute("PRAGMA table_info(ec2_scans)")
        columns = [row["name"] for row in cursor.fetchall()]
        if "hourly_cost" not in columns:
            conn.execute("ALTER TABLE ec2_scans ADD COLUMN hourly_cost REAL DEFAULT 0.0")
        if "monthly_cost" not in columns:
            conn.execute("ALTER TABLE ec2_scans ADD COLUMN monthly_cost REAL DEFAULT 0.0")

    logger.info(f"Database initialised at {DB_PATH}")


# ─────────────────────────────────────────────────────────────
# Scan Status Cross-Worker Synchronization
# ─────────────────────────────────────────────────────────────

def update_scan_status(service: str, is_running: bool, last_run: str | None = None, error: str | None = None, message: str | None = None):
    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO scan_status (service, is_running, last_run, error, message, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(service) DO UPDATE SET
                is_running = excluded.is_running,
                last_run = COALESCE(excluded.last_run, scan_status.last_run),
                error = excluded.error,
                message = excluded.message,
                updated_at = excluded.updated_at
            """,
            (service, 1 if is_running else 0, last_run, error, message, now_str)
        )


def get_scan_status(service: str) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM scan_status WHERE service = ?", (service,)).fetchone()
        if not row:
            last_run = None
            if service == "s3":
                _, last_run = get_latest_s3_scan()
            elif service == "ec2":
                _, last_run = get_latest_ec2_scan()
            elif service == "lightsail":
                _, last_run = get_latest_lightsail_scan()
            elif service == "rds":
                _, last_run = get_latest_rds_scan()
            elif service == "datatransfer":
                _, last_run = get_latest_datatransfer_scan()

            return {
                "is_running": False,
                "last_run": last_run,
                "error": None,
                "message": f"No {service.upper()} scan run yet."
            }

        return {
            "is_running": bool(row["is_running"]),
            "last_run": row["last_run"],
            "error": row["error"],
            "message": row["message"]
        }


# ─────────────────────────────────────────────────────────────
# S3 helpers
# ─────────────────────────────────────────────────────────────

def insert_s3_scan(rows: list[dict], scanned_at: str | None = None):
    if not scanned_at:
        scanned_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO s3_scans
                (scanned_at, bucket, storage_class, object_count, size_bytes, status)
            VALUES
                (:scanned_at, :bucket, :storage_class, :object_count, :size_bytes, :status)
            """,
            [
                {
                    "scanned_at": scanned_at,
                    "bucket": r["bucket"],
                    "storage_class": r["storage_class"],
                    "object_count": r.get("object_count", 0),
                    "size_bytes": r.get("size_bytes", 0),
                    "status": r.get("status", "OK"),
                }
                for r in rows
            ],
        )
    logger.info(f"Inserted {len(rows)} S3 rows for scan at {scanned_at}")


def get_latest_s3_scan():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT scanned_at FROM s3_scans ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return [], None
        latest_ts = row["scanned_at"]
        rows = conn.execute(
            "SELECT * FROM s3_scans WHERE scanned_at = ?", (latest_ts,)
        ).fetchall()
        return [dict(r) for r in rows], latest_ts


def get_s3_scan_history():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT scanned_at FROM s3_scans ORDER BY id DESC"
        ).fetchall()
        return [r["scanned_at"] for r in rows]


# ─────────────────────────────────────────────────────────────
# EC2 helpers
# ─────────────────────────────────────────────────────────────

def insert_ec2_scan(rows: list[dict], scanned_at: str | None = None):
    if not scanned_at:
        scanned_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO ec2_scans (
                scanned_at, region, instance_id, instance_type, state, name,
                public_ip, private_ip, launch_time, platform, vpc_id, subnet_id,
                key_name, monitoring, availability_zone, ami_id, architecture,
                core_count, thread_per_core, hourly_cost, monthly_cost
            ) VALUES (
                :scanned_at, :region, :instance_id, :instance_type, :state, :name,
                :public_ip, :private_ip, :launch_time, :platform, :vpc_id, :subnet_id,
                :key_name, :monitoring, :availability_zone, :ami_id, :architecture,
                :core_count, :thread_per_core, :hourly_cost, :monthly_cost
            )
            """,
            [
                {
                    "scanned_at": scanned_at,
                    "hourly_cost": r.get("hourly_cost", 0.0),
                    "monthly_cost": r.get("monthly_cost", 0.0),
                    **r
                } for r in rows
            ],
        )
    logger.info(f"Inserted {len(rows)} EC2 rows for scan at {scanned_at}")


def get_latest_ec2_scan():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT scanned_at FROM ec2_scans ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return [], None
        latest_ts = row["scanned_at"]
        rows = conn.execute(
            "SELECT * FROM ec2_scans WHERE scanned_at = ?", (latest_ts,)
        ).fetchall()
        return [dict(r) for r in rows], latest_ts


def get_ec2_scan_history():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT scanned_at FROM ec2_scans ORDER BY id DESC"
        ).fetchall()
        return [r["scanned_at"] for r in rows]


# ─────────────────────────────────────────────────────────────
# Lightsail helpers
# ─────────────────────────────────────────────────────────────

def insert_lightsail_scan(rows: list[dict], scanned_at: str | None = None):
    if not scanned_at:
        scanned_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO lightsail_scans (
                scanned_at, region, name, arn, bundle_id, blueprint_id, state,
                public_ip, private_ip, created_at, availability_zone, cpu_count,
                ram_gb, disk_gb, monthly_transfer_gb, hourly_cost, monthly_cost
            ) VALUES (
                :scanned_at, :region, :name, :arn, :bundle_id, :blueprint_id, :state,
                :public_ip, :private_ip, :created_at, :availability_zone, :cpu_count,
                :ram_gb, :disk_gb, :monthly_transfer_gb, :hourly_cost, :monthly_cost
            )
            """,
            [
                {
                    "scanned_at": scanned_at,
                    "hourly_cost": r.get("hourly_cost", 0.0),
                    "monthly_cost": r.get("monthly_cost", 0.0),
                    "cpu_count": r.get("cpu_count", 1),
                    "ram_gb": r.get("ram_gb", 0.0),
                    "disk_gb": r.get("disk_gb", 0),
                    "monthly_transfer_gb": r.get("monthly_transfer_gb", 0),
                    **r
                } for r in rows
            ],
        )
    logger.info(f"Inserted {len(rows)} Lightsail rows for scan at {scanned_at}")


def get_latest_lightsail_scan():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT scanned_at FROM lightsail_scans ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return [], None
        latest_ts = row["scanned_at"]
        rows = conn.execute(
            "SELECT * FROM lightsail_scans WHERE scanned_at = ?", (latest_ts,)
        ).fetchall()
        return [dict(r) for r in rows], latest_ts


def get_lightsail_scan_history():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT scanned_at FROM lightsail_scans ORDER BY id DESC"
        ).fetchall()
        return [r["scanned_at"] for r in rows]


# ─────────────────────────────────────────────────────────────
# Data Transfer helpers
# ─────────────────────────────────────────────────────────────

def insert_datatransfer_scan(rows: list[dict], scanned_at: str | None = None):
    if not scanned_at:
        scanned_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO datatransfer_scans (
                scanned_at, service, resource_id, resource_name, region, state,
                inbound_gb, outbound_gb, allowance_gb, allowance_used_pct,
                overage_gb, egress_cost
            ) VALUES (
                :scanned_at, :service, :resource_id, :resource_name, :region, :state,
                :inbound_gb, :outbound_gb, :allowance_gb, :allowance_used_pct,
                :overage_gb, :egress_cost
            )
            """,
            [
                {
                    "scanned_at": scanned_at,
                    "inbound_gb": r.get("inbound_gb", 0.0),
                    "outbound_gb": r.get("outbound_gb", 0.0),
                    "allowance_gb": r.get("allowance_gb", 0.0),
                    "allowance_used_pct": r.get("allowance_used_pct", 0.0),
                    "overage_gb": r.get("overage_gb", 0.0),
                    "egress_cost": r.get("egress_cost", 0.0),
                    **r
                } for r in rows
            ],
        )
    logger.info(f"Inserted {len(rows)} Data Transfer rows for scan at {scanned_at}")


def get_latest_datatransfer_scan():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT scanned_at FROM datatransfer_scans ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return [], None
        latest_ts = row["scanned_at"]
        rows = conn.execute(
            "SELECT * FROM datatransfer_scans WHERE scanned_at = ?", (latest_ts,)
        ).fetchall()
        return [dict(r) for r in rows], latest_ts


def get_datatransfer_scan_history():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT scanned_at FROM datatransfer_scans ORDER BY id DESC"
        ).fetchall()
        return [r["scanned_at"] for r in rows]


# ─────────────────────────────────────────────────────────────
# RDS helpers
# ─────────────────────────────────────────────────────────────

def insert_rds_scan(rows: list[dict], scanned_at: str | None = None):
    if not scanned_at:
        scanned_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO rds_scans (
                scanned_at, region, db_instance_identifier, db_instance_class,
                engine, engine_version, status, allocated_storage_gb, storage_type,
                multi_az, endpoint, vpc_id, availability_zone, created_at,
                storage_encrypted, hourly_cost, monthly_cost
            ) VALUES (
                :scanned_at, :region, :db_instance_identifier, :db_instance_class,
                :engine, :engine_version, :status, :allocated_storage_gb, :storage_type,
                :multi_az, :endpoint, :vpc_id, :availability_zone, :created_at,
                :storage_encrypted, :hourly_cost, :monthly_cost
            )
            """,
            [
                {
                    "scanned_at": scanned_at,
                    "hourly_cost": r.get("hourly_cost", 0.0),
                    "monthly_cost": r.get("monthly_cost", 0.0),
                    "allocated_storage_gb": r.get("allocated_storage_gb", 20),
                    "multi_az": 1 if r.get("multi_az") else 0,
                    "storage_encrypted": 1 if r.get("storage_encrypted") else 0,
                    **r
                } for r in rows
            ],
        )
    logger.info(f"Inserted {len(rows)} RDS rows for scan at {scanned_at}")


def get_latest_rds_scan():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT scanned_at FROM rds_scans ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return [], None
        latest_ts = row["scanned_at"]
        rows = conn.execute(
            "SELECT * FROM rds_scans WHERE scanned_at = ?", (latest_ts,)
        ).fetchall()
        return [dict(r) for r in rows], latest_ts


def get_rds_scan_history():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT scanned_at FROM rds_scans ORDER BY id DESC"
        ).fetchall()
        return [r["scanned_at"] for r in rows]


# ─────────────────────────────────────────────────────────────
# Project Mapping Helpers
# ─────────────────────────────────────────────────────────────

def set_resource_project(resource_id: str, resource_type: str, project_name: str):
    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO resource_project_mappings (resource_id, resource_type, project_name, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(resource_id) DO UPDATE SET
                project_name = excluded.project_name,
                updated_at = excluded.updated_at
        """, (resource_id, resource_type, project_name, now_str))
        
        conn.execute("""
            INSERT INTO projects (name, description, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO NOTHING
        """, (project_name, "User assigned project", now_str))


def get_resource_projects() -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT resource_id, project_name FROM resource_project_mappings").fetchall()
        return {r["resource_id"]: r["project_name"] for r in rows}


def get_all_projects() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY name ASC").fetchall()
        return [dict(r) for r in rows]


def clear_all_scans():
    """Wipe all old scan records from SQLite database."""
    with get_conn() as conn:
        conn.execute("DELETE FROM s3_scans")
        conn.execute("DELETE FROM ec2_scans")
        conn.execute("DELETE FROM lightsail_scans")
        conn.execute("DELETE FROM rds_scans")
        conn.execute("DELETE FROM datatransfer_scans")
        conn.execute("DELETE FROM scan_status")
        try:
            conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('s3_scans', 'ec2_scans', 'lightsail_scans', 'rds_scans', 'datatransfer_scans')")
        except Exception:
            pass
    logger.info("Wiped all scan records from SQLite database.")


# ─────────────────────────────────────────────────────────────
# System Settings Helpers (Billing Overrides)
# ─────────────────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO system_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """, (key, str(value), now_str))


def get_all_settings() -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM system_settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def save_project_monthly_costs(records: list[dict]):
    """Save or update historical project monthly costs in SQLite."""
    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with get_conn() as conn:
        for r in records:
            conn.execute("""
                INSERT INTO project_monthly_costs 
                    (year_month, project_name, total_cost, s3_cost, ec2_cost, rds_cost, lightsail_cost, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(year_month, project_name) DO UPDATE SET
                    total_cost = excluded.total_cost,
                    s3_cost = excluded.s3_cost,
                    ec2_cost = excluded.ec2_cost,
                    rds_cost = excluded.rds_cost,
                    lightsail_cost = excluded.lightsail_cost,
                    updated_at = excluded.updated_at
            """, (
                r["year_month"], r["project_name"],
                float(r.get("total_cost") or 0.0), float(r.get("s3_cost") or 0.0),
                float(r.get("ec2_cost") or 0.0), float(r.get("rds_cost") or 0.0),
                float(r.get("lightsail_cost") or 0.0), now_str
            ))


def get_project_monthly_costs() -> list[dict]:
    """Retrieve all historical project monthly costs ordered by year_month ASC."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM project_monthly_costs ORDER BY year_month ASC, total_cost DESC").fetchall()
        return [dict(r) for r in rows]
