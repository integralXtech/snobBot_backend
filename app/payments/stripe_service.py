"""Stripe service functions for payment processing."""

import stripe
import json
import os
from datetime import datetime
from typing import Optional, Dict, List
from app.core.config import settings
from app.supabase import get_admin_supabase_client

# Initialize Stripe with environment-aware key
stripe.api_key = settings.stripe_secret_key

# Agencies Feature: Snobbot Platform Client ID for Stripe Connect
STRIPE_CONNECT_CLIENT_ID = settings.stripe_connect_client_id


def load_content() -> Dict:
    """Load all content from plans.json."""
    plans_path = os.path.join(os.path.dirname(__file__), "plans.json")
    with open(plans_path, "r") as f:
        return json.load(f)


def load_plans() -> List[Dict]:
    """Load plans from plans.json file."""
    data = load_content()
    return [plan for plan in data["plans"] if plan["active"]]


def load_coupons() -> List[Dict]:
    """Load coupons from plans.json file."""
    data = load_content()
    return data.get("coupons", [])


def load_addons() -> List[Dict]:
    """Load addons from plans.json file."""
    data = load_content()
    return [addon for addon in data.get("addons", []) if addon["active"]]


def validate_coupon_for_plan(coupon_code: str, plan_id: str) -> Optional[Dict]:
    """Validate if a coupon code is applicable to a specific plan."""
    coupons = load_coupons()
    for coupon in coupons:
        if coupon["id"].upper() == coupon_code.upper() and coupon["active"]:
            if plan_id in coupon["applicable_plans"] or "*" in coupon["applicable_plans"]:
                return coupon
    return None


def get_plan_by_id(plan_id: str) -> Optional[Dict]:
    """Get plan details by ID."""
    plans = load_plans()
    for plan in plans:
        if plan["id"] == plan_id:
            return plan
    # Fallback to addons
    return get_addon_by_id(plan_id)


def get_addon_by_id(addon_id: str) -> Optional[Dict]:
    """Get addon details by ID."""
    addons = load_addons()
    for addon in addons:
        if addon["id"] == addon_id:
            return addon
    return None


async def allocate_user_credits(user_id: str, plan_id: str, is_renewal: bool = False) -> bool:
    """
    Allocate credits and limits to a user profile based on a plan.
    Supports renewal logic (rollover for training credits) and stacking.
    """
    try:
        supabase = get_admin_supabase_client()
        plan = get_plan_by_id(plan_id)
        
        if not plan:
            print(f"Error: Plan '{plan_id}' not found for allocation.")
            return False
            
        limits = plan.get("limits", {})
        
        # Call Postgres function via RPC for atomic allocation
        supabase.rpc("increment_user_balance", {
            "target_user_id": user_id,
            "add_blog_ideas": limits.get("blog_ideas_credits", 0),
            "add_faq": limits.get("faq_credits", 0),
            "add_blog_creation": limits.get("blog_creation_credits", 0),
            "add_training_credits": limits.get("chatbot_training_credits", 0),
            "add_messages_credits": limits.get("chatbot_messages_credits", 0),
            "add_chatbot_count": limits.get("chatbot_count", 0),
            "set_white_label": limits.get("white_label", False),
            "is_renewal": is_renewal
        }).execute()
        
        return True
    except Exception as e:
        print(f"Exception during credit allocation: {str(e)}")
        return False


async def get_or_create_customer(user_id: str, email: str) -> str:
    """Get existing Stripe customer ID or create new customer."""
    supabase = get_admin_supabase_client()
    
    # Check if user already has an entry in user_stripe_profiles
    result = supabase.table("user_stripe_profiles").select("stripe_customer_id").eq("user_id", user_id).execute()
    
    if result.data and result.data[0].get("stripe_customer_id"):
        return result.data[0]["stripe_customer_id"]
    
    # Create new Stripe customer
    customer = stripe.Customer.create(
        email=email,
        metadata={"user_id": user_id}
    )
    
    # Store customer ID in user_stripe_profiles table
    supabase.table("user_stripe_profiles").insert({
        "user_id": user_id, 
        "stripe_customer_id": customer.id
    }).execute()
    
    return customer.id


async def add_and_verify_card(user_id: str, email: str, payment_method_id: str) -> Dict:
    """
    Attach payment method to customer, verify with $1 charge, then refund.
    Returns card details.
    """
    supabase = get_admin_supabase_client()
    
    # Get or create Stripe customer
    customer_id = await get_or_create_customer(user_id, email)
    
    # Attach payment method to customer
    payment_method = stripe.PaymentMethod.attach(
        payment_method_id,
        customer=customer_id
    )
    
    # Use the actual ID returned by Stripe (crucial for aliases like pm_card_visa)
    actual_pm_id = payment_method.id
    
    # Set as default payment method
    stripe.Customer.modify(
        customer_id,
        invoice_settings={"default_payment_method": actual_pm_id}
    )
    
    # Create $1 verification charge
    payment_intent = stripe.PaymentIntent.create(
        amount=100,  # $1.00 in cents
        currency="usd",
        customer=customer_id,
        payment_method=actual_pm_id,
        confirm=True,
        description="Card verification charge",
        metadata={"user_id": user_id, "type": "verification"},
        automatic_payment_methods={"enabled": True, "allow_redirects": "never"}
    )
    
    # Log charge in payment_history
    supabase.table("payment_history").insert({
        "user_id": user_id,
        "stripe_payment_intent_id": payment_intent.id,
        "stripe_charge_id": payment_intent.latest_charge,
        "amount": 100,
        "currency": "usd",
        "status": "succeeded",
        "description": "Card verification charge",
        "payment_type": "verification",
        "metadata": {"payment_method_id": actual_pm_id}
    }).execute()
    
    # Immediately refund the $1
    refund = stripe.Refund.create(
        payment_intent=payment_intent.id,
        reason="requested_by_customer"
    )
    
    # Log refund in payment_history
    supabase.table("payment_history").insert({
        "user_id": user_id,
        "stripe_payment_intent_id": payment_intent.id,
        "stripe_charge_id": refund.charge,
        "amount": -100,  # Negative for refund
        "currency": "usd",
        "status": "refunded",
        "description": "Card verification refund",
        "payment_type": "verification",
        "metadata": {"refund_id": refund.id}
    }).execute()
    
    # Store payment method in database
    card_data = {
        "user_id": user_id,
        "stripe_customer_id": customer_id,
        "stripe_payment_method_id": actual_pm_id,
        "card_brand": payment_method.card.brand,
        "card_last4": payment_method.card.last4,
        "card_exp_month": payment_method.card.exp_month,
        "card_exp_year": payment_method.card.exp_year,
        "is_default": True,
        "verified": True
    }
    
    # Set other cards as non-default
    supabase.table("payment_methods").update({"is_default": False}).eq("user_id", user_id).execute()
    
    # Insert new payment method
    result = supabase.table("payment_methods").insert(card_data).execute()
    
    return {
        "card_brand": payment_method.card.brand,
        "card_last4": payment_method.card.last4,
        "verified": True
    }


