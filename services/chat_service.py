"""
Chat service — orchestrates the conversation flow:
  1. User sends message + optional file IDs
  2. AI engine interprets the request
  3. File service executes the operation
  4. Response is saved and returned

Now powered by MongoDB via Motor.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from motor.motor_asyncio import AsyncIOMotorDatabase

from core.config import settings
from models.schemas import ConversationDoc, MessageDoc, MessageRole, FileDoc
from services.ai_engine import interpret_request
from services.file_service import FileService

logger = logging.getLogger(__name__)


class ChatService:

    @staticmethod
    async def create_conversation(db: AsyncIOMotorDatabase, title: Optional[str] = None) -> ConversationDoc:
        now = datetime.now(timezone.utc)
        convo = ConversationDoc(
            title=title or "New Conversation",
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=settings.DATA_RETENTION_HOURS),
        )
        await db.conversations.insert_one(convo.to_mongo())
        return convo

    @staticmethod
    async def get_conversation(db: AsyncIOMotorDatabase, conversation_id: str) -> Optional[ConversationDoc]:
        doc = await db.conversations.find_one({"_id": conversation_id})
        return ConversationDoc.from_mongo(doc) if doc else None

    @staticmethod
    async def list_conversations(db: AsyncIOMotorDatabase, limit: int = 50, offset: int = 0) -> List[ConversationDoc]:
        now = datetime.now(timezone.utc)
        cursor = db.conversations.find(
            {"expires_at": {"$gt": now}}
        ).sort("updated_at", -1).skip(offset).limit(limit)
        return [ConversationDoc.from_mongo(doc) async for doc in cursor]

    @staticmethod
    async def delete_conversation(db: AsyncIOMotorDatabase, conversation_id: str) -> bool:
        from core.data_retention import delete_conversation_files

        convo = await ChatService.get_conversation(db, conversation_id)
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
        assistant_content = decision["explanation"]

        if decision.get("needs_clarification"):
            pass
        elif decision["operation"] != "unknown":
            try:
                result_msg, output_records = await FileService.process_operation(
                    db=db,
                    operation=decision["operation"],
                    file_ids=decision.get("file_ids", []),
                    params=decision.get("params", {}),
                    conversation_id=conversation_id,
                )
                assistant_content = f"{decision['explanation']}\n\n**Result:** {result_msg}"
                processed_files = output_records
            except Exception as e:
                assistant_content = f"I tried to {decision['explanation'].lower()}, but encountered an error: {str(e)}"

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
