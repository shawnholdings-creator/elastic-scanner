"""
One-time script to create the dashboard.json Gist.
Usage: python scripts/create_gist.py <GITHUB_PAT>
Prints the Gist ID to add as a GitHub Actions secret.
"""
import sys, json, requests

if len(sys.argv) < 2:
    print("Usage: python scripts/create_gist.py <GITHUB_PAT>")
    sys.exit(1)

token = sys.argv[1]

seed = {
    "timestamp": "—",
    "tickers_scanned": 0,
    "actionable_count": 0,
    "signals": [],
}

resp = requests.post(
    "https://api.github.com/gists",
    headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    },
    json={
        "description": "Elastic Scanner — AI Dashboard Feed",
        "public": False,
        "files": {
            "dashboard.json": {
                "content": json.dumps(seed, indent=2),
            }
        },
    },
    timeout=15,
)
resp.raise_for_status()
gist = resp.json()
print(f"✅ Gist created successfully!")
print(f"   Gist ID:  {gist['id']}")
print(f"   Raw URL:  {gist['files']['dashboard.json']['raw_url']}")
print(f"\nAdd these as GitHub Actions secrets in elastic-scanner repo:")
print(f"   GIST_ID    = {gist['id']}")
print(f"   GIST_TOKEN = <your PAT>")
