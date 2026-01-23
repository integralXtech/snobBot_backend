import asyncio
import os
import sys
import uuid
from datetime import datetime

# Add app to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.supabase import get_admin_supabase_client
from app.helpers.agency_helper import check_usage_limits, track_and_log_usage

async def init_test_data():
    print("--- Phase 1: Test Data Initialization ---")
    supabase = get_admin_supabase_client()
    
    # 1. Get a dummy owner ID from registered_users
    users_res = supabase.table("registered_users").select("id").limit(1).execute()
    if not users_res.data:
        print("❌ No users found to act as owner/test user.")
        return None
    
    owner_id = users_res.data[0]["id"]
    
    # 2. Create Test Agency
    agency_data = {
        "owner_id": owner_id,
        "name": "Test Agency",
        "logo_url": "https://example.com/logo.png",
        "primary_color": "#FF5733",
        "secondary_color": "#C70039",
        "custom_domain": "test.localhost",
        "stripe_connect_id": "acct_test_123",
        "base_plan_price": 50 # $50 base price
    }
    
    # Upsert agency
    agency_res = supabase.table("agencies").upsert(agency_data, on_conflict="custom_domain").execute()
    agency_id = agency_res.data[0]["id"]
    print(f"✅ Agency created: {agency_id}")
    
    # 3. Create Agency Pool
    pool_data = {
        "agency_id": agency_id,
        "limit_messages": 10,
        "limit_chatbots": 2,
        "limit_training": 5,
        "current_messages": 0,
        "current_chatbots": 0,
        "current_training": 0,
        "overage_messages": 0,
        "overage_training": 0
    }
    supabase.table("agency_pools").upsert(pool_data, on_conflict="agency_id").execute()
    print("✅ Agency Pool initialized (Limit: 10).")
    
    # 4. Create/Setup Test User
    # We'll use the same user or a different one if available
    test_user_id = owner_id
    if len(users_res.data) > 1:
        test_user_id = users_res.data[1]["id"]
        
    supabase.table("registered_users").update({
        "agency_id": agency_id,
        "message_quota": 50,
        "user_type": "agency_user"
    }).eq("id", test_user_id).execute()
    print(f"✅ Test User {test_user_id} linked to Agency and quota set to 50.")
    
    # Create a dummy chatbot for the user if needed for logging
    bot_res = supabase.table("chatbot_configs").select("id").eq("user_id", test_user_id).limit(1).execute()
    if not bot_res.data:
        # Create a dummy one
        dummy_bot = {
            "user_id": test_user_id,
            "chatbot_title": "E2E Bot",
            "api_key": "sk_test_e2e_123",
            "agency_id": agency_id
        }
        bot_res = supabase.table("chatbot_configs").insert(dummy_bot).execute()
    
    chatbot_id = bot_res.data[0]["id"]
    
    return {
        "agency_id": agency_id,
        "user_id": test_user_id,
        "chatbot_id": chatbot_id
    }

async def run_scenario_tests(data):
    print("\n--- Phase 3: Resource Tracking Logic ---")
    user_id = data["user_id"]
    chatbot_id = data["chatbot_id"]
    agency_id = data["agency_id"]
    
    supabase = get_admin_supabase_client()
    
    # Reset counters first for clean test
    supabase.table("agency_pools").update({"current_messages": 0, "overage_messages": 0}).eq("agency_id", agency_id).execute()
    supabase.table("registered_users").update({"message_quota": 50}).eq("id", user_id).execute()

    print("Scenario 1: Normal Usage (5 requests)")
    for i in range(5):
        check = check_usage_limits(user_id, chatbot_id)
        if check["allowed"]:
            await track_and_log_usage(user_id, chatbot_id, "query", 1)
        else:
             print(f"❌ Blocked at request {i+1}: {check['reason']}")
    
    # Verify counters
    user_res = supabase.table("registered_users").select("message_quota").eq("id", user_id).execute()
    pool_res = supabase.table("agency_pools").select("current_messages, overage_messages").eq("agency_id", agency_id).execute()
    
    print(f"User quota left: {user_res.data[0]['message_quota']} (Expected: 45)")
    print(f"Agency usage: {pool_res.data[0]['current_messages']} (Expected: 5)")
    print(f"Agency overage: {pool_res.data[0]['overage_messages']} (Expected: 0)")

    print("\nScenario 2: Agency Overage (6 more requests, total 11)")
    for i in range(6):
        check = check_usage_limits(user_id, chatbot_id)
        if check["allowed"]:
            await track_and_log_usage(user_id, chatbot_id, "query", 1)
        else:
             print(f"❌ Blocked at request {i+6}: {check['reason']}")

    pool_res = supabase.table("agency_pools").select("current_messages, overage_messages").eq("agency_id", agency_id).execute()
    print(f"Agency usage: {pool_res.data[0]['current_messages']} (Expected: 11)")
    print(f"Agency overage: {pool_res.data[0]['overage_messages']} (Expected: 1)")

    print("\nScenario 3: User Limit Reached")
    supabase.table("registered_users").update({"message_quota": 0}).eq("id", user_id).execute()
    check = check_usage_limits(user_id, chatbot_id)
    print(f"User quota 0 check: allowed={check['allowed']}, reason={check['reason']}")
    if not check["allowed"] and "quota reached" in check["reason"].lower():
        print("✅ User limit correctly blocked.")
    else:
        print("❌ User limit failed to block.")

async def verify_reset(data):
    print("\n--- Phase 5: Verification of Reset ---")
    agency_id = data["agency_id"]
    supabase = get_admin_supabase_client()
    
    pool_res = supabase.table("agency_pools").select("*").eq("agency_id", agency_id).execute()
    pool = pool_res.data[0]
    
    if pool["current_messages"] == 0 and pool["overage_messages"] == 0:
        print("✅ Agency counters successfully reset to 0.")
    else:
        print(f"❌ Reset failed. Current: {pool['current_messages']}, Overage: {pool['overage_messages']}")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    data = loop.run_until_complete(init_test_data())
    if data:
        loop.run_until_complete(run_scenario_tests(data))
        
        # Manually trigger billing script (simulated)
        print("\n--- Phase 5: Triggering Monthly Billing ---")
        import subprocess
        subprocess.run([sys.executable, "scripts/monthly_billing.py"], check=True)
        
        loop.run_until_complete(verify_reset(data))
