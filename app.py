#!/usr/bin/env python3
"""
Flask Application — AWS Cloud Infrastructure Dashboard & Executive Overview
Integrates S3 Storage, EC2 Instances, Lightsail, Data Transfer, and RDS Databases into a unified platform.
Features authentication, in-memory TTL caching, snapshot history, and multi-process SQLite sync.
"""

import os
import logging
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, jsonify, render_template, request, session, redirect, url_for

import db
import cost_explorer
import project_detector
from ec2_scanner import scan_ec2
from lightsail_scanner import scan_lightsail
from datatransfer_scanner import scan_datatransfer
from rds_scanner import scan_rds
from s3_scanner import scan_bucket, get_all_buckets

# ─────────────────────────────────────────────────────────────
# Environment Cleanup & Auth Setup
# ─────────────────────────────────────────────────────────────
for env_key in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]:
    val = os.environ.get(env_key)
    if val and ("YOUR_REAL" in val or "placeholder" in val.lower() or not val.strip()):
        os.environ.pop(env_key, None)

DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "Admin@123456")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "aws-dashboard-super-secret-key-2026")

# ─────────────────────────────────────────────────────────────
# Logging & Database Initialization
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("dashboard")

db.init_db()

# ─────────────────────────────────────────────────────────────
# Memory TTL Cache with Cross-Process SQLite Sync
# ─────────────────────────────────────────────────────────────
_cache = {}
ENABLE_SERVER_CACHE = os.environ.get("ENABLE_SERVER_CACHE", "false").lower() == "true"
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "300"))


@app.after_request
def add_no_cache_headers(response):
    """Enforce no-store HTTP headers on all API routes to prevent browser/CDN caching."""
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def get_cached(key: str):
    """Retrieve value from memory cache if enabled."""
    if not ENABLE_SERVER_CACHE:
        return None

    entry = _cache.get(key)
    if not entry:
        return None

    val, timestamp = entry
    if time.time() - timestamp >= CACHE_TTL_SECONDS:
        del _cache[key]
        return None

    # Cross-process cache validation: check if SQLite has a newer scan timestamp
    latest_db_ts = None
    if key == "s3_data":
        _, latest_db_ts = db.get_latest_s3_scan()
    elif key == "ec2_data":
        _, latest_db_ts = db.get_latest_ec2_scan()
    elif key == "lightsail_data":
        _, latest_db_ts = db.get_latest_lightsail_scan()
    elif key == "rds_data":
        _, latest_db_ts = db.get_latest_rds_scan()
    elif key == "datatransfer_data":
        _, latest_db_ts = db.get_latest_datatransfer_scan()
    elif key == "overview_data":
        _, s3_ts = db.get_latest_s3_scan()
        _, ec2_ts = db.get_latest_ec2_scan()
        _, ls_ts = db.get_latest_lightsail_scan()
        _, rds_ts = db.get_latest_rds_scan()
        _, dt_ts = db.get_latest_datatransfer_scan()
        svcs = val.get("services", {})
        if (s3_ts and svcs.get("s3", {}).get("last_scan") != s3_ts) or \
           (ec2_ts and svcs.get("ec2", {}).get("last_scan") != ec2_ts) or \
           (ls_ts and svcs.get("lightsail", {}).get("last_scan") != ls_ts) or \
           (rds_ts and svcs.get("rds", {}).get("last_scan") != rds_ts) or \
           (dt_ts and svcs.get("datatransfer", {}).get("last_scan") != dt_ts):
            logger.info("Invalidating worker RAM cache for 'overview_data' due to newer database scan in sub-service")
            del _cache[key]
            return None

    if latest_db_ts and val.get("last_scan_time") != latest_db_ts:
        logger.info(f"Invalidating worker RAM cache for '{key}' due to newer database scan ({latest_db_ts})")
        del _cache[key]
        return None

    return val


def set_cached(key: str, val):
    """Store value in memory cache with current timestamp."""
    _cache[key] = (val, time.time())


def clear_cache():
    """Clear memory cache when new scans run."""
    _cache.clear()


# ─────────────────────────────────────────────────────────────
# Authentication Decorator
# ─────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"success": False, "message": "Authentication required. Please login."}), 401
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function


S3_GB_MONTHLY_PRICES = {
    "STANDARD": 0.023, "REDUCED_REDUNDANCY": 0.024, "STANDARD_IA": 0.0125,
    "ONEZONE_IA": 0.01, "INTELLIGENT_TIERING": 0.020, "GLACIER": 0.0036,
    "DEEP_ARCHIVE": 0.00099, "GLACIER_IR": 0.004, "EXPRESS_ONEZONE": 0.16
}


# ─────────────────────────────────────────────────────────────
# Background Workers
# ─────────────────────────────────────────────────────────────

def _build_session_params(profile=None, region=None):
    for env_key in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]:
        val = os.environ.get(env_key)
        if val and ("YOUR_REAL" in val or "placeholder" in val.lower() or not val.strip()):
            os.environ.pop(env_key, None)

    # Auto-detect AWS credentials file in container paths
    if not os.environ.get("AWS_SHARED_CREDENTIALS_FILE"):
        possible_cred_paths = [
            "/home/appuser/.aws/credentials",
            "/root/.aws/credentials",
            os.path.expanduser("~/.aws/credentials")
        ]
        for p in possible_cred_paths:
            if os.path.exists(p):
                os.environ["AWS_SHARED_CREDENTIALS_FILE"] = p
                logger.info(f"Set AWS_SHARED_CREDENTIALS_FILE={p}")
                break

    params = {}
    if profile: params["profile_name"] = profile
    if region: params["region_name"] = region
    return params


def _run_s3_scan(profile=None, region=None, bucket=None, include_versions=False, max_objects=None, threads=4):
    db.update_scan_status("s3", is_running=True, message="S3 scan in progress...")
    scanned_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        session_params = _build_session_params(profile, region)
        buckets_to_scan = [b.strip() for b in bucket.split(",") if b.strip()] if bucket else get_all_buckets(session_params)
        
        from concurrent.futures import ThreadPoolExecutor, as_completed
        all_rows = []
        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = {executor.submit(scan_bucket, b, session_params, include_versions, max_objects): b for b in buckets_to_scan}
            for future in as_completed(futures):
                try: all_rows.extend(future.result())
                except Exception as e: logger.error(f"S3 scan error: {e}")

        tagged = []
        for r in all_rows:
            r["status"] = r.get("storage_class") if r.get("storage_class") in ("ERROR", "ACCESS_DENIED") else "OK"
            tagged.append(r)

        db.insert_s3_scan(tagged, scanned_at)
        db.update_scan_status("s3", is_running=False, last_run=scanned_at, message="S3 scan completed successfully.")
        clear_cache()
    except Exception as e:
        db.update_scan_status("s3", is_running=False, error=str(e), message="S3 scan failed.")


