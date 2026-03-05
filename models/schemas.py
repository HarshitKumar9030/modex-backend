"""
MongoDB document schemas — Pydantic models for conversations, messages, files, and beta signups.
"""

import uuid
import enum
from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel, Field


def generate_uuid() -> str:
    return str(uuid.uuid4())


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class FileStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class BetaStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


# ── Document shapes (what gets stored in MongoDB) ─────────────────

class ConversationDoc(BaseModel):
    id: str = Field(default_factory=generate_uuid)
    user_id: str = ""
    title: Optional[str] = "New Conversation"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(...)

    def to_mongo(self) -> dict:
        d = self.model_dump()
        d["_id"] = d.pop("id")
        return d

    @classmethod
    def from_mongo(cls, doc: dict) -> "ConversationDoc":
        if doc and "_id" in doc:
            doc["id"] = doc.pop("_id")
        return cls(**doc)


class MessageDoc(BaseModel):
    id: str = Field(default_factory=generate_uuid)
    conversation_id: str
    role: MessageRole
    content: str
    file_ids: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_mongo(self) -> dict:
        d = self.model_dump()
        d["_id"] = d.pop("id")
        d["role"] = d["role"].value if isinstance(d["role"], MessageRole) else d["role"]
        return d

    @classmethod
    def from_mongo(cls, doc: dict) -> "MessageDoc":
        if doc and "_id" in doc:
            doc["id"] = doc.pop("_id")
        return cls(**doc)


class FileDoc(BaseModel):
    id: str = Field(default_factory=generate_uuid)
    conversation_id: str
    original_filename: str
    storage_path: str
    output_path: Optional[str] = None
    mime_type: str
    file_size: int
    file_type: str  # "pdf", "image", "audio", "document"
    status: FileStatus = FileStatus.UPLOADED
    operation: Optional[str] = None
    operation_params: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    exported: bool = False

    def to_mongo(self) -> dict:
        d = self.model_dump()
        d["_id"] = d.pop("id")
        d["status"] = d["status"].value if isinstance(d["status"], FileStatus) else d["status"]
        return d

    @classmethod
    def from_mongo(cls, doc: dict) -> "FileDoc":
        if doc and "_id" in doc:
            doc["id"] = doc.pop("_id")
        return cls(**doc)


class BetaSignupDoc(BaseModel):
    id: str = Field(default_factory=generate_uuid)
    email: str
    status: BetaStatus = BetaStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_mongo(self) -> dict:
        d = self.model_dump()
        d["_id"] = d.pop("id")
        d["status"] = d["status"].value if isinstance(d["status"], BetaStatus) else d["status"]
        return d

    @classmethod
    def from_mongo(cls, doc: dict) -> "BetaSignupDoc":
        if doc and "_id" in doc:
            doc["id"] = doc.pop("_id")
        return cls(**doc)


class FeedbackDoc(BaseModel):
    id: str = Field(default_factory=generate_uuid)
    email: str
    type: str = "feedback"
    subject: str = ""
    message: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_mongo(self) -> dict:
        d = self.model_dump()
        d["_id"] = d.pop("id")
        return d

    @classmethod
    def from_mongo(cls, doc: dict) -> "FeedbackDoc":
        if doc and "_id" in doc:
            doc["id"] = doc.pop("_id")
        return cls(**doc)
