from app.supabase import get_admin_supabase_client

def run_migration():
    supabase = get_admin_supabase_client()
    with open("add_stripe_columns.sql", "r") as f:
        sql = f.read()
    
    try:
        res = supabase.rpc('exec_sql', {'query': sql}).execute()
        print("Migration successful!")
    except Exception as e:
        print(f"Migration failed: {e}")

if __name__ == "__main__":
    run_migration()