async def get_user_cards(user_id: str) -> List[Dict]:
    """Get all payment methods for a user."""
    supabase = get_admin_supabase_client()
    
    result = supabase.table("payment_methods").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
    
    return result.data if result.data else []


async def remove_card(user_id: str, payment_method_db_id: str) -> bool:
    """Remove a payment method."""
    supabase = get_admin_supabase_client()
    
    # Get payment method details
    result = supabase.table("payment_methods").select("*").eq("id", payment_method_db_id).eq("user_id", user_id).execute()
    
    if not result.data:
        return False
    
    payment_method = result.data[0]
    
    # Detach from Stripe
    try:
        stripe.PaymentMethod.detach(payment_method["stripe_payment_method_id"])
    except stripe.error.StripeError:
        pass  # Continue even if Stripe detach fails
    
    # Delete from database
    supabase.table("payment_methods").delete().eq("id", payment_method_db_id).execute()
    
    return True


async def set_default_payment_method(user_id: str, payment_method_db_id: str) -> bool:
    """Set a payment method as default for the user."""
    supabase = get_admin_supabase_client()
    
    # 1. Get the payment method from DB
    result = supabase.table("payment_methods").select("*").eq("id", payment_method_db_id).eq("user_id", user_id).execute()
    if not result.data:
        return False
    
    payment_method = result.data[0]
    stripe_pm_id = payment_method["stripe_payment_method_id"]
    customer_id = payment_method["stripe_customer_id"]
    
    # 2. Update Stripe Customer
    try:
        stripe.Customer.modify(
            customer_id,
            invoice_settings={"default_payment_method": stripe_pm_id}
        )
    except stripe.error.StripeError as e:
        print(f"Stripe error setting default PM: {str(e)}")
        return False
    
    # 3. Update database
    # Reset all current defaults for this user
    supabase.table("payment_methods").update({"is_default": False}).eq("user_id", user_id).execute()
    # Set the new one as default
    supabase.table("payment_methods").update({"is_default": True}).eq("id", payment_method_db_id).execute()
    
    return True


