"""Seed roles, admin user, and rules from ai-brain/rulebook/rules_1000.jsonl."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

# Ensure backend root is on path when run as script
BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.rbac import RoleCode
from app.core.security import hash_password
from app.infrastructure.repositories.rule import SqlAlchemyRuleRepository
from app.infrastructure.repositories.user import SqlAlchemyUserRepository

ROLE_DEFS = [
    (RoleCode.ADMIN.value, "Administrator"),
    (RoleCode.QA_LEAD.value, "QA Lead"),
    (RoleCode.COACH.value, "Coach"),
    (RoleCode.AGENT.value, "Agent"),
    (RoleCode.VIEWER.value, "Viewer"),
]

DEFAULT_RULES = [
    {
        "rule_code": "R-OPEN-001",
        "category": "opening",
        "subcategory": "greeting",
        "title": "Chào khách hàng",
        "description": "Agent phải có lời chào lịch sự ở opening.",
        "severity": "major",
        "weight": 1.35,
        "auto_fail": False,
        "required": True,
        "status": "active",
        "evaluator_type": "keyword",
        "evidence_requirements": {
            "min_spans": 1,
            "speakers_allowed": ["agent"],
            "stage_keys": ["opening"],
            "slots": ["greeting_phrase"],
            "min_confidence": 0.7,
        },
        "evaluator_config": {
            "keywords": ["xin chào", "chào chị", "chào anh", "hello"],
            "positive_labels": ["has_greeting"],
            "applicability": {"directions": ["inbound", "outbound"]},
        },
        "cause_code_on_fail": "RC-OPEN-NO-GREET",
        "coaching_template_code": "CT-OPEN-GREET-01",
        "industries": ["*"],
    },
    {
        "rule_code": "R-OPEN-002",
        "category": "opening",
        "title": "Xưng tên agent",
        "description": "Agent phải xưng tên trong opening.",
        "severity": "major",
        "weight": 1.2,
        "auto_fail": False,
        "status": "active",
        "evaluator_type": "keyword",
        "evidence_requirements": {
            "min_spans": 1,
            "speakers_allowed": ["agent"],
            "stage_keys": ["opening"],
            "slots": ["agent_name"],
            "min_confidence": 0.7,
        },
        "evaluator_config": {
            "keywords": ["em tên", "tôi tên", "tên em là", "my name"],
            "applicability": {"directions": ["inbound", "outbound"]},
        },
        "cause_code_on_fail": "RC-OPEN-NO-NAME",
        "coaching_template_code": "CT-OPEN-NAME-01",
    },
    {
        "rule_code": "R-CLOS-001",
        "category": "closing",
        "title": "Chốt số lượng",
        "description": "Agent phải hỏi/chốt số lượng sản phẩm.",
        "severity": "critical",
        "weight": 1.8,
        "auto_fail": True,
        "status": "active",
        "evaluator_type": "keyword",
        "evidence_requirements": {
            "min_spans": 1,
            "speakers_allowed": ["agent"],
            "stage_keys": ["closing"],
            "slots": ["quantity"],
            "min_confidence": 0.7,
        },
        "evaluator_config": {
            "keywords": ["mấy hộp", "bao nhiêu", "chốt", "số lượng", "cái"],
        },
        "cause_code_on_fail": "RC-CLOSE-NO-ASK",
        "coaching_template_code": "CT-CLOSE-ASK-01",
        "revenue_impact_code": "REV-CLOSE-QTY",
    },
    {
        "rule_code": "R-COMP-001",
        "category": "compliance",
        "title": "Không cam kết quá lời",
        "description": "Cấm cam kết chữa bệnh / kết quả tuyệt đối.",
        "severity": "critical",
        "weight": 2.0,
        "auto_fail": True,
        "status": "active",
        "evaluator_type": "absence",
        "evidence_requirements": {
            "min_spans": 1,
            "speakers_allowed": ["agent"],
            "min_confidence": 0.6,
        },
        "evaluator_config": {
            "forbidden_keywords": ["chữa khỏi", "100%", "cam kết khỏi", "không tác dụng phụ"],
        },
        "cause_code_on_fail": "RC-COMP-OVERCLAIM",
        "coaching_template_code": "CT-COMP-CLAIM-01",
    },
    {
        "rule_code": "R-DISC-001",
        "category": "discovery",
        "title": "Hỏi nhu cầu",
        "description": "Agent phải hỏi nhu cầu/tình trạng khách.",
        "severity": "major",
        "weight": 1.3,
        "status": "active",
        "evaluator_type": "keyword",
        "evidence_requirements": {
            "min_spans": 1,
            "speakers_allowed": ["agent"],
            "stage_keys": ["discovery"],
            "min_confidence": 0.7,
        },
        "evaluator_config": {
            "keywords": ["anh chị đang", "nhu cầu", "tình trạng", "dùng gì"],
        },
        "cause_code_on_fail": "RC-DISC-NO-NEED",
    },
]


def find_rules_jsonl() -> Path | None:
    candidates = [
        REPO_ROOT / "ai-brain" / "rulebook" / "rules_1000.jsonl",
        Path("/ai-brain/rulebook/rules_1000.jsonl"),
        BACKEND_ROOT / "seed_data" / "rules_1000.jsonl",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_rule_payloads() -> list[dict]:
    path = find_rules_jsonl()
    if path is None:
        print("No rules_1000.jsonl found — seeding DEFAULT_RULES sample set.")
        return DEFAULT_RULES
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    print(f"Loaded {len(rows)} rules from {path}")
    return rows or DEFAULT_RULES


async def seed() -> None:
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        users = SqlAlchemyUserRepository(session)
        rules = SqlAlchemyRuleRepository(session)

        await users.ensure_roles(ROLE_DEFS)

        existing = await users.get_by_email(settings.seed_admin_email)
        if existing is None:
            await users.create(
                email=settings.seed_admin_email,
                full_name="AQATE Admin",
                password_hash=hash_password(settings.seed_admin_password),
                role_codes=[RoleCode.ADMIN.value],
                tenant_id=uuid4(),
            )
            print(f"Created admin user {settings.seed_admin_email}")
        else:
            print(f"Admin user already exists: {settings.seed_admin_email}")

        # Seed demo agent
        agent_email = "agent@aqate.local"
        if await users.get_by_email(agent_email) is None:
            await users.create(
                email=agent_email,
                full_name="Demo Agent",
                password_hash=hash_password("ChangeMeAgent123!"),
                role_codes=[RoleCode.AGENT.value],
            )
            print(f"Created agent user {agent_email}")

        payloads = load_rule_payloads()
        count = 0
        for payload in payloads:
            await rules.upsert_from_seed(payload)
            count += 1
            if count % 100 == 0:
                await session.commit()
                print(f"Upserted {count} rules...")
        await session.commit()
        print(f"Seed complete. Rules upserted: {count}")


if __name__ == "__main__":
    asyncio.run(seed())
