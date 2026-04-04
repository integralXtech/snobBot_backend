from app.supabase import get_admin_supabase_client
import json

def inspect_constraint():
    supabase = get_admin_supabase_client()
    try:
        sql = """
        SELECT pg_get_constraintdef((SELECT oid FROM pg_constraint WHERE conname = 'registered_users_user_type_check'));
        """
        res = supabase.rpc('exec_sql', {'query': sql}).execute()
        
        if res.data:
            print(f"Constraint Definition: {res.data}")
        else:
            print("Constraint not found or no data returned.")
    except Exception as e:
        print(f"Error during inspection: {e}")

if __name__ == "__main__":
    inspect_constraint()