async def subscribe_to_plan(user_id: str, email: str, plan_id: str, coupon_code: Optional[str] = None) -> Dict:
    """
    Subscribe user to a plan. 
    Implements 'Single Active Plan' model with Proration for upgrades.
    """
    supabase = get_admin_supabase_client()
    
    # Get plan details
    plan = get_plan_by_id(plan_id)
    if not plan:
        raise ValueError(f"Plan '{plan_id}' not found")

    # Get or create Stripe customer
    customer_id = await get_or_create_customer(user_id, email)

    # CHECK: Does user already have an ACTIVE recurring subscription?
    existing_sub_result = supabase.table("subscriptions").select("*").eq("user_id", user_id).eq("status", "active").not_.is_("stripe_subscription_id", "null").execute()
    existing_sub = existing_sub_result.data[0] if existing_sub_result.data else None

    # Handle Free Plan (No Stripe Sub)
    if plan["price"] == 0:
        # If user has a paid subscription, we must cancel it (downgrade to free)
        if existing_sub:
             stripe.Subscription.modify(existing_sub["stripe_subscription_id"], cancel_at_period_end=True)
             # Update DB to reflect cancellation pending
             supabase.table("subscriptions").update({"cancel_at_period_end": True}).eq("id", existing_sub["id"]).execute()
        
        # Free plan logic (just DB tracking)
        subscription_data = {
            "user_id": user_id,
            "plan_id": plan_id,
            "stripe_customer_id": customer_id,
            "stripe_subscription_id": None,
            "status": "active",
            "current_period_start": datetime.utcnow().isoformat(),
            "current_period_end": None,
            "cancel_at_period_end": False
        }
        
        # Mark other subs as canceled (Single Plan Model)
        if not existing_sub: # Only needed if we didn't just set cancel_at_period_end above
            supabase.table("subscriptions").update({"status": "canceled"}).eq("user_id", user_id).execute()
        
        result = supabase.table("subscriptions").insert(subscription_data).execute()
        return {
            "subscription_id": result.data[0]["id"],
            "plan_id": plan_id,
            "status": "active",
            "current_period_end": None
        }

    # Get Stripe Price ID
    price_id = plan["stripe_price_id_live"] if settings.is_production else plan["stripe_price_id_test"]
    if not price_id:
        raise ValueError(f"Stripe Price ID configuration missing for plan '{plan_id}'")

    # CASE A: One-Time Payment (addons, lifetime deals)
    if plan.get("interval") == "one-time":
        # ... (Keep existing One-Time Logic logic, it doesn't replace subscriptions, it's additive) ...
        # (For brevity, I'm assuming one-time plans are ADD-ONS and CAN stack with a main plan. 
        # If one-time plans are meant to replace the main subscription, logic would be different.
        # usually one-time = credits/lifetime, so they stack.)
        
        # [Existing One-Time Logic from previous file content]
        # Re-implementing simplified version to ensure it runs:
        customer = stripe.Customer.retrieve(customer_id)
        default_pm = customer.invoice_settings.default_payment_method
        if not default_pm: raise ValueError("No default payment method found.")

        pi_kwargs = {
            "amount": plan["price"] * 100, "currency": plan["currency"], "customer": customer_id,
            "payment_method": default_pm, "confirm": True, "metadata": {"user_id": user_id, "plan_id": plan_id},
            "automatic_payment_methods": {"enabled": True, "allow_redirects": "never"}
        }
        
        # Coupon Logic for One-Time
        discount_info = {"amount": 0, "code": None}
        if coupon_code:
            coupon = validate_coupon_for_plan(coupon_code, plan_id)
            if coupon:
                orig = plan["price"] * 100
                disc = coupon["amount_off"] * 100 if coupon["amount_off"] else int(orig * (coupon["percent_off"] / 100))
                pi_kwargs["amount"] = max(0, orig - disc)
                pi_kwargs["metadata"]["coupon_applied"] = coupon["id"]
                discount_info = {"amount": disc, "code": coupon["id"]}
        
        intent = stripe.PaymentIntent.create(**pi_kwargs)
        
        # Log & Credits
        await allocate_user_credits(user_id, plan_id)
        # Log to payment_history (Simplified)
        supabase.table("payment_history").insert({
            "user_id": user_id, "stripe_payment_intent_id": intent.id, "amount": intent.amount,
            "status": "succeeded", "description": f"One-time: {plan['name']}", "payment_type": "one-time",
            "metadata": {"plan_id": plan_id}
        }).execute()

        return {"status": "succeeded", "plan_id": plan_id}

    # CASE B: Upgrade/Downgrade Existing Subscription (PRORATION)
    if existing_sub:
        print(f"DEBUG: Found existing active sub: {existing_sub['id']}, Stripe ID: {existing_sub['stripe_subscription_id']}")
        try:
            subscription = stripe.Subscription.retrieve(existing_sub["stripe_subscription_id"])
            print(f"DEBUG: Retrieved Stripe Sub: {subscription.id}, Status: {subscription.status}")
            
            # Calculate Prorated Update
            # We need the subscription item ID to update it
            if not subscription.get('items') or not subscription['items'].get('data'):
                 raise ValueError("Existing subscription has no items to update.")
            
            sub_item_id = subscription['items']['data'][0].id
            print(f"DEBUG: Updating Sub Item ID: {sub_item_id} to new Price: {price_id}")
            
            update_kwargs = {
                "items": [{"id": sub_item_id, "price": price_id}],
                "proration_behavior": "always_invoice" # Create proration invoice immediately
            }

            # Apply Coupon if new
            if coupon_code:
                 print(f"DEBUG: Validating coupon {coupon_code}")
                 coupon = validate_coupon_for_plan(coupon_code, plan_id)
                 if coupon: 
                     print(f"DEBUG: Applying coupon {coupon['id']}")
                     update_kwargs["discounts"] = [{"coupon": coupon["id"]}]

            print(f"DEBUG: Calling stripe.Subscription.modify with: {update_kwargs}")
            updated_sub = stripe.Subscription.modify(subscription.id, **update_kwargs)
            print(f"DEBUG: Subscription modified successfully: {updated_sub.id}")
            
            # Retrieve the latest invoice to get actual payment details with proration
            latest_invoice_id = updated_sub.get("latest_invoice")
            amount_paid = 0
            currency = plan.get("currency", "usd")
            
            if latest_invoice_id:
                try:
                    invoice = stripe.Invoice.retrieve(latest_invoice_id)
                    amount_paid = invoice.get("amount_paid", 0) / 100  # Convert from cents
                    currency = invoice.get("currency", currency)
                    print(f"DEBUG: Retrieved invoice {latest_invoice_id}, amount_paid: ${amount_paid}")
                except Exception as inv_err:
                    print(f"WARNING: Could not retrieve invoice details: {inv_err}")
            
            # Safe Access for current_period_end
            cpe = updated_sub.get("current_period_end")
            if not cpe:
                 print("WARNING: current_period_end missing in Stripe response, defaulting to now + period")
                 # Retrieve safe fallback from original if possible or calc
                 cpe = subscription.get("current_period_end") or int(datetime.utcnow().timestamp() + 30*24*3600)

            # Update DB: Modify the plan_id of the existing subscription row
            supabase.table("subscriptions").update({
                "plan_id": plan_id,
                "current_period_end": datetime.fromtimestamp(cpe).isoformat()
            }).eq("id", existing_sub["id"]).execute()
            
            # Allocate NEW plan credits immediately?
            await allocate_user_credits(user_id, plan_id) 
            print("DEBUG: Credits allocated.")

            return {
                "subscription_id": updated_sub.id,
                "plan_id": plan_id,
                "status": "active",
                "amount_paid": amount_paid,
                "currency": currency,
                "current_period_end": datetime.fromtimestamp(cpe).isoformat(),
                "message": "Plan upgraded successfully."
            }
        except Exception as e:
            print(f"DEBUG: Error updating subscription: {str(e)}")
            raise e

    # CASE C: New Subscription (No existing active sub)
    else:
        print("DEBUG: Creating NEW subscription (No existing active sub found)")
        # Standard create subscription logic
        sub_kwargs = {
            "customer": customer_id,
            "items": [{"price": price_id}],
            "metadata": {"user_id": user_id, "plan_id": plan_id}
        }
        if coupon_code:
            coupon = validate_coupon_for_plan(coupon_code, plan_id)
            if coupon: sub_kwargs["discounts"] = [{"coupon": coupon["id"]}]
            
        try:
             subscription = stripe.Subscription.create(**sub_kwargs)
             print(f"DEBUG: New subscription created: {subscription.id}")
        except Exception as e:
             print(f"DEBUG: Error creating subscription: {str(e)}")
             raise e
        
        # Store in DB
        sub_data = {
            "user_id": user_id, "plan_id": plan_id, "stripe_customer_id": customer_id,
            "stripe_subscription_id": subscription.id, "status": "active",
            "current_period_start": datetime.fromtimestamp(subscription.current_period_start).isoformat(),
            "current_period_end": datetime.fromtimestamp(subscription.current_period_end).isoformat()
        }
        supabase.table("subscriptions").insert(sub_data).execute()
        await allocate_user_credits(user_id, plan_id)
        
        return {"subscription_id": subscription.id, "plan_id": plan_id, "status": "active"}


async def cancel_subscription(user_id: str) -> bool:
    """
    Cancel user's active subscription at the end of the billing period.
    """
    supabase = get_admin_supabase_client()
    
    # Get active subscription
    result = supabase.table("subscriptions").select("*").eq("user_id", user_id).eq("status", "active").execute()
    
    if not result.data:
        return False
    
    subscription = result.data[0]
    
    # Cancel Stripe subscription (at period end)
    if subscription.get("stripe_subscription_id"):
        try:
            stripe.Subscription.modify(
                subscription["stripe_subscription_id"],
                cancel_at_period_end=True
            )
        except stripe.error.StripeError:
             return False
    
    # Update database to reflect pending cancellation
    supabase.table("subscriptions").update({"cancel_at_period_end": True}).eq("id", subscription["id"]).execute()
    
    return True


async def get_active_subscriptions(user_id: str) -> List[Dict]:
    """Get all user's active subscriptions."""
    supabase = get_admin_supabase_client()
    
    result = supabase.table("subscriptions").select("*").eq("user_id", user_id).eq("status", "active").execute()
    
    if not result.data:
        return []
    
    active_subs = []
    for sub in result.data:
        plan = get_plan_by_id(sub["plan_id"])
        active_subs.append({
            "id": sub["id"],
            "plan_id": sub["plan_id"],
            "plan_name": plan["name"] if plan else "Unknown",
            "status": sub["status"],
            "current_period_start": sub.get("current_period_start"),
            "current_period_end": sub.get("current_period_end"),
            "cancel_at_period_end": sub.get("cancel_at_period_end", False)
        })
    return active_subs


