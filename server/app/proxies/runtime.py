"""Tác vụ nền của kho proxy: kiểm tra sống/chết, đổi IP proxy 4G và lịch chạy định kỳ."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import false, not_, or_, select, true, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.crypto import SecretBox, SecretDecryptionError
from app.db import Database
from app.errors import NotFoundError
from app.models import Proxy, ProxyHealth, ProxyKind, RotationMode, RotationState, resolve_rotation_mode
from app.proxies import leasing, queries
from app.proxies.checker import CheckResult, check_proxy, short_error
from app.proxies.credentials import ProxyCredentials, build_credentials, resolve_credentials
from app.proxies.parser import Endpoint, has_session_placeholder, host_for_url
from app.proxies.rotation import RotationCallError, call_rotation_url, new_session_id
from app.proxies.service import ROTATABLE_MODES, cooldown_remaining
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

PURGE_INTERVAL_SEC = 3600
PENDING_BATCH = 200
RotationStatus = Literal["rotated", "failed", "started", "pending", "skipped"]


@dataclass(frozen=True, slots=True)
class RotationOutcome:
    ok: bool
    message: str


@dataclass(frozen=True, slots=True)
class RotationRequestResult:
    status: RotationStatus
    message: str


@dataclass(frozen=True, slots=True)
class _RotationTarget:
    mode: RotationMode
    rotation_url: str | None
    rotation_method: str
    protocol: str
    host: str
    port: int
    username: str
    password: str | None
    session_id: str | None
    old_ip: str | None

    def credentials(self, session_id: str | None = None) -> ProxyCredentials:
        return build_credentials(
            self.protocol,
            self.host,
            self.port,
            self.username,
            self.password,
            session_id if session_id is not None else self.session_id,
        )


def apply_check_result(proxy: Proxy, result: CheckResult, now: datetime) -> None:
    proxy.last_checked_at = now
    if not result.ok:
        proxy.health = ProxyHealth.DEAD
        proxy.latency_ms = None
        proxy.last_check_error = (result.error or "Không rõ lỗi")[:500]
        return
    ip_changed = result.exit_ip != proxy.exit_ip
    proxy.health = ProxyHealth.ALIVE
    proxy.latency_ms = result.latency_ms
    proxy.last_check_error = None
    proxy.exit_ip = result.exit_ip
    if result.country or ip_changed:
        proxy.country = result.country
    if result.isp or ip_changed:
        proxy.isp = result.isp


def _not_rotatable_message(proxy: Proxy) -> str:
    if proxy.kind == ProxyKind.STATIC:
        return "Proxy tĩnh không đổi IP được"
    return "Proxy 4G này chưa có link đổi IP hoặc {session}, hãy thêm link đổi IP khi sửa proxy"


def _interval_due(interval_sec: int, bases: Sequence[datetime | None], fallback: datetime, now: datetime) -> bool:
    known = [value for value in bases if value is not None]
    base = max(known) if known else fallback
    return (now - base).total_seconds() >= interval_sec


def _join_message(message: str, notes: Sequence[str]) -> str:
    return " · ".join([message, *notes]) if notes else message


class ProxyRuntime:
    def __init__(self, *, settings: Settings, db: Database, box: SecretBox) -> None:
        self._settings = settings
        self._db = db
        self._box = box
        self._check_queue: asyncio.Queue[int] = asyncio.Queue()
        self._rotation_queue: asyncio.Queue[int] = asyncio.Queue()
        self._tasks: list[asyncio.Task[None]] = []
        self._inline: set[asyncio.Task[Any]] = set()
        self._last_purge: float | None = None

    # -- vòng đời ---------------------------------------------------------

    async def start(self) -> None:
        await self._recover()
        for index in range(self._settings.proxy_check_concurrency):
            self._tasks.append(
                asyncio.create_task(self._worker(self._check_queue, self._run_check), name=f"proxy-check-{index}")
            )
        for index in range(self._settings.proxy_rotation_concurrency):
            self._tasks.append(
                asyncio.create_task(
                    self._worker(self._rotation_queue, self._run_rotation), name=f"proxy-rotation-{index}"
                )
            )
        if self._settings.scheduler_enabled:
            self._tasks.append(asyncio.create_task(self._scheduler_loop(), name="proxy-scheduler"))

    async def stop(self) -> None:
        tasks = [*self._tasks, *self._inline]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._inline.clear()

    async def wait_idle(self) -> None:
        """Chờ hết việc trong hàng đợi (dùng cho test và lệnh quản trị)."""
        while True:
            await self._check_queue.join()
            await self._rotation_queue.join()
            if self._inline:
                await asyncio.gather(*list(self._inline), return_exceptions=True)
            if self._check_queue.empty() and self._rotation_queue.empty() and not self._inline:
                return

    async def _recover(self) -> None:
        """Hàng đợi nằm trong bộ nhớ: sau khi khởi động lại, trả các proxy đang dở việc về trạng thái chờ."""
        async with self._db.sessionmaker() as session:
            await session.execute(
                update(Proxy)
                .where(Proxy.check_in_progress == true())
                .values(check_in_progress=False)
                .execution_options(synchronize_session=False)
            )
            await session.execute(
                update(Proxy)
                .where(Proxy.rotation_state == RotationState.ROTATING)
                .values(rotation_state=RotationState.PENDING)
                .execution_options(synchronize_session=False)
            )
            await session.commit()

    async def _worker(self, queue: asyncio.Queue[int], handler: Callable[[int], Awaitable[object]]) -> None:
        while True:
            proxy_id = await queue.get()
            try:
                await handler(proxy_id)
            except Exception:
                logger.exception("Lỗi khi xử lý proxy #%s", proxy_id)
            finally:
                queue.task_done()

    def _spawn[T](self, coro: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        task = asyncio.create_task(coro)
        self._inline.add(task)
        task.add_done_callback(self._inline.discard)
        return task

    async def _shielded[T](self, coro: Coroutine[Any, Any, T]) -> T:
        # Request HTTP bị huỷ (client đóng kết nối) không được bỏ dở việc đang ghi trạng thái proxy.
        return await asyncio.shield(self._spawn(coro))

    async def _check(self, credentials: ProxyCredentials) -> CheckResult:
        return await check_proxy(
            credentials.url,
            check_urls=self._settings.check_url_list,
            timeout_sec=self._settings.proxy_check_timeout_sec,
        )

    # -- kiểm tra sống/chết ----------------------------------------------

    async def schedule_checks(self, ids: Sequence[int]) -> int:
        claimed: list[int] = []
        async with self._db.sessionmaker() as session:
            for chunk in queries.chunked(ids):
                result = await session.execute(
                    update(Proxy)
                    .where(
                        Proxy.id.in_(chunk),
                        Proxy.check_in_progress == false(),
                        Proxy.rotation_state != RotationState.ROTATING,
                    )
                    .values(check_in_progress=True)
                    .returning(Proxy.id)
                    .execution_options(synchronize_session=False)
                )
                claimed += list(result.scalars())
            await session.commit()
        for proxy_id in claimed:
            self._check_queue.put_nowait(proxy_id)
        return len(claimed)

    async def check_now(self, proxy_id: int) -> CheckResult | None:
        return await self._shielded(self._run_check(proxy_id))

    def _credentials_or_error(self, proxy: Proxy) -> ProxyCredentials | CheckResult:
        try:
            return resolve_credentials(proxy, self._box)
        except SecretDecryptionError as exc:
            return CheckResult(ok=False, error=str(exc))

    async def _run_check(self, proxy_id: int) -> CheckResult | None:
        stored = False
        try:
            async with self._db.sessionmaker() as session:
                proxy = await session.get(Proxy, proxy_id)
                if proxy is None:
                    return None
                prepared = self._credentials_or_error(proxy)
            result = await self._check(prepared) if isinstance(prepared, ProxyCredentials) else prepared
            stored = await self._store_check(proxy_id, result)
            return result
        finally:
            if not stored:
                await self._clear_check_flag(proxy_id)

    async def _store_check(self, proxy_id: int, result: CheckResult) -> bool:
        async with self._db.sessionmaker() as session:
            proxy = await session.get(Proxy, proxy_id)
            if proxy is None:
                return True
            now = utcnow()
            previous_ip = proxy.exit_ip
            apply_check_result(proxy, result, now)
            proxy.check_in_progress = False
            if (
                proxy.rotation_mode == RotationMode.PROVIDER
                and result.ok
                and previous_ip
                and result.exit_ip != previous_ip
            ):
                proxy.last_rotated_at = now
                proxy.rotation_count += 1
                proxy.last_rotation_ok = True
                proxy.last_rotation_message = f"Nhà cung cấp đã đổi IP: {previous_ip} → {result.exit_ip}"
            await session.commit()
        return True

    async def _clear_check_flag(self, proxy_id: int) -> None:
        async with self._db.sessionmaker() as session:
            await session.execute(
                update(Proxy)
                .where(Proxy.id == proxy_id)
                .values(check_in_progress=False)
                .execution_options(synchronize_session=False)
            )
            await session.commit()

    # -- đổi IP -----------------------------------------------------------

    async def request_rotation(self, proxy_id: int, *, force: bool = False, wait: bool = True) -> RotationRequestResult:
        now = utcnow()
        async with self._db.sessionmaker() as session:
            proxy = await session.get(Proxy, proxy_id)
            if proxy is None:
                raise NotFoundError(f"Không tìm thấy proxy #{proxy_id}")
            mode = proxy.rotation_mode
            if mode == RotationMode.NONE:
                return RotationRequestResult("skipped", _not_rotatable_message(proxy))
            if mode == RotationMode.PROVIDER:
                return RotationRequestResult(
                    "skipped",
                    f"Nhà cung cấp tự đổi IP mỗi {proxy.rotation_interval_sec} giây; "
                    "proxy này không có link đổi IP để xoay thủ công",
                )
            if proxy.rotation_state == RotationState.ROTATING:
                return RotationRequestResult("skipped", "Proxy đang đổi IP, hãy chờ trong giây lát")
            remaining = cooldown_remaining(proxy, now)
            if remaining and not force:
                return RotationRequestResult("skipped", f"Vừa đổi IP gần đây, hãy chờ thêm {remaining} giây")
            active = await queries.count_active_leases(session, proxy_id, now)
            if active and not force:
                if proxy.rotation_state == RotationState.IDLE:
                    proxy.rotation_state = RotationState.PENDING
                    proxy.rotation_requested_at = now
                    await session.commit()
                return RotationRequestResult(
                    "pending", f"Proxy đang được {active} máy sử dụng, sẽ đổi IP ngay khi máy trả proxy"
                )
            claimed = await self._claim_rotation(session, proxy_id, now)
            await session.commit()
        if not claimed:
            return RotationRequestResult("skipped", "Proxy đang đổi IP, hãy chờ trong giây lát")
        if not wait:
            self._spawn(self._run_rotation(proxy_id))
            return RotationRequestResult("started", "Đã bắt đầu đổi IP")
        outcome = await self._shielded(self._run_rotation(proxy_id))
        return RotationRequestResult("rotated" if outcome.ok else "failed", outcome.message)

    async def request_rotations(self, ids: Sequence[int]) -> int:
        now = utcnow()
        marked = 0
        async with self._db.sessionmaker() as session:
            for chunk in queries.chunked(ids):
                result = await session.execute(
                    update(Proxy)
                    .where(
                        Proxy.id.in_(chunk),
                        Proxy.kind == ProxyKind.ROTATING,
                        Proxy.rotation_state == RotationState.IDLE,
                        or_(Proxy.rotation_url_enc.is_not(None), Proxy.uses_session == true()),
                    )
                    .values(rotation_state=RotationState.PENDING, rotation_requested_at=now)
                    .execution_options(synchronize_session=False)
                )
                marked += queries.rowcount(result)
            await session.commit()
        if marked:
            await self._dispatch_pending(now)
        return marked

    async def dispatch_pending_now(self) -> int:
        """Bắt đầu ngay các lượt đổi IP đang chờ (ví dụ vừa có máy trả proxy) thay vì đợi nhịp lịch kế tiếp."""
        return await self._dispatch_pending(utcnow())

    async def _claim_rotation(self, session: AsyncSession, proxy_id: int, now: datetime) -> bool:
        result = await session.execute(
            update(Proxy)
            .where(
                Proxy.id == proxy_id,
                Proxy.rotation_state.in_([RotationState.IDLE, RotationState.PENDING]),
            )
            .values(rotation_state=RotationState.ROTATING, last_rotation_attempt_at=now)
            .execution_options(synchronize_session=False)
        )
        return queries.rowcount(result) == 1

    async def _run_rotation(self, proxy_id: int) -> RotationOutcome:
        try:
            return await self._rotate(proxy_id)
        except Exception as exc:
            logger.exception("Lỗi khi đổi IP proxy #%s", proxy_id)
            return await self._finish_rotation(proxy_id, RotationOutcome(False, f"Lỗi khi đổi IP: {short_error(exc)}"))

    async def _load_rotation_target(self, proxy_id: int) -> _RotationTarget | None:
        async with self._db.sessionmaker() as session:
            proxy = await session.get(Proxy, proxy_id)
            if proxy is None:
                return None
            return _RotationTarget(
                mode=proxy.rotation_mode,
                rotation_url=self._box.decrypt(proxy.rotation_url_enc),
                rotation_method=proxy.rotation_method,
                protocol=proxy.protocol,
                host=proxy.host,
                port=proxy.port,
                username=proxy.username,
                password=self._box.decrypt(proxy.password_enc),
                session_id=proxy.session_id,
                old_ip=proxy.exit_ip,
            )

    async def _rotate(self, proxy_id: int) -> RotationOutcome:
        target = await self._load_rotation_target(proxy_id)
        if target is None:
            return RotationOutcome(False, "Proxy đã bị xoá")
        if target.mode == RotationMode.URL and target.rotation_url:
            return await self._rotate_by_url(proxy_id, target, target.rotation_url)
        if target.mode == RotationMode.SESSION:
            return await self._rotate_by_session(proxy_id, target)
        return await self._finish_rotation(
            proxy_id, RotationOutcome(False, "Proxy không có link đổi IP hoặc {session} để đổi IP")
        )

    async def _rotate_by_url(self, proxy_id: int, target: _RotationTarget, rotation_url: str) -> RotationOutcome:
        try:
            call = await call_rotation_url(
                rotation_url,
                method=target.rotation_method,
                timeout_sec=self._settings.proxy_rotation_call_timeout_sec,
                protocol=target.protocol,
            )
        except RotationCallError as exc:
            return await self._finish_rotation(proxy_id, RotationOutcome(False, str(exc)))
        notes = [f"Nhà cung cấp: {call.message}"] if call.message else []
        credentials = target.credentials()
        if call.new_endpoint is not None:
            credentials, note = await self._apply_new_endpoint(proxy_id, target, call.new_endpoint)
            if note:
                notes.append(note)
        if self._settings.proxy_rotation_settle_sec:
            await asyncio.sleep(self._settings.proxy_rotation_settle_sec)
        check, changed = await self._verify_new_ip(credentials, target.old_ip)
        if check.ok and changed:
            message = f"Đã đổi IP: {target.old_ip} → {check.exit_ip}" if target.old_ip else f"IP mới: {check.exit_ip}"
        elif check.ok:
            message = f"Đã gọi link đổi IP nhưng IP vẫn là {check.exit_ip}"
        else:
            message = f"Sau khi đổi IP, proxy chưa hoạt động lại: {check.error}"
        return await self._finish_rotation(
            proxy_id, RotationOutcome(check.ok and changed, _join_message(message, notes)), check=check
        )

    async def _verify_new_ip(self, credentials: ProxyCredentials, old_ip: str | None) -> tuple[CheckResult, bool]:
        result = CheckResult(ok=False, error="Chưa kiểm tra được")
        for attempt in range(self._settings.proxy_rotation_verify_attempts):
            if attempt and self._settings.proxy_rotation_verify_interval_sec:
                await asyncio.sleep(self._settings.proxy_rotation_verify_interval_sec)
            result = await self._check(credentials)
            if result.ok and (old_ip is None or result.exit_ip != old_ip):
                return result, True
        return result, False

    async def _rotate_by_session(self, proxy_id: int, target: _RotationTarget) -> RotationOutcome:
        working: tuple[str, CheckResult] | None = None
        last = CheckResult(ok=False, error="Chưa kiểm tra được")
        for _ in range(self._settings.proxy_rotation_verify_attempts):
            candidate = new_session_id()
            last = await self._check(target.credentials(candidate))
            if last.ok:
                working = (candidate, last)
                if target.old_ip is None or last.exit_ip != target.old_ip:
                    break
        if working is None:
            return await self._finish_rotation(
                proxy_id, RotationOutcome(False, f"Session mới chưa kết nối được: {last.error}"), check=last
            )
        session_id, check = working
        changed = target.old_ip is None or check.exit_ip != target.old_ip
        if changed:
            message = (
                f"Đã đổi session, IP: {target.old_ip} → {check.exit_ip}"
                if target.old_ip
                else f"Đã đổi session, IP mới: {check.exit_ip}"
            )
        else:
            message = f"Đã đổi session nhưng nhà cung cấp vẫn cấp IP {check.exit_ip}"
        return await self._finish_rotation(
            proxy_id, RotationOutcome(changed, message), check=check, session_id=session_id
        )

    async def _apply_new_endpoint(
        self, proxy_id: int, target: _RotationTarget, endpoint: Endpoint
    ) -> tuple[ProxyCredentials, str | None]:
        """Nhà cung cấp kiểu 'lấy proxy mới theo key' có thể trả host/port mới sau mỗi lần đổi IP."""
        current = target.credentials()
        protocol = str(endpoint.protocol or target.protocol)
        username, password = (
            (endpoint.username, endpoint.password) if endpoint.username else (target.username, target.password)
        )
        new_key = (protocol, endpoint.host, endpoint.port, username, password)
        if new_key == (target.protocol, target.host, target.port, target.username, target.password):
            return current, None
        conflict_note = f"Endpoint mới {host_for_url(endpoint.host)}:{endpoint.port} trùng proxy khác, giữ endpoint cũ"
        async with self._db.sessionmaker() as session:
            proxy = await session.get(Proxy, proxy_id)
            if proxy is None:
                return current, None
            conflict = await session.scalar(
                select(Proxy.id).where(
                    Proxy.protocol == protocol,
                    Proxy.host == endpoint.host,
                    Proxy.port == endpoint.port,
                    Proxy.username == username,
                    Proxy.id != proxy_id,
                )
            )
            if conflict is not None:
                return current, conflict_note
            proxy.protocol = protocol
            proxy.host = endpoint.host
            proxy.port = endpoint.port
            proxy.username = username
            proxy.password_enc = self._box.encrypt(password)
            proxy.uses_session = has_session_placeholder(username, password)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return current, conflict_note
        credentials = build_credentials(protocol, endpoint.host, endpoint.port, username, password, target.session_id)
        return credentials, f"Nhà cung cấp cấp endpoint mới {host_for_url(endpoint.host)}:{endpoint.port}"

    async def _finish_rotation(
        self,
        proxy_id: int,
        outcome: RotationOutcome,
        *,
        check: CheckResult | None = None,
        session_id: str | None = None,
    ) -> RotationOutcome:
        async with self._db.sessionmaker() as session:
            proxy = await session.get(Proxy, proxy_id)
            if proxy is None:
                return outcome
            now = utcnow()
            proxy.rotation_state = RotationState.IDLE
            proxy.rotation_requested_at = None
            proxy.last_rotation_ok = outcome.ok
            proxy.last_rotation_message = outcome.message[:500]
            if session_id is not None:
                proxy.session_id = session_id
            if check is not None:
                apply_check_result(proxy, check, now)
            if outcome.ok:
                proxy.last_rotated_at = now
                proxy.rotation_count += 1
                proxy.consecutive_failures = 0
                proxy.quarantined_until = None
            await session.commit()
        return outcome

    # -- lịch định kỳ -------------------------------------------------------

    async def _scheduler_loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("Lỗi trong vòng lặp lịch của kho proxy")
            await asyncio.sleep(self._settings.scheduler_tick_sec)

    async def tick(self) -> None:
        now = utcnow()
        await leasing.reap_expired_leases(self._db, now)
        await self._queue_interval_rotations(now)
        await self._dispatch_pending(now)
        await self._queue_due_checks(now)
        monotonic = time.monotonic()
        if self._last_purge is None or monotonic - self._last_purge >= PURGE_INTERVAL_SEC:
            self._last_purge = monotonic
            retention = timedelta(days=self._settings.proxy_lease_retention_days)
            await leasing.purge_old_leases(self._db, now - retention)

    async def _dispatch_pending(self, now: datetime) -> int:
        claimed: list[int] = []
        async with self._db.sessionmaker() as session:
            rows = (
                await session.execute(
                    select(
                        Proxy.id,
                        Proxy.kind,
                        Proxy.rotation_url_enc.is_not(None).label("has_url"),
                        Proxy.uses_session,
                        Proxy.rotation_interval_sec,
                        Proxy.rotation_cooldown_sec,
                        Proxy.last_rotation_attempt_at,
                    )
                    .where(
                        Proxy.rotation_state == RotationState.PENDING,
                        Proxy.enabled == true(),
                        not_(queries.has_active_lease(now)),
                    )
                    .order_by(Proxy.rotation_requested_at.asc().nulls_first(), Proxy.id.asc())
                    .limit(PENDING_BATCH)
                )
            ).all()
            for row in rows:
                mode = resolve_rotation_mode(
                    row.kind,
                    has_rotation_url=bool(row.has_url),
                    uses_session=row.uses_session,
                    rotation_interval_sec=row.rotation_interval_sec,
                )
                if mode not in ROTATABLE_MODES:
                    await session.execute(
                        update(Proxy)
                        .where(Proxy.id == row.id, Proxy.rotation_state == RotationState.PENDING)
                        .values(rotation_state=RotationState.IDLE, rotation_requested_at=None)
                        .execution_options(synchronize_session=False)
                    )
                    continue
                last_attempt = row.last_rotation_attempt_at
                if last_attempt is not None and (now - last_attempt).total_seconds() < row.rotation_cooldown_sec:
                    continue
                if await self._claim_rotation(session, row.id, now):
                    claimed.append(row.id)
            await session.commit()
        for proxy_id in claimed:
            self._rotation_queue.put_nowait(proxy_id)
        return len(claimed)

    async def _queue_interval_rotations(self, now: datetime) -> int:
        async with self._db.sessionmaker() as session:
            rows = (
                await session.execute(
                    select(
                        Proxy.id,
                        Proxy.rotation_interval_sec,
                        Proxy.last_rotated_at,
                        Proxy.last_rotation_attempt_at,
                        Proxy.created_at,
                    ).where(
                        Proxy.kind == ProxyKind.ROTATING,
                        Proxy.enabled == true(),
                        Proxy.rotation_interval_sec > 0,
                        Proxy.rotation_state == RotationState.IDLE,
                        or_(Proxy.rotation_url_enc.is_not(None), Proxy.uses_session == true()),
                    )
                )
            ).all()
            due = [
                row.id
                for row in rows
                if _interval_due(
                    row.rotation_interval_sec, (row.last_rotated_at, row.last_rotation_attempt_at), row.created_at, now
                )
            ]
            marked = 0
            for chunk in queries.chunked(due):
                result = await session.execute(
                    update(Proxy)
                    .where(Proxy.id.in_(chunk), Proxy.rotation_state == RotationState.IDLE)
                    .values(rotation_state=RotationState.PENDING, rotation_requested_at=now)
                    .execution_options(synchronize_session=False)
                )
                marked += queries.rowcount(result)
            await session.commit()
        return marked

    async def _queue_due_checks(self, now: datetime) -> int:
        batch = self._settings.proxy_health_check_batch
        if self._check_queue.qsize() >= batch:
            return 0
        interval = self._settings.proxy_health_check_interval_sec
        base = [
            Proxy.enabled == true(),
            Proxy.check_in_progress == false(),
            Proxy.rotation_state != RotationState.ROTATING,
        ]
        due_condition = (
            or_(Proxy.last_checked_at.is_(None), Proxy.last_checked_at < now - timedelta(seconds=interval))
            if interval > 0
            else Proxy.last_checked_at.is_(None)
        )
        async with self._db.sessionmaker() as session:
            ids = set(
                await queries.select_ids(
                    session,
                    select(Proxy.id)
                    .where(*base, due_condition)
                    .order_by(Proxy.last_checked_at.asc().nulls_first(), Proxy.id.asc())
                    .limit(batch),
                )
            )
            provider_rows = (
                await session.execute(
                    select(Proxy.id, Proxy.last_checked_at, Proxy.rotation_interval_sec).where(
                        *base,
                        Proxy.kind == ProxyKind.ROTATING,
                        Proxy.rotation_interval_sec > 0,
                        Proxy.rotation_url_enc.is_(None),
                        Proxy.uses_session == false(),
                    )
                )
            ).all()
        ids.update(
            row.id
            for row in provider_rows
            if row.last_checked_at is None or (now - row.last_checked_at).total_seconds() >= row.rotation_interval_sec
        )
        return await self.schedule_checks(sorted(ids)) if ids else 0
