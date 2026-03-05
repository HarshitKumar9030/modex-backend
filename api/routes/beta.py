"""
Beta signup & admin management routes - MongoDB edition.
"""

from fastapi import APIRouter, Depends, HTTPException, Header
from motor.motor_asyncio import AsyncIOMotorDatabase

from core.config import settings
from core.database import get_db
from models.schemas import BetaSignupDoc, BetaStatus
from models.api_models import (
    BetaSignupRequest,
    BetaSignupResponse,
    BetaCheckResponse,
    BetaListOut,
    BetaSignupOut,
    BetaUpdateRequest,
)

router = APIRouter(tags=["Beta"])


# ── Public endpoints ──────────────────────────────────────────────

@router.post("/beta/signup", response_model=BetaSignupResponse)
async def beta_signup(
    body: BetaSignupRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Submit an email for beta access."""
    if not settings.BETA_ENABLED:
        raise HTTPException(400, "Beta signups are currently closed")

    existing = await db.beta_signups.find_one({"email": body.email})
    if existing:
        doc = BetaSignupDoc.from_mongo(existing)
        return BetaSignupResponse(
            message="You've already signed up for the beta!",
            status=doc.status.value if hasattr(doc.status, "value") else doc.status,
        )

    signup = BetaSignupDoc(email=body.email)
    await db.beta_signups.insert_one(signup.to_mongo())

    return BetaSignupResponse(message="Thanks for signing up! We'll review your request.", status="pending")


@router.get("/beta/check", response_model=BetaCheckResponse)
async def beta_check(
    email: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Check if an email has been approved for beta access."""
    doc = await db.beta_signups.find_one({"email": email})
    if not doc:
        return BetaCheckResponse(approved=False)

    return BetaCheckResponse(approved=doc.get("status") == BetaStatus.APPROVED.value)


# ── Admin endpoints (protected by ADMIN_SECRET header) ────────────

def _verify_admin(x_admin_secret: str = Header(...)):
    if x_admin_secret != settings.ADMIN_SECRET:
        raise HTTPException(403, "Invalid admin secret")


@router.get("/admin/beta", response_model=BetaListOut, dependencies=[Depends(_verify_admin)])
async def admin_list_beta(
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """List all beta signups. Optionally filter by status."""
    query = {}
    if status:
        query["status"] = status

    total = await db.beta_signups.count_documents(query)
    cursor = db.beta_signups.find(query).sort("created_at", -1).skip(offset).limit(limit)

    signups = []
    async for doc in cursor:
        s = BetaSignupDoc.from_mongo(doc)
        signups.append(BetaSignupOut(
            id=s.id,
            email=s.email,
            status=s.status.value if hasattr(s.status, "value") else s.status,
            created_at=s.created_at,
        ))

    return BetaListOut(signups=signups, total=total)


@router.patch("/admin/beta/{signup_id}", response_model=BetaSignupOut, dependencies=[Depends(_verify_admin)])
async def admin_update_beta(
    signup_id: str,
    body: BetaUpdateRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Approve or reject a beta signup."""
    result = await db.beta_signups.find_one_and_update(
        {"_id": signup_id},
        {"$set": {"status": body.status}},
        return_document=True,
    )
    if not result:
        raise HTTPException(404, "Signup not found")

    s = BetaSignupDoc.from_mongo(result)
    return BetaSignupOut(
        id=s.id,
        email=s.email,
        status=s.status.value if hasattr(s.status, "value") else s.status,
        created_at=s.created_at,
    )


@router.delete("/admin/beta/{signup_id}", status_code=204, dependencies=[Depends(_verify_admin)])
async def admin_delete_beta(
    signup_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Delete a beta signup."""
    result = await db.beta_signups.delete_one({"_id": signup_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Signup not found")
