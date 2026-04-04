from app.supabase import get_admin_supabase_client
import json

def fetch_types():
    supabase = get_admin_supabase_client()
    try:
        res = supabase.table("registered_users").select("user_type").execute()
        if res.data:
            types = set(r['user_type'] for r in res.data if 'user_type' in r)
            print("USER_TYPES_BEGIN")
            for t in types:
                print(repr(t))
            print("USER_TYPES_END")
        else:
            print("No users found.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    fetch_types()
