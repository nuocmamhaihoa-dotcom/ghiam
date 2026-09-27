from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from tiktok_osint.config import TikTokSettings, get_settings
from tiktok_osint.domain.models import CONTACT_KINDS
from tiktok_osint.errors import (
    BookNotFound,
    JobNotFound,
    JobStateError,
    ReverseLookupForbidden,
    SessionNotFound,
    TikTokOsintError,
    ValidationError,
)
from tiktok_osint.export.writers import flat_rows, write_csv, write_sqlite, write_xlsx
from tiktok_osint.logging_config import log_event, setup_logging
from tiktok_osint.policy import reject_reverse_lookup
from tiktok_osint.queue.redis_queue import JobQueue, build_queue
from tiktok_osint.storage.db import build_repository
from tiktok_osint.storage.repo import Repository
from tiktok_osint.sync.book import ImportedContact, build_contact, parse_address_book_file, parse_phone_lines
from tiktok_osint.sync.official import assert_record_payload, reconciliation_rows

logger = logging.getLogger(__name__)


class Services:
    def __init__(self, settings: TikTokSettings, repo: Repository, queue: JobQueue) -> None:
        self.settings = settings
        self.repo = repo
        self.queue = queue


class JobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class TargetImport(BaseModel):
    lines: list[str]


class ContactIn(BaseModel):
    display_name: str = "Không tên"
    phone: str | None = None
    email: str | None = None


class BookCreate(BaseModel):
    name: str
    contacts: list[ContactIn] = Field(default_factory=list)


class PhoneBulk(BaseModel):
    text: str = Field(min_length=1, max_length=500_000)


class SessionCreate(BaseModel):
    book_id: str
    note: str | None = None


class MatchIn(BaseModel):
    tiktok_username: str
    display_name_shown: str | None = None
    user_contact_id: str | None = None
    note: str | None = None


