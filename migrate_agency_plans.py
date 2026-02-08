
import os
import sys
from dotenv import load_dotenv
from supabase import create_client, Client

# Use the same credentials as setup_agency_subscriptions.py
SUPABASE_URL = "https://dolihvpjrbkajwkmvmcp.supabase.co"
SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRvbGlodnBqcmJrYWp3a212bWNwIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1NDczNzcwNywiZXhwIjoyMDcwMzEzNzA3fQ.rsiVrBMjCRwG6As2xdFBej2oXfZigbjfVuToyD2fhQs"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

def run_sql(sql_query):
    try:
        print(f"Executing SQL:\n{sql_query}\n")
        response = supabase.rpc('exec_sql', {'query': sql_query}).execute()
        print("Success:", response)
    except Exception as e:
        print(f"Error executing SQL via RPC: {e}")
        print("\nPLEASE RUN THIS SQL IN SUPABASE SQL EDITOR:\n")
        print("="*50)
        print(sql_query)
        print("="*50)

def migrate():
    # We will add columns if they don't exist
    sql = """
    DO $$ 
    BEGIN 
        -- Add chatbot_count if missing
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='agency_plans' AND column_name='chatbot_count') THEN
            ALTER TABLE agency_plans ADD COLUMN chatbot_count INTEGER DEFAULT 1;
        END IF;

        -- Add messages_limit if missing
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='agency_plans' AND column_name='messages_limit') THEN
            ALTER TABLE agency_plans ADD COLUMN messages_limit INTEGER DEFAULT 1000;
        END IF;

        -- Add training_chars_limit if missing
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='agency_plans' AND column_name='training_chars_limit') THEN
            ALTER TABLE agency_plans ADD COLUMN training_chars_limit INTEGER DEFAULT 100000;
        END IF;

        -- Add blog_limit if missing
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='agency_plans' AND column_name='blog_limit') THEN
            ALTER TABLE agency_plans ADD COLUMN blog_limit INTEGER DEFAULT 0;
        END IF;

        -- Add blog_ideas_limit if missing
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='agency_plans' AND column_name='blog_ideas_limit') THEN
            ALTER TABLE agency_plans ADD COLUMN blog_ideas_limit INTEGER DEFAULT 0;
        END IF;

        -- Add faq_limit if missing
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='agency_plans' AND column_name='faq_limit') THEN
            ALTER TABLE agency_plans ADD COLUMN faq_limit INTEGER DEFAULT 0;
        END IF;

        -- Add description if missing
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='agency_plans' AND column_name='description') THEN
            ALTER TABLE agency_plans ADD COLUMN description TEXT;
        END IF;

        -- Add interval if missing
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='agency_plans' AND column_name='interval') THEN
            ALTER TABLE agency_plans ADD COLUMN interval TEXT DEFAULT 'month';
        END IF;
    END $$;
    """
    
    run_sql(sql)

if __name__ == "__main__":
    migrate()
