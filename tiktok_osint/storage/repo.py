from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tiktok_osint.domain.models import ContactCandidate, PublicSnapshot
from tiktok_osint.domain.normalize import parse_profile_input
from tiktok_osint.errors import BookNotFound, JobNotFound, SessionNotFound, ValidationError
from tiktok_osint.storage.orm import (
    ContactBookRow,
    ExtractedContactRow,
    OfficialDisplayedMatchRow,
    OfficialSyncSessionRow,
    PublicProfileRow,
    ScanJobRow,
    ScanTargetRow,
    UserContactRow,
)
from tiktok_osint.sync.book import ImportedContact


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Repository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create_job(self, name: str) -> dict[str, Any]:
        clean = " ".join(name.split()).strip()
        if not clean or len(clean) > 120:
            raise ValidationError("Tên job phải từ 1 đến 120 ký tự")
        with self._sessions() as session:
            now = utcnow()
            row = ScanJobRow(
                id=new_id(),
                name=clean,
                status="draft",
                checkpoint_json="{}",
                error=None,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            return _job_dict(row)

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._sessions() as session:
            rows = session.scalars(select(ScanJobRow).order_by(ScanJobRow.created_at.desc())).all()
            return [_job_dict(row) for row in rows]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._sessions() as session:
            row = session.get(ScanJobRow, job_id)
            return _job_dict(row) if row else None

    def set_job_status(self, job_id: str, status: str, error: str | None = None) -> dict[str, Any]:
        with self._sessions() as session:
            row = _require_job(session, job_id)
            row.status = status
            row.error = error
            row.updated_at = utcnow()
            session.commit()
            return _job_dict(row)

    def save_checkpoint(self, job_id: str, checkpoint: dict[str, Any]) -> None:
        payload = dict(checkpoint)
        payload["updated_at"] = utcnow().isoformat()
        with self._sessions() as session:
            row = _require_job(session, job_id)
            row.checkpoint_json = json.dumps(payload, ensure_ascii=False)
            row.updated_at = utcnow()
            session.commit()

    def add_targets(self, job_id: str, lines: list[str], *, max_lines: int) -> dict[str, Any]:
        if len(lines) > max_lines:
            raise ValidationError(f"Mỗi lần import tối đa {max_lines} dòng")
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        duplicates: list[str] = []
        with self._sessions() as session:
            _require_job(session, job_id)
            existing = set(
                session.scalars(select(ScanTargetRow.raw_input).where(ScanTargetRow.job_id == job_id)).all()
            )
            for line in lines:
                parsed = parse_profile_input(line)
                if not line.strip() or line.strip().startswith("#"):
                    continue
                if parsed.reject_reason or (parsed.username is None and not parsed.short_link):
                    rejected.append({"raw": line.strip(), "reason": parsed.reject_reason or "Không hợp lệ"})
                    continue
                raw_key = parsed.raw
                if raw_key in existing:
                    duplicates.append(raw_key)
                    continue
                now = utcnow()
                row = ScanTargetRow(
                    id=new_id(),
                    job_id=job_id,
                    raw_input=raw_key,
                    username=parsed.username,
                    profile_url=parsed.profile_url,
                    status="pending",
                    attempts=0,
                    last_error=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                existing.add(raw_key)
                accepted.append(_target_dict(row))
            session.commit()
        return {"accepted": accepted, "rejected": rejected, "duplicates": duplicates}

    def list_targets(self, job_id: str) -> list[dict[str, Any]]:
        with self._sessions() as session:
            _require_job(session, job_id)
            rows = session.scalars(
                select(ScanTargetRow).where(ScanTargetRow.job_id == job_id).order_by(ScanTargetRow.created_at)
            ).all()
            return [_target_dict(row) for row in rows]

    def runnable_targets(self, job_id: str, max_attempts: int) -> list[dict[str, Any]]:
        with self._sessions() as session:
            rows = session.scalars(
                select(ScanTargetRow)
                .where(
                    ScanTargetRow.job_id == job_id,
                    ScanTargetRow.status.in_(("pending", "failed")),
                    ScanTargetRow.attempts < max_attempts,
                )
                .order_by(ScanTargetRow.created_at, ScanTargetRow.id)
            ).all()
            return [_target_dict(row) for row in rows]

    def reclaim_running(self, job_id: str, max_attempts: int) -> None:
        with self._sessions() as session:
            rows = session.scalars(
                select(ScanTargetRow).where(ScanTargetRow.job_id == job_id, ScanTargetRow.status == "running")
            ).all()
            now = utcnow()
            for row in rows:
                row.status = "failed" if row.attempts >= max_attempts else "pending"
                row.updated_at = now
            session.commit()

    def mark_target(
        self,
        target_id: str,
        *,
        status: str,
        attempts: int | None = None,
        last_error: str | None = None,
        username: str | None = None,
        profile_url: str | None = None,
    ) -> None:
        with self._sessions() as session:
            row = session.get(ScanTargetRow, target_id)
            if row is None:
                raise ValidationError("Target không tồn tại")
            row.status = status
            if attempts is not None:
                row.attempts = attempts
            if last_error is not None or status in {"done", "pending"}:
                row.last_error = last_error
            if username is not None:
                row.username = username
            if profile_url is not None:
                row.profile_url = profile_url
            row.updated_at = utcnow()
            session.commit()

    def target_counts(self, job_id: str) -> dict[str, Any]:
        with self._sessions() as session:
            rows = session.execute(
                select(ScanTargetRow.status, func.count()).where(ScanTargetRow.job_id == job_id).group_by(ScanTargetRow.status)
            ).all()
        counts = {"total": 0, "pending": 0, "running": 0, "done": 0, "failed": 0, "skipped": 0}
        for status, count in rows:
            counts[str(status)] = int(count)
            counts["total"] += int(count)
        return counts

    def finish_job_if_idle(self, job_id: str) -> None:
        with self._sessions() as session:
            row = _require_job(session, job_id)
            if row.status == "paused":
                return
            counts = self.target_counts(job_id)
            if counts["pending"] or counts["running"]:
                return
            if counts["done"] == 0 and counts["failed"] > 0:
                row.status = "failed"
            else:
                row.status = "completed"
            row.updated_at = utcnow()
            row.checkpoint_json = json.dumps({**counts, "updated_at": row.updated_at.isoformat()}, ensure_ascii=False)
            session.commit()

    def claim_next_queued(self) -> str | None:
        with self._sessions() as session:
            row = session.scalar(select(ScanJobRow).where(ScanJobRow.status == "queued").order_by(ScanJobRow.created_at))
            if row is None:
                return None
            row.status = "running"
            row.updated_at = utcnow()
            session.commit()
            return row.id

    def reset_failed(self, job_id: str) -> int:
        with self._sessions() as session:
            _require_job(session, job_id)
            rows = session.scalars(
                select(ScanTargetRow).where(ScanTargetRow.job_id == job_id, ScanTargetRow.status == "failed")
            ).all()
            now = utcnow()
            for row in rows:
                row.status = "pending"
                row.attempts = 0
                row.last_error = None
                row.updated_at = now
            session.commit()
            return len(rows)

    def upsert_profile(self, snapshot: PublicSnapshot, job_id: str) -> str:
        with self._sessions() as session:
            row = session.scalar(select(PublicProfileRow).where(PublicProfileRow.username == snapshot.username))
            now = utcnow()
            if row is None:
                row = PublicProfileRow(
                    id=new_id(),
                    username=snapshot.username,
                    nickname=snapshot.nickname,
                    bio=snapshot.bio,
                    followers=snapshot.followers,
                    following=snapshot.following,
                    likes=snapshot.likes,
                    verified=snapshot.verified,
                    private_account=snapshot.private_account,
                    avatar_url=snapshot.avatar_url,
                    bio_link=snapshot.bio_link,
                    source_url=snapshot.source_url,
                    partial=snapshot.partial,
                    job_id=job_id,
                    scraped_at=now,
                )
                session.add(row)
            else:
                row.nickname = snapshot.nickname
                row.bio = snapshot.bio
                row.followers = snapshot.followers
                row.following = snapshot.following
                row.likes = snapshot.likes
                row.verified = snapshot.verified
                row.private_account = snapshot.private_account
                row.avatar_url = snapshot.avatar_url
                row.bio_link = snapshot.bio_link
                row.source_url = snapshot.source_url
                row.partial = snapshot.partial
                row.job_id = job_id
                row.scraped_at = now
            session.commit()
            return row.id

    def replace_contacts(self, profile_id: str, contacts: list[ContactCandidate]) -> None:
        with self._sessions() as session:
            existing = session.scalars(select(ExtractedContactRow).where(ExtractedContactRow.profile_id == profile_id)).all()
            for row in existing:
                session.delete(row)
            session.flush()
            for contact in contacts:
                session.add(
                    ExtractedContactRow(
                        id=new_id(),
                        profile_id=profile_id,
                        kind=contact.kind,
                        raw_value=contact.raw_value[:500],
                        normalized_value=contact.normalized_value[:500],
                        source=contact.source,
                        confidence=contact.confidence,
                        evidence=contact.evidence,
                    )
                )
            session.commit()

    def get_profile(self, username: str) -> dict[str, Any] | None:
        with self._sessions() as session:
            row = session.scalar(select(PublicProfileRow).where(PublicProfileRow.username == username))
            if row is None:
                return None
            contacts = session.scalars(
                select(ExtractedContactRow).where(ExtractedContactRow.profile_id == row.id).order_by(ExtractedContactRow.confidence.desc())
            ).all()
            return _profile_dict(row, contacts)

    def list_profiles(self, *, limit: int, offset: int, query: str | None) -> dict[str, Any]:
        limit = _page_limit(limit)
        offset = max(offset, 0)
        with self._sessions() as session:
            stmt = select(PublicProfileRow)
            count_stmt = select(func.count()).select_from(PublicProfileRow)
            if query:
                like = f"%{query.strip()}%"
                condition = PublicProfileRow.username.like(like) | PublicProfileRow.nickname.like(like)
                stmt = stmt.where(condition)
                count_stmt = count_stmt.where(condition)
            total = int(session.scalar(count_stmt) or 0)
            rows = session.scalars(stmt.order_by(PublicProfileRow.scraped_at.desc()).limit(limit).offset(offset)).all()
            items = []
            for row in rows:
                count = int(
                    session.scalar(
                        select(func.count()).select_from(ExtractedContactRow).where(ExtractedContactRow.profile_id == row.id)
                    )
                    or 0
                )
                item = _profile_dict(row, [])
                item["contact_count"] = count
                items.append(item)
            return {"items": items, "total": total, "limit": limit, "offset": offset}

    def list_contacts(self, *, limit: int, offset: int, kind: str | None, job_id: str | None) -> dict[str, Any]:
        limit = _page_limit(limit)
        offset = max(offset, 0)
        with self._sessions() as session:
            stmt = select(ExtractedContactRow, PublicProfileRow).join(
                PublicProfileRow, PublicProfileRow.id == ExtractedContactRow.profile_id
            )
            if kind:
                stmt = stmt.where(ExtractedContactRow.kind == kind)
            if job_id:
                usernames = session.scalars(select(ScanTargetRow.username).where(ScanTargetRow.job_id == job_id)).all()
                names = [name for name in usernames if name]
                stmt = stmt.where(PublicProfileRow.username.in_(names or ["__none__"]))
            rows = session.execute(stmt.order_by(ExtractedContactRow.confidence.desc())).all()
            total = len(rows)
            page = rows[offset : offset + limit]
            items = [_contact_dict(contact, profile.username) for contact, profile in page]
            return {"items": items, "total": total, "limit": limit, "offset": offset}

    def profiles_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self._sessions() as session:
            _require_job(session, job_id)
            targets = session.scalars(
                select(ScanTargetRow).where(ScanTargetRow.job_id == job_id).order_by(ScanTargetRow.created_at)
            ).all()
            profiles: list[dict[str, Any]] = []
            for target in targets:
                if not target.username:
                    profiles.append(
                        {
                            "username": "",
                            "nickname": None,
                            "bio": None,
                            "followers": None,
                            "following": None,
                            "likes": None,
                            "verified": False,
                            "private_account": False,
                            "bio_link": None,
                            "source_url": target.profile_url,
                            "partial": True,
                            "target_status": target.status,
                            "contacts": [],
                        }
                    )
                    continue
                row = session.scalar(select(PublicProfileRow).where(PublicProfileRow.username == target.username))
                if row is None:
                    profiles.append(
                        {
                            "username": target.username,
                            "nickname": None,
                            "bio": None,
                            "followers": None,
                            "following": None,
                            "likes": None,
                            "verified": False,
                            "private_account": False,
                            "bio_link": None,
                            "source_url": target.profile_url,
                            "partial": True,
                            "target_status": target.status,
                            "contacts": [],
                        }
                    )
                    continue
                contacts = session.scalars(select(ExtractedContactRow).where(ExtractedContactRow.profile_id == row.id)).all()
                item = _profile_dict(row, contacts)
                item["target_status"] = target.status
                profiles.append(item)
            return profiles

    def dashboard(self) -> dict[str, Any]:
        with self._sessions() as session:
            jobs = session.execute(select(ScanJobRow.status, func.count()).group_by(ScanJobRow.status)).all()
            by_status = {str(status): int(count) for status, count in jobs}
            profiles_total = int(session.scalar(select(func.count()).select_from(PublicProfileRow)) or 0)
            verified = int(
                session.scalar(select(func.count()).select_from(PublicProfileRow).where(PublicProfileRow.verified.is_(True)))
                or 0
            )
            private_profiles = int(
                session.scalar(
                    select(func.count()).select_from(PublicProfileRow).where(PublicProfileRow.private_account.is_(True))
                )
                or 0
            )
            contacts_total = int(session.scalar(select(func.count()).select_from(ExtractedContactRow)) or 0)
            kinds = session.execute(
                select(ExtractedContactRow.kind, func.count()).group_by(ExtractedContactRow.kind)
            ).all()
            avg = session.scalar(select(func.avg(ExtractedContactRow.confidence)))
            books = int(session.scalar(select(func.count()).select_from(ContactBookRow)) or 0)
            return {
                "jobs_total": sum(by_status.values()),
                "jobs_by_status": by_status,
                "profiles_total": profiles_total,
                "verified_profiles": verified,
                "private_profiles": private_profiles,
                "contacts_total": contacts_total,
                "contacts_by_kind": {str(kind): int(count) for kind, count in kinds},
                "avg_confidence": round(float(avg), 3) if avg is not None else 0.0,
                "contact_books": books,
                "policy": "public-only",
            }

    def create_book(self, name: str) -> dict[str, Any]:
        clean = " ".join(name.split()).strip()
        if not clean or len(clean) > 120:
            raise ValidationError("Tên danh bạ phải từ 1 đến 120 ký tự")
        with self._sessions() as session:
            row = ContactBookRow(id=new_id(), name=clean, created_at=utcnow())
            session.add(row)
            session.commit()
            return {"id": row.id, "name": row.name, "created_at": row.created_at.isoformat(), "contacts": []}

    def add_contacts_to_book(self, book_id: str, contacts: list[ImportedContact]) -> list[dict[str, Any]]:
        stored, _skipped = self.add_contacts(book_id, contacts, skip_existing_phones=False)
        return stored

    def add_contacts(
        self,
        book_id: str,
        contacts: list[ImportedContact],
        *,
        skip_existing_phones: bool,
    ) -> tuple[list[dict[str, Any]], int]:
        with self._sessions() as session:
            book = session.get(ContactBookRow, book_id)
            if book is None:
                raise BookNotFound(book_id)
            existing: set[str] = set()
            if skip_existing_phones:
                phones = session.scalars(
                    select(UserContactRow.phone_e164).where(
                        UserContactRow.book_id == book_id,
                        UserContactRow.phone_e164.is_not(None),
                    )
                ).all()
                existing = {phone for phone in phones if phone}
            stored: list[dict[str, Any]] = []
            skipped = 0
            for contact in contacts:
                if skip_existing_phones and contact.phone_e164 and contact.phone_e164 in existing:
                    skipped += 1
                    continue
                row = UserContactRow(
                    id=new_id(),
                    book_id=book_id,
                    display_name=contact.display_name,
                    phone_raw=contact.phone_raw,
                    phone_e164=contact.phone_e164,
                    email=contact.email,
                    created_at=utcnow(),
                )
                session.add(row)
                stored.append(_user_contact_dict(row))
                if contact.phone_e164:
                    existing.add(contact.phone_e164)
            session.commit()
            return stored, skipped

    def list_books(self) -> list[dict[str, Any]]:
        with self._sessions() as session:
            rows = session.scalars(select(ContactBookRow).order_by(ContactBookRow.created_at.desc())).all()
            items = []
            for row in rows:
                count = int(
                    session.scalar(select(func.count()).select_from(UserContactRow).where(UserContactRow.book_id == row.id))
                    or 0
                )
                items.append({"id": row.id, "name": row.name, "created_at": row.created_at.isoformat(), "contact_count": count})
            return items

    def get_book(self, book_id: str) -> dict[str, Any]:
        with self._sessions() as session:
            book = session.get(ContactBookRow, book_id)
            if book is None:
                raise BookNotFound(book_id)
            contacts = session.scalars(
                select(UserContactRow).where(UserContactRow.book_id == book_id).order_by(UserContactRow.created_at)
            ).all()
            return {
                "id": book.id,
                "name": book.name,
                "created_at": book.created_at.isoformat(),
                "contacts": [_user_contact_dict(row) for row in contacts],
            }

    def create_session(self, book_id: str, note: str | None) -> dict[str, Any]:
        if note is not None and len(note) > 1000:
            raise ValidationError("Ghi chú phiên quá dài")
        with self._sessions() as session:
            if session.get(ContactBookRow, book_id) is None:
                raise BookNotFound(book_id)
            row = OfficialSyncSessionRow(id=new_id(), book_id=book_id, note=note, created_at=utcnow())
            session.add(row)
            session.commit()
            return _session_dict(row)

    def add_matches(self, session_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._sessions() as session:
            sync = session.get(OfficialSyncSessionRow, session_id)
            if sync is None:
                raise SessionNotFound(session_id)
            stored: list[dict[str, Any]] = []
            for item in items:
                username = str(item["tiktok_username"])
                existing = session.scalar(
                    select(OfficialDisplayedMatchRow).where(
                        OfficialDisplayedMatchRow.session_id == session_id,
                        OfficialDisplayedMatchRow.tiktok_username == username,
                    )
                )
                contact_id = item.get("user_contact_id")
                if contact_id is not None:
                    contact = session.get(UserContactRow, str(contact_id))
                    if contact is None or contact.book_id != sync.book_id:
                        raise ValidationError("Liên hệ được gắn không thuộc danh bạ của phiên này")
                if existing is None:
                    existing = OfficialDisplayedMatchRow(
                        id=new_id(),
                        session_id=session_id,
                        user_contact_id=str(contact_id) if contact_id else None,
                        tiktok_username=username,
                        display_name_shown=item.get("display_name_shown"),
                        note=item.get("note"),
                        recorded_at=utcnow(),
                    )
                    session.add(existing)
                else:
                    existing.user_contact_id = str(contact_id) if contact_id else existing.user_contact_id
                    if item.get("display_name_shown") is not None:
                        existing.display_name_shown = item["display_name_shown"]
                    if item.get("note") is not None:
                        existing.note = item["note"]
                stored.append(_match_dict(existing))
            session.commit()
            return stored

    def list_matches(self, session_id: str) -> list[dict[str, Any]]:
        with self._sessions() as session:
            if session.get(OfficialSyncSessionRow, session_id) is None:
                raise SessionNotFound(session_id)
            rows = session.scalars(
                select(OfficialDisplayedMatchRow)
                .where(OfficialDisplayedMatchRow.session_id == session_id)
                .order_by(OfficialDisplayedMatchRow.recorded_at)
            ).all()
            return [_match_dict(row) for row in rows]

    def contacts_by_ids(self, contact_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not contact_ids:
            return {}
        with self._sessions() as session:
            rows = session.scalars(select(UserContactRow).where(UserContactRow.id.in_(contact_ids))).all()
            return {row.id: _user_contact_dict(row) for row in rows}


def _require_job(session: Session, job_id: str) -> ScanJobRow:
    row = session.get(ScanJobRow, job_id)
    if row is None:
        raise JobNotFound(job_id)
    return row


def _page_limit(limit: int) -> int:
    return min(max(limit, 1), 200)


def _job_dict(row: ScanJobRow) -> dict[str, Any]:
    try:
        checkpoint = json.loads(row.checkpoint_json or "{}")
    except json.JSONDecodeError:
        checkpoint = {}
    return {
        "id": row.id,
        "name": row.name,
        "status": row.status,
        "checkpoint": checkpoint,
        "error": row.error,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _target_dict(row: ScanTargetRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "job_id": row.job_id,
        "raw_input": row.raw_input,
        "username": row.username,
        "profile_url": row.profile_url,
        "status": row.status,
        "attempts": row.attempts,
        "last_error": row.last_error,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _profile_dict(row: PublicProfileRow, contacts: list[ExtractedContactRow]) -> dict[str, Any]:
    return {
        "id": row.id,
        "username": row.username,
        "nickname": row.nickname,
        "bio": row.bio,
        "followers": row.followers,
        "following": row.following,
        "likes": row.likes,
        "verified": row.verified,
        "private_account": row.private_account,
        "avatar_url": row.avatar_url,
        "bio_link": row.bio_link,
        "source_url": row.source_url,
        "partial": row.partial,
        "job_id": row.job_id,
        "scraped_at": row.scraped_at.isoformat(),
        "contacts": [_contact_dict(contact, row.username) for contact in contacts],
    }


def _contact_dict(row: ExtractedContactRow, username: str) -> dict[str, Any]:
    return {
        "id": row.id,
        "profile_id": row.profile_id,
        "username": username,
        "kind": row.kind,
        "raw_value": row.raw_value,
        "normalized_value": row.normalized_value,
        "source": row.source,
        "confidence": row.confidence,
        "evidence": row.evidence,
    }


def _user_contact_dict(row: UserContactRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "book_id": row.book_id,
        "display_name": row.display_name,
        "phone_raw": row.phone_raw,
        "phone_e164": row.phone_e164,
        "email": row.email,
    }


def _session_dict(row: OfficialSyncSessionRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "book_id": row.book_id,
        "note": row.note,
        "created_at": row.created_at.isoformat(),
    }


def _match_dict(row: OfficialDisplayedMatchRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "user_contact_id": row.user_contact_id,
        "tiktok_username": row.tiktok_username,
        "display_name_shown": row.display_name_shown,
        "note": row.note,
        "recorded_at": row.recorded_at.isoformat(),
    }
