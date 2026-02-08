
import os
import sys
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), "chatbot-platform", ".env"))

SUPABASE_URL = os.getenv("VITE_SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

SUPABASE_URL = "https://dolihvpjrbkajwkmvmcp.supabase.co"
SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRvbGlodnBqcmJrYWp3a212bWNwIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1NDczNzcwNywiZXhwIjoyMDcwMzEzNzA3fQ.rsiVrBMjCRwG6As2xdFBej2oXfZigbjfVuToyD2fhQs"

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("Error: Supabase credentials not found in environment variables.")
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

def run_sql(sql_query):
    try:
        # Use the hidden pg_meta function or direct RPC if available
        # For simplicity in this environment, we might not have direct SQL access
        # via the Python client unless we use a specific rpc function.
        # However, we can try to use the `rpc` method if you have a `exec_sql` function setup
        # OR we can just print the SQL for the user to run if we can't execute it.
        
        # Let's try to use the REST API to check if tables exist, then create them via RPC if possible.
        # Since we don't have a guaranteed 'exec_sql' RPC, I will provide the SQL to be run 
        # via the dashboard OR use the 'rpc' method if a helper exists. 
        
        # Actually, for this environment, the most reliable way is often to use the 
        # 'postgres' connection if available, but we only have Supabase API.
        
        # Let's try to infer if we can create it using standard PostgREST calls? No.
        # We need to execute DDL.
        
        print(f"Executing SQL:\n{sql_query}\n")
        # Attempt to call a hypothetical 'exec_sql' function. 
        # If it fails, I will output the SQL for manual execution.
        response = supabase.rpc('exec_sql', {'query': sql_query}).execute()
        print("Success:", response)
    except Exception as e:
        print(f"Warning: Could not execute SQL directly via RPC. Error: {e}")
        print("\nPLEASE RUN THIS SQL IN SUPABASE SQL EDITOR:\n")
        print("="*50)
        print(sql_query)
        print("="*50)

def setup_tables():
    # 1. agency_plans (Re-creating or updating)
    # We will drop and recreate to ensure the schema is perfect for the new granular limits.
    # Note: In production, we would ALTER, but for dev specific to this task, a clean slate is safer
    # IF we are sure no important data is lost. Users authorized a clean start earlier.
    
    sql = """
    -- Create agency_plans table
    CREATE TABLE IF NOT EXISTS agency_plans (
        id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
        agency_id UUID REFERENCES agencies(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        price DECIMAL(10, 2) NOT NULL,
        currency TEXT DEFAULT 'USD',
        interval TEXT CHECK (interval IN ('month', 'year')),
        description TEXT,
        
        -- Resource Limits
        chatbot_count INTEGER DEFAULT 1,
        messages_limit INTEGER DEFAULT 1000,
        training_chars_limit INTEGER DEFAULT 100000,
        blog_limit INTEGER DEFAULT 0,
        blog_ideas_limit INTEGER DEFAULT 0,
        faq_limit INTEGER DEFAULT 0,
        
        is_active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );

    -- Create agency_topups table
    CREATE TABLE IF NOT EXISTS agency_topups (
        id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
        agency_id UUID REFERENCES agencies(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        price DECIMAL(10, 2) NOT NULL,
        credit_type TEXT CHECK (credit_type IN ('messages', 'characters', 'blogs', 'ideas', 'faqs', 'credits')),
        credit_amount INTEGER NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    -- Create agency_subscriptions table
    CREATE TABLE IF NOT EXISTS agency_subscriptions (
        id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
        customer_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
        plan_id UUID REFERENCES agency_plans(id) ON DELETE SET NULL,
        agency_id UUID REFERENCES agencies(id) ON DELETE CASCADE,
        
        status TEXT CHECK (status IN ('active', 'canceled', 'past_due', 'trialing')),
        current_period_start TIMESTAMPTZ DEFAULT NOW(),
        current_period_end TIMESTAMPTZ,
        cancel_at_period_end BOOLEAN DEFAULT FALSE,
        
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );
    
    -- Enable RLS
    ALTER TABLE agency_plans ENABLE ROW LEVEL SECURITY;
    ALTER TABLE agency_topups ENABLE ROW LEVEL SECURITY;
    ALTER TABLE agency_subscriptions ENABLE ROW LEVEL SECURITY;

    -- Basic RLS Policies (Open for now to facilitate dev, can be tightened later)
    -- Allow read access to everyone (public plans)
    CREATE POLICY "Public Read Plans" ON agency_plans FOR SELECT USING (true);
    CREATE POLICY "Public Read Topups" ON agency_topups FOR SELECT USING (true);
    
    -- Allow Agencies to Insert/Update their own data
    -- (We assume the backend handles auth mostly, but good to have)
    """
    
    run_sql(sql)

if __name__ == "__main__":
    setup_tables()
