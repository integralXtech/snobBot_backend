"""Monthly billing and usage reset script for Snobbot White-Label SaaS."""
import sys
import os
import asyncio
from datetime import datetime

# Add app to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.supabase import get_admin_supabase_client
from app.payments.stripe_service import bill_agency_overage
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("monthly_billing")

async def process_monthly_billing():
    """
    1. Iterate through all agencies.
    2. Calculate overage charges.
    3. Generate Stripe invoices for agencies.
    4. Reset usage counters in agency_pools.
    """
    logger.info(f"Starting monthly billing process: {datetime.now().isoformat()}")
    supabase = get_admin_supabase_client()
    
    try:
        # Get all agency pools
        pools_res = supabase.table("agency_pools").select("*, agencies(id, name, base_plan_price)").execute()
        pools = pools_res.data
        
        for pool in pools:
            agency = pool["agencies"]
            agency_id = pool["agency_id"]
            
            # 1. Calculate Charges
            base_price = agency.get("base_plan_price", 0)
            
            # Overage messages charge ($0.01 per overage message)
            message_overage_charge = pool.get("overage_messages", 0) * 1 # 1 cent
            
            # Overage training charge ($0.10 per overage training)
            training_overage_charge = pool.get("overage_training", 0) * 10 # 10 cents
            
            total_charge_cents = (base_price * 100) + message_overage_charge + training_overage_charge
            
            if total_charge_cents > 0:
                logger.info(f"Billing Agency {agency['name']} ({agency_id}): {total_charge_cents} cents")
                try:
                    desc = f"Monthly Subscription + Overages ({pool['overage_messages']} msgs, {pool['overage_training']} training)"
                    await bill_agency_overage(agency_id, total_charge_cents, desc)
                except Exception as e:
                    logger.error(f"Failed to bill agency {agency_id}: {e}")
            
            # 2. Reset Counters
            logger.info(f"Resetting usage for Agency {agency_id}")
            supabase.table("agency_pools").update({
                "current_messages": 0,
                "current_training": 0,
                "overage_messages": 0,
                "overage_training": 0,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("agency_id", agency_id).execute()

        logger.info("Monthly billing process completed successfully.")

    except Exception as e:
        logger.error(f"Error in monthly billing process: {e}")

if __name__ == "__main__":
    asyncio.run(process_monthly_billing())
