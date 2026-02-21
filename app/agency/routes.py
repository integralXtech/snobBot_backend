from fastapi import APIRouter, Depends, HTTPException, status
from app.RAG.auth_utils import get_current_user
from app.supabase import get_admin_supabase_client
from .models import (
    AgencyBrandingUpdate, AgencyDomainUpdate, AgencySettingsResponse,
    AgencyPlanCreate, AgencyPlanUpdate, AgencyPlanResponse, 
    CustomerCreate, TicketCreate
)
from typing import Dict, Any, List

agency_router = APIRouter(prefix="/whitelabel", tags=["White Label Management"])

async def get_agency_by_owner(user_id: str, raise_error: bool = True):
    supabase = get_admin_supabase_client()
    res = supabase.table("agencies").select("*").eq("owner_id", user_id).execute()
    if not res.data:
        if raise_error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agency not found for this user."
            )
        return None
    return res.data[0]

@agency_router.get("/settings")
async def get_settings(current_user: dict = Depends(get_current_user)):
    """Get current agency settings for the logged-in owner."""
    agency = await get_agency_by_owner(current_user["id"], raise_error=False)
    if not agency:
        # Return a "shadow" agency object for the UI
        return {
            "id": "new",
            "name": "My New Agency",
            "company_name": "",
            "custom_domain": "",
            "logo_url": None,
            "primary_color": "#2563EB",
            "secondary_color": "#7C3AED",
            "branding_settings": {}
        }
    return agency

@agency_router.get("/public/config")
async def get_public_config(domain: str = None, agency_id: str = None):
    """Retrieve public branding (logo, colors) for a domain or ID."""
    supabase = get_admin_supabase_client()
    query = supabase.table("agencies").select("id, name, company_name, logo_url, primary_color, secondary_color, branding_settings")
    
    if agency_id:
        res = query.eq("id", agency_id).execute()
    elif domain:
        # Handle cases where domain might have port like localhost:5173
        clean_domain = domain.split(":")[0]
        res = query.eq("custom_domain", clean_domain).execute()
    else:
        raise HTTPException(status_code=400, detail="Domain or Agency ID required")
        
    if not res.data:
        return None # Return None if not a white-label domain
        
    return res.data[0]

@agency_router.patch("/branding")
async def update_branding(
    update_data: AgencyBrandingUpdate, 
    current_user: dict = Depends(get_current_user)
):
    """Update agency branding settings. Creates agency if it doesn't exist."""
    agency = await get_agency_by_owner(current_user["id"], raise_error=False)
    supabase = get_admin_supabase_client()
    
    update_dict = update_data.dict(exclude_unset=True)
    
    if not agency:
        # Create new agency
        import uuid
        agency_data = {
            "id": str(uuid.uuid4()),
            "name": update_dict.get("company_name", "My New Agency"),
            "owner_id": current_user["id"],
            "company_name": update_dict.get("company_name", ""),
            "primary_color": update_dict.get("primary_color", "#2563EB"),
            "secondary_color": update_dict.get("secondary_color", "#7C3AED"),
            "branding_settings": update_dict.get("branding_settings", {})
        }
        res = supabase.table("agencies").insert(agency_data).execute()
    else:
        if not update_dict:
            return agency
        res = supabase.table("agencies").update(update_dict).eq("id", agency["id"]).execute()
    
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to update branding settings.")
        
    return res.data[0]

@agency_router.patch("/domain")
async def update_domain(
    update_data: AgencyDomainUpdate, 
    current_user: dict = Depends(get_current_user)
):
    """Update agency custom domain. Creates agency if it doesn't exist."""
    agency = await get_agency_by_owner(current_user["id"], raise_error=False)
    supabase = get_admin_supabase_client()
    
    # Check if domain is already taken
    check_res = supabase.table("agencies").select("id").eq("custom_domain", update_data.custom_domain).execute()
    if check_res.data and (not agency or check_res.data[0]["id"] != agency["id"]):
        raise HTTPException(status_code=400, detail="This domain is already in use by another agency.")
        
    if not agency:
        import uuid
        agency_data = {
            "id": str(uuid.uuid4()),
            "name": "My New Agency",
            "owner_id": current_user["id"],
            "custom_domain": update_data.custom_domain
        }
        res = supabase.table("agencies").insert(agency_data).execute()
    else:
        res = supabase.table("agencies").update({"custom_domain": update_data.custom_domain}).eq("id", agency["id"]).execute()
    
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to update domain settings.")
        
    return res.data[0]

