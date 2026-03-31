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
import re
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone

from fastapi import UploadFile
from motor.motor_asyncio import AsyncIOMotorDatabase

from core.config import settings
from models.schemas import FileDoc, FileStatus
from services.pdf_service import PDFService
from services.image_service import ImageService
from services.audio_service import AudioService
from services.study_service import StudyService
from services.diagram_service import DiagramService

logger = logging.getLogger(__name__)


_GENERATIVE_OPS = {
    "generate_latex_pdf", "generate_study_pack", "generate_study_schedule",
    "generate_formula_sheet", "generate_revision_notes", "generate_practice_questions",
    "generate_flashcards", "generate_worksheet", "generate_exam",
    "generate_from_template", "generate_diagram",
}

_OP_ALIASES = {
    "draw_diagram": "generate_diagram", "create_diagram": "generate_diagram",
    "vector_diagram": "generate_diagram", "plot_diagram": "generate_diagram",
    "make_diagram": "generate_diagram", "draw_vectors": "generate_diagram",
    "create_study_pack": "generate_study_pack", "make_study_pack": "generate_study_pack",
    "create_worksheet": "generate_worksheet", "make_worksheet": "generate_worksheet",
    "create_exam": "generate_exam", "make_exam": "generate_exam",
    "create_flashcards": "generate_flashcards", "make_flashcards": "generate_flashcards",
    "create_formula_sheet": "generate_formula_sheet",
    "create_revision_notes": "generate_revision_notes",
    "create_latex_pdf": "generate_latex_pdf", "latex_pdf": "generate_latex_pdf",
}


def _safe_output_filename(filename: str, default_name: str) -> str:
    """Sanitize user-provided output filenames and prevent path traversal."""
    name = str(filename or "").strip()
    if not name:
        return default_name
    name = os.path.basename(name)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    name = name.strip("._")
    return name or default_name


