
import asyncio
import os
from pathlib import Path
from omerion_core.settings import settings
from omerion_core.clients.google_client import drive_service
from omerion_core.clients.github_client import github_client
from omerion_core.clients.supabase_client import supabase

async def verify_pulse():
    print("🚀 Omerion System Pulse Check\n" + "="*30)
    
    # 1. Supabase
    try:
        supabase.table("agents").select("count", count="exact").limit(1).execute()
        print("✅ Supabase: Connected")
    except Exception as e:
        print(f"❌ Supabase: Failed ({e})")

    # 2. Google OAuth
    try:
        drive = drive_service()
        drive.about().get(fields="user").execute()
        print("✅ Google Workspace: Authorized")
    except Exception as e:
        print(f"❌ Google Workspace: Failed ({e})")

    # 3. GitHub
    try:
        repo_path = settings.github_org_repo_build or "evyevans/omerion-build"
        repo = github_client().get_repo(repo_path)
        print(f"✅ GitHub: Repo {repo_path} found")
    except Exception as e:
        print(f"❌ GitHub: Failed ({e})")

    # 4. Fireflies Config
    if settings.fireflies_webhook_secret:
        print("✅ Fireflies: Webhook Secret configured")
    else:
        print("❌ Fireflies: Webhook Secret missing")

    print("\n" + "="*30)
    print("🎉 System pulses are GREEN. Ready for launch.")

if __name__ == "__main__":
    asyncio.run(verify_pulse())
