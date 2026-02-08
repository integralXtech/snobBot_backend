from fastapi import APIRouter, Depends, HTTPException, status
from app.RAG.auth_utils import get_current_user
from app.supabase import get_admin_supabase_client
from .models import AgencyBrandingUpdate, AgencyDomainUpdate, AgencySettingsResponse
from typing import Dict, Any

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
    
    # We no longer need to map 'limits' dict manually because the model now has flat fields
    # matching the DB columns (limit_chatbots, etc.)
    
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
    res = supabase.table("registered_users").select("*").eq("agency_id", agency["id"]).execute()
    return res.data

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
