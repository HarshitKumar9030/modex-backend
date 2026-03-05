"""Contact & Feedback routes."""

from fastapi import APIRouter, Depends, HTTPException, Header
from motor.motor_asyncio import AsyncIOMotorDatabase

from core.database import get_db
from core.config import settings
from models.schemas import FeedbackDoc
from models.api_models import FeedbackCreateRequest, FeedbackOut, FeedbackListOut

router = APIRouter(tags=["Contact"])


def _verify_admin(x_admin_secret: str = Header(...)):
    if x_admin_secret != settings.ADMIN_SECRET:
        raise HTTPException(403, "Invalid admin secret")


@router.post("/contact", response_model=dict)
async def submit_feedback(
    body: FeedbackCreateRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Submit a contact/feedback message."""
    feedback = FeedbackDoc(
        email=body.email,
        type=body.type,
        subject=body.subject,
        message=body.message,
    )
    await db.feedbacks.insert_one(feedback.to_mongo())
    return {"message": "Thank you for your feedback!"}


@router.get("/admin/feedbacks", response_model=FeedbackListOut, dependencies=[Depends(_verify_admin)])
async def admin_list_feedbacks(
    limit: int = 100,
    offset: int = 0,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """List all feedbacks for admin."""
    total = await db.feedbacks.count_documents({})
    cursor = db.feedbacks.find({}).sort("created_at", -1).skip(offset).limit(limit)
    feedbacks = []
    async for doc in cursor:
        fb = FeedbackDoc.from_mongo(doc)
        feedbacks.append(FeedbackOut(
            id=fb.id,
            email=fb.email,
            type=fb.type,
            subject=fb.subject,
            message=fb.message,
            created_at=fb.created_at,
        ))
    return FeedbackListOut(feedbacks=feedbacks, total=total)


@router.delete("/admin/feedbacks/{feedback_id}", status_code=204, dependencies=[Depends(_verify_admin)])
async def admin_delete_feedback(
    feedback_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Delete a feedback entry."""
    result = await db.feedbacks.delete_one({"_id": feedback_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Feedback not found")

def _verify_admin(x_admin_secret: str = Header(...)):
    if x_admin_secret != settings.ADMIN_SECRET:
        raise HTTPException(403, "Invalid admin secret")


@router.post("/contact", response_model=dict)
async def submit_feedback(
    body: FeedbackCreateRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Submit a contact/feedback message."""
    feedback = FeedbackDoc(name=body.name, email=body.email, message=body.message)
    await db.feedbacks.insert_one(feedback.to_mongo())
    
    return {"message": "Thank you for your feedback!"}


@router.get("/admin/feedbacks", response_model=FeedbackListOut, dependencies=[Depends(_verify_admin)])
async def admin_list_feedbacks(
    limit: int = 100,
    offset: int = 0,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """List all feedbacks for admin."""
    total = await db.feedbacks.count_documents({})
    cursor = db.feedbacks.find({}).sort("created_at", -1).skip(offset).limit(limit)

    feedbacks = []
    async for doc in cursor:
        fb = FeedbackDoc.from_mongo(doc)
        feedbacks.append(FeedbackOut(
            id=fb.id,
            name=fb.name,
            email=fb.email,
            message=fb.message,
            created_at=fb.created_at,
        ))

    return FeedbackListOut(feedbacks=feedbacks, total=total)


@router.delete("/admin/feedbacks/{feedback_id}", status_code=204, dependencies=[Depends(_verify_admin)])
async def admin_delete_feedback(
    feedback_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Delete a feedback entry."""
    result = await db.feedbacks.delete_one({"_id": feedback_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Feedback not found")