@agency_router.post("/disconnect-stripe")
async def disconnect_stripe(current_user: dict = Depends(get_current_user)):
    """Disconnect Stripe account from the agency."""
    supabase = get_admin_supabase_client()
    agency = await get_agency_by_owner(current_user["id"])
    
    # Reset Stripe fields
    res = supabase.table("agencies").update({
        "stripe_connect_id": None,
        "stripe_connected_at": None,
        "stripe_account_email": None,
        "stripe_account_status": "inactive"
    }).eq("id", agency["id"]).execute()
    
    return {"status": "success", "message": "Stripe account disconnected."}

# --- Plan Management ---

from .models import AgencyPlanCreate, AgencyPlanUpdate, AgencyPlanResponse, CustomerCreate
from typing import List

@agency_router.post("/customers")
async def create_customer(
    customer_data: CustomerCreate,
    current_user: dict = Depends(get_current_user)
):
    """Create a new customer for the agency."""
    agency = await get_agency_by_owner(current_user["id"])
    supabase = get_admin_supabase_client()
    
    # 1. Create User in Supabase Auth
    try:
        user_res = supabase.auth.admin.create_user({
            "email": customer_data.email,
            "password": customer_data.password,
            "user_metadata": {"name": customer_data.name},
            "email_confirm": True
        })
        # Note: supabase-py returns a User object or similar response
        new_user = user_res.user
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create user: {str(e)}")
        
    # 2. Add to registered_users with proper isolation
    # We can use our service or direct insert. Direct insert is clearer here for custom fields.
    try:
        supabase.table("registered_users").insert({
            "id": new_user.id,
            "email": customer_data.email,
            "name": customer_data.name,
            "approved": True,
            "agency_id": agency["id"],
            "user_type": "customer"
        }).execute()
    except Exception as e:
        # Rollback auth creation? For now just error.
        raise HTTPException(status_code=500, detail=f"Failed to register customer profile: {str(e)}")
        
    return {"message": "Customer created successfully", "id": new_user.id}

@agency_router.get("/plans", response_model=List[AgencyPlanResponse])
async def list_plans(current_user: dict = Depends(get_current_user)):
    """List all plans for the current agency."""
    agency = await get_agency_by_owner(current_user["id"])
    supabase = get_admin_supabase_client()
    res = supabase.table("agency_plans").select("*").eq("agency_id", agency["id"]).execute()
    return res.data

@agency_router.post("/plans", response_model=AgencyPlanResponse)
async def create_plan(
    plan_data: AgencyPlanCreate, 
    current_user: dict = Depends(get_current_user)
):
    """Create a new plan for the agency."""
    agency = await get_agency_by_owner(current_user["id"])
    supabase = get_admin_supabase_client()
    
    # Create Plan: Map Pydantic fields to DB columns
    payload = plan_data.dict()
    payload["agency_id"] = agency["id"]
    
    # Enforce minimum price
    from app.core.config import settings
    if payload.get("price", 0) < settings.agency_plan_min_price:
        raise HTTPException(
            status_code=400, 
            detail=f"Plan price must be at least ${settings.agency_plan_min_price}."
        )
    
    res = supabase.table("agency_plans").insert(payload).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to create agency plan.")
    return res.data[0]

