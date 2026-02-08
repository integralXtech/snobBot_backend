from app.supabase import get_admin_supabase_client
import json

def inspect_agency_plans():
    supabase = get_admin_supabase_client()
    try:
        # Fetch columns from information_schema for agency_plans table
        # We'll use a slightly more specific query to avoid cross-schema issues if any
        sql = "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = 'agency_plans'"
        res = supabase.rpc('exec_sql', {'query': sql}).execute()
        
        if res.data:
            print("COLUMNS in agency_plans:")
            for row in res.data:
                nullable = "NULL" if row['is_nullable'] == 'YES' else "NOT NULL"
                print(f" - {row['column_name']} ({row['data_type']}) {nullable}")
        else:
            print("No columns found for 'agency_plans' in information_schema.")
            # Standard select as backup
            res = supabase.table("agency_plans").select("*").limit(0).execute()
            # Note: execute() returns the data, but column info is hard to get if empty
            print("Table might exist but is empty and RPC 'exec_sql' failed or returned nothing.")
    except Exception as e:
        print(f"Error during inspection: {e}")

def inspect_all_tables():
    supabase = get_admin_supabase_client()
    try:
        sql = "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        res = supabase.rpc('exec_sql', {'query': sql}).execute()
        if res.data:
            print("\nPUBLIC TABLES:")
            for row in res.data:
                print(f" - {row['table_name']}")
    except Exception as e:
        print(f"Error listing tables: {e}")

if __name__ == "__main__":
    inspect_agency_plans()
