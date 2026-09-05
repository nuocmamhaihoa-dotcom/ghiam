#!/usr/bin/env python3
"""Export VECD JSONL into PostgreSQL tables.

Uses DATABASE_URL when available. Always refreshes datasets/sql/vecd_seed.sql sample.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql"

COLLECTIONS = [
    ("customers", "vecd_utterances"),
    ("agents", "vecd_utterances"),
    ("conversations", "vecd_conversations"),
    ("intents", "vecd_intents"),
    ("objections", "vecd_objections"),
    ("emotions", "vecd_emotions"),
    ("buying_signals", "vecd_buying_signals"),
    ("root_causes", "vecd_root_causes"),
    ("qa_benchmark", "vecd_qa_scores"),
    ("golden_calls", "vecd_golden_calls"),
]


def latest(folder: Path, prefix: str) -> Path | None:
    """Pick the largest corpus by line count (not lexicographic name)."""
    files = list(folder.glob(f"{prefix}_*.jsonl"))
    if not files:
        return None

    def line_count(path: Path) -> int:
        with path.open(encoding="utf-8") as fh:
            return sum(1 for _ in fh)

    return max(files, key=line_count)


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


DDL = """
CREATE TABLE IF NOT EXISTS vecd_conversations (
  id TEXT PRIMARY KEY,
  payload JSONB NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS vecd_utterances (
  id TEXT PRIMARY KEY,
  speaker TEXT,
  payload JSONB NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS vecd_intents (
  id TEXT PRIMARY KEY,
  payload JSONB NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS vecd_objections (
  id TEXT PRIMARY KEY,
  payload JSONB NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS vecd_emotions (
  id TEXT PRIMARY KEY,
  payload JSONB NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS vecd_buying_signals (
  id TEXT PRIMARY KEY,
  payload JSONB NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS vecd_root_causes (
  id TEXT PRIMARY KEY,
  payload JSONB NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS vecd_qa_scores (
  id TEXT PRIMARY KEY,
  payload JSONB NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS vecd_golden_calls (
  id TEXT PRIMARY KEY,
  payload JSONB NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vecd_utt_speaker ON vecd_utterances(speaker);
"""


def refresh_sql_sample() -> Path:
    SQL_DIR.mkdir(parents=True, exist_ok=True)
    path = SQL_DIR / "vecd_seed.sql"
    lines = ["-- VECD seed sample (first 100 rows / collection)", DDL, ""]
    for name, table in COLLECTIONS:
        fp = latest(ROOT / name, name)
        if not fp:
            continue
        rows = load_jsonl(fp)[:100]
        for row in rows:
            rid = str(row["id"]).replace("'", "''")
            payload = json.dumps(row, ensure_ascii=False).replace("'", "''")
            if table == "vecd_utterances":
                speaker = "customer" if name == "customers" else "agent"
                lines.append(
                    f"INSERT INTO vecd_utterances(id, speaker, payload) VALUES ('{rid}', '{speaker}', '{payload}'::jsonb) "
                    f"ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload;"
                )
            else:
                lines.append(
                    f"INSERT INTO {table}(id, payload) VALUES ('{rid}', '{payload}'::jsonb) "
                    f"ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload;"
                )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    return path


def export_db() -> None:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set — wrote SQL seed only.")
        return
    try:
        import psycopg
    except ImportError:
        print("psycopg not installed — wrote SQL seed only.")
        return
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
            for name, table in COLLECTIONS:
                fp = latest(ROOT / name, name)
                if not fp:
                    print(f"skip missing {name}")
                    continue
                n = 0
                for row in load_jsonl(fp):
                    rid = str(row["id"])
                    payload = json.dumps(row, ensure_ascii=False)
                    if table == "vecd_utterances":
                        speaker = "customer" if name == "customers" else "agent"
                        cur.execute(
                            """
                            INSERT INTO vecd_utterances(id, speaker, payload)
                            VALUES (%s, %s, %s::jsonb)
                            ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload, speaker = EXCLUDED.speaker
                            """,
                            (rid, speaker, payload),
                        )
                    else:
                        cur.execute(
                            f"""
                            INSERT INTO {table}(id, payload)
                            VALUES (%s, %s::jsonb)
                            ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload
                            """,
                            (rid, payload),
                        )
                    n += 1
                print(f"imported {name}: {n}")
        conn.commit()
    print("export_postgres done")


def main() -> int:
    refresh_sql_sample()
    export_db()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
