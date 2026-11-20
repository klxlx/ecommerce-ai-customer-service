"""Customer photo return/exchange API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, rate_limit_chat
from app.db.models import User
from app.db.session import get_session
from app.schemas.returns import (
    ReturnAnalysisOut,
    ReturnOrderContextOut,
    ReturnSubmitIn,
    ReturnSubmitOut,
)
from app.services import returns as return_service

router = APIRouter()


@router.post("/returns/analyze", response_model=ReturnAnalysisOut)
async def analyze_return_photo(
    conversation_id: int = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(rate_limit_chat),
    session: AsyncSession = Depends(get_session),
    container=Depends(get_container),
):
    max_bytes = max(1, container.settings.return_image_max_mb) * 1024 * 1024
    data = await file.read(max_bytes + 1)
    await file.close()
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"照片不能超过 {container.settings.return_image_max_mb} MB",
        )
    try:
        result = await return_service.analyze_photo(
            session,
            user,
            conversation_id,
            data,
            container,
        )
    except return_service.ReturnConflict as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except return_service.ReturnValidationError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    await session.commit()
    return result


@router.get(
    "/returns/orders/{order_no}",
    response_model=ReturnOrderContextOut,
)
async def get_return_order(
    order_no: str,
    user: User = Depends(rate_limit_chat),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await return_service.get_order_context(
            session, user.id, order_no
        )
    except return_service.ReturnValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.post("/returns", response_model=ReturnSubmitOut)
async def submit_return(
    payload: ReturnSubmitIn,
    user: User = Depends(rate_limit_chat),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await return_service.submit_request(session, user, payload)
    except return_service.ReturnConflict as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except return_service.ReturnValidationError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return result