@agency_router.get("/customers")
async def list_customers(current_user: dict = Depends(get_current_user)):
    """List all customers (users) for the current agency."""
    agency = await get_agency_by_owner(current_user["id"])
    supabase = get_admin_supabase_client()
    
    # Fetch users associated with this agency
    users_res = supabase.table("registered_users").select("*").eq("agency_id", agency["id"]).execute()
    users = users_res.data
    
    if not users:
        return []
        
    user_ids = [u["id"] for u in users]
    
    # Batch fetch usage balances
    balances_res = supabase.table("user_usage_balances").select("*").in_("user_id", user_ids).execute()
    balances_map = {b["user_id"]: b for b in balances_res.data}
    
    # Batch fetch agency plans to map names/prices
    plans_res = supabase.table("agency_plans").select("id, name, price").eq("agency_id", agency["id"]).execute()
    plans_map = {p["id"]: p for p in plans_res.data}
    
    # Batch fetch chatbot counts
    # Supabase doesn't support simple GroupBy in JS client easily for this, so we fetch id/user_id
    bots_res = supabase.table("chatbot_configs").select("id, user_id").in_("user_id", user_ids).execute()
    bots_map = {}
    for b in bots_res.data:
        uid = b["user_id"]
        bots_map[uid] = bots_map.get(uid, 0) + 1
        
    # Merge data
    result = []
    for u in users:
        uid = u["id"]
        bal = balances_map.get(uid, {})
        plan_id = u.get("plan_id")
        plan_info = plans_map.get(plan_id, {})
        
        # Calculate usage stats
        # Messages
        msg_used = bal.get("chatbot_messages_credits_used", 0)
        msg_total = bal.get("chatbot_messages_credits_total", 0)
        
        # Training Characters
        char_used = bal.get("chatbot_training_credits_used", 0)
        char_total = bal.get("chatbot_training_credits_total", 0)
        
        # Chatbots
        bot_count = bots_map.get(uid, 0)
        bot_limit = bal.get("chatbot_count_allowed", 1) # Default to 1 if not set
        
        u["plan_name"] = plan_info.get("name", "No Plan")
        u["plan_price"] = plan_info.get("price", 0)
        
        u["usage"] = {
            "messages": {"used": msg_used, "limit": msg_total},
            "characters": {"used": char_used, "limit": char_total},
            "chatbots": {"used": bot_count, "limit": bot_limit},
            "credits": {
                "used": bal.get("blog_creation_credits_used", 0) + bal.get("faq_credits_used", 0) + bal.get("blog_ideas_credits_used", 0),
                "limit": bal.get("blog_creation_credits_total", 0) + bal.get("faq_credits_total", 0) + bal.get("blog_ideas_credits_total", 0)
            }
        }
        u["balance_ref"] = bal
        
        result.append(u)
        
    return result

@agency_router.patch("/plans/{plan_id}", response_model=AgencyPlanResponse)
async def update_plan(
    plan_id: str,
    plan_data: AgencyPlanUpdate, 
    current_user: dict = Depends(get_current_user)
):
    """Update an existing agency plan."""
    agency = await get_agency_by_owner(current_user["id"])
    supabase = get_admin_supabase_client()
    
    # Ensure plan belongs to this agency
    plan_res = supabase.table("agency_plans").select("id").eq("id", plan_id).eq("agency_id", agency["id"]).execute()
    if not plan_res.data:
        raise HTTPException(status_code=403, detail="Not authorized to update this plan.")
        
    update_dict = plan_data.dict(exclude_unset=True)
    res = supabase.table("agency_plans").update(update_dict).eq("id", plan_id).execute()
    
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to update agency plan.")
    return res.data[0]