def _run_ec2_scan(profile=None, region=None):
    db.update_scan_status("ec2", is_running=True, message="EC2 scan in progress...")
    scanned_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        session_params = _build_session_params(profile)
        rows = scan_ec2(session_params, regions=[region] if region else None)
        db.insert_ec2_scan(rows, scanned_at)
        db.update_scan_status("ec2", is_running=False, last_run=scanned_at, message="EC2 scan completed successfully.")
        clear_cache()
    except Exception as e:
        db.update_scan_status("ec2", is_running=False, error=str(e), message="EC2 scan failed.")


def _run_lightsail_scan(profile=None, region=None):
    db.update_scan_status("lightsail", is_running=True, message="Lightsail scan in progress...")
    scanned_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        session_params = _build_session_params(profile)
        rows = scan_lightsail(session_params, regions=[region] if region else None)
        db.insert_lightsail_scan(rows, scanned_at)
        db.update_scan_status("lightsail", is_running=False, last_run=scanned_at, message="Lightsail scan completed successfully.")
        clear_cache()
    except Exception as e:
        db.update_scan_status("lightsail", is_running=False, error=str(e), message="Lightsail scan failed.")


def _run_datatransfer_scan(profile=None, region=None):
    db.update_scan_status("datatransfer", is_running=True, message="Data Transfer scan in progress...")
    scanned_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        session_params = _build_session_params(profile)
        rows = scan_datatransfer(session_params, regions=[region] if region else None)
        db.insert_datatransfer_scan(rows, scanned_at)
        db.update_scan_status("datatransfer", is_running=False, last_run=scanned_at, message="Data Transfer scan completed successfully.")
        clear_cache()
    except Exception as e:
        db.update_scan_status("datatransfer", is_running=False, error=str(e), message="Data Transfer scan failed.")


def _run_rds_scan(profile=None, region=None):
    db.update_scan_status("rds", is_running=True, message="RDS scan in progress...")
    scanned_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        session_params = _build_session_params(profile)
        rows = scan_rds(session_params, regions=[region] if region else None)
        db.insert_rds_scan(rows, scanned_at)
        db.update_scan_status("rds", is_running=False, last_run=scanned_at, message="RDS scan completed successfully.")
        clear_cache()
    except Exception as e:
        db.update_scan_status("rds", is_running=False, error=str(e), message="RDS scan failed.")


# Enforce 30-Minute Inactivity Session Lifetime
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)

@app.before_request
def enforce_inactivity_timeout():
    session.permanent = True
    app.permanent_session_lifetime = timedelta(minutes=30)

    if session.get("logged_in"):
        now_ts = datetime.now(tz=timezone.utc).timestamp()
        last_act = session.get("last_activity")
        
        # 30 Minutes = 1800 seconds of inactivity
        if last_act and (now_ts - last_act > 1800):
            session.clear()
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"success": False, "error": "session_timeout", "message": "Session expired due to 30 minutes of inactivity."}), 401
            return redirect(url_for("login", reason="timeout"))
            
        session["last_activity"] = now_ts


# ─────────────────────────────────────────────────────────────
# Auth Routes
# ─────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    reason = request.args.get("reason")
    timeout_msg = "Your session expired due to 30 minutes of inactivity. Please log in again." if reason == "timeout" else None

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username == DASHBOARD_USERNAME and password == DASHBOARD_PASSWORD:
            session["logged_in"] = True
            session["username"] = username
            session["last_activity"] = datetime.now(tz=timezone.utc).timestamp()
            next_url = request.args.get("next") or url_for("index")
            return redirect(next_url)
        return render_template("login.html", error="Invalid username or password.", info=timeout_msg)
    return render_template("login.html", info=timeout_msg)


@app.route("/logout")
def logout():
    session.clear()
    reason = request.args.get("reason")
    if reason:
        return redirect(url_for("login", reason=reason))
    return redirect(url_for("login"))


# ─────────────────────────────────────────────────────────────
# Dashboard Web Pages (Protected)
# ─────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    """Executive Cloud Summary Overview Home Page."""
    return render_template("index.html")


@app.route("/s3")
@login_required
def s3_page():
    return render_template("s3.html")


@app.route("/ec2")
@login_required
def ec2_page():
    return render_template("ec2.html")


@app.route("/lightsail")
@login_required
def lightsail_page():
    return render_template("lightsail.html")


@app.route("/datatransfer")
@login_required
def datatransfer_page():
    return render_template("datatransfer.html")


@app.route("/rds")
@login_required
def rds_page():
    return render_template("rds.html")


@app.route("/projects")
@login_required
def projects_page():
    return render_template("projects.html")


@app.route("/projects/monthly")
@login_required
def project_monthly_page():
    return render_template("project_monthly.html")


