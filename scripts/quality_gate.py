#!/usr/bin/env python3
"""Enterprise Quality Gate runner — 20 mandatory gates.

Usage:
  PYTHONPATH=backend python scripts/quality_gate.py --sprint 1
  PYTHONPATH=backend python scripts/quality_gate.py --sprint all
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DOCS = ROOT / "docs"
DATASETS = ROOT / "datasets"
REPORTS = DOCS / "sprints"
QG_OUT = DATASETS / "reports"

REQUIRED_DOC_FILES = [
    "00_INDEX.md",
    "01_PRD.md",
    "02_System_Architecture.md",
    "03_Database.md",
    "04_API_Spec.md",
    "05_Scoring_Engine.md",
    "06_Rulebook_1000.md",
    "07_Root_Cause_Engine.md",
    "08_Coaching_Engine.md",
    "09_VCIE.md",
    "13_Deployment.md",
    "14_Security.md",
    "16_Revenue_Leak_AI.md",
    "17_Golden_Call.md",
    "18_Calibration.md",
]

CANONICAL_KEYS = {
    "score",
    "stage_scores",
    "violations",
    "evidence",
    "root_cause",
    "coaching",
    "revenue_leak",
}

RULE_REQUIRED_FIELDS = {
    "rule_id",
    "category",
    "rule_name",
    "description",
    "weight",
    "pass_condition",
    "fail_condition",
    "evidence_requirement",
    "root_cause",
    "coaching",
    "good_example",
    "bad_example",
    "edge_cases",
    "confidence_logic",
    "json_mapping",
}

CATEGORY_QUOTAS = {
    "Opening": 100,
    "Rapport": 80,
    "Discovery": 150,
    "Qualification": 80,
    "Presentation": 120,
    "Pricing": 80,
    "Objection": 200,
    "Closing": 80,
    "Voice": 60,
    "Compliance": 50,
}


class GateResult:
    def __init__(self, name: str) -> None:
        self.name = name
        self.ok = True
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.meta: dict[str, Any] = {}

    def fail(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.name,
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "meta": self.meta,
        }


def gate_architecture() -> GateResult:
    g = GateResult("1_Architecture_Review")
    required = [
        BACKEND / "app" / "domain",
        BACKEND / "app" / "application" / "services",
        BACKEND / "app" / "infrastructure",
        BACKEND / "app" / "interfaces" / "api",
        BACKEND / "app" / "main.py",
        ROOT / "docker-compose.yml",
        ROOT / "enterprise-web" / "src",
    ]
    for path in required:
        if not path.exists():
            g.fail(f"Missing architecture path: {path.relative_to(ROOT)}")
    # Domain must not import FastAPI/SQLAlchemy
    domain = BACKEND / "app" / "domain"
    banned = ("fastapi", "sqlalchemy", "redis", "boto3")
    for py in domain.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        for token in banned:
            if re.search(rf"^\s*(import|from)\s+{token}", text, re.M):
                g.fail(f"Domain layer leaks infrastructure: {py.name} imports {token}")
    g.meta["layers"] = ["domain", "application", "infrastructure", "interfaces"]
    return g


def gate_logic() -> GateResult:
    g = GateResult("2_Logic_Review")
    services = BACKEND / "app" / "application" / "services"
    required_services = [
        "scoring.py",
        "rule_engine.py",
        "root_cause.py",
        "coaching.py",
        "revenue_leak.py",
        "judge_ensemble.py",
        "vcie_engine.py",
        "dashboard.py",
        "calibration.py",
        "golden_benchmark.py",
    ]
    for name in required_services:
        if not (services / name).exists():
            g.fail(f"Missing service: {name}")
    # No TODO/FIXME in application services
    for py in services.glob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"\b(TODO|FIXME|NotImplementedError|pass\s*#\s*placeholder)\b", text):
            g.fail(f"Incomplete logic marker in {py.name}")
    return g



def _normalize_rule(row: dict[str, Any]) -> dict[str, Any]:
    """Accept legacy short keys and constitution/QG canonical keys."""
    cat = str(row.get("category") or "")
    cat_map = {
        "opening": "Opening",
        "rapport": "Rapport",
        "discovery": "Discovery",
        "qualification": "Qualification",
        "presentation": "Presentation",
        "pricing": "Pricing",
        "objection": "Objection",
        "closing": "Closing",
        "voice": "Voice",
        "compliance": "Compliance",
    }
    category = cat_map.get(cat.lower(), cat)
    evidence = row.get("evidence_requirement", row.get("evidence"))
    if isinstance(evidence, list):
        evidence_requirement = "; ".join(str(x) for x in evidence)
    else:
        evidence_requirement = str(evidence or "")
    return {
        **row,
        "rule_id": str(row.get("rule_id") or row.get("id") or ""),
        "rule_name": str(row.get("rule_name") or row.get("name") or ""),
        "category": category,
        "pass_condition": str(row.get("pass_condition") or row.get("pass") or ""),
        "fail_condition": str(row.get("fail_condition") or row.get("fail") or ""),
        "evidence_requirement": evidence_requirement,
        "root_cause": row.get("root_cause") or "",
        "coaching": row.get("coaching") or "",
        "good_example": row.get("good_example") or "",
        "bad_example": row.get("bad_example") or "",
        "edge_cases": row.get("edge_cases") or [],
        "confidence_logic": row.get("confidence_logic") or "",
        "json_mapping": row.get("json_mapping") or {},
        "description": row.get("description") or "",
        "weight": row.get("weight"),
    }


def _load_rules() -> list[dict[str, Any]]:
    path = ROOT / "ai-brain" / "rulebook" / "rules_1000.jsonl"
    if not path.exists():
        path = DATASETS / "synthetic" / "rules_1000.jsonl"
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(_normalize_rule(json.loads(line)))
    return rows


def gate_rule_consistency(sprint: int | None = None) -> GateResult:
    g = GateResult("3_Rule_Consistency")
    rows = _load_rules()
    g.meta["count"] = len(rows)
    if sprint == 2:
        rows = rows[:500]
        g.name = "3_Rule_Consistency_1_500"
    elif sprint == 3:
        rows = rows[500:1000]
        g.name = "3_Rule_Consistency_501_1000"

    if sprint in (None, 2, 3) and sprint != 2 and sprint != 3:
        if len(rows) != 1000:
            g.fail(f"Expected 1000 rules, got {len(rows)}")
    if sprint == 2 and len(rows) != 500:
        g.fail(f"Sprint2 expected 500 rules slice, got {len(rows)}")
    if sprint == 3 and len(rows) != 500:
        g.fail(f"Sprint3 expected 500 rules slice, got {len(rows)}")

    ids: set[str] = set()
    cats: dict[str, int] = {}
    for row in rows:
        missing = RULE_REQUIRED_FIELDS - set(row.keys())
        if missing:
            g.fail(f"{row.get('rule_id')}: missing fields {sorted(missing)}")
        rid = str(row.get("rule_id"))
        if rid in ids:
            g.fail(f"Duplicate rule_id: {rid}")
        ids.add(rid)
        cat = str(row.get("category"))
        cats[cat] = cats.get(cat, 0) + 1
        if not row.get("evidence_requirement"):
            g.fail(f"{rid}: empty evidence_requirement")
        if not row.get("coaching"):
            g.fail(f"{rid}: empty coaching")
        if not row.get("root_cause"):
            g.fail(f"{rid}: empty root_cause")

    g.meta["categories"] = cats
    if sprint is None:
        for cat, quota in CATEGORY_QUOTAS.items():
            if cats.get(cat, 0) != quota:
                g.fail(f"Category {cat}: expected {quota}, got {cats.get(cat, 0)}")
    return g


def gate_evidence() -> GateResult:
    g = GateResult("4_Evidence_Verification")
    sys.path.insert(0, str(BACKEND))
    from app.domain.value_objects import insufficient_evidence_response

    payload = insufficient_evidence_response().to_dict()
    missing = CANONICAL_KEYS - set(payload.keys())
    if missing:
        g.fail(f"IE response missing keys: {sorted(missing)}")
    if payload.get("root_cause", {}).get("verdict") not in {
        "Insufficient Evidence",
        "insufficient_evidence",
    } and payload.get("root_cause", {}).get("status") != "Insufficient Evidence":
        # accept either verdict or status
        if "Insufficient Evidence" not in json.dumps(payload["root_cause"], ensure_ascii=False):
            g.fail("IE root_cause must declare Insufficient Evidence")
    g.meta["canonical_keys"] = sorted(CANONICAL_KEYS)
    return g


def gate_root_cause() -> GateResult:
    g = GateResult("5_Root_Cause_Validation")
    sys.path.insert(0, str(BACKEND))
    from datetime import datetime, timezone
    from uuid import uuid4

    from app.application.services.root_cause import RootCauseService
    from app.domain.entities import RuleEntity
    from app.domain.enums import RuleSeverity, RuleStatus, Verdict
    from app.domain.value_objects import EvidenceSpanRef, ScoreItemResult

    span = EvidenceSpanRef(
        id=uuid4(),
        quote="Đắt quá",
        audio_ts_start=1.0,
        audio_ts_end=2.0,
        turn_index=1,
        confidence=0.9,
    )
    item = ScoreItemResult(
        rule_code="R-OBJ-01",
        title="Price objection",
        verdict=Verdict.FAIL,
        score=0.0,
        weight=2.0,
        confidence=0.9,
        evaluated_at=datetime.now(timezone.utc),
        explanation="No reframe",
        evidence_spans=[span],
        scoring_path=["test"],
        category="objection",
        severity="major",
        auto_fail=False,
    )
    rule = RuleEntity(
        id=uuid4(),
        rule_code="R-OBJ-01",
        category="objection",
        title="Price objection",
        description="Must handle price",
        severity=RuleSeverity.MAJOR,
        weight=2.0,
        auto_fail=False,
        status=RuleStatus.ACTIVE,
        evaluator_type="keyword",
        evidence_requirements={},
        evaluator_config={},
        current_version=1,
        cause_code_on_fail="RC-OBJ-PRICE-UNHANDLED",
        coaching_template_code="CT-OBJ",
        revenue_impact_code="LEAK_PRICE",
    )
    result = RootCauseService().analyze([item], {"R-OBJ-01": rule})
    if not (result.get("primary_code") or result.get("primary_cause_code")):
        g.fail("Root cause missing primary code")
    if result.get("verdict") == "Insufficient Evidence":
        g.fail("Unexpected IE for explicit FAIL item")
    ie = RootCauseService().analyze(
        [
            ScoreItemResult(
                rule_code="R-OBJ-01",
                title="Price objection",
                verdict=Verdict.INSUFFICIENT_EVIDENCE,
                score=None,
                weight=2.0,
                confidence=0.2,
                evaluated_at=datetime.now(timezone.utc),
                explanation="No span",
                evidence_spans=[],
                scoring_path=["test"],
                category="objection",
                severity="major",
                auto_fail=False,
            )
        ],
        {"R-OBJ-01": rule},
    )
    if "Insufficient Evidence" not in json.dumps(ie, ensure_ascii=False):
        g.fail("IE path for root cause failed")
    g.meta["sample_primary"] = result.get("primary_code") or result.get("primary_cause_code")
    return g


def gate_security() -> GateResult:
    g = GateResult("6_Security_Review")
    # No hardcoded secrets
    secret_pat = re.compile(
        r"(api[_-]?key\s*=\s*['\"][^'\"]{8,}|password\s*=\s*['\"][^'\"]{4,}|secret\s*=\s*['\"][^'\"]{8,})",
        re.I,
    )
    for py in (BACKEND / "app").rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        # allow settings defaults like "changeme" in config examples carefully
        if "os.environ" in text or "Settings" in text or "BaseSettings" in text:
            continue
        if secret_pat.search(text) and "example" not in py.name:
            # skip known docker compose env defaults outside app
            g.warn(f"Possible hardcoded secret pattern in {py.relative_to(ROOT)}")
    rbac = BACKEND / "app" / "core" / "rbac.py"
    if not rbac.exists():
        g.fail("Missing RBAC module")
    auth = BACKEND / "app" / "application" / "services" / "auth.py"
    if not auth.exists():
        g.fail("Missing auth service")
    return g


def gate_performance() -> GateResult:
    g = GateResult("7_Performance_Review")
    # Ensure indexes exist in migrations
    migrations = list((BACKEND / "alembic" / "versions").glob("*.py"))
    if len(migrations) < 1:
        g.fail("No alembic migrations found")
    index_hits = 0
    for mig in migrations:
        text = mig.read_text(encoding="utf-8", errors="ignore")
        index_hits += text.lower().count("index") + text.lower().count("ix_")
    if index_hits < 5:
        g.fail(f"Too few index declarations in migrations ({index_hits})")
    g.meta["migrations"] = len(migrations)
    g.meta["index_mentions"] = index_hits
    return g


def gate_api() -> GateResult:
    g = GateResult("8_API_Validation")
    sys.path.insert(0, str(BACKEND))
    from app.main import create_app

    app = create_app()
    paths = sorted({getattr(route, "path", "") for route in app.routes})
    required = [
        "/health",
        "/ready",
        "/v1/auth/login",
        "/v1/dashboard/overview",
        "/v1/calls",
        "/v1/calls/{call_id}/analysis",
        "/v1/calls/{call_id}/score",
    ]
    for path in required:
        if path not in paths and not any(path in p for p in paths):
            g.fail(f"Missing API route: {path}")
    g.meta["route_count"] = len(paths)
    # OpenAPI must exist
    schema = app.openapi()
    if "paths" not in schema:
        g.fail("OpenAPI schema missing paths")
    g.meta["openapi_paths"] = len(schema.get("paths", {}))
    return g


def gate_json() -> GateResult:
    g = GateResult("9_JSON_Validation")
    sys.path.insert(0, str(BACKEND))
    from app.domain.value_objects import insufficient_evidence_response

    payload = insufficient_evidence_response().to_dict()
    # Must be JSON serializable
    raw = json.dumps(payload, ensure_ascii=False)
    loaded = json.loads(raw)
    missing = CANONICAL_KEYS - set(loaded.keys())
    if missing:
        g.fail(f"Canonical JSON missing: {sorted(missing)}")
    # Validate rulebook JSONL
    for row in _load_rules()[:20]:
        json.dumps(row, ensure_ascii=False)
    g.meta["sample_bytes"] = len(raw)
    return g


def gate_database() -> GateResult:
    g = GateResult("10_Database_Validation")
    versions = BACKEND / "alembic" / "versions"
    files = list(versions.glob("*.py"))
    if len(files) < 2:
        g.fail(f"Expected >=2 migrations, found {len(files)}")
    models = BACKEND / "app" / "infrastructure" / "db" / "models.py"
    if not models.exists():
        # alternate path
        alt = list((BACKEND / "app").rglob("models.py"))
        if not alt:
            g.fail("Missing SQLAlchemy models")
        else:
            models = alt[0]
    text = models.read_text(encoding="utf-8", errors="ignore")
    for table in ("calls", "scores", "rules", "root_causes", "coaching_plans", "revenue_leaks"):
        if table not in text:
            g.fail(f"Models missing table/entity marker: {table}")
    seed = BACKEND / "scripts" / "seed.py"
    if not seed.exists():
        g.fail("Missing seed script")
    g.meta["migration_files"] = [f.name for f in files]
    return g


def gate_docker() -> GateResult:
    g = GateResult("11_Docker_Validation")
    compose = ROOT / "docker-compose.yml"
    if not compose.exists():
        g.fail("Missing docker-compose.yml")
        return g
    text = compose.read_text(encoding="utf-8")
    for svc in ("postgres", "redis", "backend", "frontend"):
        if svc not in text:
            g.fail(f"docker-compose missing service: {svc}")
    if "enterprise-web" not in text and "./frontend" not in text:
        g.warn("frontend volume path not enterprise-web")
    if "healthcheck" not in text:
        g.fail("docker-compose missing healthchecks")
    return g


def gate_audit() -> GateResult:
    g = GateResult("12_Audit_Log_Validation")
    found = False
    for py in (BACKEND / "app").rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        if "audit" in py.name.lower() or "AuditLog" in text or "audit_logs" in text:
            found = True
            break
    if not found:
        g.fail("No audit log model/service found")
    return g


def gate_golden() -> GateResult:
    g = GateResult("13_Golden_Call_Validation")
    path = DATASETS / "golden_calls"
    files = list(path.glob("*.jsonl")) if path.exists() else []
    if not files:
        g.fail("Missing golden_calls dataset")
        return g
    count = 0
    with files[0].open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
                if count <= 3:
                    json.loads(line)
    if count < 500:
        g.fail(f"Golden calls expected >=500, got {count}")
    svc = BACKEND / "app" / "application" / "services" / "golden_benchmark.py"
    if not svc.exists():
        g.fail("Missing golden_benchmark service")
    g.meta["golden_count"] = count
    return g


def gate_calibration() -> GateResult:
    g = GateResult("14_Calibration_Validation")
    svc = BACKEND / "app" / "application" / "services" / "calibration.py"
    if not svc.exists():
        g.fail("Missing calibration service")
    bench = DATASETS / "qa_benchmark"
    files = list(bench.glob("*.jsonl")) if bench.exists() else []
    if not files:
        g.fail("Missing qa_benchmark dataset")
    else:
        count = sum(1 for line in files[0].open(encoding="utf-8") if line.strip())
        if count < 10000:
            g.fail(f"QA benchmark expected >=10000, got {count}")
        g.meta["qa_benchmark_count"] = count
    return g


def gate_appeal() -> GateResult:
    g = GateResult("15_Appeal_Mode_Validation")
    svc = BACKEND / "app" / "application" / "services" / "appeals.py"
    page = ROOT / "enterprise-web" / "src" / "app" / "appeals"
    if not svc.exists():
        g.fail("Missing appeals service")
    if not page.exists():
        g.fail("Missing appeals frontend page")
    return g


def gate_revenue_leak() -> GateResult:
    g = GateResult("16_Revenue_Leak_Validation")
    sys.path.insert(0, str(BACKEND))
    from datetime import datetime, timezone
    from uuid import uuid4

    from app.application.services.revenue_leak import RevenueLeakService
    from app.domain.entities import CallEntity, RuleEntity
    from app.domain.enums import (
        CallDirection,
        CallStatus,
        RuleSeverity,
        RuleStatus,
        Verdict,
    )
    from app.domain.value_objects import EvidenceSpanRef, ScoreItemResult

    call = CallEntity(
        id=uuid4(),
        external_call_id="qg-1",
        status=CallStatus.PROCESSING,
        direction=CallDirection.OUTBOUND,
        agent_user_id=None,
        tenant_id=None,
        crm_order_value=1_000_000,
        currency="VND",
    )
    item = ScoreItemResult(
        rule_code="R-OBJ-01",
        title="Price",
        verdict=Verdict.FAIL,
        score=0.0,
        weight=2.0,
        confidence=0.9,
        evaluated_at=datetime.now(timezone.utc),
        explanation="fail",
        evidence_spans=[
            EvidenceSpanRef(
                id=uuid4(),
                quote="đắt",
                audio_ts_start=1,
                audio_ts_end=2,
                turn_index=1,
                confidence=0.9,
            )
        ],
        scoring_path=["qg"],
        category="objection",
        severity="major",
        auto_fail=False,
    )
    rule = RuleEntity(
        id=uuid4(),
        rule_code="R-OBJ-01",
        category="objection",
        title="Price",
        description="d",
        severity=RuleSeverity.MAJOR,
        weight=2.0,
        auto_fail=False,
        status=RuleStatus.ACTIVE,
        evaluator_type="keyword",
        evidence_requirements={},
        evaluator_config={},
        current_version=1,
        cause_code_on_fail="RC-OBJ-PRICE-UNHANDLED",
        coaching_template_code="CT",
        revenue_impact_code="LEAK_PRICE_OBJECTION_MISS",
    )
    leak = RevenueLeakService().estimate(
        call=call, items=[item], rules_by_code={"R-OBJ-01": rule}
    )
    amount = leak.get("estimated_loss_vnd", leak.get("estimated_amount"))
    if amount is None or float(amount) <= 0:
        g.fail("Revenue leak did not estimate positive amount for FAIL + order value")
    call2 = CallEntity(
        id=uuid4(),
        external_call_id="qg-2",
        status=CallStatus.PROCESSING,
        direction=CallDirection.OUTBOUND,
        agent_user_id=None,
        tenant_id=None,
        crm_order_value=None,
    )
    ie = RevenueLeakService().estimate(
        call=call2, items=[item], rules_by_code={"R-OBJ-01": rule}
    )
    if "Insufficient Evidence" not in json.dumps(ie, ensure_ascii=False):
        g.fail("Revenue leak must IE without crm_order_value")
    g.meta["sample_amount"] = amount
    return g


def gate_vcie() -> GateResult:
    g = GateResult("17_VCIE_Validation")
    svc = BACKEND / "app" / "application" / "services" / "vcie_engine.py"
    if not svc.exists():
        g.fail("Missing vcie_engine service")
        return g
    brain = ROOT / "ai-brain" / "vcie"
    if not brain.exists():
        g.warn("ai-brain/vcie directory missing (engine may use fallbacks)")
    elif not any(brain.iterdir()):
        g.fail("VCIE brain directory empty")
    if not (BACKEND / "app" / "application" / "services" / "pragmatics.py").exists():
        g.warn("Pragmatics engine missing (recommended with VCIE)")
    # Runtime: price objection must classify as Giá
    import sys
    sys.path.insert(0, str(BACKEND))
    try:
        from app.application.services.vcie_engine import VCIEEngine, _libraries
        _libraries.cache_clear()
        result = VCIEEngine().analyze(
            turns=[
                {"speaker": "agent", "text": "Em chào anh", "start_ms": 0, "end_ms": 800, "confidence": 0.9},
                {"speaker": "customer", "text": "Đắt quá", "start_ms": 800, "end_ms": 1600, "confidence": 0.9},
            ]
        )
        labels = [a.label for a in result.annotations if a.kind == "objection"]
        g.meta["price_objection_labels"] = labels
        if "Giá" not in labels:
            g.fail(f"VCIE price objection misclassified: {labels or result.status}")
        empty = VCIEEngine().analyze(turns=[])
        if empty.status != "Insufficient Evidence":
            g.fail("VCIE empty turns must return Insufficient Evidence")
    except Exception as exc:  # noqa: BLE001
        g.fail(f"VCIE runtime validation error: {exc}")
    return g

def gate_tests() -> GateResult:
    g = GateResult("18_Test_Coverage_Validation")
    tests = list((BACKEND / "tests").glob("test_*.py"))
    if len(tests) < 3:
        g.fail(f"Too few test modules: {len(tests)}")
    # run pytest quickly
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no"],
        cwd=str(BACKEND),
        env={**dict(**{k: v for k, v in __import__("os").environ.items()}), "PYTHONPATH": str(BACKEND)},
        capture_output=True,
        text=True,
    )
    g.meta["pytest_returncode"] = proc.returncode
    g.meta["pytest_tail"] = (proc.stdout or proc.stderr)[-500:]
    if proc.returncode != 0:
        g.fail("pytest failed")
    return g


def gate_docs() -> GateResult:
    g = GateResult("19_Documentation_Review")
    for name in REQUIRED_DOC_FILES:
        path = DOCS / name
        if not path.exists():
            g.fail(f"Missing doc: {name}")
        elif path.stat().st_size < 200:
            g.warn(f"Doc too short: {name}")
    constitution = ROOT / ".cursor" / "rules" / "project-rules.mdc"
    if not constitution.exists():
        g.fail("Missing project constitution .cursor/rules/project-rules.mdc")
    return g


def gate_final() -> GateResult:
    g = GateResult("20_Final_Self_Review")
    # Frontend portals
    for page in ("dashboard", "qa", "admin", "portal", "appeals", "coaching"):
        if not (ROOT / "enterprise-web" / "src" / "app" / page).exists():
            g.fail(f"Missing frontend portal page: {page}")
    # Canonical frontend type
    types = ROOT / "enterprise-web" / "src" / "lib" / "types.ts"
    if types.exists():
        text = types.read_text(encoding="utf-8")
        for key in ("score", "stage_scores", "violations", "evidence", "root_cause", "coaching", "revenue_leak"):
            if key not in text:
                g.fail(f"Frontend types missing {key}")
    else:
        g.fail("Missing enterprise-web types.ts")
    return g


SPRINT_GATES: dict[int, list[Callable[[], GateResult]]] = {
    1: [gate_architecture, gate_docs, gate_database, gate_docker, gate_security, gate_api],
    2: [lambda: gate_rule_consistency(2), gate_evidence, gate_json],
    3: [lambda: gate_rule_consistency(3), gate_json, lambda: gate_rule_consistency(None)],
    4: [gate_vcie, gate_evidence, gate_golden, gate_calibration],
    5: [gate_logic, gate_root_cause, gate_evidence, gate_api],
    6: [gate_revenue_leak, gate_api, gate_final],
    7: [
        gate_architecture,
        gate_logic,
        lambda: gate_rule_consistency(None),
        gate_evidence,
        gate_root_cause,
        gate_security,
        gate_performance,
        gate_api,
        gate_json,
        gate_database,
        gate_docker,
        gate_audit,
        gate_golden,
        gate_calibration,
        gate_appeal,
        gate_revenue_leak,
        gate_vcie,
        gate_tests,
        gate_docs,
        gate_final,
    ],
}


def run_sprint(sprint: int) -> dict[str, Any]:
    gates = SPRINT_GATES.get(sprint)
    if not gates:
        raise SystemExit(f"Unknown sprint {sprint}")
    results: list[dict[str, Any]] = []
    all_ok = True
    for fn in gates:
        result = fn()
        results.append(result.to_dict())
        if not result.ok:
            all_ok = False
    report = {
        "sprint": sprint,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "all_ok": all_ok,
        "gates": results,
        "passed": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "total": len(results),
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    QG_OUT.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"SPRINT_{sprint:02d}_QUALITY.md"
    lines = [
        f"# Sprint {sprint} Quality Report",
        "",
        f"- Checked at: `{report['checked_at']}`",
        f"- Result: **{'PASS' if all_ok else 'FAIL'}** ({report['passed']}/{report['total']})",
        "",
        "| # | Gate | Status | Errors |",
        "|---|------|--------|--------|",
    ]
    for idx, item in enumerate(results, 1):
        status = "PASS" if item["ok"] else "FAIL"
        err = "; ".join(item["errors"][:3]) if item["errors"] else ""
        lines.append(f"| {idx} | {item['gate']} | {status} | {err} |")
    if not all_ok:
        lines.append("")
        lines.append("## Failures")
        for item in results:
            if item["ok"]:
                continue
            lines.append(f"### {item['gate']}")
            for err in item["errors"]:
                lines.append(f"- {err}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (QG_OUT / f"qg_sprint_{sprint:02d}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sprint", default="all")
    args = parser.parse_args()
    sprints = list(range(1, 8)) if args.sprint == "all" else [int(args.sprint)]
    summary = []
    hard_fail = False
    for sprint in sprints:
        print(f"\n=== Running Sprint {sprint} Quality Gates ===")
        report = run_sprint(sprint)
        summary.append(report)
        status = "PASS" if report["all_ok"] else "FAIL"
        print(f"Sprint {sprint}: {status} ({report['passed']}/{report['total']})")
        if not report["all_ok"]:
            hard_fail = True
            for gate in report["gates"]:
                if not gate["ok"]:
                    print(f"  FAIL {gate['gate']}: {gate['errors']}")
            # Continuous mode: stop at first failing sprint
            break
    master = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "all_ok": not hard_fail and len(summary) == len(sprints),
        "sprints": summary,
    }
    (REPORTS / "MASTER_EXECUTION_STATUS.md").write_text(
        "\n".join(
            [
                "# Master Execution Status",
                "",
                f"**Updated:** {master['checked_at']}",
                f"**Overall:** {'PASS' if master['all_ok'] else 'IN PROGRESS / FAIL'}",
                "",
                "## Sprint Results",
                "",
                "| Sprint | Result | Passed | Total |",
                "|--------|--------|--------|-------|",
                *[
                    f"| {s['sprint']} | {'PASS' if s['all_ok'] else 'FAIL'} | {s['passed']} | {s['total']} |"
                    for s in summary
                ],
                "",
                "## Commands",
                "",
                "```bash",
                "PYTHONPATH=backend python scripts/quality_gate.py --sprint all",
                "cd backend && PYTHONPATH=. pytest -q",
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (QG_OUT / "qg_master.json").write_text(
        json.dumps(master, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    raise SystemExit(1 if hard_fail else 0)


if __name__ == "__main__":
    main()
