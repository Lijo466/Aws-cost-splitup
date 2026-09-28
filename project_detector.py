#!/usr/bin/env python3
"""
Project Detector Module — Smart rule-based project inferencing and tag parsing.
Maps AWS S3 Buckets, EC2 Instances, Lightsail Instances, RDS Databases, and Data Transfer resources to Projects.
"""

import re

KNOWN_PROJECT_PATTERNS = [
    (r"pfizer", "Pfizer"),
    (r"simtra", "Simtra"),
    (r"thermo", "Thermo Fisher"),
    (r"itac", "ITAC Platform"),
    (r"visionbox", "Vision Box"),
    (r"kfc", "KFC Operations"),
    (r"atc", "ATC Logistics"),
    (r"(intel|cafe|aivi)", "Intel Cafe & AIVI"),
    (r"mrf", "MRF Retail"),
    (r"sentry", "Sentry Core"),
    (r"(labeling|label)", "AI Image Labeling"),
    (r"vdi", "Custom VDI"),
    (r"drumstick", "Drumstick Dashboard"),
    (r"vault", "Common Vault"),
    (r"logging", "System Logging"),
]


def detect_project_name(resource_name: str, tags: dict = None) -> str:
    """
    Infers project name from AWS Resource Tags or Resource Name patterns.
    """
    # 1. Check explicit AWS Tags if available
    if tags and isinstance(tags, dict):
        for key in ["Project", "project", "PROJECT", "Environment", "Application"]:
            val = tags.get(key)
            if val and str(val).strip():
                return str(val).strip().title()

    if not resource_name:
        return "Core / Shared"

    name_lower = resource_name.lower()

    # 2. Pattern Matching against known project keys
    for pattern, proj_name in KNOWN_PROJECT_PATTERNS:
        if re.search(pattern, name_lower):
            return proj_name

    return "Core / Shared"