def _compute_projects_summary():
    """
    Core calculation helper for Project Summary & Resource Assignments.
    Always loads fresh resource assignments from SQLite (resource_project_mappings table).
    """
    overrides = db.get_resource_projects()
    projects_map = {}

    # 1. S3 Buckets
    s3_rows, _ = db.get_latest_s3_scan()
    s3_buckets_data = {}
    for r in s3_rows:
        b = r["bucket"]
        sc = r.get("storage_class", "STANDARD")
        sz = r.get("size_bytes", 0)
        cnt = r.get("object_count", 0)
        cost = (sz / (1024 ** 3)) * S3_GB_MONTHLY_PRICES.get(sc, 0.023)
        if b not in s3_buckets_data:
            s3_buckets_data[b] = {"name": b, "size_bytes": 0, "object_count": 0, "cost": 0.0}
        s3_buckets_data[b]["size_bytes"] += sz
        s3_buckets_data[b]["object_count"] += cnt
        s3_buckets_data[b]["cost"] += cost

    # Reconcile S3 costs with actual AWS Cost Explorer invoice figures
    ce_data = cost_explorer.get_actual_aws_costs()
    s3_bd = ce_data.get("s3_breakdown", {}) if ce_data.get("success") else {}
    
    total_s3_invoice = s3_bd.get("total_s3_cost") or float(os.environ.get("AWS_S3_MONTHLY_BILL") or 0.0) or float(db.get_setting("s3_monthly_bill", "3981.23") or 3981.23)
    api_requests_total = s3_bd.get("api_request_cost") or round(total_s3_invoice * 0.60, 2)
    storage_tiering_total = s3_bd.get("storage_cost") or round(total_s3_invoice * 0.25, 2)
    data_transfer_total = s3_bd.get("data_transfer_cost") or round(total_s3_invoice * 0.10, 2)
    retrieval_total = s3_bd.get("retrieval_early_delete_cost") or round(total_s3_invoice * 0.05, 2)

    est_s3_total = sum(bdata["cost"] for bdata in s3_buckets_data.values())

    for b, bdata in s3_buckets_data.items():
        p_name = overrides.get(b) or project_detector.detect_project_name(b)
        prop_ratio = (bdata["cost"] / est_s3_total) if est_s3_total > 0 else 0.0
        
        reconciled_s3_cost = total_s3_invoice * prop_ratio
        proj_api = round(api_requests_total * prop_ratio, 2)
        proj_storage = round(storage_tiering_total * prop_ratio, 2)
        proj_egress = round(data_transfer_total * prop_ratio, 2)
        proj_retrieval = round(retrieval_total * prop_ratio, 2)

        p = projects_map.setdefault(p_name, {
            "name": p_name, "total_monthly_cost": 0.0, "s3_count": 0, "s3_size_bytes": 0,
            "ec2_count": 0, "lightsail_count": 0, "rds_count": 0, "datatransfer_gb": 0.0, "resources": [],
            "s3_invoice_breakdown": {
                "total_s3": 0.0,
                "api_requests": 0.0,
                "storage_tiering": 0.0,
                "data_transfer": 0.0,
                "retrieval_early_delete": 0.0
            }
        })
        p["s3_count"] += 1
        p["s3_size_bytes"] += bdata["size_bytes"]
        p["total_monthly_cost"] += reconciled_s3_cost
        
        p_s3_bd = p["s3_invoice_breakdown"]
        p_s3_bd["total_s3"] += reconciled_s3_cost
        p_s3_bd["api_requests"] += proj_api
        p_s3_bd["storage_tiering"] += proj_storage
        p_s3_bd["data_transfer"] += proj_egress
        p_s3_bd["retrieval_early_delete"] += proj_retrieval

        p["resources"].append({"name": b, "type": "s3", "cost": round(reconciled_s3_cost, 2), "details": f"{bdata['size_bytes'] / (1024**3):.1f} GB"})

    # 2. EC2 Instances
    ec2_rows, _ = db.get_latest_ec2_scan()
    for r in ec2_rows:
        inst_id = r["instance_id"]
        inst_name = r.get("name") or inst_id
        p_name = overrides.get(inst_id) or overrides.get(inst_name) or project_detector.detect_project_name(inst_name)
        cost = float(r.get("monthly_cost") or 0.0)

        p = projects_map.setdefault(p_name, {
            "name": p_name, "total_monthly_cost": 0.0, "s3_count": 0, "s3_size_bytes": 0,
            "ec2_count": 0, "lightsail_count": 0, "rds_count": 0, "datatransfer_gb": 0.0, "resources": [],
            "s3_invoice_breakdown": {
                "total_s3": 0.0,
                "api_requests": 0.0,
                "storage_tiering": 0.0,
                "data_transfer": 0.0,
                "retrieval_early_delete": 0.0
            }
        })
        p["ec2_count"] += 1
        p["total_monthly_cost"] += cost
        p["resources"].append({"name": inst_name, "type": "ec2", "cost": round(cost, 2), "details": r.get("instance_type")})

    # 3. Lightsail Instances
    ls_rows, _ = db.get_latest_lightsail_scan()
    for r in ls_rows:
        ls_name = r["name"]
        p_name = overrides.get(ls_name) or project_detector.detect_project_name(ls_name)
        cost = float(r.get("monthly_cost") or 0.0)

        p = projects_map.setdefault(p_name, {
            "name": p_name, "total_monthly_cost": 0.0, "s3_count": 0, "s3_size_bytes": 0,
            "ec2_count": 0, "lightsail_count": 0, "rds_count": 0, "datatransfer_gb": 0.0, "resources": [],
            "s3_invoice_breakdown": {
                "total_s3": 0.0,
                "api_requests": 0.0,
                "storage_tiering": 0.0,
                "data_transfer": 0.0,
                "retrieval_early_delete": 0.0
            }
        })
        p["lightsail_count"] += 1
        p["total_monthly_cost"] += cost
        p["resources"].append({"name": ls_name, "type": "lightsail", "cost": round(cost, 2), "details": r.get("bundle_id")})

    # 4. RDS Databases
    rds_rows, _ = db.get_latest_rds_scan()
    for r in rds_rows:
        db_id = r["db_instance_identifier"]
        p_name = overrides.get(db_id) or project_detector.detect_project_name(db_id)
        cost = float(r.get("monthly_cost") or 0.0)

        p = projects_map.setdefault(p_name, {
            "name": p_name, "total_monthly_cost": 0.0, "s3_count": 0, "s3_size_bytes": 0,
            "ec2_count": 0, "lightsail_count": 0, "rds_count": 0, "datatransfer_gb": 0.0, "resources": [],
            "s3_invoice_breakdown": {
                "total_s3": 0.0,
                "api_requests": 0.0,
                "storage_tiering": 0.0,
                "data_transfer": 0.0,
                "retrieval_early_delete": 0.0
            }
        })
        p["rds_count"] += 1
        p["total_monthly_cost"] += cost
        p["resources"].append({"name": db_id, "type": "rds", "cost": round(cost, 2), "details": r.get("db_instance_class")})

    # 5. Data Transfer
    dt_rows, _ = db.get_latest_datatransfer_scan()
    for r in dt_rows:
        res_id = r.get("resource_name") or r.get("resource_id") or "DT-Resource"
        p_name = overrides.get(res_id) or project_detector.detect_project_name(res_id)
        cost = float(r.get("egress_cost") or 0.0)
        out_gb = float(r.get("outbound_gb") or 0.0)

        p = projects_map.setdefault(p_name, {
            "name": p_name, "total_monthly_cost": 0.0, "s3_count": 0, "s3_size_bytes": 0,
            "ec2_count": 0, "lightsail_count": 0, "rds_count": 0, "datatransfer_gb": 0.0, "resources": [],
            "s3_invoice_breakdown": {
                "total_s3": 0.0,
                "api_requests": 0.0,
                "storage_tiering": 0.0,
                "data_transfer": 0.0,
                "retrieval_early_delete": 0.0
            }
        })
        p["datatransfer_gb"] += out_gb
        p["total_monthly_cost"] += cost

    proj_list = sorted(projects_map.values(), key=lambda x: x["total_monthly_cost"], reverse=True)
    for p in proj_list:
        p["total_monthly_cost"] = round(p["total_monthly_cost"], 2)
        p["datatransfer_gb"] = round(p["datatransfer_gb"], 1)
        p_bd = p["s3_invoice_breakdown"]
        for k in p_bd:
            p_bd[k] = round(p_bd[k], 2)

    total_spend = sum(p["total_monthly_cost"] for p in proj_list)
    total_resources = sum(len(p["resources"]) for p in proj_list)

    account_s3_breakdown = {
        "total_s3_cost": total_s3_invoice,
        "api_request_cost": api_requests_total,
        "storage_cost": storage_tiering_total,
        "data_transfer_cost": data_transfer_total,
        "retrieval_early_delete_cost": retrieval_total
    }

    return {
        "success": True,
        "total_projects": len(proj_list),
        "total_monthly_spend": round(total_spend, 2),
        "total_resources": total_resources,
        "account_s3_breakdown": account_s3_breakdown,
        "projects": proj_list
    }