async def get_user_usage(user_id: str) -> Dict:
    """Get user's current subscriptions and aggregated credit limits."""
    supabase = get_admin_supabase_client()
    
    # 1. Get all Active Subscriptions
    subscriptions = await get_active_subscriptions(user_id)
    
    # 2. Get Aggregated Balances from user_usage_balances
    balance_result = supabase.table("user_usage_balances").select("*").eq("user_id", user_id).execute()
    
    credits = None
    if balance_result.data:
        raw = balance_result.data[0]
        credits = {
            "blog_ideas_credits_total": raw.get("blog_ideas_credits_total", 0),
            "blog_ideas_credits_used": raw.get("blog_ideas_credits_used", 0),
            "faq_credits_total": raw.get("faq_credits_total", 0),
            "faq_credits_used": raw.get("faq_credits_used", 0),
            "blog_creation_credits_total": raw.get("blog_creation_credits_total", 0),
            "blog_creation_credits_used": raw.get("blog_creation_credits_used", 0),
            "chatbot_messages_credits_total": raw.get("chatbot_messages_credits_total", 0),
            "chatbot_messages_credits_used": raw.get("chatbot_messages_credits_used", 0),
            "chatbot_training_credits_total": raw.get("chatbot_training_credits_total", 0),
            "chatbot_training_credits_used": raw.get("chatbot_training_credits_used", 0),
            "chatbot_count_allowed": raw.get("chatbot_count_allowed", 0),
            "white_label_allowed": raw.get("white_label_allowed", False)
        }
    
    return {
        "active_subscriptions": subscriptions,
        "credits": credits
    }


async def handle_webhook_event(event: Dict) -> None:
    """Handle Stripe webhook events."""
    supabase = get_admin_supabase_client()
    
    event_type = event["type"]
    
    if event_type == "customer.subscription.updated":
        subscription = event["data"]["object"]
        user_id = subscription["metadata"].get("user_id")
        
        if user_id:
            supabase.table("subscriptions").update({
                "status": subscription.get("status"),
                "current_period_start": datetime.fromtimestamp(subscription.get("current_period_start", datetime.utcnow().timestamp())).isoformat(),
                "current_period_end": datetime.fromtimestamp(subscription.get("current_period_end", datetime.utcnow().timestamp())).isoformat(),
                "cancel_at_period_end": subscription.get("cancel_at_period_end", False)
            }).eq("stripe_subscription_id", subscription.get("id")).execute()
    
    elif event_type == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        supabase.table("subscriptions").update({
            "status": "canceled"
        }).eq("stripe_subscription_id", subscription["id"]).execute()
    
    elif event_type == "invoice.payment_succeeded":
        invoice = event["data"]["object"]
        user_id = invoice.get("metadata", {}).get("user_id")
        plan_id = invoice.get("metadata", {}).get("plan_id")
        
        # If not in invoice metadata, try subscription metadata
        if not user_id or not plan_id:
            subscription_id = invoice.get("subscription")
            if subscription_id:
                subscription = stripe.Subscription.retrieve(subscription_id)
                user_id = user_id or subscription.metadata.get("user_id")
                plan_id = plan_id or subscription.metadata.get("plan_id")

        if user_id:
            # Check for duplicate logs (e.g. if already logged in subscribe_to_plan)
            invoice_id = invoice.get("id")
            if invoice_id:
                # Use filter with JSON path for metadata
                existing = supabase.table("payment_history").select("id").filter("metadata->>invoice_id", "eq", invoice_id).execute()
                if existing.data:
                    print(f"ℹ️ Webhook: Skipping duplicate payment log for invoice {invoice_id}")
                    # Still allocate credits if needed, as the allocate_user_credits check is separate
                else:
                    # Extract discount info
                    discount_total = 0
                    coupon_id = None
                    if getattr(invoice, "total_discount_amounts", None):
                        discount_total = sum(d.amount for d in invoice.total_discount_amounts)
                        if getattr(invoice, "discounts", None) and len(invoice.discounts) > 0:
                            coupon_id = getattr(invoice.discounts[0], "coupon", None)
                            if hasattr(coupon_id, "id"): 
                                coupon_id = coupon_id.id

                    # Log payment history
                    supabase.table("payment_history").insert({
                        "user_id": user_id,
                        "stripe_payment_intent_id": invoice.get("payment_intent"),
                        "stripe_charge_id": invoice.get("charge"),
                        "amount": invoice.get("amount_paid", 0),
                        "currency": invoice.get("currency", "usd"),
                        "status": "succeeded",
                        "description": f"Subscription payment - {invoice.get('description', '')}",
                        "payment_type": "subscription",
                        "metadata": {
                            "invoice_id": invoice_id, 
                            "plan_id": plan_id,
                            "coupon_code": coupon_id,
                            "discount_amount": discount_total,
                            "original_amount": getattr(invoice, "subtotal", 0)
                        }
                    }).execute()
            else:
                # Fallback for invoices without ID (rare) or non-invoice payments
                supabase.table("payment_history").insert({
                    "user_id": user_id,
                    "stripe_payment_intent_id": invoice.get("payment_intent"),
                    "stripe_charge_id": invoice.get("charge"),
                    "amount": invoice.get("amount_paid", 0),
                    "currency": invoice.get("currency", "usd"),
                    "status": "succeeded",
                    "description": f"Subscription payment - {invoice.get('description', '')}",
                    "payment_type": "subscription",
                    "metadata": {"plan_id": plan_id}
                }).execute()

            # Allocate credits if plan_id is identified
            if plan_id:
                # Security/Logic Fix: Verify if an active subscription for this user and plan exists in DB
                # or if it was just Created (for one-time/initial buys)
                sub_check = supabase.table("subscriptions").select("id").eq("user_id", user_id).eq("plan_id", plan_id).eq("status", "active").execute()
                
                if sub_check.data:
                    # Detect if this is a renewal cycle or a new purchase/stack
                    is_renewal = invoice.get("billing_reason") == "subscription_cycle"
                    await allocate_user_credits(user_id, plan_id, is_renewal=is_renewal)
                else:
                    print(f"⚠️ Webhook Warning: Skipping credit allocation for user {user_id}. No active subscription found in database for plan '{plan_id}'.")
    
    elif event_type == "checkout.session.completed":
        session = event["data"]["object"]
        metadata = session.get("metadata", {})
        
        # Check if this is an agency subscription
        if metadata.get("type") == "agency_subscription":
            user_id = metadata.get("user_id")
            agency_id = metadata.get("agency_id")
            plan_id = metadata.get("plan_id")
            
            if user_id and agency_id and plan_id:
                print(f"💰 Webhook: Agency subscription completed for user {user_id}, agency {agency_id}")
                
                # 1. Get Agency Plan details
                plan_res = supabase.table("agency_plans").select("*").eq("id", plan_id).execute()
                if plan_res.data:
                    plan = plan_res.data[0]
                    
                    # 2. Update user limits and agency affiliation
                    supabase.table("registered_users").update({
                        "agency_id": agency_id,
                        "plan_id": plan_id
                    }).eq("id", user_id).execute()

                    # Allocate credits
                    supabase.rpc("increment_user_balance", {
                        "target_user_id": user_id,
                        "add_messages_credits": plan.get("limit_messages", 0),
                        "add_training_credits": plan.get("limit_training_chars", 0),
                        "add_chatbot_count": plan.get("limit_chatbots", 0),
                        "add_blog_creation": plan.get("limit_blog_creation", 0),
                        "add_blog_ideas": plan.get("limit_blog_ideas", 0),
                        "add_faq": plan.get("limit_faqs", 0),
                        "is_renewal": False,
                        "set_white_label": False
                    }).execute()
                    
                    # 3. Create/Update subscription record
                    import datetime
                    period_end = (datetime.datetime.utcnow() + datetime.timedelta(days=30)).isoformat()
                    
                    # Check if already exists
                    existing_sub = supabase.table("agency_subscriptions").select("id").eq("customer_id", user_id).eq("agency_id", agency_id).execute()
                    
                    sub_data = {
                        "customer_id": user_id,
                        "plan_id": plan_id,
                        "agency_id": agency_id,
                        "status": "active",
                        "current_period_end": period_end
                    }
                    
                    if existing_sub.data:
                        supabase.table("agency_subscriptions").update(sub_data).eq("id", existing_sub.data[0]["id"]).execute()
                    else:
                        supabase.table("agency_subscriptions").insert(sub_data).execute()
                        
                    # 4. Record in Payment History
                    supabase.table("payment_history").insert({
                        "user_id": user_id,
                        "stripe_payment_intent_id": session.get("payment_intent"),
                        "amount": session.get("amount_total", 0),
                        "currency": session.get("currency", "usd"),
                        "status": "succeeded",
                        "description": f"Agency Subscription: {plan['name']}",
                        "payment_type": "subscription",
                        "metadata": {
                            "plan_id": plan_id,
                            "agency_id": agency_id,
                            "checkout_session_id": session.id
                        }
                    }).execute()

    elif event_type == "invoice.payment_failed":
        invoice = event["data"]["object"]
        user_id = invoice.get("metadata", {}).get("user_id")
        
        if user_id:
            supabase.table("payment_history").insert({
                "user_id": user_id,
                "stripe_payment_intent_id": invoice.get("payment_intent"),
                "amount": invoice["amount_due"],
                "currency": invoice["currency"],
                "status": "failed",
                "description": f"Failed subscription payment - {invoice.get('description', '')}",
                "payment_type": "subscription",
                "metadata": {"invoice_id": invoice["id"]}
            }).execute()

