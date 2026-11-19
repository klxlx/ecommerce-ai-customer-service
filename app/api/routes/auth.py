"""Authentication routes: login, logout, current user."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.security import hash_session_token
from app.db.models import User
from app.db.session import get_session
from app.schemas.auth import LoginRequest, LoginResponse, UserOut
from app.services import auth as auth_service

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    client_host = request.client.host if request.client else "unknown"
    guard_key = f"{payload.username.strip().lower()}|{client_host}"
    login_guard = request.app.state.login_guard
    metrics = request.app.state.metrics

    if login_guard.is_locked(guard_key):
        retry_after = login_guard.retry_after(guard_key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录尝试过于频繁，请稍后重试",
            headers={
                "X-Error-Code": "AUTH_TEMPORARILY_LOCKED",
                "Retry-After": str(retry_after),
            },
        )

    result = await auth_service.login(session, payload.username, payload.password)
    if result is None:
        metrics.increment("auth.login.failure.total")
        if login_guard.record_failure(guard_key):
            metrics.increment("auth.login.locked.total")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="登录尝试过于频繁，请稍后重试",
                headers={
                    "X-Error-Code": "AUTH_TEMPORARILY_LOCKED",
                    "Retry-After": str(login_guard.retry_after(guard_key)),
                },
            )
        # Non-enumerating failure: wrong user and wrong password look identical.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"X-Error-Code": "AUTH_INVALID_CREDENTIALS"},
        )
    login_guard.reset(guard_key)
    token, user = result
    return LoginResponse(token=token, user=UserOut.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    authorization = request.headers.get("Authorization", "")
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if token:
        await auth_service.logout(session, hash_session_token(token))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)
