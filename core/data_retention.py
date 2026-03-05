"""
Strict data-retention policy - MongoDB edition.

- Runs on a schedule (every 30 minutes) to delete expired files & DB records.
- Files older than DATA_RETENTION_HOURS are purged.
"""

import os
import shutil
import logging
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from core.config import settings
from models.schemas import FileDoc

logger = logging.getLogger(__name__)


async def cleanup_expired_data(db: AsyncIOMotorDatabase):
    """Delete all conversations, messages, and files older than retention window."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.DATA_RETENTION_HOURS)

    # Find expired conversations
    expired_cursor = db.conversations.find({"created_at": {"$lt": cutoff}})
    convo_ids = []
    async for doc in expired_cursor:
        convo_ids.append(doc["_id"])
        # Delete associated files from disk
        file_cursor = db.files.find({"conversation_id": doc["_id"]})
        async for f_doc in file_cursor:
            f = FileDoc.from_mongo(f_doc)
            _safe_delete_file(f.storage_path)
            if f.output_path:
                _safe_delete_file(f.output_path)

    if convo_ids:
        await db.files.delete_many({"conversation_id": {"$in": convo_ids}})
        await db.messages.delete_many({"conversation_id": {"$in": convo_ids}})
        await db.conversations.delete_many({"_id": {"$in": convo_ids}})
        logger.info(f"Cleaned up {len(convo_ids)} expired conversations")

    # Also clean orphaned files on disk older than retention
    _cleanup_directory(settings.UPLOAD_DIR, cutoff)
    _cleanup_directory(settings.OUTPUT_DIR, cutoff)


async def delete_conversation_files(db: AsyncIOMotorDatabase, conversation_id: str):
    """Immediately delete all files for a conversation (post-export)."""
    cursor = db.files.find({"conversation_id": conversation_id})
    async for f_doc in cursor:
        f = FileDoc.from_mongo(f_doc)
        _safe_delete_file(f.storage_path)
        if f.output_path:
            _safe_delete_file(f.output_path)

    await db.files.delete_many({"conversation_id": conversation_id})
    logger.info(f"Deleted all files for conversation {conversation_id}")


def _safe_delete_file(path: str):
    """Delete a file, ignore if missing."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError as e:
        logger.warning(f"Could not delete {path}: {e}")


def _cleanup_directory(directory: str, cutoff: datetime):
    """Remove files older than cutoff from a directory."""
    if not os.path.exists(directory):
        return
    for root, dirs, files in os.walk(directory):
        for filename in files:
            filepath = os.path.join(root, filename)
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(filepath), tz=timezone.utc)
                if mtime < cutoff:
                    os.remove(filepath)
            except OSError:
                pass
        for d in dirs:
            dirpath = os.path.join(root, d)
            try:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
            except OSError:
                pass
