"""FastAPI dependencies for authentication and authorization."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_session_token
from app.db.models import User
from app.db.repositories import auth as auth_repo
from app.db.session import get_session


def _extract_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def get_current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    """Resolve the authenticated user from the bearer token."""
    token = _extract_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token_hash = hash_session_token(token)
    auth_session = await auth_repo.get_session_by_token_hash(session, token_hash)
    if auth_session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效"
        )
    if auth_session.expires_at < datetime.now(timezone.utc):
        await auth_repo.delete_session(session, token_hash)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效"
        )
    user = await auth_repo.get_user_by_id(session, auth_session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效"
        )
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Require the authenticated user to be an administrator."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="无权限访问"
        )
    return user


def _enforce_rate_limit(
    request: Request,
    user: User,
    *,
    limiter_name: str,
    error_code: str,
) -> User:
    limiter = getattr(request.app.state, limiter_name)
    decision = limiter.check(f"user:{user.id}")
    if not decision.allowed:
        request.app.state.metrics.increment(
            "rate_limit.exceeded.total",
            bucket=limiter_name.removesuffix("_rate_limiter"),
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="请求过于频繁，请稍后重试",
            headers={
                "X-Error-Code": error_code,
                "Retry-After": str(decision.retry_after_seconds),
            },
        )
    return user


async def rate_limit_chat(
    request: Request,
    user: User = Depends(get_current_user),
) -> User:
    return _enforce_rate_limit(
        request,
        user,
        limiter_name="chat_rate_limiter",
        error_code="CHAT_RATE_LIMITED",
    )


async def rate_limit_admin(
    request: Request,
    admin: User = Depends(require_admin),
) -> User:
    return _enforce_rate_limit(
        request,
        admin,
        limiter_name="admin_rate_limiter",
        error_code="ADMIN_RATE_LIMITED",
    )


def get_container(request: Request):
    """Return the (cached) provider container for the application."""
    container = getattr(request.app.state, "container", None)
    if container is None:
        from app.bootstrap import build_container

        container = build_container(
            request.app.state.settings,
            metrics=getattr(request.app.state, "metrics", None),
        )
        request.app.state.container = container
    return container
