import sys
import os
import asyncio
from unittest.mock import MagicMock

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), "backend"))

# Mock Supabase
import app.supabase
app.supabase.get_admin_supabase_client = MagicMock()

# Mock Stripe Service load_plans
import app.payments.stripe_service
app.payments.stripe_service.load_plans = MagicMock(return_value=[
    {
        "id": "launch",
        "name": "Launch",
        "price": 39,
        "features": ["Feature 1", "Feature 2"],
        "limits": {"chatbot_count": 1, "chatbot_messages_credits": 500},
        "active": True
    }
])

from app.agency.routes import list_public_plans

async def test():
    print("Testing list_public_plans fallback...")
    # Test with no domain or unknown domain
    res = await (await list_public_plans(domain="unknown.com"))
    print(f"Result for unknown.com: {len(res)} plans found")
    if len(res) > 0:
        print(f"First plan: {res[0]['name']} (mapped from {res[0]['id']})")
        print(f"Description: {res[0]['description']}")
    else:
        print("FAILED: No plans returned")

if __name__ == "__main__":
    asyncio.run(test())
