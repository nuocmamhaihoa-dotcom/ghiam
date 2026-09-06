#!/usr/bin/env python3
"""Validate audit evidence + emit gate summary (reports already generated)."""
from __future__ import annotations
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "reports" / "audit"
AUDIT = ROOT / "docs" / "audit"
required = [
    REPORTS / "phase1_repo_inventory.json",
    REPORTS / "phase_multi_evidence.json",
    REPORTS / "phase_pipeline_imports.json",
    AUDIT / "00_INDEX.md",
    ROOT / "docs" / "Audit_Repository.md",
    ROOT / "docs" / "Enterprise_Master_Audit_Executive_Report.md",
    AUDIT / "Global_Quality_Gate.json",
]
# flexible names
alts = {
    "phase1_repo_inventory.json": ["phase1_repo_inventory.json", "phase1_repository.json"],
    "phase_multi_evidence.json": ["phase_multi_evidence.json", "phase_multi_audit.json"],
    "phase_pipeline_imports.json": ["phase_pipeline_imports.json", "phase_pipeline_verification.json"],
    "00_INDEX.md": ["00_INDEX.md", "00_INDEX.md"],
    "Global_Quality_Gate.json": ["Global_Quality_Gate.json", "Global_Quality_Gate.json"],
}
missing = []
for req in required:
    if req.exists():
        continue
    name = req.name
    found = False
    for alt in alts.get(name, []):
        cand = req.parent / alt
        if cand.exists():
            found = True
            break
    # also search reports for any phase1*
    if not found and req.parent == REPORTS:
        if any(REPORTS.glob("phase1*.json")) and "phase1" in name:
            found = True
        if any(REPORTS.glob("phase_multi*.json")) and "multi" in name:
            found = True
        if any(REPORTS.glob("phase_pipeline*.json")) and "pipeline" in name:
            found = True
    if not found and req.parent == AUDIT:
        if name.startswith("00") and any(AUDIT.glob("*INDEX*")):
            found = True
        if "Quality_Gate" in name and any(AUDIT.glob("*Quality_Gate*")):
            found = True
    if not found:
        missing.append(str(req))
gate = None
for g in AUDIT.glob("*Quality_Gate*.json"):
    gate = json.loads(g.read_text())
    break
if gate is None:
    for g in REPORTS.glob("*executive*.json"):
        gate = json.loads(g.read_text())
        break
print("missing", missing or "none")
print("system_health", (gate or {}).get("system_health_score") or (gate or {}).get("system_health"))
print("launch_allowed", (gate or {}).get("launch_allowed"))
print("high_open", (gate or {}).get("high_open_ids") or (gate or {}).get("high_open"))
if missing:
    raise SystemExit(1)
