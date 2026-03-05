"""
Pydantic models for API request/response validation.
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, EmailStr


# ─── Conversations ────────────────────────────────────────────────

class ConversationCreate(BaseModel):
    title: Optional[str] = None


class ConversationOut(BaseModel):
    id: str
    title: Optional[str]
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class ConversationListOut(BaseModel):
    conversations: List[ConversationOut]
    total: int


# ─── Messages ─────────────────────────────────────────────────────

class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    file_ids: Optional[str] = None
    created_at: datetime


class ChatRequest(BaseModel):
    """User sends a message (optionally with file IDs already uploaded)."""
    message: str = Field(..., min_length=1, max_length=5000)
    file_ids: Optional[List[str]] = Field(default=None)


class ChatResponse(BaseModel):
    """Response from the assistant after processing."""
    message: MessageOut
    processed_files: List[FileOut] = []


# ─── Files ────────────────────────────────────────────────────────

class FileOut(BaseModel):
    id: str
    original_filename: str
    mime_type: str
    file_size: int
    file_type: str
    status: str
    operation: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    has_output: bool = False


class FileUploadOut(BaseModel):
    files: List[FileOut]
    message: str


# ─── Beta ─────────────────────────────────────────────────────────

class BetaSignupRequest(BaseModel):
    email: EmailStr


class BetaSignupResponse(BaseModel):
    message: str
    status: str


class BetaCheckResponse(BaseModel):
    approved: bool


class BetaListOut(BaseModel):
    signups: List[BetaSignupOut]
    total: int


class BetaSignupOut(BaseModel):
    id: str
    email: str
    status: str
    created_at: datetime


class BetaUpdateRequest(BaseModel):
    status: str = Field(..., pattern="^(approved|rejected)$")


# ─── Feedback / Contact ──────────────────────────────────────────

class FeedbackCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    message: str = Field(..., min_length=10, max_length=5000)

class FeedbackOut(BaseModel):
    id: str
    name: str
    email: str
    message: str
    created_at: datetime

class FeedbackListOut(BaseModel):
    feedbacks: List[FeedbackOut]
    total: int


# ─── AI Operation Schema ─────────────────────────────────────────

class OperationDecision(BaseModel):
    operation: str
    file_ids: List[str] = Field(default_factory=list)
    params: dict = Field(default_factory=dict)
    explanation: str = Field("")
    needs_clarification: bool = Field(False)


# ─── Health ───────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    data_retention_hours: int


# Fix forward reference
ChatResponse.model_rebuild()
BetaListOut.model_rebuild()
