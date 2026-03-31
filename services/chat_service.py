"""
Chat service — orchestrates the conversation flow:
  1. User sends message + optional file IDs
  2. AI engine interprets the request
  3. File service executes the operation
  4. Response is saved and returned

Now powered by MongoDB via Motor.
"""

import json
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from motor.motor_asyncio import AsyncIOMotorDatabase

from core.config import settings
from models.schemas import ConversationDoc, MessageDoc, MessageRole, FileDoc
from services.ai_engine import interpret_request, analyze_file_content, general_chat, CONTENT_OPERATIONS, CHAT_OPERATION
from services.file_service import FileService

logger = logging.getLogger(__name__)

# Overall timeout for the full send_message pipeline (AI interpretation + all processing)
MESSAGE_PIPELINE_TIMEOUT = settings.MESSAGE_PIPELINE_TIMEOUT_SECONDS


def _safe_error_msg(error: Exception, context: str = "processing your request") -> str:
    """Return a user-friendly error message without leaking internals."""
    msg = str(error).lower()
    if any(tok in msg for tok in ("timeout", "deadline", "timed out")):
        return f"The operation timed out while {context}. Please try again with a smaller file or simpler request."
    if any(tok in msg for tok in ("429", "rate limit", "resource exhausted", "overloaded")):
        return f"Modex is under heavy load right now. Please wait a moment and try again."
    if "no files" in msg or "not found" in msg:
        return f"I couldn't find the files needed for this operation. Please make sure your files are uploaded."
    if "unsupported" in msg or "unknown operation" in msg:
        return f"I don't support that operation yet. Try describing what you'd like differently."
    # Generic fallback — never expose raw traceback/error strings
    logger.error(f"Unhandled error during {context}: {error}")
    return f"Something went wrong while {context}. Please try again — if the problem persists, try a different approach."


def _sanitize_assistant_explanation(operation: str, explanation: str) -> str:
    """Keep user-facing operation summaries generic and free of implementation details."""
    if operation == "generate_diagram":
        return "I will generate the requested diagram as an image."
    return explanation


