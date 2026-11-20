"""Administrator knowledge-management routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, rate_limit_admin
from app.db.models import User
from app.db.repositories import knowledge_admin as knowledge_repo
from app.db.session import get_session
from app.schemas.knowledge import (
    KnowledgeDetailOut,
    KnowledgeFilePreviewOut,
    KnowledgeItemOut,
    KnowledgeUpsert,
    KnowledgeUpsertOut,
)
from app.services import knowledge_admin as knowledge_service
from app.services import knowledge_import
from app.services import semantic_cache

router = APIRouter()


@router.get("/knowledge", response_model=list[KnowledgeItemOut])
async def list_knowledge(
    _admin: User = Depends(rate_limit_admin),
    session: AsyncSession = Depends(get_session),
):
    snapshots = await knowledge_repo.list_snapshots(session)
    return [_public_snapshot(snapshot) for snapshot in snapshots]


@router.post("/knowledge/preview", response_model=KnowledgeFilePreviewOut)
async def preview_knowledge_file(
    file: UploadFile = File(...),
    _admin: User = Depends(rate_limit_admin),
    ocr_provider=Depends(knowledge_import.get_ocr_provider),
):
    try:
        data = await file.read(knowledge_import.MAX_FILE_BYTES + 1)
        preview = knowledge_import.preview_knowledge_file(
            file.filename,
            data,
            ocr_provider,
        )
    except knowledge_import.UnsupportedKnowledgeFileError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(exc),
        ) from exc
    except knowledge_import.KnowledgeFileTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except knowledge_import.OCRUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except (
        knowledge_import.InvalidKnowledgeFileError,
        knowledge_import.OCRProcessingError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    finally:
        await file.close()

    return KnowledgeFilePreviewOut(
        source_key=preview.source_key,
        title=preview.title,
        source_kind=preview.source_kind,
        original_filename=preview.original_filename,
        content=preview.content,
        applicability=preview.applicability,
        effective_date=preview.effective_date,
        page_count=preview.page_count,
        warnings=preview.warnings,
    )


@router.get("/knowledge/{knowledge_id}", response_model=KnowledgeDetailOut)
async def get_knowledge(
    knowledge_id: int,
    _admin: User = Depends(rate_limit_admin),
    session: AsyncSession = Depends(get_session),
):
    snapshot = await knowledge_repo.get_snapshot(session, knowledge_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="知识不存在")
    content = await knowledge_repo.get_manual_content(
        session,
        knowledge_id,
        snapshot["_current_version_id"],
    )
    return KnowledgeDetailOut(
        **_public_snapshot(snapshot),
        content=content,
    )


@router.delete(
    "/knowledge/{knowledge_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_knowledge(
    knowledge_id: int,
    _admin: User = Depends(rate_limit_admin),
    session: AsyncSession = Depends(get_session),
):
    deleted = await knowledge_repo.soft_delete_document(session, knowledge_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="知识不存在")
    await semantic_cache.clear(session)
    await session.commit()


@router.post("/knowledge", response_model=KnowledgeUpsertOut)
async def upsert_knowledge(
    payload: KnowledgeUpsert,
    _admin: User = Depends(rate_limit_admin),
    session: AsyncSession = Depends(get_session),
    container=Depends(get_container),
):
    try:
        result = await knowledge_service.upsert_manual_knowledge(
            session,
            container,
            payload,
        )
    except knowledge_service.KnowledgeConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    # A published knowledge change invalidates curated FAQ answers that may
    # now contradict the current policy/product source.
    await semantic_cache.clear(session)
    await session.commit()
    return KnowledgeUpsertOut(
        source_key=result.source_key,
        ingestion=result.ingestion,
        item=(
            KnowledgeItemOut(**_public_snapshot(result.snapshot))
            if result.snapshot is not None
            else None
        ),
    )


def _public_snapshot(snapshot: dict) -> dict:
    return {
        key: value
        for key, value in snapshot.items()
        if not key.startswith("_")
    }
