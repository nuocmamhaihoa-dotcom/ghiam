"""Đăng nhập, tạo nhân viên, đặt hạn mức tra cứu."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from control_plane import accounts, db
from control_plane.accounts import Actor
from control_plane.settings import settings

router = APIRouter()


def _actor(request: Request) -> Actor:
    db.init_db(settings.db_path)
    found = accounts.actor_from_master_token(request.headers.get("authorization"), settings.token or "")
    if found is not None:
        return found
    try:
        return accounts.require_session(settings.db_path, request.cookies.get(accounts.SESSION_COOKIE))
    except accounts.AccountError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


def _manager(actor: Actor) -> None:
    if not actor.is_manager:
        raise HTTPException(status_code=403, detail="Chỉ quản lý được làm việc này.")


def _raise_account(exc: accounts.AccountError) -> None:
    raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


def _json(request_body: Any) -> dict[str, Any]:
    if not isinstance(request_body, dict):
        raise HTTPException(status_code=400, detail="Dữ liệu không đúng.")
    return request_body


async def _body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Dữ liệu không đúng.") from exc
    return _json(payload)


def _with_usage(actor: Actor) -> dict[str, Any]:
    used = 0 if actor.daily_lookup_limit is None else accounts.usage_today(settings.db_path, actor.id)
    return accounts.public_account(actor, used)


def _session_response(actor: Actor, token: str) -> JSONResponse:
    response = JSONResponse({"account": _with_usage(actor)})
    response.set_cookie(
        accounts.SESSION_COOKIE,
        token,
        max_age=accounts.SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/v1/accounts/session")
def account_session(request: Request) -> dict[str, Any]:
    db.init_db(settings.db_path)
    account = None
    token = request.cookies.get(accounts.SESSION_COOKIE)
    if token:
        try:
            account = _with_usage(accounts.require_session(settings.db_path, token))
        except accounts.AccountError:
            account = None
    return {"needsSetup": accounts.needs_setup(settings.db_path), "account": account}


@router.post("/v1/accounts/setup")
async def account_setup(request: Request) -> JSONResponse:
    db.init_db(settings.db_path)
    payload = await _body(request)
    try:
        actor = accounts.setup_manager(
            settings.db_path,
            payload.get("username"),
            payload.get("password"),
            payload.get("displayName") or "",
        )
        _actor_row, token = accounts.login(settings.db_path, actor.username, str(payload.get("password") or ""))
    except accounts.AccountError as exc:
        _raise_account(exc)
    return _session_response(_actor_row, token)


@router.post("/v1/accounts/login")
async def account_login(request: Request) -> JSONResponse:
    db.init_db(settings.db_path)
    payload = await _body(request)
    try:
        actor, token = accounts.login(settings.db_path, payload.get("username"), str(payload.get("password") or ""))
    except accounts.AccountError as exc:
        _raise_account(exc)
    return _session_response(actor, token)


@router.post("/v1/accounts/logout")
def account_logout(request: Request) -> JSONResponse:
    db.init_db(settings.db_path)
    accounts.logout(settings.db_path, request.cookies.get(accounts.SESSION_COOKIE))
    response = JSONResponse({"ok": True})
    response.delete_cookie(accounts.SESSION_COOKIE, path="/")
    return response


@router.get("/v1/accounts/me")
def account_me(request: Request) -> dict[str, Any]:
    actor = _actor(request)
    if actor.via == "token":
        return {"account": accounts.public_account(actor, 0)}
    return {"account": _with_usage(actor)}


@router.get("/v1/accounts")
def account_list(request: Request) -> dict[str, Any]:
    actor = _actor(request)
    _manager(actor)
    return {"accounts": accounts.list_accounts(settings.db_path)}


@router.post("/v1/accounts")
async def account_create(request: Request) -> dict[str, Any]:
    actor = _actor(request)
    _manager(actor)
    payload = await _body(request)
    try:
        created = accounts.create_staff(
            settings.db_path,
            payload.get("username"),
            payload.get("password"),
            payload.get("displayName") or "",
            payload.get("dailyLookupLimit"),
        )
    except accounts.AccountError as exc:
        _raise_account(exc)
    saved = next(item for item in accounts.list_accounts(settings.db_path) if item["id"] == created.id)
    return {"account": saved}


@router.patch("/v1/accounts/{account_id}")
async def account_update(account_id: str, request: Request) -> dict[str, Any]:
    actor = _actor(request)
    _manager(actor)
    payload = await _body(request)
    active = payload.get("active")
    if active is not None and not isinstance(active, bool):
        raise HTTPException(status_code=400, detail="Trạng thái tài khoản không đúng.")
    try:
        saved = accounts.update_staff(
            settings.db_path,
            account_id,
            daily_limit=payload.get("dailyLookupLimit") if "dailyLookupLimit" in payload else None,
            active=active,
            display_name=payload.get("displayName") if "displayName" in payload else None,
            password=payload.get("password") if "password" in payload else None,
        )
    except accounts.AccountError as exc:
        _raise_account(exc)
    return {"account": saved}


@router.post("/v1/accounts/password")
async def account_password(request: Request) -> dict[str, Any]:
    actor = _actor(request)
    payload = await _body(request)
    try:
        accounts.change_password(
            settings.db_path,
            actor,
            str(payload.get("password") or ""),
            str(payload.get("next") or ""),
        )
    except accounts.AccountError as exc:
        _raise_account(exc)
    return {"ok": True}
