from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import PlainTextResponse

from app.deps import ContainerDep, SessionDep
from app.errors import NotFoundError
from app.proxies import service
from app.proxies.schemas import (
    BulkAction,
    BulkIn,
    BulkOut,
    CheckOut,
    ExportFormat,
    ImportIn,
    ImportOut,
    ImportPreviewIn,
    PreviewOut,
    ProxyDetailOut,
    ProxyFilterIn,
    ProxyListOut,
    ProxyListQuery,
    ProxyStatsOut,
    ProxyUpdateIn,
    RotateOut,
)
from app.security import require_admin
from app.timeutil import utcnow

router = APIRouter(prefix="/api/proxies", tags=["proxies"], dependencies=[Depends(require_admin)])

BULK_MESSAGES: dict[BulkAction, str] = {
    "enable": "Đã bật {n} proxy",
    "disable": "Đã tắt {n} proxy",
    "delete": "Đã xoá {n} proxy",
    "set_pool": "Đã chuyển {n} proxy sang pool mới",
    "reset_stats": "Đã đặt lại thống kê của {n} proxy",
}


class ExportQuery(ProxyFilterIn):
    format: ExportFormat = "url"
    with_options: bool = False


@router.get("")
async def list_proxies(
    query: Annotated[ProxyListQuery, Query()], session: SessionDep, container: ContainerDep
) -> ProxyListOut:
    return await service.list_proxies(session, query, container.box)


@router.get("/stats")
async def proxy_stats(session: SessionDep) -> ProxyStatsOut:
    return await service.proxy_stats(session)


@router.post("/import/preview")
async def preview_import(data: ImportPreviewIn, session: SessionDep, container: ContainerDep) -> PreviewOut:
    return await service.preview_import(
        session,
        data.text,
        data.defaults.to_defaults(),
        data.on_duplicate,
        max_lines=container.settings.proxy_import_max_lines,
    )


@router.post("/import")
async def import_proxies(data: ImportIn, session: SessionDep, container: ContainerDep) -> ImportOut:
    result = await service.import_proxies(
        session, data, container.box, max_lines=container.settings.proxy_import_max_lines
    )
    if data.check_after_import and result.check_ids:
        result.out.check_scheduled = await container.runtime.schedule_checks(result.check_ids)
    return result.out


@router.get("/export", response_class=PlainTextResponse)
async def export_proxies(
    query: Annotated[ExportQuery, Query()], session: SessionDep, container: ContainerDep
) -> PlainTextResponse:
    text = await service.export_proxies(
        session, query, container.box, fmt=query.format, with_options=query.with_options
    )
    filename = f"proxies-{utcnow():%Y%m%d-%H%M%S}.txt"
    return PlainTextResponse(
        text,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.post("/bulk")
async def bulk_action(data: BulkIn, session: SessionDep, container: ContainerDep) -> BulkOut:
    ids = await service.resolve_bulk_ids(session, data.ids, data.filter)
    await session.commit()
    if data.action == "check":
        affected = await container.runtime.schedule_checks(ids)
        message = f"Đã đưa {affected} proxy vào hàng đợi kiểm tra"
    elif data.action == "rotate":
        affected = await container.runtime.request_rotations(ids)
        message = f"Đã yêu cầu đổi IP {affected} proxy 4G; proxy đang được dùng sẽ đổi IP khi được trả"
    else:
        affected = await service.apply_bulk(session, data.action, ids, data.pool)
        message = BULK_MESSAGES[data.action].format(n=affected)
    return BulkOut(action=data.action, matched=len(ids), affected=affected, message=message)


@router.get("/{proxy_id}")
async def get_proxy(proxy_id: int, session: SessionDep, container: ContainerDep) -> ProxyDetailOut:
    proxy = await service.get_proxy(session, proxy_id)
    return await service.proxy_detail(session, proxy, container.box)


@router.patch("/{proxy_id}")
async def update_proxy(
    proxy_id: int, data: ProxyUpdateIn, session: SessionDep, container: ContainerDep
) -> ProxyDetailOut:
    proxy = await service.update_proxy(session, proxy_id, data, container.box)
    return await service.proxy_detail(session, proxy, container.box)


@router.delete("/{proxy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_proxy(proxy_id: int, session: SessionDep) -> Response:
    await service.delete_proxy(session, proxy_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{proxy_id}/check")
async def check_proxy(proxy_id: int, session: SessionDep, container: ContainerDep) -> CheckOut:
    await service.get_proxy(session, proxy_id)
    await session.commit()
    result = await container.runtime.check_now(proxy_id)
    if result is None:
        raise NotFoundError(f"Không tìm thấy proxy #{proxy_id}")
    proxy = await service.get_proxy(session, proxy_id, refresh=True)
    return CheckOut(
        ok=result.ok,
        latency_ms=result.latency_ms,
        exit_ip=result.exit_ip,
        country=result.country,
        isp=result.isp,
        error=result.error,
        proxy=await service.proxy_out(session, proxy, container.box),
    )


@router.post("/{proxy_id}/rotate")
async def rotate_proxy(
    proxy_id: int, session: SessionDep, container: ContainerDep, force: bool = False, wait: bool = True
) -> RotateOut:
    outcome = await container.runtime.request_rotation(proxy_id, force=force, wait=wait)
    proxy = await service.get_proxy(session, proxy_id, refresh=True)
    return RotateOut(
        status=outcome.status, message=outcome.message, proxy=await service.proxy_out(session, proxy, container.box)
    )
