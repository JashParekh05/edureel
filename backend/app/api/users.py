import logging
from fastapi import APIRouter, Depends, HTTPException
from app.models.schemas import InterestsPayload
from app.db.supabase import get_client
from app.auth import require_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/{user_id}/profile")
async def get_profile(user_id: str, caller_id: str = Depends(require_user)):
    if caller_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    try:
        db = get_client()
        row = db.table("user_profiles").select("*").eq("user_id", user_id).limit(1).execute()
        if row.data:
            return row.data[0]
    except Exception as e:
        logger.warning(f"get_profile failed: {e}")
    return {"user_id": user_id, "interests": [], "onboarding_complete": False}


@router.post("/{user_id}/interests", status_code=204)
async def set_interests(user_id: str, payload: InterestsPayload, caller_id: str = Depends(require_user)):
    if caller_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    try:
        db = get_client()
        db.table("user_profiles").upsert({
            "user_id": user_id,
            "interests": payload.interests,
            "onboarding_complete": True,
        }).execute()
    except Exception as e:
        logger.warning(f"set_interests failed: {e}")