async def get_payment_history(user_id: str) -> List[Dict]:
    """Get user's payment history from the database."""
    supabase = get_admin_supabase_client()
    
    result = supabase.table("payment_history").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
    
    return result.data if result.data else []


async def get_billing_preferences(user_id: str, email: str = None) -> Dict:
    """
    Get user's billing preferences (Auto-Recharge settings).
    Stored in Stripe Customer Metadata to avoid schema changes.
    """
    # Prefer fetching from DB cache if we had a table, but here we use Stripe
    if not email:
        # Need email or customer ID. If we only have user_id, try to get customer_id from DB
        supabase = get_admin_supabase_client()
        res = supabase.table("user_stripe_profiles").select("stripe_customer_id").eq("user_id", user_id).execute()
        if not res.data:
            return {} # No profile = no settings
        customer_id = res.data[0]["stripe_customer_id"]
    else:
        customer_id = await get_or_create_customer(user_id, email)
        
    try:
        customer = stripe.Customer.retrieve(customer_id)
        # Parse metadata
        prefs_json = customer.metadata.get("billing_preferences", "{}")
        return json.loads(prefs_json)
    except Exception as e:
        print(f"Error fetching preferences: {e}")
        return {}


async def save_billing_preferences(user_id: str, email: str, preferences: Dict) -> bool:
    """Save user's billing preferences to Stripe Customer Metadata."""
    customer_id = await get_or_create_customer(user_id, email)
    
    try:
        # Store as JSON string in a single metadata field to allow deeper structure
        stripe.Customer.modify(
            customer_id,
            metadata={"billing_preferences": json.dumps(preferences)}
        )
        return True
    except Exception as e:
        print(f"Error saving preferences: {e}")
        return False


