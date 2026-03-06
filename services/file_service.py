"""
File service — handles uploads, storage, downloads, and connecting
file operations to the processing services.

Now powered by MongoDB via Motor.
"""

import os
import uuid
import json
import asyncio
import logging
import mimetypes
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone

from fastapi import UploadFile
from motor.motor_asyncio import AsyncIOMotorDatabase

from core.config import settings
from models.schemas import FileDoc, FileStatus
from services.pdf_service import PDFService
from services.image_service import ImageService
from services.audio_service import AudioService

logger = logging.getLogger(__name__)


class FileService:

    @staticmethod
    async def upload_files(
        db: AsyncIOMotorDatabase,
        conversation_id: str,
        files: List[UploadFile],
    ) -> List[FileDoc]:
        records: List[FileDoc] = []

        conv_upload_dir = os.path.join(settings.UPLOAD_DIR, conversation_id)
        os.makedirs(conv_upload_dir, exist_ok=True)

        for file in files:
            ext = _get_extension(file.filename)
            if ext not in settings.allowed_extensions_list:
                logger.warning(f"Rejected file {file.filename} — unsupported extension .{ext}")
                continue

            content = await file.read()
            if len(content) > settings.max_file_size_bytes:
                logger.warning(f"Rejected file {file.filename} — exceeds {settings.MAX_FILE_SIZE_MB}MB")
                continue

            file_type = _classify_file(ext)
            file_id = str(uuid.uuid4())
            safe_name = f"{file_id}.{ext}"
            storage_path = os.path.join(conv_upload_dir, safe_name)

            with open(storage_path, "wb") as f:
                f.write(content)

            mime_type = file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream"

            record = FileDoc(
                id=file_id,
                conversation_id=conversation_id,
                original_filename=file.filename,
                storage_path=storage_path,
                mime_type=mime_type,
                file_size=len(content),
                file_type=file_type,
                status=FileStatus.UPLOADED,
            )
            await db.files.insert_one(record.to_mongo())
            records.append(record)

        return records

    @staticmethod
    async def get_file(db: AsyncIOMotorDatabase, file_id: str) -> Optional[FileDoc]:
        doc = await db.files.find_one({"_id": file_id})
        return FileDoc.from_mongo(doc) if doc else None

    @staticmethod
    async def get_conversation_files(db: AsyncIOMotorDatabase, conversation_id: str) -> List[FileDoc]:
        cursor = db.files.find({"conversation_id": conversation_id})
        return [FileDoc.from_mongo(doc) async for doc in cursor]

    @staticmethod
    async def process_operation(
        db: AsyncIOMotorDatabase,
        operation: str,
        file_ids: List[str],
        params: Dict[str, Any],
        conversation_id: str,
    ) -> Tuple[str, List[FileDoc]]:
        # Fetch files
        files: List[FileDoc] = []
        for fid in file_ids:
            record = await FileService.get_file(db, fid)
            if record and record.conversation_id == conversation_id:
                files.append(record)

        if not files:
            files = await FileService.get_conversation_files(db, conversation_id)

        if not files:
            return "No files found to process. Please upload files first.", []

        conv_output_dir = os.path.join(settings.OUTPUT_DIR, conversation_id)
        os.makedirs(conv_output_dir, exist_ok=True)

        # Mark files as processing
        for f in files:
            await db.files.update_one(
                {"_id": f.id},
                {"$set": {"status": "processing", "operation": operation, "operation_params": json.dumps(params)}},
            )

        try:
            result_msg, output_records = await asyncio.wait_for(
                _dispatch_operation(
                    db, operation, files, params, conv_output_dir, conversation_id
                ),
                timeout=120,  # 2 min hard cap per operation
            )

            # Mark originals as completed
            for f in files:
                await db.files.update_one({"_id": f.id}, {"$set": {"status": "completed"}})

            return result_msg, output_records

        except asyncio.TimeoutError:
            for f in files:
                await db.files.update_one(
                    {"_id": f.id},
                    {"$set": {"status": "failed", "error_message": "Operation timed out"}},
                )
            raise TimeoutError(f"The {operation} operation took too long and was cancelled.")
        except Exception as e:
            for f in files:
                await db.files.update_one(
                    {"_id": f.id},
                    {"$set": {"status": "failed", "error_message": str(e)[:200]}},
                )
            raise

    @staticmethod
    async def mark_exported(db: AsyncIOMotorDatabase, file_id: str):
        await db.files.update_one({"_id": file_id}, {"$set": {"exported": True}})


