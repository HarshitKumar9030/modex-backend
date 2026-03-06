"""
Conversation & Chat API routes - MongoDB edition.
"""

import asyncio
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Header
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

from core.database import get_db
from models.api_models import (
    ConversationCreate,
    ConversationOut,
    ConversationListOut,
    ChatRequest,
    ChatResponse,
    MessageOut,
    FileOut,
)
from services.chat_service import ChatService

router = APIRouter(prefix="/conversations", tags=["Conversations"])


def _get_user_id(x_user_id: Optional[str] = Header(None)) -> str:
    return x_user_id or ""


def _convo_out(c):
    return ConversationOut(id=c.id, title=c.title, created_at=c.created_at, updated_at=c.updated_at, expires_at=c.expires_at)


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


@router.post("", response_model=ConversationOut, status_code=201)
async def create_conversation(
    body: ConversationCreate = ConversationCreate(),
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(_get_user_id),
):
    convo = await ChatService.create_conversation(db, title=body.title, user_id=user_id)
    return _convo_out(convo)


@router.get("", response_model=ConversationListOut)
async def list_conversations(
    limit: int = 50,
    offset: int = 0,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(_get_user_id),
):
    convos = await ChatService.list_conversations(db, limit=limit, offset=offset, user_id=user_id)
    return ConversationListOut(conversations=[_convo_out(c) for c in convos], total=len(convos))


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(_get_user_id),
):
    convo = await ChatService.get_conversation(db, conversation_id, user_id=user_id)
    if not convo:
        raise HTTPException(404, "Conversation not found")
    return _convo_out(convo)


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(_get_user_id),
):
    deleted = await ChatService.delete_conversation(db, conversation_id, user_id=user_id)
    if not deleted:
        raise HTTPException(404, "Conversation not found")


@router.get("/{conversation_id}/messages", response_model=List[MessageOut])
async def get_messages(
    conversation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(_get_user_id),
):
    convo = await ChatService.get_conversation(db, conversation_id, user_id=user_id)
    if not convo:
        raise HTTPException(404, "Conversation not found")
    messages = await ChatService.get_messages(db, conversation_id)
    return [
        MessageOut(
            id=m.id,
            role=m.role.value if hasattr(m.role, 'value') else m.role,
            content=m.content,
            file_ids=m.file_ids,
            created_at=m.created_at,
        )
        for m in messages
    ]


@router.post("/{conversation_id}/chat", response_model=ChatResponse)
async def send_chat_message(
    conversation_id: str,
    body: ChatRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user_id: str = Depends(_get_user_id),
):
    convo = await ChatService.get_conversation(db, conversation_id, user_id=user_id)
    if not convo:
        raise HTTPException(404, "Conversation not found")

    try:
        result = await asyncio.wait_for(
            ChatService.send_message(
                db=db,
                conversation_id=conversation_id,
                user_message=body.message,
                file_ids=body.file_ids,
            ),
            timeout=180,  # 3 min hard limit for entire pipeline
        )
    except asyncio.TimeoutError:
        logger.error(f"Chat pipeline timed out for conversation {conversation_id}")
        raise HTTPException(504, "Your request took too long to process. Please try again with a simpler request or smaller files.")
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.error(f"Unexpected error in chat pipeline: {e}")
        raise HTTPException(500, "Something went wrong processing your request. Please try again.")

    processed = [_file_out(f) for f in result.get("processed_files", [])]
    msg = result["message"]

    return ChatResponse(
        message=MessageOut(
            id=msg.id,
            role=msg.role.value if hasattr(msg.role, 'value') else msg.role,
            content=msg.content,
            file_ids=msg.file_ids,
            created_at=msg.created_at,
        ),
        processed_files=processed,
    )