def _normalize_operation(operation: str) -> str:
    """Normalize operation names and recover from malformed AI outputs."""
    op = str(operation or "").strip().lower().replace("-", "_").replace(" ", "_")
    op = re.sub(r"[^a-z0-9_]", "", op)
    if op in _OP_ALIASES:
        return _OP_ALIASES[op]

    # Heuristic recovery if model returns a sentence in operation field.
    raw = str(operation or "").lower()
    if "watermark" in raw and "pdf" in raw:
        return "watermark_pdf"
    if "extract" in raw and "image" in raw and "pdf" in raw:
        return "extract_pdf_images"
    if "render" in raw and "pdf" in raw and "image" in raw:
        return "pdf_pages_to_images"
    if "study" in raw and "pack" in raw:
        return "generate_study_pack"
    if "worksheet" in raw:
        return "generate_worksheet"
    if "exam" in raw:
        return "generate_exam"
    if "diagram" in raw or "plot" in raw:
        return "generate_diagram"
    if "latex" in raw and "pdf" in raw:
        return "generate_latex_pdf"

    return _OP_ALIASES.get(op, op)


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
        params = params if isinstance(params, dict) else {}
        operation = _normalize_operation(operation)

        # Fetch files
        files: List[FileDoc] = []
        for fid in file_ids:
            record = await FileService.get_file(db, fid)
            if record and record.conversation_id == conversation_id:
                files.append(record)

        if not files:
            files = await FileService.get_conversation_files(db, conversation_id)

        if not files and operation not in _GENERATIVE_OPS:
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
            timeout = 180 if operation in _GENERATIVE_OPS else 120
            result_msg, output_records = await asyncio.wait_for(
                _dispatch_operation(
                    db, operation, files, params, conv_output_dir, conversation_id
                ),
                timeout=timeout,
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


def _is_pdf_like(file_doc: FileDoc) -> bool:
    """Treat legacy generated PDFs as PDFs based on mime type or extension."""
    if file_doc.file_type == "pdf":
        return True
    if file_doc.mime_type == "application/pdf":
        return True
    return file_doc.original_filename.lower().endswith(".pdf")


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
            if not _is_pdf_like(f):
                continue
            out_path = os.path.join(output_dir, f"compressed_{os.path.basename(f.original_filename)}")
            msg = await PDFService.compress_pdf(f.storage_path, out_path, params)
            await db.files.update_one({"_id": f.id}, {"$set": {"output_path": out_path}})
            f.output_path = out_path
            results.append(msg)
            output_records.append(f)
        return "\n".join(results) or "No PDFs to compress.", output_records

    elif operation == "merge_pdf":
        pdf_files = [f for f in files if _is_pdf_like(f)]
        if len(pdf_files) < 2:
            return "Need at least 2 PDFs to merge.", []
        out_path = os.path.join(output_dir, "merged.pdf")
        msg = await PDFService.merge_pdf([f.storage_path for f in pdf_files], out_path, params)
        rec = await _create_output_record(db, conversation_id, "merged.pdf", out_path, "application/pdf", "pdf", operation)
        return msg, [rec]

    elif operation == "split_pdf":
        results = []
        for f in files:
            if not _is_pdf_like(f):
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
            if not _is_pdf_like(f):
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
            if not _is_pdf_like(f):
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
            if not _is_pdf_like(f):
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
            if not _is_pdf_like(f):
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
            if not _is_pdf_like(f):
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
            if not _is_pdf_like(f):
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
            if not _is_pdf_like(f):
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
            if not _is_pdf_like(f):
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
        custom_prompt = str(params.get("prompt", "")).strip()
        if not latex_code and not custom_prompt:
            return "No LaTeX code or prompt provided.", []
            
        filename = _safe_output_filename(params.get("filename", "document.pdf"), "document.pdf")
        if not filename.endswith(".pdf"):
            filename += ".pdf"
            
        out_path = os.path.join(output_dir, filename)

        if latex_code:
            msg = await PDFService.generate_latex_pdf(latex_code, out_path, params)
        else:
            msg = await StudyService.generate_custom_pdf(out_path, {"prompt": custom_prompt})
        
        rec = await _create_output_record(
            db, conversation_id, filename, out_path, "application/pdf", "document", operation
        )
        return msg, [rec]

    # ── Utility operations ──────────────────────────────────────────
    elif operation == "zip_files":
        import zipfile as zf_mod
        zip_name = _safe_output_filename(params.get("filename", "modex_archive.zip"), "modex_archive.zip")
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

    # ── Study & Education Operations ──────────────────────────────
    elif operation == "generate_study_pack":
        filename = _safe_output_filename(params.get("filename", "study_pack.pdf"), "study_pack.pdf")
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        out_path = os.path.join(output_dir, filename)
        msg = await StudyService.generate_study_pack(out_path, params)
        rec = await _create_output_record(db, conversation_id, filename, out_path, "application/pdf", "document", operation)
        return msg, [rec]

    elif operation == "generate_study_schedule":
        filename = _safe_output_filename(params.get("filename", "study_schedule.pdf"), "study_schedule.pdf")
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        out_path = os.path.join(output_dir, filename)
        msg = await StudyService.generate_study_schedule(out_path, params)
        rec = await _create_output_record(db, conversation_id, filename, out_path, "application/pdf", "document", operation)
        return msg, [rec]

    elif operation == "generate_formula_sheet":
        filename = _safe_output_filename(params.get("filename", "formula_sheet.pdf"), "formula_sheet.pdf")
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        out_path = os.path.join(output_dir, filename)
        msg = await StudyService.generate_formula_sheet(out_path, params)
        rec = await _create_output_record(db, conversation_id, filename, out_path, "application/pdf", "document", operation)
        return msg, [rec]

    elif operation == "generate_revision_notes":
        filename = _safe_output_filename(params.get("filename", "revision_notes.pdf"), "revision_notes.pdf")
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        out_path = os.path.join(output_dir, filename)
        source_text = ""
        for f in files:
            try:
                if f.file_type == "pdf":
                    from PyPDF2 import PdfReader
                    reader = PdfReader(f.storage_path)
                    for page in reader.pages[:20]:
                        source_text += (page.extract_text() or "") + "\n"
                elif f.file_type == "document":
                    with open(f.storage_path, "r", encoding="utf-8", errors="replace") as fh:
                        source_text += fh.read(8000) + "\n"
            except Exception:
                pass
        msg = await StudyService.generate_revision_notes(out_path, params, source_text)
        rec = await _create_output_record(db, conversation_id, filename, out_path, "application/pdf", "document", operation)
        return msg, [rec]

    elif operation == "generate_practice_questions":
        filename = _safe_output_filename(params.get("filename", "practice_questions.pdf"), "practice_questions.pdf")
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        out_path = os.path.join(output_dir, filename)
        msg = await StudyService.generate_practice_questions(out_path, params)
        rec = await _create_output_record(db, conversation_id, filename, out_path, "application/pdf", "document", operation)
        return msg, [rec]

    elif operation == "generate_flashcards":
        filename = _safe_output_filename(params.get("filename", "flashcards.pdf"), "flashcards.pdf")
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        out_path = os.path.join(output_dir, filename)
        msg = await StudyService.generate_flashcards(out_path, params)
        rec = await _create_output_record(db, conversation_id, filename, out_path, "application/pdf", "document", operation)
        return msg, [rec]

    elif operation == "generate_worksheet":
        filename = _safe_output_filename(params.get("filename", "worksheet.pdf"), "worksheet.pdf")
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        out_path = os.path.join(output_dir, filename)
        msg = await StudyService.generate_worksheet(out_path, params)
        rec = await _create_output_record(db, conversation_id, filename, out_path, "application/pdf", "document", operation)
        return msg, [rec]

    elif operation == "generate_exam":
        filename = _safe_output_filename(params.get("filename", "exam_paper.pdf"), "exam_paper.pdf")
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        out_path = os.path.join(output_dir, filename)
        msg = await StudyService.generate_exam(out_path, params)
        rec = await _create_output_record(db, conversation_id, filename, out_path, "application/pdf", "document", operation)
        return msg, [rec]

    elif operation == "cleanup_notes":
        source_text = ""
        for f in files:
            try:
                if f.file_type == "pdf":
                    from PyPDF2 import PdfReader
                    reader = PdfReader(f.storage_path)
                    for page in reader.pages[:30]:
                        source_text += (page.extract_text() or "") + "\n"
                elif f.file_type == "document":
                    with open(f.storage_path, "r", encoding="utf-8", errors="replace") as fh:
                        source_text += fh.read(15000) + "\n"
            except Exception:
                pass
        if not source_text.strip():
            return "No readable content found in uploaded files.", []
        filename = _safe_output_filename(params.get("filename", "cleaned_notes.pdf"), "cleaned_notes.pdf")
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        out_path = os.path.join(output_dir, filename)
        msg = await StudyService.cleanup_notes(out_path, params, source_text)
        rec = await _create_output_record(db, conversation_id, filename, out_path, "application/pdf", "document", operation)
        return msg, [rec]

    elif operation == "generate_from_template":
        filename = _safe_output_filename(params.get("filename", "document.pdf"), "document.pdf")
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        out_path = os.path.join(output_dir, filename)
        msg = await StudyService.generate_from_template(out_path, params)
        rec = await _create_output_record(db, conversation_id, filename, out_path, "application/pdf", "document", operation)
        return msg, [rec]

    # ── Formula OCR ───────────────────────────────────────────────
    elif operation == "formula_ocr":
        image_files = [f for f in files if f.file_type == "image"]
        if not image_files:
            return "Please upload an image containing formulas.", []
        results = []
        for f in image_files:
            filename = f"formulas_{f.id}.pdf"
            out_path = os.path.join(output_dir, filename)
            msg = await StudyService.formula_ocr(f.storage_path, out_path, params)
            rec = await _create_output_record(db, conversation_id, filename, out_path, "application/pdf", "document", operation)
            output_records.append(rec)
            results.append(msg)
        return "\n".join(results), output_records

    # ── Multi-File Synthesis ──────────────────────────────────────
    elif operation == "synthesize_files":
        if len(files) < 1:
            return "Please upload files to synthesize.", []
        file_infos = [{"path": f.storage_path, "filename": f.original_filename, "type": f.file_type} for f in files]
        filename = _safe_output_filename(params.get("filename", "synthesized.pdf"), "synthesized.pdf")
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        out_path = os.path.join(output_dir, filename)
        msg = await StudyService.synthesize_files(file_infos, out_path, params)
        rec = await _create_output_record(db, conversation_id, filename, out_path, "application/pdf", "document", operation)
        return msg, [rec]

    # ── Diagram Generation ────────────────────────────────────────
    elif operation == "generate_diagram":
        requested_filename = _safe_output_filename(str(params.get("filename", "")).strip(), "")
        requested_format = params.get("output_format")
        if requested_format is None and requested_filename.lower().endswith(".pdf"):
            output_format = "png"
        else:
            output_format = str(requested_format or "png").lower()
        if output_format in {"jpeg", "jpg"}:
            default_name = "diagram.jpg"
            mime_type = "image/jpeg"
            file_type = "image"
        elif output_format == "pdf":
            default_name = "diagram.pdf"
            mime_type = "application/pdf"
            file_type = "pdf"
        else:
            default_name = "diagram.png"
            mime_type = "image/png"
            file_type = "image"

        filename = requested_filename or default_name
        if output_format != "pdf" and filename.lower().endswith(".pdf"):
            filename = os.path.splitext(filename)[0] + (".jpg" if output_format in {"jpeg", "jpg"} else ".png")
        if "." not in os.path.basename(filename):
            filename = default_name
        out_path = os.path.join(output_dir, filename)
        msg = await DiagramService.generate_diagram(out_path, params)
        rec = await _create_output_record(db, conversation_id, filename, out_path, mime_type, file_type, operation)
        return msg, [rec]

    else:
        return f"Unknown operation: {operation}. Please describe what you'd like me to do with your files.", []

