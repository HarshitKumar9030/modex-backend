""""""

































































        raise HTTPException(404, "Feedback not found")    if result.deleted_count == 0:    result = await db.feedbacks.delete_one({"_id": feedback_id})    """Delete a feedback entry."""):    db: AsyncIOMotorDatabase = Depends(get_db),    feedback_id: str,async def admin_delete_feedback(@router.delete("/admin/feedbacks/{feedback_id}", status_code=204, dependencies=[Depends(_verify_admin)])    return FeedbackListOut(feedbacks=feedbacks, total=total)        ))            created_at=fb.created_at,            message=fb.message,            email=fb.email,            name=fb.name,            id=fb.id,        feedbacks.append(FeedbackOut(        fb = FeedbackDoc.from_mongo(doc)    async for doc in cursor:    feedbacks = []    cursor = db.feedbacks.find({}).sort("created_at", -1).skip(offset).limit(limit)    total = await db.feedbacks.count_documents({})    """List all feedbacks for admin."""):    db: AsyncIOMotorDatabase = Depends(get_db),    offset: int = 0,    limit: int = 100,async def admin_list_feedbacks(@router.get("/admin/feedbacks", response_model=FeedbackListOut, dependencies=[Depends(_verify_admin)])    return {"message": "Thank you for your feedback!"}        await db.feedbacks.insert_one(feedback.to_mongo())    feedback = FeedbackDoc(name=body.name, email=body.email, message=body.message)    """Submit a contact/feedback message."""):    db: AsyncIOMotorDatabase = Depends(get_db),    body: FeedbackCreateRequest,async def submit_feedback(@router.post("/contact", response_model=dict)        raise HTTPException(403, "Invalid admin secret")    if x_admin_secret != settings.ADMIN_SECRET:def _verify_admin(x_admin_secret: str = Header(...)):router = APIRouter(tags=["Contact"])from models.api_models import FeedbackCreateRequest, FeedbackOut, FeedbackListOutfrom models.schemas import FeedbackDocfrom core.config import settingsfrom core.database import get_dbfrom motor.motor_asyncio import AsyncIOMotorDatabasefrom fastapi import APIRouter, Depends, HTTPException, Header"""Contact & Feedback routesContact & Feedback routes
"""

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