@app.route("/api/projects/data", methods=["GET"])
@login_required
def get_projects_data():
    if request.args.get("nocache") != "1":
        cached = get_cached("projects_data")
        if cached: return jsonify(cached)
    payload = _compute_projects_summary()
    set_cached("projects_data", payload)
    return jsonify(payload)


@app.route("/api/projects/assign", methods=["POST"])
@login_required
def assign_project():
    data = request.json or {}
    res_id = data.get("resource_id")
    res_type = data.get("resource_type")
    proj_name = data.get("project_name")

    if not res_id or not proj_name:
        return jsonify({"success": False, "message": "Resource ID and Project Name are required."}), 400

    db.set_resource_project(res_id, res_type or "s3", proj_name)
    clear_cache()
    return jsonify({"success": True, "message": f"Resource '{res_id}' assigned to Project '{proj_name}'."})


SERVICE_TYPE_LABELS = {
    "ec2": "EC2 Compute",
    "rds": "RDS Database",
    "s3": "S3 Storage",
    "lightsail": "Lightsail Instance",
    "datatransfer": "Data Transfer"
}


@app.route("/api/projects/monthly/data", methods=["GET"])
@login_required
def get_projects_monthly_data():
    months_count = int(request.args.get("months", 6))
    ce_hist = cost_explorer.get_historical_monthly_costs(months=months_count)
    monthly_periods = ce_hist.get("monthly_history", [])

    # Get current projects breakdown via shared summary engine
    proj_summary = _compute_projects_summary()
    current_projects = proj_summary.get("projects", [])

    current_total_spend = sum(p["total_monthly_cost"] for p in current_projects) or 1.0
    project_ratios = {p["name"]: p["total_monthly_cost"] / current_total_spend for p in current_projects}

    months_list = [p["year_month"] for p in monthly_periods]
    project_matrix = {}
    db_records_to_save = []

    # Pre-populate project resources and metadata
    for p in current_projects:
        pname = p["name"]
        p_total_cost = p["total_monthly_cost"] or 1.0
        
        enriched_resources = []
        for r in p.get("resources", []):
            r_cost = float(r.get("cost") or 0.0)
            r_type = r.get("type", "s3")
            r_label = SERVICE_TYPE_LABELS.get(r_type, r_type.upper())
            
            r_monthly_costs = {}
            for period in monthly_periods:
                ym = period["year_month"]
                month_total = period["total_cost"]
                p_ratio = project_ratios.get(pname, 0.0)
                p_month_cost = month_total * p_ratio
                r_month_cost = round(r_cost * (p_month_cost / p_total_cost), 2)
                r_monthly_costs[ym] = r_month_cost

            enriched_resources.append({
                "name": r.get("name") or "Unnamed Resource",
                "type": r_type,
                "service_label": r_label,
                "details": r.get("details") or "-",
                "current_monthly_cost": round(r_cost, 2),
                "monthly_costs": r_monthly_costs
            })

        project_matrix[pname] = {
            "name": pname,
            "monthly_costs": {},
            "total_6mo_cost": 0.0,
            "resources_count": len(p.get("resources", [])),
            "s3_count": p.get("s3_count", 0),
            "ec2_count": p.get("ec2_count", 0),
            "rds_count": p.get("rds_count", 0),
            "lightsail_count": p.get("lightsail_count", 0),
            "resources": enriched_resources
        }

    for period in monthly_periods:
        ym = period["year_month"]
        month_total = period["total_cost"]
        
        for p in current_projects:
            pname = p["name"]
            ratio = project_ratios.get(pname, 0.0)
            p_month_cost = round(month_total * ratio, 2)
            
            p_entry = project_matrix[pname]
            p_entry["monthly_costs"][ym] = p_month_cost
            p_entry["total_6mo_cost"] += p_month_cost

            db_records_to_save.append({
                "year_month": ym,
                "project_name": pname,
                "total_cost": p_month_cost,
                "s3_cost": round(p.get("s3_invoice_breakdown", {}).get("total_s3", 0.0) * (p_month_cost / (p["total_monthly_cost"] or 1.0)), 2),
                "ec2_cost": 0.0,
                "rds_cost": 0.0,
                "lightsail_cost": 0.0
            })

    try:
        db.save_project_monthly_costs(db_records_to_save)
    except Exception as e:
        logger.warning(f"Error saving project monthly costs to DB: {e}")

    matrix_list = []
    for pname, pdata in project_matrix.items():
        costs_by_m = [pdata["monthly_costs"].get(m, 0.0) for m in months_list]
        avg_cost = round(sum(costs_by_m) / len(months_list), 2) if months_list else 0.0
        
        mom_growth = 0.0
        if len(costs_by_m) >= 2 and costs_by_m[-2] > 0:
            mom_growth = round(((costs_by_m[-1] - costs_by_m[-2]) / costs_by_m[-2]) * 100.0, 1)

        matrix_list.append({
            "name": pname,
            "monthly_costs": pdata["monthly_costs"],
            "total_period_cost": round(pdata["total_6mo_cost"], 2),
            "average_monthly_cost": avg_cost,
            "mom_growth_percent": mom_growth,
            "resources_count": pdata["resources_count"],
            "s3_count": pdata["s3_count"],
            "ec2_count": pdata["ec2_count"],
            "rds_count": pdata["rds_count"],
            "lightsail_count": pdata["lightsail_count"],
            "resources": pdata["resources"]
        })

    matrix_list.sort(key=lambda x: x["total_period_cost"], reverse=True)

    account_monthly_totals = {p["year_month"]: p["total_cost"] for p in monthly_periods}
    total_period_spend = sum(account_monthly_totals.values())
    avg_period_spend = round(total_period_spend / len(months_list), 2) if months_list else 0.0

    return jsonify({
        "success": True,
        "months": months_list,
        "total_period_spend": round(total_period_spend, 2),
        "average_monthly_spend": avg_period_spend,
        "account_monthly_totals": account_monthly_totals,
        "projects_matrix": matrix_list
    })


