import json
from app.supabase import get_admin_supabase_client

def print_columns(name):
    supabase = get_admin_supabase_client()
    try:
        res = supabase.table(name).select("*").limit(1).execute()
        if res.data:
            row = res.data[0]
            info = {"all_keys": list(row.keys()), "data_preview": {k: str(v) for k, v in row.items()}}
            with open("agencies_info.json", "w") as f:
                json.dump(info, f, indent=2)
            print("Info written to agencies_info.json")
        else:
            print(json.dumps({"error": f"Table '{name}' is empty."}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))

if __name__ == "__main__":
    print_columns("agencies")
