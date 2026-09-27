from __future__ import annotations

import logging
import sqlite3

import pytest
from sqlalchemy.exc import OperationalError

from tiktok_osint.errors import ReverseLookupForbidden
from tiktok_osint.storage.db import init_db, make_engine
from tiktok_osint.sync.official import parse_displayed_account_lines, reconciliation_rows
from tiktok_osint.sync.retry import retry_sqlite_write


def test_parse_displayed_accounts_rejects_phone_and_deduplicates() -> None:
    usernames, skipped, rejected = parse_displayed_account_lines(
        "@shop.one\nhttps://www.tiktok.com/@shop_two\n@shop.one\n0901234567\n# ghi chú"
    )
    assert usernames == ["shop.one", "shop_two"]
    assert skipped == 1
    assert rejected[0]["line"] == 4
    assert "số điện thoại" in str(rejected[0]["reason"])


def test_reconciliation_never_joins_by_phone() -> None:
    rows = reconciliation_rows(
        [
            {
                "id": "match-1",
                "tiktok_username": "publicshop",
                "user_contact_id": "contact-1",
                "display_name_shown": "Public Shop",
                "note": "TikTok đã hiển thị",
            }
        ],
        {"different-user": {"username": "different-user", "nickname": "Wrong"}},
        {"contact-1": {"id": "contact-1", "display_name": "An", "phone_e164": "+84901234567", "email": None}},
    )
    assert rows[0]["matched_public_profile"] is False
    assert rows[0]["public_profile"] is None
    assert rows[0]["match_key"] == "tiktok_username"
    assert rows[0]["contact"]["phone_e164"] == "+84901234567"


def test_sqlite_retry_resumes_after_transient_lock() -> None:
    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OperationalError("insert", {}, Exception("database is locked"))
        return "ok"

    assert (
        retry_sqlite_write(
            operation,
            event="test.write",
            logger=logging.getLogger(__name__),
            attempts=3,
            base_delay_sec=0,
        )
        == "ok"
    )
    assert attempts == 3


def test_sqlite_retry_does_not_retry_policy_errors() -> None:
    attempts = 0

    def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise ReverseLookupForbidden()

    with pytest.raises(ReverseLookupForbidden):
        retry_sqlite_write(operation, event="test.policy", logger=logging.getLogger(__name__))
    assert attempts == 1


def test_existing_sqlite_session_table_gets_checkpoint_columns(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE official_sync_sessions (
                id VARCHAR(36) PRIMARY KEY,
                book_id VARCHAR(36) NOT NULL,
                note TEXT,
                created_at DATETIME NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO official_sync_sessions(id, book_id, note, created_at) VALUES (?, ?, ?, ?)",
            ("session-1", "book-1", "legacy", "2026-09-27 00:00:00"),
        )
    engine = make_engine(f"sqlite:///{path}")
    init_db(engine)
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(official_sync_sessions)")}
        migrated = connection.execute(
            "SELECT status, checkpoint_json, updated_at FROM official_sync_sessions WHERE id='session-1'"
        ).fetchone()
    assert {"status", "checkpoint_json", "updated_at"} <= columns
    assert migrated == ("recording", "{}", "2026-09-27 00:00:00")
