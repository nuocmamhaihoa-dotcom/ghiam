#!/usr/bin/env python3
"""Export VECD JSONL into PostgreSQL using DATABASE_URL.

Does not hardcode business rules — only loads versioned datasets.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("Insufficient Evidence: DATABASE_URL not set — export aborted.")
        sys.exit(2)
    try:
        import psycopg
    except ImportError:
        print("psycopg not installed. pip install psycopg[binary]")
        sys.exit(2)

    files = {
        "vecd_intents": ROOT / "intents" / "intents_1000.jsonl",
        "vecd_emotions": ROOT / "emotions" / "emotions_500.jsonl",
        "vecd_buying_signals": ROOT / "buying_signals" / "buying_signals_500.jsonl",
        "vecd_objections": ROOT / "objections" / "objections_5000.jsonl",
        "vecd_root_causes": ROOT / "root_causes" / "root_causes_500.jsonl",
        "vecd_golden_calls": ROOT / "golden_calls" / "golden_calls_500.jsonl",
        "vecd_rules": ROOT.parent / "ai-brain" / "rulebook" / "rules_1000.jsonl",
    }

    ddl = """
    CREATE TABLE IF NOT EXISTS vecd_import (
      id TEXT PRIMARY KEY,
      collection TEXT NOT NULL,
      payload JSONB NOT NULL,
      imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_vecd_collection ON vecd_import(collection);
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
            for collection, path in files.items():
                if not path.exists():
                    print(f"skip missing {path}")
                    continue
                n = 0
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    rid = str(row.get("id") or row.get("rule_id") or f"{collection}-{n}")
                    cur.execute(
                        """
                        INSERT INTO vecd_import(id, collection, payload)
                        VALUES (%s, %s, %s::jsonb)
                        ON CONFLICT (id) DO UPDATE
                        SET payload = EXCLUDED.payload, collection = EXCLUDED.collection, imported_at = NOW()
                        """,
                        (rid, collection, json.dumps(row, ensure_ascii=False)),
                    )
                    n += 1
                print(f"imported {collection}: {n}")
        conn.commit()
    print("export_postgres done")


if __name__ == "__main__":
    main()
