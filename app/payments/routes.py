"""Payment API routes."""

import logging
import json
import os
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from typing import Optional, List
import stripe

logger = logging.getLogger(__name__)

from app.RAG.auth_utils import get_current_user
from app.core.config import settings
from app.supabase import get_admin_supabase_client
from .models import (
    AddCardRequest,
    AddCardResponse,
    PaymentMethodResponse,
    SubscribeRequest,
    SubscribeResponse,
    SubscriptionResponse,
    UserUsageResponse,
    PlanResponse,
    AddonResponse,
    PlansAndAddonsResponse,
    ValidateCouponRequest,
    ValidateCouponResponse,
    StripeConfigResponse,
    SetupFreeTrialRequest,
    SetupFreeTrialResponse,
    AgencySetupBillingRequest,
    AgencySetupBillingResponse,
    ExpireFreeTrialRequest,
)
from .stripe_service import (
    load_plans,
    load_addons,
    add_and_verify_card,
    get_user_cards,
    remove_card,
    set_default_payment_method,
    subscribe_to_plan,
    cancel_subscription,
    get_active_subscriptions,
    get_payment_history,
    get_user_usage,
    handle_webhook_event,
    validate_coupon_for_plan,
    get_plan_by_id
)

payments_router = APIRouter(prefix="/payments", tags=["payments"])


def _load_free_trial_config() -> dict:
    """Load free_trial section from plans.json."""
    plans_path = os.path.join(os.path.dirname(__file__), "plans.json")
    with open(plans_path, "r") as f:
        data = json.load(f)
    return data.get("free_trial", {})


@payments_router.get("/config", response_model=StripeConfigResponse)
async def get_stripe_config():
    """Get Stripe publishable key for frontend."""
    return {
        "publishable_key": settings.stripe_publishable_key,
        "environment": settings.environment
    }


@payments_router.get("/plans", response_model=PlansAndAddonsResponse)
async def get_plans_and_addons():
    """Get all active plans and addons."""
    plans = load_plans()
    addons = load_addons()
    
    return {
        "plans": [
            {
                "id": plan["id"],
                "name": plan["name"],
                "price": plan["price"],
                "currency": plan["currency"],
                "interval": plan["interval"],
                "features": plan["features"],
                "active": plan["active"]
            }
            for plan in plans
        ],
        "addons": [
            {
                "id": addon["id"],
                "name": addon["name"],
                "price": addon["price"],
                "currency": addon["currency"],
                "description": addon["description"],
                "active": addon["active"]
            }
            for addon in addons
        ]
    }


@payments_router.post("/add-card", response_model=AddCardResponse)
async def add_card(
    request: AddCardRequest,
    current_user: dict = Depends(get_current_user)
):
    """Add and verify a payment method with $1 charge + refund."""
    user_id = current_user["id"]
    email = current_user["email"]
    
    try:
        result = await add_and_verify_card(user_id, email, request.payment_method_id)
        
        return {
            "message": "Card added and verified successfully. $1 verification charge has been refunded.",
            "card_brand": result["card_brand"],
            "card_last4": result["card_last4"],
            "verified": result["verified"]
        }
    except stripe.error.CardError as e:
        raise HTTPException(status_code=400, detail=f"Card error: {e.user_message}")
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=500, detail=f"Payment processing error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add card: {str(e)}")