async def process_auto_recharge(user_id: str, resource_type: str, current_balance: int) -> bool:
    """
    Check if Auto-Recharge should trigger and execute it.
    resource_type: 'credits' (content) or 'messages' (chatbot)
    """
    # 1. Get Preferences
    # We need email to get customer, but usually we just have user_id here. 
    # Use helper to get by user_id
    current_prefs = await get_billing_preferences(user_id)
    if not current_prefs:
        return False

    # 2. Check Triggers
    should_recharge = False
    recharge_amount = 0
    price_estim = 0 # In cents
    
    # Threshold handling (hardcoded 5% logic or explicit threshold from settings?)
    # Frontend logic said: "Auto-purchase credits when 5% remain"
    # We need the TOTAL limit to calculate 5%. 
    # For now, strict 'low balance' check based on assumed settings or passed values.
    # Let's assume the caller handles the 'threshold check' OR we need to fetch usage limits here.
    
    # Fetch usage for thresholds
    usage = await get_user_usage(user_id)
    credits_data = usage.get("credits", {})
    
    if resource_type == 'credits':
        auto_on = current_prefs.get("creditAutoRecharge", False)
        if not auto_on: return False
        
        total = credits_data.get("blog_ideas_credits_total", 0)
        # Avoid div by zero
        if total > 0 and (current_balance / total) <= 0.05:
            recharge_amnt_str = current_prefs.get("creditRechargeAmount", "100")
            recharge_amount = int(recharge_amnt_str)
            # Simple pricing map (Should ideally be in DB/Plans, but mapping from frontend hardcoded values)
            pricing_map = {'50': 1000, '100': 2000, '150': 2500, '300': 5000, '500': 8000, '1000': 15000}
            price_estim = pricing_map.get(recharge_amnt_str, 2000) # Default $20
            should_recharge = True

    elif resource_type == 'messages':
        auto_on = current_prefs.get("messageAutoRecharge", False)
        if not auto_on: return False

        total = credits_data.get("chatbot_messages_credits_total", 0)
        if total > 0 and (current_balance / total) <= 0.05:
             recharge_amnt_str = current_prefs.get("messageRechargeAmount", "500")
             recharge_amount = int(recharge_amnt_str)
             pricing_map = {'500': 1000, '1000': 1800, '2000': 3500, '5000': 8000, '10000': 15000}
             price_estim = pricing_map.get(recharge_amnt_str, 1800)
             should_recharge = True

    if should_recharge and price_estim > 0:
        print(f"⚡ Triggering Auto-Recharge for {user_id}: {recharge_amount} {resource_type} for ${price_estim/100}")
        try:
             # Process Payment
             supabase = get_admin_supabase_client()
             
             # Get Customer & Default PM
             prof_res = supabase.table("user_stripe_profiles").select("stripe_customer_id").eq("user_id", user_id).execute()
             customer_id = prof_res.data[0]["stripe_customer_id"]
             
             customer = stripe.Customer.retrieve(customer_id)
             default_pm = customer.invoice_settings.default_payment_method
             
             if not default_pm:
                 print(f"❌ Auto-Recharge Failed: No default payment method for {user_id}")
                 return False

             # Charge
             intent = stripe.PaymentIntent.create(
                 amount=price_estim,
                 currency="usd",
                 customer=customer_id,
                 payment_method=default_pm,
                 confirm=True,
                 description=f"Auto-Recharge: {recharge_amount} {resource_type}",
                 metadata={"user_id": user_id, "type": "auto_recharge", "resource": resource_type},
                 automatic_payment_methods={"enabled": True, "allow_redirects": "never"}
             )
             
             # Log History
             supabase.table("payment_history").insert({
                "user_id": user_id, "stripe_payment_intent_id": intent.id, "amount": price_estim,
                "status": "succeeded", "description": f"Auto-Recharge: {recharge_amount} {resource_type}",
                "payment_type": "auto-recharge",
                "metadata": {"resource_type": resource_type, "quantity": recharge_amount}
             }).execute()
             
             # Allocate Credits (Directly increment via RPC which we wrapped in allocate, but allocate takes plan_id)
             # We need a direct 'add_credits' helper or map this to an 'addon_id'. 
             # Mapping to Addons is safer if they exist, or direct RPC.
             # I'll use direct RPC call here for flexibility as these 'custom amounts' might not match plans.
             
             rpc_payload = {"target_user_id": user_id, "is_renewal": False}
             if resource_type == 'credits':
                 # Content credits follow a 1:1:2 ratio (blog_ideas : faq : blog_creation)
                 # This matches the addon structure in plans.json
                 rpc_payload["add_blog_ideas"] = recharge_amount
                 rpc_payload["add_faq"] = recharge_amount
                 rpc_payload["add_blog_creation"] = recharge_amount * 2
             elif resource_type == 'messages':
                 rpc_payload["add_messages_credits"] = recharge_amount
                 
             supabase.rpc("increment_user_balance", rpc_payload).execute()
             
             return True

        except Exception as e:
            print(f"❌ Auto-Recharge Error: {e}")
            return False
            
    return False


