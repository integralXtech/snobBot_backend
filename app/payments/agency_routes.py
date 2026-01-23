from fastapi import APIRouter, Depends, HTTPException, Query
from app.RAG.auth_utils import get_current_user
from .stripe_service import (
    generate_agency_connect_url,
    handle_agency_connect_callback,
    subscribe_to_agency_plan
)
from pydantic import BaseModel
from typing import Optional

agency_payments_router = APIRouter(prefix="/payments/agency", tags=["Agency Payments"])

class AgencySubscribeRequest(BaseModel):
    plan_id: str
    agency_id: str
    coupon_code: Optional[str] = None

@agency_payments_router.get("/connect-url")
async def get_connect_url(
    redirect_uri: str = Query(..., description="The UI URL to return to after Stripe onboarding"),
    current_user: dict = Depends(get_current_user)
):
    """Generate Stripe Connect onboarding URL."""
    try:
        url = await generate_agency_connect_url(current_user["id"], redirect_uri)
        return {"url": url}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate Connect URL: {str(e)}")

@agency_payments_router.get("/callback")
async def connect_callback(
    code: str,
    state: str, # agency_id passed as state
):
    """Handle Stripe Connect OAuth callback."""
    success = await handle_agency_connect_callback(code, state)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to finalize Stripe Connect onboarding.")
    return {"status": "success", "message": "Stripe Connect account linked successfully!"}

@agency_payments_router.post("/subscribe")
async def subscribe_agency(
    request: AgencySubscribeRequest,
    current_user: dict = Depends(get_current_user)
):
    """User subscribes to an Agency's white-label plan."""
    try:
        result = await subscribe_to_agency_plan(
            user_id=current_user["id"],
            email=current_user["email"],
            plan_id=request.plan_id,
            agency_id=request.agency_id,
            coupon_code=request.coupon_code
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agency subscription failed: {str(e)}")