def create_app(
    settings: TikTokSettings | None = None,
    *,
    repo: Repository | None = None,
    queue: JobQueue | None = None,
) -> FastAPI:
    active = settings or get_settings()
    active.ensure_dirs()
    setup_logging(active.log_level)
    services = Services(
        active,
        repo or build_repository(active.database_url),
        queue if queue is not None else build_queue(active),
    )
    app = FastAPI(
        title="TikTok Public OSINT Scanner",
        version="0.1.0",
        summary="Thu thập hồ sơ công khai và ghi nhận kết quả đồng bộ danh bạ chính thức.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.services = services

    @app.exception_handler(TikTokOsintError)
    async def _handle_domain(_request: object, exc: TikTokOsintError) -> JSONResponse:
        status = 400
        if isinstance(exc, (JobNotFound, BookNotFound, SessionNotFound)):
            status = 404
        elif isinstance(exc, ReverseLookupForbidden):
            status = 403
        elif isinstance(exc, JobStateError):
            status = 409
        return JSONResponse({"detail": {"code": exc.__class__.__name__, "message": str(exc)}}, status_code=status)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "policy": "public-only"}

    @app.get("/api/dashboard")
    def dashboard() -> dict[str, object]:
        return services.repo.dashboard()

    @app.post("/api/jobs")
    def create_job(body: JobCreate) -> dict[str, object]:
        return services.repo.create_job(body.name)

    @app.get("/api/jobs")
    def list_jobs() -> list[dict[str, object]]:
        return services.repo.list_jobs()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, object]:
        job = services.repo.get_job(job_id)
        if job is None:
            raise JobNotFound(job_id)
        return job

    @app.post("/api/jobs/{job_id}/targets")
    def import_targets(job_id: str, body: TargetImport) -> dict[str, object]:
        return services.repo.add_targets(job_id, body.lines, max_lines=services.settings.max_import_lines)

    @app.get("/api/jobs/{job_id}/targets")
    def list_targets(job_id: str) -> list[dict[str, object]]:
        return services.repo.list_targets(job_id)

    @app.post("/api/jobs/{job_id}/enqueue")
    def enqueue(job_id: str) -> dict[str, object]:
        return _enqueue(services, job_id)

    @app.post("/api/jobs/{job_id}/pause")
    def pause(job_id: str) -> dict[str, object]:
        return services.repo.set_job_status(job_id, "paused")

    @app.post("/api/jobs/{job_id}/resume")
    def resume(job_id: str) -> dict[str, object]:
        return _enqueue(services, job_id)

    @app.post("/api/jobs/{job_id}/retry-failed")
    def retry_failed(job_id: str) -> dict[str, object]:
        reset = services.repo.reset_failed(job_id)
        job = _enqueue(services, job_id)
        job["reset_failed"] = reset
        return job

    @app.get("/api/profiles")
    def list_profiles(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        q: str | None = None,
    ) -> dict[str, object]:
        return services.repo.list_profiles(limit=limit, offset=offset, query=q)

    @app.get("/api/profiles/{username}")
    def get_profile(username: str) -> dict[str, object]:
        profile = services.repo.get_profile(username)
        if profile is None:
            raise HTTPException(status_code=404, detail={"code": "ProfileNotFound", "message": "Chưa có hồ sơ công khai này"})
        return profile

    @app.get("/api/contacts")
    def list_contacts(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        kind: str | None = None,
        job_id: str | None = None,
    ) -> dict[str, object]:
        if kind is not None and kind not in CONTACT_KINDS:
            raise ValidationError("Loại liên hệ không hỗ trợ")
        return services.repo.list_contacts(limit=limit, offset=offset, kind=kind, job_id=job_id)

    @app.get("/api/jobs/{job_id}/export.csv")
    def export_csv(job_id: str) -> FileResponse:
        path = _prepare_tabular(services, job_id, ".csv")
        return FileResponse(path, filename=path.name, media_type="text/csv")

    @app.get("/api/jobs/{job_id}/export.xlsx")
    def export_xlsx(job_id: str) -> FileResponse:
        path = _prepare_tabular(services, job_id, ".xlsx")
        return FileResponse(
            path,
            filename=path.name,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @app.get("/api/jobs/{job_id}/export.sqlite")
    def export_sqlite(job_id: str) -> FileResponse:
        path = services.settings.export_dir / f"{job_id}.sqlite"
        write_sqlite(path, services.repo, job_id)
        return FileResponse(path, filename=path.name, media_type="application/vnd.sqlite3")

    @app.post("/api/contact-books")
    def create_book(body: BookCreate) -> dict[str, object]:
        book = services.repo.create_book(body.name)
        if body.contacts:
            imported = [
                build_contact(display_name=item.display_name, phone=item.phone, email=item.email) for item in body.contacts
            ]
            book["contacts"] = services.repo.add_contacts_to_book(str(book["id"]), imported)
        return book

    @app.get("/api/contact-books")
    def list_books() -> list[dict[str, object]]:
        return services.repo.list_books()

    @app.get("/api/contact-books/{book_id}")
    def get_book(book_id: str) -> dict[str, object]:
        return services.repo.get_book(book_id)

    @app.post("/api/contact-books/{book_id}/contacts")
    def add_contact(book_id: str, body: ContactIn) -> dict[str, object]:
        contact = build_contact(display_name=body.display_name, phone=body.phone, email=body.email)
        if contact.phone_e164 is None and contact.email is None and contact.display_name == "Không tên":
            raise ValidationError("Cần tên, số điện thoại hoặc email")
        stored, skipped = services.repo.add_contacts(book_id, [contact], skip_existing_phones=True)
        return {"imported": len(stored), "skipped_duplicates": skipped, "contacts": stored}

    @app.post("/api/contact-books/{book_id}/phones")
    def import_phone_lines(book_id: str, body: PhoneBulk) -> dict[str, object]:
        if not body.text.strip():
            raise ValidationError("Chưa có số điện thoại nào")
        return _store_parsed_contacts(services, book_id, *parse_phone_lines(body.text))

    @app.post("/api/contact-books/{book_id}/import")
    async def import_book_file(book_id: str, file: UploadFile = File(...)) -> dict[str, object]:
        raw_bytes = await file.read()
        if len(raw_bytes) > 2_000_000:
            raise ValidationError("File danh bạ vượt quá 2MB")
        try:
            text = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError("File danh bạ phải là UTF-8") from exc
        contacts, skipped, rejected = parse_address_book_file(file.filename or "", text)
        return _store_parsed_contacts(services, book_id, contacts, skipped, rejected)

    @app.post("/api/official-sync/sessions")
    def create_session(body: SessionCreate) -> dict[str, object]:
        return services.repo.create_session(body.book_id, body.note)

    @app.post("/api/official-sync/sessions/{session_id}/results")
    def record_results(session_id: str, body: list[MatchIn]) -> dict[str, object]:
        items = []
        for row in body:
            username = assert_record_payload(row.tiktok_username, row.display_name_shown)
            items.append(
                {
                    "tiktok_username": username,
                    "display_name_shown": row.display_name_shown,
                    "user_contact_id": row.user_contact_id,
                    "note": row.note,
                }
            )
        stored = services.repo.add_matches(session_id, items)
        return {"recorded": len(stored), "matches": stored}

    @app.get("/api/official-sync/sessions/{session_id}/reconciliation")
    def reconcile(session_id: str) -> dict[str, object]:
        matches = services.repo.list_matches(session_id)
        profiles = {}
        for match in matches:
            profile = services.repo.get_profile(str(match["tiktok_username"]))
            if profile is not None:
                profiles[str(match["tiktok_username"])] = profile
        contact_ids = [str(match["user_contact_id"]) for match in matches if match.get("user_contact_id")]
        return {
            "policy": "match-by-displayed-username-only",
            "rows": reconciliation_rows(matches, profiles, services.repo.contacts_by_ids(contact_ids)),
        }

    @app.post("/api/official-sync/phone-to-id")
    def phone_to_id_refused() -> None:
        reject_reverse_lookup("phone_to_user_id")

    return app


def _store_parsed_contacts(
    services: Services,
    book_id: str,
    contacts: list[ImportedContact],
    skipped_in_batch: int,
    rejected: list[dict[str, object]],
) -> dict[str, object]:
    stored, skipped_existing = services.repo.add_contacts(book_id, contacts, skip_existing_phones=True)
    return {
        "imported": len(stored),
        "skipped_duplicates": skipped_in_batch + skipped_existing,
        "rejected_count": len(rejected),
        "rejected": rejected[:40],
        "contacts": stored,
    }


def _enqueue(services: Services, job_id: str) -> dict[str, object]:
    if services.repo.get_job(job_id) is None:
        raise JobNotFound(job_id)
    job = services.repo.set_job_status(job_id, "queued")
    try:
        services.queue.push(job_id)
    except Exception as exc:
        log_event(logger, logging.WARNING, "queue.push_failed", job_id=job_id, error=exc)
    return job


def _prepare_tabular(services: Services, job_id: str, suffix: str) -> Path:
    if services.repo.get_job(job_id) is None:
        raise JobNotFound(job_id)
    profiles = services.repo.profiles_for_job(job_id)
    rows = flat_rows(profiles)
    path = services.settings.export_dir / f"{job_id}{suffix}"
    if suffix == ".csv":
        write_csv(path, rows)
    elif suffix == ".xlsx":
        write_xlsx(path, profiles, rows)
    else:
        raise ValidationError("Định dạng xuất không hỗ trợ")
    return path


app = create_app()