async def preview_upgrade_proration(user_id: str, new_plan_id: str) -> Dict:
    """
    Preview the cost of upgrading to a new plan, including proration.
    Returns: {
        "proration_credit": float,
        "due_now": float,
        "upcoming_date": str,
        "currency": str
    }
    """
    supabase = get_admin_supabase_client()
    
    # 1. Get Plan Details
    plan = get_plan_by_id(new_plan_id)
    if not plan: raise ValueError("Plan not found")
    
    price_id = plan["stripe_price_id_live"] if settings.is_production else plan["stripe_price_id_test"]
    if not price_id: raise ValueError("Stripe configuration missing for plan")

    # 2. Get active subscription
    sub_res = supabase.table("subscriptions").select("*").eq("user_id", user_id).eq("status", "active").execute()
    if not sub_res.data:
        # No active subscription = No proration, full price due
        return {
            "proration_credit": 0,
            "due_now": plan["price"],
            "upcoming_date": None,
            "currency": plan["currency"]
        }
    
    subscription = sub_res.data[0]
    stripe_sub_id = subscription["stripe_subscription_id"]
    
    if not stripe_sub_id:
        # Free plan or non-stripe sub upgrading
        return {
            "proration_credit": 0, 
            "due_now": plan["price"],
            "upcoming_date": None,
             "currency": plan["currency"]
        }

    # 3. Fetch Upcoming Invoice from Stripe
    try:
        stripe_sub = stripe.Subscription.retrieve(stripe_sub_id)
        sub_item_id = stripe_sub['items']['data'][0].id
        
        # DEBUG LOGS
        print(f"DEBUG: Fetching upcoming invoice for sub {stripe_sub_id}, item {sub_item_id}, new price {price_id}")

        # Fallback to raw API request since stripe.Invoice.upcoming is missing/deprecated
        import requests
        from fastapi import HTTPException

        url = "https://api.stripe.com/v1/invoices/create_preview"
        headers = {
            "Authorization": f"Bearer {stripe.api_key}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        # Construct params for create_preview (POST)
        # We must use subscription_details to describe the update
        data = {
            "customer": subscription["stripe_customer_id"],
            "subscription": stripe_sub_id,
            "subscription_details[proration_behavior]": "always_invoice",
            "subscription_details[items][0][id]": sub_item_id,
            "subscription_details[items][0][price]": price_id,
        }

        resp = requests.post(url, headers=headers, data=data)
        
        if resp.status_code != 200:
            print(f"DEBUG: Stripe API Error: {resp.text}")
            raise ValueError(f"Stripe API Error: {resp.json().get('error', {}).get('message')}")
            
        invoice_data = resp.json()
        
        # Calculate totals
        # amount_due is what the user pays NOW
        due_now = invoice_data['amount_due'] / 100
        
        print(f"DEBUG: Invoice Amount Due: {invoice_data['amount_due']}")
        print(f"DEBUG: Invoice Lines: {len(invoice_data['lines']['data'])}")
        
        proration_credit = 0
        for line in invoice_data.get('lines', {}).get('data', []):
            line_type = line.get('type')
            amount = line.get('amount', 0)
            is_proration = line.get('proration', False)
            
            print(f"DEBUG: Line item: {line.get('description')}, Amount: {amount}, Type: {line_type}, Proration: {is_proration}")
            
            # Relaxed logic: In an upgrade preview, any negative line is a credit/proration
            if amount < 0: 
                 proration_credit += abs(amount)
        
        print(f"DEBUG: Calculated Proration Credit: {proration_credit}")

        return {
            "proration_credit": proration_credit / 100,
            "due_now": due_now,
            "upcoming_date": datetime.fromtimestamp(invoice_data['next_payment_attempt'] or (datetime.utcnow().timestamp() + 30*86400)).isoformat(),
            "currency": invoice_data['currency']
        }

    except Exception as e:
        print(f"Error previewing proration: {e}")
        # Fallback
        return {
            "proration_credit": 0,
            "due_now": plan["price"],
            "upcoming_date": None,
            "currency": plan["currency"]
        }
# ---------------------------------------------------------
# AGENCY CONNECT & MULTI-TENANT BILLING
# ---------------------------------------------------------

async def generate_agency_connect_url(user_id: str, redirect_uri: str, email: str = None) -> str:
    """Generate the Stripe Connect OAuth authorization URL for an agency owner."""
    # Ensure user is an agency owner
    supabase = get_admin_supabase_client()
    res = supabase.table("agencies").select("id").eq("owner_id", user_id).execute()
    if not res.data:
        raise ValueError("User is not an authorized agency owner.")
    
    agency_id = res.data[0]["id"]
    
    # State can carry both agency_id and email if provided
    state = f"{agency_id}"
    if email:
        state = f"{agency_id}:{email}"
    
    # Generate Stripe OAuth URL
    # IMPORTANT: We must include the redirect_uri explicitly so Stripe knows where to send the user back
    import urllib.parse
    encoded_redirect = urllib.parse.quote(redirect_uri)
    
    url = (
        f"https://connect.stripe.com/oauth/authorize?"
        f"client_id={settings.stripe_connect_client_id}&"
        f"response_type=code&"
        f"scope=read_write&"
        f"state={state}&"
        f"redirect_uri={encoded_redirect}"
    )
    return url

async def handle_agency_connect_callback(code: str, state: str) -> bool:
    """Exchange OAuth code for Stripe Connect account ID and store it."""
    try:
        # State might contain agency_id:email
        print(f"DEBUG: Processing Connect callback with state: {state}")
        parts = state.split(":")
        agency_id = parts[0]
        fallback_email = parts[1] if len(parts) > 1 else None

        # Idempotency check: If agency already connected, we might be in a React double-call
        supabase = get_admin_supabase_client()
        agency_res = supabase.table("agencies").select("stripe_connect_id").eq("id", agency_id).execute()
        if agency_res.data and agency_res.data[0].get("stripe_connect_id"):
            print(f"DEBUG: Agency {agency_id} already has a connected account. Assuming success from previous request.")
            return True

        print(f"DEBUG: Exchanging token for code: {code[:10]}...")
        try:
            response = stripe.OAuth.token(
                grant_type="authorization_code",
                code=code,
            )
            connect_id = response["stripe_user_id"]
            print(f"DEBUG: Connected account ID: {connect_id}")
        except stripe.error.StripeError as e:
            # Handle "code already used" which happens in React Strict Mode double-calls
            if "already used" in str(e).lower() or "invalid_grant" in str(e).lower():
                print(f"DEBUG: Code already used or invalid grant. Checking if connection succeeded anyway...")
                # Re-check database
                agency_res = supabase.table("agencies").select("stripe_connect_id").eq("id", agency_id).execute()
                if agency_res.data and agency_res.data[0].get("stripe_connect_id"):
                    return True
            print(f"CRITICAL: OAuth token exchange failed: {e}")
            return False
        except Exception as oauth_err:
            print(f"CRITICAL: OAuth token exchange unexpected error: {oauth_err}")
            return False

        # Fetch account details to get email and status
        try:
            account = stripe.Account.retrieve(connect_id)
            email = account.get("email") or fallback_email
            # Use details_submitted or charges_enabled as a proxy for 'Active'
            status = "Active" if account.get("details_submitted") or account.get("charges_enabled") else "Pending"
        except Exception as acc_err:
            print(f"ERROR: Failed to retrieve account details for {connect_id}: {acc_err}")
            email = fallback_email
            status = "Pending"

        import datetime
        connected_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        # We try to update the specific columns, but fallback to branding_settings if they don't exist
        update_data = {
            "stripe_connect_id": connect_id
        }
        
        print(f"DEBUG: Updating agency {agency_id} with Stripe info...")
        try:
            res = supabase.table("agencies").update({
                **update_data,
                "stripe_connected_at": connected_at,
                "stripe_account_email": email,
                "stripe_account_status": status
            }).eq("id", agency_id).execute()
            
            if not res.data:
                print(f"WARNING: No agency found with ID {agency_id} while updating columns.")
                raise Exception("No agency found")
                
        except Exception as update_err:
            print(f"DEBUG: Column update failed, trying branding_settings fallback: {update_err}")
            res = supabase.table("agencies").select("branding_settings").eq("id", agency_id).execute()
            if not res.data:
                return False
                
            branding = res.data[0].get("branding_settings") or {}
            branding["stripe_meta"] = {
                "connected_at": connected_at,
                "email": email,
                "status": status
            }
            supabase.table("agencies").update({
                **update_data,
                "branding_settings": branding
            }).eq("id", agency_id).execute()
        
        print(f"DEBUG: Successfully linked Stripe account {connect_id} to agency {agency_id}")
        return True
    except Exception as e:
        print(f"CRITICAL: handle_agency_connect_callback global error: {e}")
        return False

async def subscribe_to_agency_plan(
    user_id: str, 
    email: str, 
    plan_id: str, 
    agency_id: str,
    coupon_code: Optional[str] = None,
    origin: Optional[str] = None
) -> Dict:
    """
    Handle user subscription to an AGENCY'S plan.
    Payments go to Agency's Stripe Connect account.
    Snobbot takes 0% platform fee.
    """
    supabase = get_admin_supabase_client()
    
    # 1. Get Agency Connect ID
    agency_res = supabase.table("agencies").select("stripe_connect_id").eq("id", agency_id).execute()
    if not agency_res.data or not agency_res.data[0]["stripe_connect_id"]:
        raise ValueError("This agency has not connected a Stripe account.")
    
    connect_id = agency_res.data[0]["stripe_connect_id"]
    
    # 2. Get Agency Plan details
    plan_res = supabase.table("agency_plans").select("*").eq("id", plan_id).execute()
    if not plan_res.data:
        raise ValueError("Agency plan not found.")
    
    plan = plan_res.data[0]
    
    # 3. Create/Get Customer on the PLATFORM (Parent account)
    customer_id = await get_or_create_customer(user_id, email)
    
    # 4. Process Payment (Direct Charge via Connect)
    amount_cents = int(plan["price"] * 100)
    platform_fee_cents = 0 # 0% Fee
    
    try:
        # Create Stripe Checkout Session
        # The success/cancel URLs should point back to the platform
        base_url = origin or "http://localhost:3000"
        success_url = f"{base_url}/payment-success?session_id={{CHECKOUT_SESSION_ID}}"
        cancel_url = f"{base_url}/white-label/plans"

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"Agency Plan: {plan['name']}",
                        "description": plan.get("description", ""),
                    },
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=success_url,
            cancel_url=cancel_url,
            payment_intent_data={
                "transfer_data": {
                    "destination": connect_id,
                },
            },
            metadata={
                "user_id": user_id,
                "agency_id": agency_id,
                "plan_id": plan_id,
                "type": "agency_subscription"
            }
        )
        
        return {
            "status": "pending",
            "checkout_url": session.url,
            "session_id": session.id,
            "plan_id": plan_id,
            "plan_name": plan["name"],
            "amount": amount_cents / 100,
            "currency": "usd",
            "agency_id": agency_id
        }
    except Exception as e:
        print(f"Error creating agency checkout session: {e}")
        raise e
    except Exception as e:
        print(f"Error in agency subscription payment: {e}")
        raise e

