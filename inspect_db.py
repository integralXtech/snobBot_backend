from app.supabase import get_admin_supabase_client
import json

def inspect_agencies_table():
    supabase = get_admin_supabase_client()
    try:
        sql = "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = 'agencies'"
        res = supabase.rpc('exec_sql', {'query': sql}).execute()
        
        if res.data:
            print("COLUMNS in agencies:")
            for row in res.data:
                nullable = "NULL" if row['is_nullable'] == 'YES' else "NOT NULL"
                print(f" - {row['column_name']} ({row['data_type']}) {nullable}")
        else:
            print("No columns found for 'agencies' in information_schema.")
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
    inspect_all_tables()
    inspect_agencies_table()