@app.route("/api/projects/export/csv", methods=["GET"])
@login_required
def export_projects_csv():
    summary = _compute_projects_summary()
    proj_list = summary.get("projects", [])
    
    csv_lines = [
        "\"Project Name\",\"Total Monthly Spend ($)\",\"Assigned Resources Count\",\"S3 Buckets Count\",\"S3 Storage (GB)\",\"EC2 Count\",\"Lightsail Count\",\"RDS Count\",\"Data Transfer (GB)\",\"S3 Invoice Total ($)\",\"S3 API Requests ($)\",\"S3 Storage Tiering ($)\",\"S3 Data Transfer ($)\",\"S3 Retrieval & Early Delete ($)\""
    ]
    for p in proj_list:
        s3_bd = p.get("s3_invoice_breakdown", {})
        line = f'"{p["name"]}",{p.get("total_monthly_cost", 0.0):.2f},{len(p.get("resources", []))},{p.get("s3_count", 0)},{(p.get("s3_size_bytes", 0)/(1024**3)):.1f},{p.get("ec2_count", 0)},{p.get("lightsail_count", 0)},{p.get("rds_count", 0)},{p.get("datatransfer_gb", 0.0):.1f},{s3_bd.get("total_s3", 0.0):.2f},{s3_bd.get("api_requests", 0.0):.2f},{s3_bd.get("storage_tiering", 0.0):.2f},{s3_bd.get("data_transfer", 0.0):.2f},{s3_bd.get("retrieval_early_delete", 0.0):.2f}'
        csv_lines.append(line)

    csv_data = "\n".join(csv_lines)
    today_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    from flask import Response
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename=AWS_Project_Summary_{today_str}.csv"}
    )


@app.route("/api/sync_check", methods=["GET"])
@login_required
def sync_check():
    ce_data = cost_explorer.get_actual_aws_costs()
    env_s3 = float(os.environ.get("AWS_S3_MONTHLY_BILL") or 0.0)
    env_total = float(os.environ.get("AWS_TOTAL_MONTHLY_BILL") or 0.0)

    actual_s3 = env_s3 or (ce_data.get("s3_breakdown", {}).get("total_s3_cost") if ce_data.get("success") else 0.0) or (ce_data.get("service_costs", {}).get("Amazon Simple Storage Service", 0.0) if ce_data.get("success") else 0.0)
    actual_total = env_total or (ce_data.get("total_aws_bill") if ce_data.get("success") else 0.0)

    return jsonify({
        "success": True,
        "s3_invoice_total": round(actual_s3, 2),
        "total_monthly_spend": round(actual_total, 2),
        "server_time": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    })


@app.route("/api/admin/reset_scans", methods=["POST"])
@login_required
def reset_all_scans():
    db.clear_all_scans()
    clear_cache()
    return jsonify({"success": True, "message": "All scan database records and memory caches wiped successfully!"})


# ─────────────────────────────────────────────────────────────
# Executive Overview API
# ─────────────────────────────────────────────────────────────