@payments_router.get("/cards", response_model=list[PaymentMethodResponse])
async def list_cards(current_user: dict = Depends(get_current_user)):
    """Get all payment methods for the current user."""
    user_id = current_user["id"]
    
    try:
        cards = await get_user_cards(user_id)
        
        return [
            {
                "id": card["id"],
                "card_brand": card["card_brand"],
                "card_last4": card["card_last4"],
                "card_exp_month": card["card_exp_month"],
                "card_exp_year": card["card_exp_year"],
                "is_default": card["is_default"],
                "verified": card["verified"],
                "created_at": card["created_at"]
            }
            for card in cards
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch cards: {str(e)}")


@payments_router.delete("/cards/{card_id}")
async def delete_card(
    card_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Remove a payment method."""
    user_id = current_user["id"]
    
    try:
        success = await remove_card(user_id, card_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Card not found")
        
        return {"message": "Card removed successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to remove card: {str(e)}")


@payments_router.post("/cards/{card_id}/default")
async def set_default_card_route(
    card_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Set a payment method as default."""
    user_id = current_user["id"]
    
    try:
        success = await set_default_payment_method(user_id, card_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Card not found or could not be set as default")
        
        return {"message": "Default payment method updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to set default card: {str(e)}")


@payments_router.post("/validate-coupon", response_model=ValidateCouponResponse)
async def validate_coupon_route(
    request: ValidateCouponRequest,
    current_user: dict = Depends(get_current_user)
):
    """Validate a coupon code for a specific plan or addon."""
    try:
        coupon = validate_coupon_for_plan(request.coupon_code, request.plan_id)
        
        if not coupon:
            return {
                "valid": False,
                "message": "Invalid or expired coupon code for this plan"
            }
            
        plan = await get_plan_by_id(request.plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")
            
        original_price = plan["price"]
        discount_amount = 0
        discount_type = None
        
        if coupon.get("amount_off"):
            discount_amount = coupon["amount_off"]
            discount_type = "amount"
        elif coupon.get("percent_off"):
            discount_amount = original_price * (coupon["percent_off"] / 100)
            discount_type = "percent"
            
        new_price = max(0, original_price - discount_amount)
        
        return {
            "valid": True,
            "message": f"Coupon applied: {coupon['name']}",
            "discount_type": discount_type,
            "discount_amount": float(discount_amount),
            "new_price": float(new_price)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to validate coupon: {str(e)}")


@payments_router.post("/subscribe", response_model=SubscribeResponse)
async def subscribe(
    request: SubscribeRequest,
    current_user: dict = Depends(get_current_user)
):
    """Subscribe to a plan."""
    user_id = current_user["id"]
    email = current_user["email"]
    
    try:
        result = await subscribe_to_plan(user_id, email, request.plan_id, request.coupon_code)
        
        return {
            "message": f"Successfully subscribed to {request.plan_id} plan",
            "subscription_id": result["subscription_id"],
            "plan_id": result["plan_id"],
            "status": result["status"],
            "amount_paid": result.get("amount_paid", 0),
            "tax_paid": result.get("tax_paid", 0),
            "discount_applied": result.get("discount_applied", 0),
            "currency": result.get("currency", "usd"),
            "current_period_end": result.get("current_period_end")
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=500, detail=f"Payment processing error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to subscribe: {str(e)}")


@payments_router.post("/cancel-subscription")
async def cancel_user_subscription(current_user: dict = Depends(get_current_user)):
    """Cancel the current subscription."""
    user_id = current_user["id"]
    
    try:
        success = await cancel_subscription(user_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="No active subscription found")
        
        return {"message": "Subscription canceled successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to cancel subscription: {str(e)}")


@payments_router.get("/subscription", response_model=List[SubscriptionResponse])
async def get_subscriptions_route(current_user: dict = Depends(get_current_user)):
    """Get all active subscription details."""
    user_id = current_user["id"]
    
    try:
        subscriptions = await get_active_subscriptions(user_id)
        return subscriptions
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch subscriptions: {str(e)}")


@payments_router.get("/history")
async def get_history_route(current_user: dict = Depends(get_current_user)):
    """Get user's payment history."""
    user_id = current_user["id"]
    
    try:
        history = await get_payment_history(user_id)
        return history
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch payment history: {str(e)}")


@payments_router.get("/user-usage", response_model=UserUsageResponse)
async def get_user_usage_route(current_user: dict = Depends(get_current_user)):
    """Get user's current subscription and credit limits."""
    user_id = current_user["id"]
    
    try:
        usage = await get_user_usage(user_id)
        return usage
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch user usage: {str(e)}")


# ─────────────────────────────────────────────────────────────
# FREE TRIAL ENDPOINTS
# ─────────────────────────────────────────────────────────────

@payments_router.post("/setup-free-trial", response_model=SetupFreeTrialResponse)
async def setup_free_trial(
    request: SetupFreeTrialRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Activate a 30-day free trial for a regular user.
    Verifies the card via a $1 charge + immediate refund, then grants trial credits.
    """
    user_id = current_user["id"]
    email = current_user["email"]
    supabase = get_admin_supabase_client()

    # 1. Verify the card ($1 charge + refund)
    try:
        card_result = await add_and_verify_card(user_id, email, request.payment_method_id)
    except Exception as e:
        err_type = type(e).__name__
        if "CardError" in err_type:
            raise HTTPException(status_code=400, detail=f"Card declined: {getattr(e, 'user_message', str(e))}")
        raise HTTPException(status_code=500, detail=f"Payment error: {str(e)}")

    # 2. Load trial quota from plans.json
    trial_config = _load_free_trial_config()
    limits = trial_config.get("regular_user", {}).get("limits", {})
    duration_days = trial_config.get("regular_user", {}).get("duration_days", 30)

    if not limits:
        raise HTTPException(status_code=500, detail="Free trial configuration not found.")

    # 3. Grant credits via RPC
    rpc_params = {
        "target_user_id": user_id,
        "add_blog_ideas": int(limits.get("blog_ideas_credits", 0)),
        "add_faq": int(limits.get("faq_credits", 0)),
        "add_blog_creation": int(limits.get("blog_creation_credits", 0)),
        "add_training_credits": int(limits.get("chatbot_training_credits", 0)),
        "add_messages_credits": int(limits.get("chatbot_messages_credits", 0)),
        "add_chatbot_count": int(limits.get("chatbot_count", 0)),
        "set_white_label": bool(limits.get("white_label", False)),
        "is_renewal": False
    }
    supabase.rpc("increment_user_balance", rpc_params).execute()

    # 4. Create subscription record in DB
    trial_ends_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
    supabase.table("subscriptions").update({"status": "canceled"}).eq("user_id", user_id).execute()
    supabase.table("subscriptions").insert({
        "user_id": user_id,
        "plan_id": "free_trial",
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
        "status": "active",
        "current_period_start": datetime.now(timezone.utc).isoformat(),
        "current_period_end": trial_ends_at.isoformat(),
        "cancel_at_period_end": False
    }).execute()

    logger.info(f"✅ Free trial activated for user {user_id}, ends {trial_ends_at.isoformat()}")
    return {
        "message": "Free trial activated! Your $1 verification charge has been refunded.",
        "card_brand": card_result["card_brand"],
        "card_last4": card_result["card_last4"],
        "trial_ends_at": trial_ends_at.isoformat()
    }


@payments_router.post("/agency-setup-billing", response_model=AgencySetupBillingResponse)
async def agency_setup_billing(
    request: AgencySetupBillingRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Verify an agency owner's card via $1 charge + refund and mark their
    account as being in the 'agency_trial' setup phase.
    """
    user_id = current_user["id"]
    email = current_user["email"]
    supabase = get_admin_supabase_client()

    # 1. Verify card ($1 charge + refund)
    try:
        card_result = await add_and_verify_card(user_id, email, request.payment_method_id)
    except Exception as e:
        err_type = type(e).__name__
        if "CardError" in err_type:
            raise HTTPException(status_code=400, detail=f"Card declined: {getattr(e, 'user_message', str(e))}")
        raise HTTPException(status_code=500, detail=f"Payment error: {str(e)}")

    # 2. Mark user's subscription as 'agency_trial' in DB
    supabase.table("subscriptions").update({"status": "canceled"}).eq("user_id", user_id).execute()
    supabase.table("subscriptions").insert({
        "user_id": user_id,
        "plan_id": "agency_trial",
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
        "status": "active",
        "current_period_start": datetime.now(timezone.utc).isoformat(),
        "current_period_end": None,
        "cancel_at_period_end": False
    }).execute()

    logger.info(f"✅ Agency billing set up for user {user_id}. Card verified.")
    return {
        "message": "Card verified! Your $1 verification charge has been refunded. Your agency account is now active — set up your platform.",
        "card_brand": card_result["card_brand"],
        "card_last4": card_result["card_last4"],
    }


@payments_router.post("/expire-free-trial")
async def expire_free_trial(
    request: ExpireFreeTrialRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    ADMIN/TESTING ONLY: Immediately expire a user's free trial.
    Sets their subscription status to 'canceled' and optionally zeros out usage balances.
    """
    supabase = get_admin_supabase_client()
    target_user_id = request.target_user_id

    # Cancel the active free trial subscription
    supabase.table("subscriptions") \
        .update({
            "status": "canceled",
            "current_period_end": datetime.now(timezone.utc).isoformat()
        }) \
        .eq("user_id", target_user_id) \
        .in_("plan_id", ["free_trial", "agency_trial"]) \
        .execute()

    if request.zero_out_balances:
        # Zero out the total limits so every feature is immediately locked
        supabase.table("user_usage_balances").update({
            "blog_ideas_credits_total": 0,
            "faq_credits_total": 0,
            "blog_creation_credits_total": 0,
            "chatbot_messages_credits_total": 0,
            "chatbot_training_credits_total": 0,
            "chatbot_count_allowed": 0
        }).eq("user_id", target_user_id).execute()

    logger.info(f"🧪 Free trial expired for user {target_user_id} by admin {current_user['id']}")
    return {
        "message": f"Free trial for user {target_user_id} has been expired.",
        "zero_out_balances": request.zero_out_balances
    }


@payments_router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="stripe-signature")
):
    """Handle Stripe webhook events."""
    payload = await request.body()
    
    # 🔍 Debug Signature Issues
    secret = settings.stripe_webhook_secret or ""
    logger.info(f"🔔 Webhook received. Payload length: {len(payload)}")
    logger.info(f"Header Signature: {stripe_signature[:20]}..." if stripe_signature else "Header Signature: MISSING")
    logger.info(f"Using Secret: {secret[:8]}...{secret[-4:]}" if secret else "Using Secret: EMPTY")

    try:
        # Verify webhook signature
        event = stripe.Webhook.construct_event(
            payload,
            stripe_signature,
            secret
        )
        
        # Handle the event
        await handle_webhook_event(event)
        
        return {"status": "success"}
    except ValueError:
        logger.error("❌ Webhook error: Invalid payload")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"❌ Webhook error: Invalid signature. {str(e)}")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        logger.error(f"❌ Webhook error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Webhook error: {str(e)}")
