from app.supabase import get_admin_supabase_client
import uuid

def setup_dev_agency():
    supabase = get_admin_supabase_client()
    
    # 1. Get owner email
    print("\n--- White-Label Dev Setup ---")
    owner_email = input("Enter the email of the user who should own this agency (must exist in DB): ").strip()
    
    user_res = supabase.table("registered_users").select("id, email").eq("email", owner_email).execute()
    if not user_res.data:
        print(f"❌ User with email '{owner_email}' not found. Please sign up first.")
        return
        
    owner = user_res.data[0]
    print(f"👤 Using user {owner['email']} ({owner['id']}) as Agency Owner")
    
    # 2. Check if agency exists
    agency_id = "james"
    check_res = supabase.table("agencies").select("id").eq("id", agency_id).execute()
    if check_res.data:
        print(f"✅ Agency '{agency_id}' already exists in DB. Re-linking to {owner_email}...")
        supabase.table("agencies").update({"owner_id": owner["id"]}).eq("id", agency_id).execute()
        return

    # 3. Create Agency
    custom_domain = input("Enter the test domain (e.g. whitelabel-test.localhost): ").strip()
    if not custom_domain:
        custom_domain = "james.localhost"

    agency_data = {
        "id": agency_id,
        "name": "James Solutions",
        "company_name": "James Solutions",
        "owner_id": owner["id"],
        "custom_domain": custom_domain,
        "primary_color": "#2563EB",
        "secondary_color": "#7C3AED",
        "branding_settings": {}
    }
    
    insert_res = supabase.table("agencies").insert(agency_data).execute()
    if insert_res.data:
        print(f"🎉 Created Agency '{agency_id}' with ID 'james' and domain '{custom_domain}'.")
        print(f"🔗 Test via URL Param: http://localhost:5173?agency=james")
        print(f"🔗 Test via Domain:    http://{custom_domain}:5173")
    else:
        print("❌ Failed to create agency.")

if __name__ == "__main__":
    setup_dev_agency()