@agency_router.delete("/plans/{plan_id}")
async def delete_plan(
    plan_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Delete an agency plan."""
    agency = await get_agency_by_owner(current_user["id"])
    supabase = get_admin_supabase_client()
    
    # Ensure plan belongs to this agency
    res = supabase.table("agency_plans").delete().eq("id", plan_id).eq("agency_id", agency["id"]).execute()
    # Note: Supabase delete returns the deleted rows
    if not res.data:
         raise HTTPException(status_code=404, detail="Plan not found or not authorized.")
    
    return {"message": "Plan deleted successfully"}


# --- Top-Up Management ---
from .models import AgencyTopUpCreate, AgencyTopUpUpdate, AgencyTopUpResponse

@agency_router.get("/topups", response_model=List[AgencyTopUpResponse])
async def list_topups(current_user: dict = Depends(get_current_user)):
    """List all top-up packages for the current agency."""
    agency = await get_agency_by_owner(current_user["id"])
    supabase = get_admin_supabase_client()
    res = supabase.table("agency_topups").select("*").eq("agency_id", agency["id"]).execute()
    return res.data

@agency_router.post("/topups", response_model=AgencyTopUpResponse)
async def create_topup(
    topup_data: AgencyTopUpCreate,
    current_user: dict = Depends(get_current_user)
):
    """Create a new top-up package."""
    agency = await get_agency_by_owner(current_user["id"])
    supabase = get_admin_supabase_client()
    
    payload = topup_data.dict()
    payload["agency_id"] = agency["id"]
    
    res = supabase.table("agency_topups").insert(payload).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to create top-up package.")
    return res.data[0]

@agency_router.patch("/topups/{topup_id}", response_model=AgencyTopUpResponse)
async def update_topup(
    topup_id: str,
    topup_data: AgencyTopUpUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Update a top-up package."""
    agency = await get_agency_by_owner(current_user["id"])
    supabase = get_admin_supabase_client()
    
    update_dict = topup_data.dict(exclude_unset=True)
    res = supabase.table("agency_topups").update(update_dict).eq("id", topup_id).eq("agency_id", agency["id"]).execute()
    
    if not res.data:
        raise HTTPException(status_code=404, detail="Top-up not found or update failed.")
    return res.data[0]

@agency_router.delete("/topups/{topup_id}")
async def delete_topup(
    topup_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Delete a top-up package."""
    agency = await get_agency_by_owner(current_user["id"])
    supabase = get_admin_supabase_client()
    
    res = supabase.table("agency_topups").delete().eq("id", topup_id).eq("agency_id", agency["id"]).execute()
    if not res.data:
         raise HTTPException(status_code=404, detail="Top-up not found or not authorized.")
    
    return {"message": "Top-up package deleted successfully"}

@agency_router.get("/public/plans", response_model=List[AgencyPlanResponse])
async def list_public_plans(domain: str = None, agency_id: str = None):
    """
    List plans for a specific agency (Publicly accessible).
    Used by the customer-facing pricing page.
    """
    supabase = get_admin_supabase_client()
    target_agency_id = agency_id
    
    if not target_agency_id and domain:
        # Resolve domain to agency_id
        clean_domain = domain.split(":")[0]
        # Try custom domain
        res = supabase.table("agencies").select("id").eq("custom_domain", clean_domain).execute()
        if res.data:
            target_agency_id = res.data[0]["id"]
        
    if not target_agency_id:
         # If no agency found, return empty list instead of error to avoid breaking UI
         return []

    # Fetch active plans only
    res = supabase.table("agency_plans").select("*").eq("agency_id", target_agency_id).eq("is_active", True).execute()
    return res.data

from fastapi import UploadFile, File
from app.s3.s3_helper import upload_file_to_s3, generate_presigned_url

@agency_router.post("/logo/upload")
async def upload_logo(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    """Upload agency logo and update settings."""
    agency = await get_agency_by_owner(current_user["id"])
    
    file_bytes = await file.read()
    s3_key = f"agencies/{agency['id']}/logo_{file.filename}"
    
    result = upload_file_to_s3(file_bytes, s3_key, file.content_type)
    if result["status"] == "error":
        raise HTTPException(500, result["message"])
    
    # Generate a presigned URL that is valid for a long time (e.g. 1 week)
    # OR if using a public bucket, just the URL. 
    # For white-label logos, we'll use a presigned URL for now, but in prod, S3 public access is better.
    public_url = generate_presigned_url(s3_key, expires_in=604800) # 1 week
    
    supabase = get_admin_supabase_client()
    supabase.table("agencies").update({"logo_url": public_url}).eq("id", agency["id"]).execute()
    
    return {"url": public_url}

@agency_router.get("/public/topups")
async def list_public_topups(domain: str = None, agency_id: str = None):
    """
    List top-up packages for a specific agency (Publicly accessible).
    Used by the customer-facing pricing page.
    """
    supabase = get_admin_supabase_client()
    target_agency_id = agency_id
    
    if not target_agency_id and domain:
        # Resolve domain to agency_id
        clean_domain = domain.split(":")[0]
        # Try custom domain
        res = supabase.table("agencies").select("id").eq("custom_domain", clean_domain).execute()
        if res.data:
            target_agency_id = res.data[0]["id"]
        
    if not target_agency_id:
         return []

    res = supabase.table("agency_topups").select("*").eq("agency_id", target_agency_id).execute()
    return res.data

# --- White Label Analytics & Support ---

@agency_router.get("/analytics")
async def get_analytics(current_user: dict = Depends(get_current_user)):
    """Get metrics for the agency analytics dashboard."""
    agency = await get_agency_by_owner(current_user["id"])
    supabase = get_admin_supabase_client()
    
    # 1. Fetch all Customers for this agency
    users_res = supabase.table("registered_users").select("*").eq("agency_id", agency["id"]).execute()
    users = users_res.data if users_res.data else []
    user_ids = [u["id"] for u in users]
    total_customers = len(user_ids)
    
    # 2. Revenue (Aggregated from payment_history)
    payments_res = supabase.table("payment_history") \
        .select("amount, created_at") \
        .in_("user_id", user_ids) \
        .eq("status", "succeeded") \
        .execute()
    
    payments = payments_res.data if payments_res.data else []
    total_revenue = sum(p["amount"] for p in payments) / 100
    
    # 3. Pool Usage (Agency Owner's limits from Platform)
    owner_bal_res = supabase.table("user_usage_balances").select("*").eq("user_id", current_user["id"]).execute()
    owner_bal = owner_bal_res.data[0] if owner_bal_res.data else {}
    
    pool_usage = {
        "messages": {
            "total": owner_bal.get("chatbot_messages_credits_total", 0),
            "used": owner_bal.get("chatbot_messages_credits_used", 0),
            "remaining": max(0, owner_bal.get("chatbot_messages_credits_total", 0) - owner_bal.get("chatbot_messages_credits_used", 0))
        },
        "characters": {
            "total": owner_bal.get("chatbot_training_credits_total", 0),
            "used": owner_bal.get("chatbot_training_credits_used", 0),
            "remaining": max(0, owner_bal.get("chatbot_training_credits_total", 0) - owner_bal.get("chatbot_training_credits_used", 0))
        },
        "credits": {
            "total": owner_bal.get("faq_credits_total", 0) + owner_bal.get("blog_creation_credits_total", 0),
            "used": owner_bal.get("faq_credits_used", 0) + owner_bal.get("blog_creation_credits_used", 0),
            "remaining": max(0, (owner_bal.get("faq_credits_total", 0) + owner_bal.get("blog_creation_credits_total", 0)) - 
                          (owner_bal.get("faq_credits_used", 0) + owner_bal.get("blog_creation_credits_used", 0)))
        }
    }
    
    # 4. Recent Customers (Top 3)
    # Fetch plans to map names
    plans_res = supabase.table("agency_plans").select("id, name").eq("agency_id", agency["id"]).execute()
    plans_map = {p["id"]: p["name"] for p in plans_res.data}
    
    recent_customers = []
    # Sort users by created_at manually if needed, or query again
    latest_users = sorted(users, key=lambda x: x.get("created_at", ""), reverse=True)[:3]
    
    for u in latest_users:
        # Get usage for this specific customer
        u_bal_res = supabase.table("user_usage_balances").select("*").eq("user_id", u["id"]).execute()
        u_bal = u_bal_res.data[0] if u_bal_res.data else {}
        
        recent_customers.append({
            "id": u["id"],
            "email": u["email"],
            "plan": plans_map.get(u.get("plan_id"), "No Plan"),
            "status": "Active" if u.get("approved") else "Inactive",
            "usage": {
                "messages": u_bal.get("chatbot_messages_credits_used", 0),
                "messagesLimit": u_bal.get("chatbot_messages_credits_total", 0)
            },
            "created_at": u.get("created_at")
        })

    # 5. Revenue Trend (Daily)
    from collections import defaultdict
    daily_revenue = defaultdict(float)
    for p in payments:
        day = p["created_at"].split("T")[0]
        daily_revenue[day] += p["amount"] / 100
        
    revenue_chart = [{"date": d, "revenue": v} for d, v in sorted(daily_revenue.items())]

    # 6. Monthly Growth (Simplistic)
    # For now, just count users from this month vs total
    from datetime import datetime
    this_month = datetime.utcnow().strftime("%Y-%m")
    new_this_month = sum(1 for u in users if u.get("created_at", "").startswith(this_month))

    return {
        "overview": {
            "totalRevenue": total_revenue,
            "revenueGrowth": 0, # Growth requires historical comparison across months
            "activeCustomers": total_customers,
            "newThisMonth": new_this_month,
            "poolLimits": pool_usage
        },
        "revenueChart": revenue_chart,
        "recentCustomers": recent_customers,
        "upcomingBill": {
            "total": 299, # Placeholder until platform billing is integrated
            "dueDate": "Mar 1, 2026",
            "base": 299,
            "overage": 0,
            "topUps": 0
        }
    }

@agency_router.get("/customers/billing")
async def list_customer_billing(current_user: dict = Depends(get_current_user)):
    """List billing history for an agency's customers."""
    agency = await get_agency_by_owner(current_user["id"])
    supabase = get_admin_supabase_client()
    
    # 1. Fetch all User IDs and metadata for this agency
    users_res = supabase.table("registered_users") \
        .select("id, name, email") \
        .eq("agency_id", agency["id"]) \
        .execute()
    
    if not users_res.data:
        return []
        
    user_map = {u["id"]: u for u in users_res.data}
    user_ids = list(user_map.keys())
    
    # 2. Query payment_history for these users
    res = supabase.table("payment_history") \
        .select("*") \
        .in_("user_id", user_ids) \
        .order("created_at", desc=True) \
        .execute()
    
    # 3. Merge user metadata into payment records
    history = []
    for p in res.data:
        p["registered_users"] = user_map.get(p["user_id"], {})
        history.append(p)
    
    return history

@agency_router.post("/tickets")
async def submit_ticket(
    ticket: TicketCreate,
    current_user: dict = Depends(get_current_user)
):
    """Submit a support ticket."""
    agency = await get_agency_by_owner(current_user["id"], raise_error=False)
    supabase = get_admin_supabase_client()
    
    payload = ticket.dict()
    payload["sender_id"] = current_user["id"]
    if agency:
        payload["agency_id"] = agency["id"]
        
    res = supabase.table("support_tickets").insert(payload).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to submit ticket.")
        
    return res.data[0]

@agency_router.get("/tickets")
async def list_tickets(current_user: dict = Depends(get_current_user)):
    """List support tickets for the current user/agency context."""
    agency = await get_agency_by_owner(current_user["id"], raise_error=False)
    supabase = get_admin_supabase_client()
    
    # Tickets sent BY the user
    own_tickets = supabase.table("support_tickets").select("*").eq("sender_id", current_user["id"]).execute()
    
    # If agency owner, also see tickets sent TO the agency
    client_tickets = []
    if agency:
        res = supabase.table("support_tickets") \
            .select("*") \
            .eq("agency_id", agency["id"]) \
            .eq("ticket_type", "client_to_agency") \
            .execute()
        client_tickets = res.data
        
    return {
        "submitted": own_tickets.data,
        "received": client_tickets
    }
