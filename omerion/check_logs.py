from omerion_core.clients.supabase_client import supabase

def check():
    print("--- RECENT RUNS ---")
    runs = supabase.table("agent_runs").select("*").order("created_at", desc=True).limit(3).execute()
    for r in runs.data:
        print(r["run_id"], r["status"], r.get("error_details"))

    print("\n--- RECENT ERRORS ---")
    errs = supabase.table("error_log").select("*").limit(3).execute()
    for e in errs.data:
        print(e)

if __name__ == "__main__":
    check()
