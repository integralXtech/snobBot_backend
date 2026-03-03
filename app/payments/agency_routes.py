from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from app.RAG.auth_utils import get_current_user
from .stripe_service import (
    generate_agency_connect_url,
    handle_agency_connect_callback,
    subscribe_to_agency_plan,
    subscribe_to_agency_plan_dummy,
    get_checkout_session_details
)
from pydantic import BaseModel
from typing import Optional

agency_payments_router = APIRouter(prefix="/payments/agency", tags=["Agency Payments"])

class AgencySubscribeRequest(BaseModel):
    plan_id: str
    agency_id: str
    coupon_code: Optional[str] = None
    origin: Optional[str] = None

@agency_payments_router.get("/connect-url")
async def get_connect_url(
    redirect_uri: str = Query(..., description="The UI URL to return to after Stripe onboarding"),
    email: Optional[str] = Query(None, description="The email entered in the UI"),
    current_user: dict = Depends(get_current_user)
):
    """Generate Stripe Connect onboarding URL."""
    try:
        # Use our own backend callback as the redirect_uri for Stripe
        # This MUST match what is in the Stripe Dashboard
        from app.core.config import settings
        backend_callback = f"{settings.backend_url}/api/payments/agency/callback"
        
        url = await generate_agency_connect_url(current_user["id"], backend_callback, email)
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
    from app.core.config import settings
    
    success = await handle_agency_connect_callback(code, state)
    if not success:
        # Redirect to dashboard with error parameter
        return RedirectResponse(url=f"{settings.frontend_url}/white-label/dashboard?stripe_error=true")
        
    # Redirect back to the white-label dashboard
    # The frontend will know it's connected because it will re-fetch the agency settings
    return RedirectResponse(url=f"{settings.frontend_url}/white-label/dashboard?stripe_connected=true")

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
            coupon_code=request.coupon_code,
            origin=request.origin
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agency subscription failed: {str(e)}")

@agency_payments_router.post("/dummy-subscribe")
async def subscribe_agency_dummy(
    request: AgencySubscribeRequest,
    current_user: dict = Depends(get_current_user)
):
    """User subscribes to an Agency's white-label plan via DUMMY flow."""
    try:
        result = await subscribe_to_agency_plan_dummy(
            user_id=current_user["id"],
            email=current_user["email"],
            plan_id=request.plan_id,
            agency_id=request.agency_id
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"Dummy agency subscription failed: {e}")
        raise e

@agency_payments_router.get("/session-status")
async def get_session_status(
    session_id: str = Query(..., description="The Stripe Checkout Session ID"),
    current_user: dict = Depends(get_current_user)
):
    """Get the status and details of a Stripe Checkout session."""
    details = await get_checkout_session_details(session_id)
    if not details:
        raise HTTPException(status_code=404, detail="Session not found or error retrieving details.")
    return details