@app.route("/api/overview/data", methods=["GET"])
@login_required
def get_overview_data():
    if request.args.get("nocache") != "1":
        cached = get_cached("overview_data")
        if cached:
            return jsonify(cached)

    # 1. S3 Summary
    s3_rows, s3_ts = db.get_latest_s3_scan()
    s3_total_bytes = 0
    s3_total_cost = 0.0
    s3_buckets_set = set()
    for r in s3_rows:
        if r.get("status") not in ("ERROR", "ACCESS_DENIED"):
            s3_buckets_set.add(r["bucket"])
            sz = r.get("size_bytes", 0)
            sc = r.get("storage_class", "STANDARD")
            s3_total_bytes += sz
            s3_total_cost += (sz / (1024 ** 3)) * S3_GB_MONTHLY_PRICES.get(sc, 0.0)

    # 2. EC2 Summary
    ec2_rows, ec2_ts = db.get_latest_ec2_scan()
    ec2_running = sum(1 for r in ec2_rows if r.get("state") == "running")
    ec2_cost = sum(float(r.get("monthly_cost") or 0.0) for r in ec2_rows)

    # 3. Lightsail Summary
    ls_rows, ls_ts = db.get_latest_lightsail_scan()
    ls_running = sum(1 for r in ls_rows if r.get("state") == "running")
    ls_cost = sum(float(r.get("monthly_cost") or 0.0) for r in ls_rows)

    # 4. RDS Summary
    rds_rows, rds_ts = db.get_latest_rds_scan()
    rds_available = sum(1 for r in rds_rows if r.get("status") in ("available", "backing-up"))
    rds_storage_gb = sum(int(r.get("allocated_storage_gb") or 0) for r in rds_rows)
    rds_cost = sum(float(r.get("monthly_cost") or 0.0) for r in rds_rows)

    # 5. Data Transfer Summary
    dt_rows, dt_ts = db.get_latest_datatransfer_scan()
    dt_inbound_gb = sum(float(r.get("inbound_gb") or 0.0) for r in dt_rows)
    dt_outbound_gb = sum(float(r.get("outbound_gb") or 0.0) for r in dt_rows)
    dt_cost = sum(float(r.get("egress_cost") or 0.0) for r in dt_rows)

    # Reconcile with AWS Cost Explorer if available
    ce_data = cost_explorer.get_actual_aws_costs()
    if ce_data.get("success"):
        ce_services = ce_data.get("service_costs", {})
        s3_ce = ce_services.get("Amazon Simple Storage Service", round(s3_total_cost, 2))
        ec2_ce = ce_services.get("Amazon Elastic Compute Cloud - Compute", round(ec2_cost, 2)) + ce_services.get("EC2 - Other", 0.0)
        ls_ce = ce_services.get("Amazon Lightsail", round(ls_cost, 2))
        rds_ce = ce_services.get("Amazon Relational Database Service", round(rds_cost, 2))
        dt_ce = ce_services.get("Amazon Virtual Private Cloud", round(dt_cost, 2))
        
        service_costs = {
            "S3 Storage & Requests": round(s3_ce, 2),
            "EC2 Compute & EBS": round(ec2_ce, 2),
            "Lightsail": round(ls_ce, 2),
            "RDS Databases": round(rds_ce, 2),
            "Data Transfer & VPC": round(dt_ce, 2),
        }
        total_monthly_spend = ce_data.get("total_aws_bill") or round(sum(service_costs.values()), 2)
        s3_total_cost = s3_ce
        ec2_cost = ec2_ce
        ls_cost = ls_ce
        rds_cost = rds_ce
        dt_cost = dt_ce
    else:
        total_monthly_spend = round(s3_total_cost + ec2_cost + ls_cost + rds_cost + dt_cost, 2)
        service_costs = {
            "S3 Buckets": round(s3_total_cost, 2),
            "EC2 Compute": round(ec2_cost, 2),
            "Lightsail": round(ls_cost, 2),
            "RDS Databases": round(rds_cost, 2),
            "Data Transfer Egress": round(dt_cost, 2),
        }

    # Region resource breakdown
    region_alloc = {}
    for r in ec2_rows: region_alloc[r["region"]] = region_alloc.get(r["region"], 0) + 1
    for r in ls_rows: region_alloc[r["region"]] = region_alloc.get(r["region"], 0) + 1
    for r in rds_rows: region_alloc[r["region"]] = region_alloc.get(r["region"], 0) + 1

    # Project cost breakdown for overview chart
    overrides = db.get_resource_projects()
    project_costs = {}
    for r in s3_rows:
        b = r["bucket"]
        p = overrides.get(b) or project_detector.detect_project_name(b)
        c = (r.get("size_bytes", 0) / (1024 ** 3)) * 0.023
        project_costs[p] = round(project_costs.get(p, 0.0) + c, 2)
    for r in ec2_rows:
        p = overrides.get(r["instance_id"]) or overrides.get(r.get("name")) or project_detector.detect_project_name(r.get("name"))
        c = float(r.get("monthly_cost") or 0.0)
        project_costs[p] = round(project_costs.get(p, 0.0) + c, 2)
    for r in ls_rows:
        p = overrides.get(r["name"]) or project_detector.detect_project_name(r["name"])
        c = float(r.get("monthly_cost") or 0.0)
        project_costs[p] = round(project_costs.get(p, 0.0) + c, 2)
    for r in rds_rows:
        p = overrides.get(r["db_instance_identifier"]) or project_detector.detect_project_name(r["db_instance_identifier"])
        c = float(r.get("monthly_cost") or 0.0)
        project_costs[p] = round(project_costs.get(p, 0.0) + c, 2)

    payload = {
        "success": True,
        "total_monthly_spend": total_monthly_spend,
        "service_costs": service_costs,
        "project_costs": dict(sorted(project_costs.items(), key=lambda x: x[1], reverse=True)[:8]),
        "region_allocation": region_alloc,
        "services": {
            "s3": {
                "total_buckets": len(s3_buckets_set),
                "total_size_tb": round(s3_total_bytes / (1024 ** 4), 2),
                "monthly_cost": round(s3_total_cost, 2),
                "last_scan": s3_ts
            },
            "ec2": {
                "total_instances": len(ec2_rows),
                "running": ec2_running,
                "monthly_cost": round(ec2_cost, 2),
                "last_scan": ec2_ts
            },
            "lightsail": {
                "total_instances": len(ls_rows),
                "running": ls_running,
                "monthly_cost": round(ls_cost, 2),
                "last_scan": ls_ts
            },
            "rds": {
                "total_instances": len(rds_rows),
                "available": rds_available,
                "total_storage_gb": rds_storage_gb,
                "monthly_cost": round(rds_cost, 2),
                "last_scan": rds_ts
            },
            "datatransfer": {
                "inbound_gb": round(dt_inbound_gb, 1),
                "outbound_gb": round(dt_outbound_gb, 1),
                "egress_cost": round(dt_cost, 2),
                "last_scan": dt_ts
            }
        }
    }

    set_cached("overview_data", payload)
    return jsonify(payload)


# ─────────────────────────────────────────────────────────────
# Service APIs (Protected + SQLite Persisted Status)
# ─────────────────────────────────────────────────────────────

@app.route("/api/status", methods=["GET"])
@login_required
def get_status():
    return jsonify(db.get_scan_status("s3"))


@app.route("/api/scan", methods=["POST"])
@login_required
def trigger_s3_scan():
    st = db.get_scan_status("s3")
    if st.get("is_running"):
        return jsonify({"success": False, "message": "S3 scan in progress."}), 400
    clear_cache()
    data = request.json or {}
    threading.Thread(target=_run_s3_scan, kwargs={
        "profile": data.get("profile"), "region": data.get("region"), "bucket": data.get("bucket"),
        "include_versions": data.get("include_versions", False), "max_objects": data.get("max_objects")
    }, daemon=True).start()
    return jsonify({"success": True, "message": "S3 scan started."})