class ChatService:

    @staticmethod
    async def create_conversation(db: AsyncIOMotorDatabase, title: Optional[str] = None, user_id: str = "") -> ConversationDoc:
        now = datetime.now(timezone.utc)
        convo = ConversationDoc(
            title=title or "New Conversation",
            user_id=user_id,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=settings.DATA_RETENTION_HOURS),
        )
        await db.conversations.insert_one(convo.to_mongo())
        return convo

    @staticmethod
    async def get_conversation(db: AsyncIOMotorDatabase, conversation_id: str, user_id: str = "") -> Optional[ConversationDoc]:
        query: dict = {"_id": conversation_id}
        if user_id:
            query["user_id"] = user_id
        doc = await db.conversations.find_one(query)
        return ConversationDoc.from_mongo(doc) if doc else None

    @staticmethod
    async def list_conversations(db: AsyncIOMotorDatabase, limit: int = 50, offset: int = 0, user_id: str = "") -> List[ConversationDoc]:
        now = datetime.now(timezone.utc)
        query: dict = {"expires_at": {"$gt": now}}
        if user_id:
            query["user_id"] = user_id
        cursor = db.conversations.find(
            query
        ).sort("updated_at", -1).skip(offset).limit(limit)
        return [ConversationDoc.from_mongo(doc) async for doc in cursor]

    @staticmethod
    async def delete_conversation(db: AsyncIOMotorDatabase, conversation_id: str, user_id: str = "") -> bool:
        from core.data_retention import delete_conversation_files

        convo = await ChatService.get_conversation(db, conversation_id, user_id=user_id)
        if not convo:
            return False

        await delete_conversation_files(db, conversation_id)
        await db.conversations.delete_one({"_id": conversation_id})
        return True

    @staticmethod
    async def get_messages(db: AsyncIOMotorDatabase, conversation_id: str) -> List[MessageDoc]:
        cursor = db.messages.find(
            {"conversation_id": conversation_id}
        ).sort("created_at", 1)
        return [MessageDoc.from_mongo(doc) async for doc in cursor]

    @staticmethod
    async def send_message(
        db: AsyncIOMotorDatabase,
        conversation_id: str,
        user_message: str,
        file_ids: Optional[List[str]] = None,
    ) -> dict:
        convo = await ChatService.get_conversation(db, conversation_id)
        if not convo:
            raise ValueError(f"Conversation {conversation_id} not found")

        # Save user message
        user_msg = MessageDoc(
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content=user_message,
            file_ids=json.dumps(file_ids) if file_ids else None,
        )
        await db.messages.insert_one(user_msg.to_mongo())

        # Gather file descriptions for AI context
        all_files = await FileService.get_conversation_files(db, conversation_id)
        file_descriptions = [
            {
                "id": f.id,
                "filename": f.original_filename,
                "type": f.file_type,
                "size_kb": f.file_size / 1024,
            }
            for f in all_files
        ]

        # Get conversation history for multi-turn context
        messages = await ChatService.get_messages(db, conversation_id)
        history = [
            {"role": m.role.value if hasattr(m.role, "value") else m.role, "content": m.content}
            for m in messages[:-1]
        ]

        # Ask AI to interpret the request
        decision = await interpret_request(
            user_message=user_message,
            file_descriptions=file_descriptions,
            conversation_history=history,
        )

        processed_files: List[FileDoc] = []
        assistant_content = _sanitize_assistant_explanation(
            decision.get("operation", "unknown"),
            decision["explanation"],
        )

        if decision.get("needs_clarification"):
            pass
        elif decision["operation"] == CHAT_OPERATION:
            # General conversational response — no file processing needed
            assistant_content = await general_chat(
                user_message=user_message,
                conversation_history=history,
            )
        elif decision["operation"] in CONTENT_OPERATIONS:
            # Content analysis — reads file content and returns a text response (no output file)
            analysis_files = []
            target_ids = decision.get("file_ids", [])
            for f in all_files:
                if not target_ids or f.id in target_ids:
                    analysis_files.append({
                        "id": f.id,
                        "filename": f.original_filename,
                        "type": f.file_type,
                        "path": f.storage_path,
                    })
            assistant_content = await analyze_file_content(
                operation=decision["operation"],
                files=analysis_files,
                params=decision.get("params", {}),
                user_message=user_message,
            )
        elif decision["operation"] == "multi_operation" and decision.get("operations"):
            # Chained multi-operation: run each step sequentially with per-step error isolation
            MAX_CHAIN_STEPS = 10
            steps = decision["operations"][:MAX_CHAIN_STEPS]
            if len(decision["operations"]) > MAX_CHAIN_STEPS:
                logger.warning(f"Multi-op chain truncated from {len(decision['operations'])} to {MAX_CHAIN_STEPS} steps")

            all_results = []
            all_output_records: List[FileDoc] = []
            failed_steps = 0
            # Track the latest output IDs so subsequent steps can consume them
            latest_output_ids: List[str] = []

            for step_idx, step in enumerate(steps):
                step_op = step["operation"]
                # Use explicitly set file_ids (from AI or from previous step forwarding),
                # fall back to the top-level decision file_ids (user's original uploads)
                step_file_ids = step.get("file_ids") or decision.get("file_ids", [])
                step_params = step.get("params", {})

                try:
                    # Content analysis steps always run against original uploads
                    if step_op in CONTENT_OPERATIONS:
                        current_files = await FileService.get_conversation_files(db, conversation_id)
                        analysis_files = []
                        target_ids = decision.get("file_ids", [])
                        for f in current_files:
                            if not target_ids or f.id in target_ids:
                                analysis_files.append({
                                    "id": f.id,
                                    "filename": f.original_filename,
                                    "type": f.file_type,
                                    "path": f.storage_path,
                                })
                        result_msg = await analyze_file_content(
                            operation=step_op,
                            files=analysis_files,
                            params=step_params,
                            user_message=user_message,
                        )
                        all_results.append(f"**{step_op}:**\n{result_msg}")
                        continue

                    result_msg, output_records = await FileService.process_operation(
                        db=db,
                        operation=step_op,
                        file_ids=step_file_ids,
                        params=step_params,
                        conversation_id=conversation_id,
                    )
                    all_results.append(f"**{step_op}:** {result_msg}")

                    # Accumulate all output records for the final response
                    if output_records:
                        all_output_records.extend(output_records)
                        latest_output_ids = [r.id for r in output_records]

                    # Forward output file IDs to the NEXT file-processing step
                    # so it consumes the output of this step as its input
                    if latest_output_ids and step_idx + 1 < len(steps):
                        next_step = steps[step_idx + 1]
                        if next_step["operation"] not in CONTENT_OPERATIONS:
                            next_step["file_ids"] = latest_output_ids

                except Exception as step_error:
                    failed_steps += 1
                    logger.error(f"Multi-op step '{step_op}' failed: {step_error}")
                    all_results.append(f"**{step_op}:** ⚠️ {_safe_error_msg(step_error, step_op)}")
                    # Don't clear latest_output_ids — let the next step try with whatever was last produced

            assistant_content = f"{_sanitize_assistant_explanation(decision['operation'], decision['explanation'])}\n\n" + "\n\n".join(all_results)
            if failed_steps == len(steps):
                assistant_content = "All operations failed. Please try again — if the problem persists, try processing files one at a time."
            # Deduplicate output records by ID
            seen_ids: set[str] = set()
            deduped: List[FileDoc] = []
            for rec in all_output_records:
                if rec.id not in seen_ids:
                    seen_ids.add(rec.id)
                    deduped.append(rec)
            processed_files = deduped
        elif decision["operation"] != "unknown":
            try:
                result_msg, output_records = await FileService.process_operation(
                    db=db,
                    operation=decision["operation"],
                    file_ids=decision.get("file_ids", []),
                    params=decision.get("params", {}),
                    conversation_id=conversation_id,
                )
                assistant_content = f"{_sanitize_assistant_explanation(decision['operation'], decision['explanation'])}\n\n**Result:** {result_msg}"
                processed_files = output_records
            except Exception as e:
                assistant_content = _safe_error_msg(e, decision['explanation'].lower())

        # Save assistant message
        assistant_msg = MessageDoc(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=assistant_content,
        )
        await db.messages.insert_one(assistant_msg.to_mongo())

        # Update conversation timestamp
        await db.conversations.update_one(
            {"_id": conversation_id},
            {"$set": {"updated_at": datetime.now(timezone.utc)}},
        )

        return {
            "message": assistant_msg,
            "processed_files": processed_files,
        }