# ── Private helpers ───────────────────────────────────────────────

def _get_extension(filename: str) -> str:
    if not filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _classify_file(ext: str) -> str:
    if ext in ("pdf",):
        return "pdf"
    elif ext in ("png", "jpg", "jpeg", "webp", "bmp", "tiff", "gif"):
        return "image"
    elif ext in ("mp3", "wav", "ogg", "flac", "aac", "m4a"):
        return "audio"
    elif ext in ("txt", "md", "csv", "json", "html", "xml", "rtf", "log"):
        return "document"
    return "unknown"


async def _create_output_record(
    db: AsyncIOMotorDatabase,
    conversation_id: str,
    filename: str,
    path: str,
    mime_type: str,
    file_type: str,
    operation: str,
) -> FileDoc:
    """Helper to create and insert an output file record."""
    rec = FileDoc(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        original_filename=filename,
        storage_path=path,
        output_path=path,
        mime_type=mime_type,
        file_size=os.path.getsize(path),
        file_type=file_type,
        status=FileStatus.COMPLETED,
        operation=operation,
    )
    await db.files.insert_one(rec.to_mongo())
    return rec


async def _dispatch_operation(
    db: AsyncIOMotorDatabase,
    operation: str,
    files: List[FileDoc],
    params: Dict[str, Any],
    output_dir: str,
    conversation_id: str,
) -> Tuple[str, List[FileDoc]]:
    """Route the operation to the correct service."""

    output_records: List[FileDoc] = []

    # ── PDF operations ────────────────────────────────────────────
    if operation == "compress_pdf":
        results = []
        for f in files:
            if f.file_type != "pdf":
                continue
            out_path = os.path.join(output_dir, f"compressed_{os.path.basename(f.original_filename)}")
            msg = await PDFService.compress_pdf(f.storage_path, out_path, params)
            await db.files.update_one({"_id": f.id}, {"$set": {"output_path": out_path}})
            f.output_path = out_path
            results.append(msg)
            output_records.append(f)
        return "\n".join(results) or "No PDFs to compress.", output_records

    elif operation == "merge_pdf":
        pdf_files = [f for f in files if f.file_type == "pdf"]
        if len(pdf_files) < 2:
            return "Need at least 2 PDFs to merge.", []
        out_path = os.path.join(output_dir, "merged.pdf")
        msg = await PDFService.merge_pdf([f.storage_path for f in pdf_files], out_path, params)
        rec = await _create_output_record(db, conversation_id, "merged.pdf", out_path, "application/pdf", "pdf", operation)
        return msg, [rec]

    elif operation == "split_pdf":
        results = []
        for f in files:
            if f.file_type != "pdf":
                continue
            paths = await PDFService.split_pdf(f.storage_path, output_dir, params)
            for p in paths:
                if isinstance(p, str) and os.path.exists(p):
                    rec = await _create_output_record(db, conversation_id, os.path.basename(p), p, "application/pdf", "pdf", operation)
                    output_records.append(rec)
                    results.append(f"Created: {os.path.basename(p)}")
                else:
                    results.append(str(p))
        return "\n".join(results) or "No PDFs to split.", output_records

    elif operation == "rotate_pdf":
        results = []
        for f in files:
            if f.file_type != "pdf":
                continue
            out_path = os.path.join(output_dir, f"rotated_{os.path.basename(f.original_filename)}")
            msg = await PDFService.rotate_pdf(f.storage_path, out_path, params)
            await db.files.update_one({"_id": f.id}, {"$set": {"output_path": out_path}})
            f.output_path = out_path
            results.append(msg)
            output_records.append(f)
        return "\n".join(results) or "No PDFs to rotate.", output_records

    elif operation == "pdf_to_images":
        results = []
        for f in files:
            if f.file_type != "pdf":
                continue
            paths = await PDFService.pdf_to_images(f.storage_path, output_dir, params)
            for p in paths:
                if isinstance(p, str) and os.path.exists(p):
                    ext = _get_extension(p)
                    rec = await _create_output_record(db, conversation_id, os.path.basename(p), p, f"image/{ext}", "image", operation)
                    output_records.append(rec)
                    results.append(f"Extracted: {os.path.basename(p)}")
                else:
                    results.append(str(p))
        return "\n".join(results) or "No PDFs to extract images from.", output_records

    elif operation == "pdf_pages_to_images":
        results = []
        for f in files:
            if f.file_type != "pdf":
                continue
            paths = await PDFService.pdf_pages_to_images(f.storage_path, output_dir, params)
            for p in paths:
                if isinstance(p, str) and os.path.exists(p):
                    ext = _get_extension(p)
                    rec = await _create_output_record(db, conversation_id, os.path.basename(p), p, f"image/{ext}", "image", operation)
                    output_records.append(rec)
                    results.append(f"Rendered: {os.path.basename(p)}")
                else:
                    results.append(str(p))
        return "\n".join(results) or "No PDFs to render pages from.", output_records

    elif operation == "extract_pdf_images":
        results = []
        for f in files:
            if f.file_type != "pdf":
                continue
            paths = await PDFService.extract_pdf_images(f.storage_path, output_dir, params)
            for p in paths:
                if isinstance(p, str) and os.path.exists(p):
                    ext = _get_extension(p)
                    rec = await _create_output_record(db, conversation_id, os.path.basename(p), p, f"image/{ext}", "image", operation)
                    output_records.append(rec)
                    results.append(f"Extracted: {os.path.basename(p)}")
                else:
                    results.append(str(p))
        return "\n".join(results) or "No embedded images found in PDF.", output_records

    elif operation == "images_to_pdf":
        image_files = [f for f in files if f.file_type == "image"]
        if not image_files:
            return "No images found to convert to PDF.", []
        out_path = os.path.join(output_dir, "images_combined.pdf")
        msg = await PDFService.images_to_pdf([f.storage_path for f in image_files], out_path, params)
        rec = await _create_output_record(db, conversation_id, "images_combined.pdf", out_path, "application/pdf", "pdf", operation)
        return msg, [rec]

    # ── Image operations ──────────────────────────────────────────
    elif operation == "compress_image":
        results = []
        for f in files:
            if f.file_type != "image":
                continue
            ext = _get_extension(f.original_filename)
            out_path = os.path.join(output_dir, f"compressed_{f.id}.{ext}")
            msg = await ImageService.compress_image(f.storage_path, out_path, params)
            await db.files.update_one({"_id": f.id}, {"$set": {"output_path": out_path}})
            f.output_path = out_path
            results.append(msg)
            output_records.append(f)
        return "\n".join(results) or "No images to compress.", output_records

    elif operation == "resize_image":
        results = []
        for f in files:
            if f.file_type != "image":
                continue
            ext = _get_extension(f.original_filename)
            out_path = os.path.join(output_dir, f"resized_{f.id}.{ext}")
            msg = await ImageService.resize_image(f.storage_path, out_path, params)
            await db.files.update_one({"_id": f.id}, {"$set": {"output_path": out_path}})
            f.output_path = out_path
            results.append(msg)
            output_records.append(f)
        return "\n".join(results) or "No images to resize.", output_records

    elif operation == "crop_image":
        results = []
        for f in files:
            if f.file_type != "image":
                continue
            ext = _get_extension(f.original_filename)
            out_path = os.path.join(output_dir, f"cropped_{f.id}.{ext}")
            msg = await ImageService.crop_image(f.storage_path, out_path, params)
            await db.files.update_one({"_id": f.id}, {"$set": {"output_path": out_path}})
            f.output_path = out_path
            results.append(msg)
            output_records.append(f)
        return "\n".join(results) or "No images to crop.", output_records

    elif operation == "convert_image":
        target_fmt = params.get("format", "png")
        mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "bmp": "image/bmp", "tiff": "image/tiff", "gif": "image/gif"}
        results = []
        for f in files:
            if f.file_type != "image":
                continue
            out_path = os.path.join(output_dir, f"converted_{f.id}.{target_fmt}")
            msg, actual_path = await ImageService.convert_image(f.storage_path, out_path, params)
            out_name = f"{os.path.splitext(f.original_filename)[0]}.{target_fmt}"
            out_mime = mime_map.get(target_fmt, f"image/{target_fmt}")
            rec = await _create_output_record(db, conversation_id, out_name, actual_path, out_mime, "image", operation)
            output_records.append(rec)
            results.append(msg)
        return "\n".join(results) or "No images to convert.", output_records

    # ── Audio operations ──────────────────────────────────────────
    elif operation == "compress_audio":
        results = []
        for f in files:
            if f.file_type != "audio":
                continue
            ext = _get_extension(f.original_filename)
            out_path = os.path.join(output_dir, f"compressed_{f.id}.{ext}")
            msg = await AudioService.compress_audio(f.storage_path, out_path, params)
            await db.files.update_one({"_id": f.id}, {"$set": {"output_path": out_path}})
            f.output_path = out_path
            results.append(msg)
            output_records.append(f)
        return "\n".join(results) or "No audio files to compress.", output_records

    elif operation == "convert_audio":
        target_fmt = params.get("format", "mp3")
        audio_mime_map = {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg", "flac": "audio/flac", "aac": "audio/aac", "m4a": "audio/mp4"}
        results = []
        for f in files:
            if f.file_type != "audio":
                continue
            out_path = os.path.join(output_dir, f"converted_{f.id}.{target_fmt}")
            msg, actual_path = await AudioService.convert_audio(f.storage_path, out_path, params)
            out_name = f"{os.path.splitext(f.original_filename)[0]}.{target_fmt}"
            out_mime = audio_mime_map.get(target_fmt, f"audio/{target_fmt}")
            rec = await _create_output_record(db, conversation_id, out_name, actual_path, out_mime, "audio", operation)
            output_records.append(rec)
            results.append(msg)
        return "\n".join(results) or "No audio files to convert.", output_records

    elif operation == "trim_audio":
        results = []
        for f in files:
            if f.file_type != "audio":
                continue
            ext = _get_extension(f.original_filename)
            out_path = os.path.join(output_dir, f"trimmed_{f.id}.{ext}")
            msg = await AudioService.trim_audio(f.storage_path, out_path, params)
            await db.files.update_one({"_id": f.id}, {"$set": {"output_path": out_path}})
            f.output_path = out_path
            results.append(msg)
            output_records.append(f)
        return "\n".join(results) or "No audio files to trim.", output_records

    elif operation == "adjust_audio_volume":
        results = []
        for f in files:
            if f.file_type != "audio":
                continue
            ext = _get_extension(f.original_filename)
            out_path = os.path.join(output_dir, f"volume_{f.id}.{ext}")
            msg = await AudioService.adjust_audio_volume(f.storage_path, out_path, params)
            await db.files.update_one({"_id": f.id}, {"$set": {"output_path": out_path}})
            f.output_path = out_path
            results.append(msg)
            output_records.append(f)
        return "\n".join(results) or "No audio files to adjust.", output_records

    # ── Document operations ────────────────────────────────────────
    elif operation == "document_to_pdf":
        results = []
        for f in files:
            if f.file_type != "document":
                continue
            ext = _get_extension(f.original_filename)
            base = os.path.splitext(f.original_filename)[0]
            out_path = os.path.join(output_dir, f"{base}.pdf")
            msg = await PDFService.document_to_pdf(f.storage_path, out_path, ext, params)
            rec = await _create_output_record(db, conversation_id, f"{base}.pdf", out_path, "application/pdf", "pdf", operation)
            output_records.append(rec)
            results.append(msg)
        return "\n".join(results) or "No document files to convert.", output_records

    # ── New PDF operations ─────────────────────────────────────────
    elif operation == "watermark_pdf":
        results = []
        for f in files:
            if f.file_type != "pdf":
                continue
            out_path = os.path.join(output_dir, f"watermarked_{os.path.basename(f.original_filename)}")
            msg = await PDFService.watermark_pdf(f.storage_path, out_path, params)
            rec = await _create_output_record(db, conversation_id, f"watermarked_{f.original_filename}", out_path, "application/pdf", "pdf", operation)
            output_records.append(rec)
            results.append(msg)
        return "\n".join(results) or "No PDFs to watermark.", output_records

    elif operation == "protect_pdf":
        results = []
        for f in files:
            if f.file_type != "pdf":
                continue
            out_path = os.path.join(output_dir, f"protected_{os.path.basename(f.original_filename)}")
            msg = await PDFService.protect_pdf(f.storage_path, out_path, params)
            rec = await _create_output_record(db, conversation_id, f"protected_{f.original_filename}", out_path, "application/pdf", "pdf", operation)
            output_records.append(rec)
            results.append(msg)
        return "\n".join(results) or "No PDFs to protect.", output_records

    elif operation == "unlock_pdf":
        results = []
        for f in files:
            if f.file_type != "pdf":
                continue
            out_path = os.path.join(output_dir, f"unlocked_{os.path.basename(f.original_filename)}")
            msg = await PDFService.unlock_pdf(f.storage_path, out_path, params)
            rec = await _create_output_record(db, conversation_id, f"unlocked_{f.original_filename}", out_path, "application/pdf", "pdf", operation)
            output_records.append(rec)
            results.append(msg)
        return "\n".join(results) or "No PDFs to unlock.", output_records

    elif operation == "ocr_pdf":
        results = []
        for f in files:
            if f.file_type != "pdf":
                continue
            out_path = os.path.join(output_dir, f"ocr_{os.path.basename(f.original_filename)}")
            msg = await PDFService.ocr_pdf(f.storage_path, out_path, params)
            rec = await _create_output_record(db, conversation_id, f"ocr_{f.original_filename}", out_path, "application/pdf", "pdf", operation)
            output_records.append(rec)
            results.append(msg)
        return "\n".join(results) or "No PDFs to OCR.", output_records

    # ── New Image operations ───────────────────────────────────────
    elif operation == "remove_background":
        results = []
        for f in files:
            if f.file_type != "image":
                continue
            out_path = os.path.join(output_dir, f"nobg_{f.id}.png")
            msg = await ImageService.remove_background(f.storage_path, out_path, params)
            out_name = f"{os.path.splitext(f.original_filename)[0]}_nobg.png"
            rec = await _create_output_record(db, conversation_id, out_name, out_path, "image/png", "image", operation)
            output_records.append(rec)
            results.append(msg)
        return "\n".join(results) or "No images to remove background from.", output_records

    # ── Generative & Advanced Operations ─────────────────────────────
    elif operation == "generate_latex_pdf":
        latex_code = params.get("latex_code", "")
        if not latex_code:
            return "No LaTeX code provided.", []
            
        filename = params.get("filename", "document.pdf")
        if not filename.endswith(".pdf"):
            filename += ".pdf"
            
        out_path = os.path.join(output_dir, filename)
        
        from services.pdf_service import PDFService
        msg = await PDFService.generate_latex_pdf(latex_code, out_path, params)
        
        rec = await _create_output_record(
            db, conversation_id, filename, out_path, "application/pdf", "document", operation
        )
        return msg, [rec]

    # ── Utility operations ──────────────────────────────────────────
    elif operation == "zip_files":
        import zipfile as zf_mod
        zip_name = params.get("filename", "modex_archive.zip")
        if not zip_name.endswith(".zip"):
            zip_name += ".zip"
        out_path = os.path.join(output_dir, zip_name)
        added = 0
        seen_names: dict[str, int] = {}
        with zf_mod.ZipFile(out_path, "w", zf_mod.ZIP_DEFLATED) as zf:
            for f in files:
                src = f.output_path if f.output_path and os.path.exists(f.output_path) else f.storage_path
                if not src or not os.path.exists(src):
                    continue
                name = f.original_filename
                if name in seen_names:
                    seen_names[name] += 1
                    base_n, ext_n = os.path.splitext(name)
                    name = f"{base_n}_{seen_names[name]}{ext_n}"
                else:
                    seen_names[name] = 0
                zf.write(src, arcname=name)
                added += 1
        if added == 0:
            return "No files available to zip.", []
        rec = await _create_output_record(db, conversation_id, zip_name, out_path, "application/zip", "archive", operation)
        size_kb = os.path.getsize(out_path) / 1024
        return f"Created ZIP archive with {added} file(s) ({size_kb:.1f} KB)", [rec]

    # ── New Audio operations ───────────────────────────────────────
    elif operation == "transcribe_audio":
        results = []
        for f in files:
            if f.file_type != "audio":
                continue
            out_path = os.path.join(output_dir, f"transcript_{f.id}.txt")
            msg = await AudioService.transcribe_audio(f.storage_path, out_path, params)
            out_name = f"{os.path.splitext(f.original_filename)[0]}_transcript.txt"
            rec = await _create_output_record(db, conversation_id, out_name, out_path, "text/plain", "document", operation)
            output_records.append(rec)
            results.append(msg)
        return "\n".join(results) or "No audio files to transcribe.", output_records

    else:
        return f"Unknown operation: {operation}. Please describe what you'd like me to do with your files.", []