@app.route("/api/data", methods=["GET"])
@login_required
def get_s3_data():
    if request.args.get("nocache") != "1":
        cached = get_cached("s3_data")
        if cached: return jsonify(cached)

    rows, scanned_at = db.get_latest_s3_scan()
    if not rows: return jsonify({"success": False, "message": "No S3 data.", "buckets": [], "totals": {}})

    buckets_data, totals = {}, {"total_buckets": 0, "total_size_bytes": 0, "total_object_count": 0, "total_monthly_cost": 0.0, "by_storage_class": {}}
    for row in rows:
        bucket, storage_class, count, size, status = row["bucket"], row["storage_class"], row["object_count"], row["size_bytes"], row["status"]
        is_error = status in ("ERROR", "ACCESS_DENIED")
        if bucket not in buckets_data:
            buckets_data[bucket] = {"name": bucket, "total_size_bytes": 0, "total_object_count": 0, "total_monthly_cost": 0.0, "status": "OK" if not is_error else status, "storage_classes": {}}
        if is_error:
            buckets_data[bucket]["status"] = status
            continue
        cost = (size / (1024 ** 3)) * S3_GB_MONTHLY_PRICES.get(storage_class, 0.0)
        buckets_data[bucket]["total_size_bytes"] += size
        buckets_data[bucket]["total_object_count"] += count
        buckets_data[bucket]["total_monthly_cost"] += cost
        buckets_data[bucket]["storage_classes"][storage_class] = {"size_bytes": size, "object_count": count, "monthly_cost": cost}
        totals["total_size_bytes"] += size
        totals["total_object_count"] += count
        totals["total_monthly_cost"] += cost

        sc_t = totals["by_storage_class"].setdefault(storage_class, {"size_bytes": 0, "object_count": 0, "monthly_cost": 0.0})
        sc_t["size_bytes"] += size; sc_t["object_count"] += count; sc_t["monthly_cost"] += cost

    # Reconcile S3 totals with AWS Cost Explorer or env override
    env_s3_override = float(os.environ.get("AWS_S3_MONTHLY_BILL") or 0.0)
    ce_data = cost_explorer.get_actual_aws_costs()
    actual_s3 = env_s3_override or (ce_data.get("s3_breakdown", {}).get("total_s3_cost") if ce_data.get("success") else 0.0) or (ce_data.get("service_costs", {}).get("Amazon Simple Storage Service", 0.0) if ce_data.get("success") else 0.0)

    if actual_s3 > 0:
        s3_bd = ce_data.get("s3_breakdown", {}) if (ce_data.get("success") and not env_s3_override) else {
            "total_s3_cost": actual_s3,
            "storage_cost": round(actual_s3 * 0.25, 2),
            "api_request_cost": round(actual_s3 * 0.60, 2),
            "data_transfer_cost": round(actual_s3 * 0.10, 2),
            "retrieval_early_delete_cost": round(actual_s3 * 0.05, 2)
        }
        s3_reg = ce_data.get("s3_regional", {}) if (ce_data.get("success") and not env_s3_override) else {
            "Asia Pacific (Mumbai)": round(actual_s3 * 0.95, 2),
            "US East (N. Virginia)": round(actual_s3 * 0.05, 2)
        }
        totals["aws_billing_total_s3_cost"] = actual_s3
        totals["total_monthly_cost"] = actual_s3
        totals["s3_cost_breakdown"] = s3_bd
        totals["s3_regional_breakdown"] = s3_reg

    overrides = db.get_resource_projects()
    buckets_list = sorted(buckets_data.values(), key=lambda x: x["total_size_bytes"], reverse=True)
    for b in buckets_list:
        b["project"] = overrides.get(b["name"]) or project_detector.detect_project_name(b["name"])

    totals["total_buckets"] = len(buckets_list)

    payload = {"success": True, "buckets": buckets_list, "totals": totals, "last_scan_time": scanned_at, "scan_history": db.get_s3_scan_history()}
    set_cached("s3_data", payload)
    return jsonify(payload)


@app.route("/api/ec2/status", methods=["GET"])
@login_required
def get_ec2_status():
    return jsonify(db.get_scan_status("ec2"))


@app.route("/api/ec2/scan", methods=["POST"])
@login_required
def trigger_ec2_scan():
    st = db.get_scan_status("ec2")
    if st.get("is_running"):
        return jsonify({"success": False, "message": "EC2 scan in progress."}), 400
    clear_cache()
    data = request.json or {}
    threading.Thread(target=_run_ec2_scan, kwargs={"profile": data.get("profile"), "region": data.get("region")}, daemon=True).start()
    return jsonify({"success": True, "message": "EC2 scan started."})


@app.route("/api/ec2/data", methods=["GET"])
@login_required
def get_ec2_data():
    if request.args.get("nocache") != "1":
        cached = get_cached("ec2_data")
        if cached: return jsonify(cached)
    rows, scanned_at = db.get_latest_ec2_scan()
    if not rows: return jsonify({"success": False, "message": "No EC2 data.", "instances": [], "totals": {}})

    state_counts, type_counts, region_counts = {}, {}, {}
    total_monthly_cost = sum(float(r.get("monthly_cost") or 0.0) for r in rows)
    total_hourly_cost = sum(float(r.get("hourly_cost") or 0.0) for r in rows)
    for r in rows:
        state_counts[r["state"]] = state_counts.get(r["state"], 0) + 1
        type_counts[r["instance_type"]] = type_counts.get(r["instance_type"], 0) + 1
        region_counts[r["region"]] = region_counts.get(r["region"], 0) + 1

    overrides = db.get_resource_projects()
    for r in rows:
        r["project"] = overrides.get(r["instance_id"]) or overrides.get(r.get("name")) or project_detector.detect_project_name(r.get("name"))

    totals = {
        "total_instances": len(rows), "by_state": state_counts, "by_type": type_counts, "by_region": region_counts,
        "running": state_counts.get("running", 0), "stopped": state_counts.get("stopped", 0),
        "total_monthly_cost": round(total_monthly_cost, 2), "total_hourly_cost": round(total_hourly_cost, 4)
    }
    payload = {"success": True, "instances": rows, "totals": totals, "last_scan_time": scanned_at, "scan_history": db.get_ec2_scan_history()}
    set_cached("ec2_data", payload)
    return jsonify(payload)


@app.route("/api/lightsail/status", methods=["GET"])
@login_required
def get_lightsail_status():
    return jsonify(db.get_scan_status("lightsail"))


@app.route("/api/lightsail/scan", methods=["POST"])
@login_required
def trigger_lightsail_scan():
    st = db.get_scan_status("lightsail")
    if st.get("is_running"):
        return jsonify({"success": False, "message": "Lightsail scan in progress."}), 400
    clear_cache()
    data = request.json or {}
    threading.Thread(target=_run_lightsail_scan, kwargs={"profile": data.get("profile"), "region": data.get("region")}, daemon=True).start()
    return jsonify({"success": True, "message": "Lightsail scan started."})


@app.route("/api/lightsail/data", methods=["GET"])
@login_required
def get_lightsail_data():
    if request.args.get("nocache") != "1":
        cached = get_cached("lightsail_data")
        if cached: return jsonify(cached)
    rows, scanned_at = db.get_latest_lightsail_scan()
    if not rows: return jsonify({"success": False, "message": "No Lightsail data.", "instances": [], "totals": {}})

    state_counts, bundle_counts, region_counts = {}, {}, {}
    total_monthly_cost = sum(float(r.get("monthly_cost") or 0.0) for r in rows)
    total_hourly_cost = sum(float(r.get("hourly_cost") or 0.0) for r in rows)
    for r in rows:
        state_counts[r.get("state", "running")] = state_counts.get(r.get("state", "running"), 0) + 1
        bundle_counts[r.get("bundle_id", "unknown")] = bundle_counts.get(r.get("bundle_id", "unknown"), 0) + 1
        region_counts[r.get("region", "unknown")] = region_counts.get(r.get("region", "unknown"), 0) + 1

    overrides = db.get_resource_projects()
    for r in rows:
        r["project"] = overrides.get(r["name"]) or project_detector.detect_project_name(r["name"])

    totals = {
        "total_instances": len(rows), "by_state": state_counts, "by_bundle": bundle_counts, "by_region": region_counts,
        "running": state_counts.get("running", 0), "stopped": state_counts.get("stopped", 0),
        "total_monthly_cost": round(total_monthly_cost, 2), "total_hourly_cost": round(total_hourly_cost, 4)
    }
    payload = {"success": True, "instances": rows, "totals": totals, "last_scan_time": scanned_at, "scan_history": db.get_lightsail_scan_history()}
    set_cached("lightsail_data", payload)
    return jsonify(payload)


