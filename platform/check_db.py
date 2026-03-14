"""Quick script to inspect the PRism platform database."""
import sqlite3
import sys
import os

sys.path.insert(0, ".")

DB_PATH = "prism_platform.db"

if not os.path.exists(DB_PATH):
    print(f"Database file not found: {DB_PATH}")
    sys.exit(1)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# List tables
tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("=== TABLES ===")
for t in tables:
    print(f"  {t}")

# Check users
print("\n=== USERS ===")
users = conn.execute("SELECT * FROM users").fetchall()
if not users:
    print("  (empty - no users registered yet)")
for row in users:
    d = dict(row)
    print(f"  id:        {d['id']}")
    print(f"  username:  {d['username']}")
    print(f"  github_id: {d['github_id']}")
    print(f"  email:     {d['email'] or '(none)'}")
    print()

# Check registrations
print("=== REGISTRATIONS ===")
regs = conn.execute("SELECT * FROM registrations").fetchall()
if not regs:
    print("  (empty - no registrations yet)")
for row in regs:
    d = dict(row)
    pat_stored = "YES" if d.get("gh_pat_encrypted") else "MISSING"
    pat_preview = d["gh_pat_encrypted"][:30] + "..." if d.get("gh_pat_encrypted") else "N/A"
    ws_name = d.get("azure_workspace_name") or "(not set)"
    ws_id = d.get("azure_workspace_id") or "(not set)"
    cust_id = d.get("azure_customer_id") or "(not set)"

    print(f"  Registration: {d['id']}")
    print(f"    repo:               {d['owner']}/{d['repo']}")
    print(f"    PAT stored:         {pat_stored}")
    print(f"    PAT (encrypted):    {pat_preview}")
    print(f"    azure_workspace_id:   {ws_id}")
    print(f"    azure_workspace_name: {ws_name}")
    print(f"    azure_customer_id:    {cust_id}")
    print(f"    workflow_installed: {d['workflow_installed']}")
    print(f"    status:            {d['status']}")
    print()

# Try decrypting PATs
print("=== PAT DECRYPTION CHECK ===")
try:
    from dotenv import load_dotenv
    load_dotenv(".env")
    from server.services.auth_service import decrypt_pat

    for row in regs:
        d = dict(row)
        try:
            pat = decrypt_pat(d["gh_pat_encrypted"])
            print(f"  {d['owner']}/{d['repo']}: OK (starts with {pat[:8]}...)")
        except Exception as e:
            print(f"  {d['owner']}/{d['repo']}: DECRYPTION FAILED - {e}")
    if not regs:
        print("  (no registrations to check)")
except Exception as e:
    print(f"  Could not test decryption: {e}")

conn.close()