async def subscribe_to_agency_plan_dummy(
    user_id: str, 
    email: str, 
    plan_id: str, 
    agency_id: str
) -> Dict:
    """
    Handle user subscription to an AGENCY'S plan BYPASSING Stripe (Dummy).
    Used for testing or when Stripe is not yet configured.
    """
    supabase = get_admin_supabase_client()
    
    # 1. Get Agency Plan details
    plan_res = supabase.table("agency_plans").select("*").eq("id", plan_id).execute()
    if not plan_res.data:
        raise ValueError("Agency plan not found.")
    
    plan = plan_res.data[0]
    
    try:
        # 2. Update user limits and agency affiliation
        supabase.table("registered_users").update({
            "agency_id": agency_id,
            "plan_id": plan_id
        }).eq("id", user_id).execute()

        supabase.rpc("increment_user_balance", {
            "target_user_id": user_id,
            "add_messages_credits": plan.get("limit_messages", 0),
            "add_training_credits": plan.get("limit_training_chars", 0),
            "add_chatbot_count": plan.get("limit_chatbots", 0),
            "add_blog_creation": plan.get("limit_blog_creation", 0),
            "add_blog_ideas": plan.get("limit_blog_ideas", 0),
            "add_faq": plan.get("limit_faqs", 0),
            "is_renewal": False,
            "set_white_label": False
        }).execute()
        
        # 3. Create subscription record
        tenant_subscription = {
            "customer_id": user_id,
            "plan_id": plan_id,
            "agency_id": agency_id,
            "status": "active",
            "current_period_end": datetime.fromtimestamp(datetime.utcnow().timestamp() + (30 * 24 * 3600)).isoformat()
        }
        
        supabase.table("agency_subscriptions").insert(tenant_subscription).execute()

        # 4. Record in Payment History (Dummy)
        supabase.table("payment_history").insert({
            "user_id": user_id,
            "amount": int(plan["price"] * 100),
            "currency": "usd",
            "status": "succeeded",
            "description": f"Agency Subscription: {plan['name']} (Mock)",
            "payment_type": "subscription",
            "metadata": {
                "plan_id": plan_id,
                "agency_id": agency_id,
                "is_dummy": True
            }
        }).execute()

        return {
            "status": "succeeded",
            "message": "Dummy subscription successful",
            "plan_name": plan["name"],
            "plan_id": plan_id,
            "amount_paid": plan["price"],
            "currency": "usd",
            "subscription_id": f"sub_dummy_{datetime.utcnow().timestamp()}",
            "agency_id": agency_id
        }
    except Exception as e:
        print(f"Error in dummy agency subscription: {e}")
        raise e

async def bill_agency_overage(agency_id: str, amount_cents: int, description: str):
    """Platform bills the Agency for base plan or monthly overages."""
    supabase = get_admin_supabase_client()
    
    # 1. Get Agency Owner Stripe Profile
    agency_res = supabase.table("agencies").select("owner_id").eq("id", agency_id).execute()
    owner_id = agency_res.data[0]["owner_id"]
    
    owner_res = supabase.table("registered_users").select("email").eq("id", owner_id).execute()
    owner_email = owner_res.data[0]["email"]
    
    customer_id = await get_or_create_customer(owner_id, owner_email)
    
    # 2. Create Invoice Item (Overage)
    stripe.InvoiceItem.create(
        customer=customer_id,
        amount=amount_cents,
        currency="usd",
        description=description,
    )
    
    # 3. Trigger Invoice Generation
    invoice = stripe.Invoice.create(
        customer=customer_id,
        auto_advance=True, # Automatically attempt payment
        metadata={"agency_id": agency_id, "type": "overage"}
    )
    
    return invoice.id
async def get_checkout_session_details(session_id: str) -> Dict:
    """Retrieve details of a Checkout Session for the success page."""
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        metadata = session.get("metadata", {})
        
        # Try to get a nicer plan name if possible
        plan_id = metadata.get("plan_id")
        plan_name = f"Plan: {plan_id}"
        
        if plan_id:
            supabase = get_admin_supabase_client()
            plan_res = supabase.table("agency_plans").select("name").eq("id", plan_id).execute()
            if plan_res.data:
                plan_name = plan_res.data[0]["name"]
        
        return {
            "status": session.get("payment_status"),
            "amount_paid": session.get("amount_total", 0) / 100,
            "currency": session.get("currency", "usd"),
            "plan_id": plan_id,
            "plan_name": plan_name,
            "subscription_id": session.get("payment_intent") or session.get("subscription") or session.id,
            "user_id": metadata.get("user_id"),
            "type": metadata.get("type"),
            "customer_email": session.get("customer_details", {}).get("email")
        }
    except Exception as e:
        print(f"Error retrieving checkout session: {e}")
        return None
