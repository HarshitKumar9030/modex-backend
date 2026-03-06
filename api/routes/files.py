"""
File upload & download API routes - MongoDB edition.
"""

import io
import os
import zipfile
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Header
from fastapi.responses import FileResponse, StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from core.database import get_db
from models.api_models import FileOut, FileUploadOut
from services.file_service import FileService
from services.chat_service import ChatService

router = APIRouter(prefix="/conversations/{conversation_id}/files", tags=["Files"])


def _get_user_id(x_user_id: Optional[str] = Header(None)) -> str:
    return x_user_id or ""


def _file_out(f):
    return FileOut(
        id=f.id,
        original_filename=f.original_filename,
        mime_type=f.mime_type,
        file_size=f.file_size,
        file_type=f.file_type,
        status=f.status.value if hasattr(f.status, 'value') else f.status,
        operation=f.operation,
        error_message=f.error_message,
        created_at=f.created_at,
        has_output=bool(f.output_path),
    )


@router.post("", response_model=FileUploadOut, status_code=201)
async def upload_files(
    conversation_id: str,
    files: List[UploadFile] = File(..., description="Files to upload"),
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(_get_user_id),
):
    convo = await ChatService.get_conversation(db, conversation_id, user_id=user_id)
    if not convo:
        raise HTTPException(404, "Conversation not found")

    records = await FileService.upload_files(db, conversation_id, files)
    if not records:
        raise HTTPException(400, "No valid files uploaded. Check file types and sizes.")

    return FileUploadOut(
        files=[_file_out(r) for r in records],
        message=f"Successfully uploaded {len(records)} file(s)",
    )


@router.get("", response_model=List[FileOut])
async def list_files(
    conversation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    files = await FileService.get_conversation_files(db, conversation_id)
    return [_file_out(f) for f in files]


@router.get("/batch/download")
async def batch_download_zip(
    conversation_id: str,
    file_ids: List[str] = Query(..., description="File IDs to include in ZIP"),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Download multiple output files as a single ZIP archive."""
    if not file_ids:
        raise HTTPException(400, "No file IDs provided")
    if len(file_ids) > 100:
        raise HTTPException(400, "Too many files — max 100 at a time")

    buf = io.BytesIO()
    added = 0
    seen_names: dict[str, int] = {}

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fid in file_ids:
            record = await FileService.get_file(db, fid)
            if not record or record.conversation_id != conversation_id:
                continue

            # Prefer output file, fall back to original
            path = record.output_path if record.output_path and os.path.exists(record.output_path) else record.storage_path
            if not path or not os.path.exists(path):
                continue

            # Deduplicate filenames inside the ZIP
            name = record.original_filename
            if name in seen_names:
                seen_names[name] += 1
                base, ext = os.path.splitext(name)
                name = f"{base}_{seen_names[name]}{ext}"
            else:
                seen_names[name] = 0

            zf.write(path, arcname=name)
            await FileService.mark_exported(db, fid)
            added += 1

    if added == 0:
        raise HTTPException(404, "No downloadable files found for the given IDs")

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=modex_files.zip"},
    )


@router.get("/{file_id}/download")
async def download_original(
    conversation_id: str,
    file_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    record = await FileService.get_file(db, file_id)
    if not record or record.conversation_id != conversation_id:
        raise HTTPException(404, "File not found")

    if not os.path.exists(record.storage_path):
        raise HTTPException(410, "File has been deleted (data retention policy)")

    return FileResponse(
        path=record.storage_path,
        filename=record.original_filename,
        media_type=record.mime_type,
    )


@router.get("/{file_id}/output")
async def download_output(
    conversation_id: str,
    file_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    record = await FileService.get_file(db, file_id)
    if not record or record.conversation_id != conversation_id:
        raise HTTPException(404, "File not found")

    if not record.output_path or not os.path.exists(record.output_path):
        raise HTTPException(404, "No output file available. Was a processing operation run?")

    await FileService.mark_exported(db, file_id)

    return FileResponse(
        path=record.output_path,
        filename=f"modex_{record.original_filename}",
        media_type=record.mime_type,
    )