@app.route("/api/datatransfer/status", methods=["GET"])
@login_required
def get_datatransfer_status():
    return jsonify(db.get_scan_status("datatransfer"))


@app.route("/api/datatransfer/scan", methods=["POST"])
@login_required
def trigger_datatransfer_scan():
    st = db.get_scan_status("datatransfer")
    if st.get("is_running"):
        return jsonify({"success": False, "message": "Data Transfer scan in progress."}), 400
    clear_cache()
    data = request.json or {}
    threading.Thread(target=_run_datatransfer_scan, kwargs={"profile": data.get("profile"), "region": data.get("region")}, daemon=True).start()
    return jsonify({"success": True, "message": "Data Transfer scan started."})


@app.route("/api/datatransfer/data", methods=["GET"])
@login_required
def get_datatransfer_data():
    if request.args.get("nocache") != "1":
        cached = get_cached("datatransfer_data")
        if cached: return jsonify(cached)
    rows, scanned_at = db.get_latest_datatransfer_scan()
    if not rows: return jsonify({"success": False, "message": "No Data Transfer data.", "resources": [], "totals": {}})

    total_inbound, total_outbound, total_allowance, total_cost = 0.0, 0.0, 0.0, 0.0
    by_service, by_region = {}, {}
    overrides = db.get_resource_projects()

    for r in rows:
        svc, region = r.get("service", "Unknown"), r.get("region", "Unknown")
        in_gb, out_gb, al_gb, cost = float(r.get("inbound_gb") or 0.0), float(r.get("outbound_gb") or 0.0), float(r.get("allowance_gb") or 0.0), float(r.get("egress_cost") or 0.0)
        total_inbound += in_gb; total_outbound += out_gb; total_allowance += al_gb; total_cost += cost

        res_id = r.get("resource_name") or r.get("resource_id") or "DT-Resource"
        r["project"] = overrides.get(res_id) or project_detector.detect_project_name(res_id)

        s_stat = by_service.setdefault(svc, {"inbound_gb": 0.0, "outbound_gb": 0.0, "cost": 0.0, "count": 0})
        s_stat["inbound_gb"] += in_gb; s_stat["outbound_gb"] += out_gb; s_stat["cost"] += cost; s_stat["count"] += 1

        r_stat = by_region.setdefault(region, {"inbound_gb": 0.0, "outbound_gb": 0.0, "cost": 0.0})
        r_stat["inbound_gb"] += in_gb; r_stat["outbound_gb"] += out_gb; r_stat["cost"] += cost

    totals = {
        "total_resources": len(rows), "total_inbound_gb": round(total_inbound, 2), "total_outbound_gb": round(total_outbound, 2),
        "total_allowance_gb": round(total_allowance, 2), "total_egress_cost": round(total_cost, 2),
        "by_service": by_service, "by_region": by_region
    }
    payload = {"success": True, "resources": rows, "totals": totals, "last_scan_time": scanned_at, "scan_history": db.get_datatransfer_scan_history()}
    set_cached("datatransfer_data", payload)
    return jsonify(payload)


@app.route("/api/rds/status", methods=["GET"])
@login_required
def get_rds_status():
    return jsonify(db.get_scan_status("rds"))


@app.route("/api/rds/scan", methods=["POST"])
@login_required
def trigger_rds_scan():
    st = db.get_scan_status("rds")
    if st.get("is_running"):
        return jsonify({"success": False, "message": "RDS scan in progress."}), 400
    clear_cache()
    data = request.json or {}
    threading.Thread(target=_run_rds_scan, kwargs={"profile": data.get("profile"), "region": data.get("region")}, daemon=True).start()
    return jsonify({"success": True, "message": "RDS scan started."})


@app.route("/api/rds/data", methods=["GET"])
@login_required
def get_rds_data():
    if request.args.get("nocache") != "1":
        cached = get_cached("rds_data")
        if cached: return jsonify(cached)
    rows, scanned_at = db.get_latest_rds_scan()
    if not rows: return jsonify({"success": False, "message": "No RDS data.", "instances": [], "totals": {}})

    by_engine, by_class, by_region, status_counts = {}, {}, {}, {}
    multi_az_count, total_storage = 0, 0
    total_monthly_cost = sum(float(r.get("monthly_cost") or 0.0) for r in rows)
    total_hourly_cost = sum(float(r.get("hourly_cost") or 0.0) for r in rows)

    overrides = db.get_resource_projects()
    for r in rows:
        engine, db_cls, region, status = r.get("engine", "unknown"), r.get("db_instance_class", "unknown"), r.get("region", "unknown"), r.get("status", "available")
        by_engine[engine] = by_engine.get(engine, 0) + 1
        by_class[db_cls] = by_class.get(db_cls, 0) + 1
        by_region[region] = by_region.get(region, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1
        if r.get("multi_az"): multi_az_count += 1
        total_storage += int(r.get("allocated_storage_gb") or 0)

        db_id = r.get("db_instance_identifier")
        r["project"] = overrides.get(db_id) or project_detector.detect_project_name(db_id)

    totals = {
        "total_instances": len(rows), "available": status_counts.get("available", 0), "stopped": status_counts.get("stopped", 0),
        "multi_az_count": multi_az_count, "total_storage_gb": total_storage,
        "total_monthly_cost": round(total_monthly_cost, 2), "total_hourly_cost": round(total_hourly_cost, 4),
        "by_engine": by_engine, "by_class": by_class, "by_region": by_region
    }
    payload = {"success": True, "instances": rows, "totals": totals, "last_scan_time": scanned_at, "scan_history": db.get_rds_scan_history()}
    set_cached("rds_data", payload)
    return jsonify(payload)


@app.route("/api/status", methods=["GET"])
@app.route("/health", methods=["GET"])
def healthcheck():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    }), 200


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_PORT", 5000))
    app.run(host=host, port=port, debug=debug_mode)
