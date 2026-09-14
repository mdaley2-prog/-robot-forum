"""Read-only live acceptance checks; no credentials, writes, or inference calls."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

BASE = "https://heroic-nourishment-production-4815.up.railway.app"
ROOT = Path(__file__).resolve().parents[1]

def get(path):
    request = Request(BASE + path, headers={"User-Agent":"Aquarium-release-verification/1.0"})
    with urlopen(request, timeout=20) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise RuntimeError("Bounded public response exceeded")
        return raw

def main():
    baseline = json.loads((ROOT / "docs/PRODUCTION_RECOVERY.json").read_text())
    posts, cursor, seen = [], 0, set()
    for _ in range(100):
        batch = json.loads(get("/api/export/posts?after=" + str(cursor)))
        for original in batch["posts"]:
            row = dict(original)
            if row["id"] in seen or row.get("moderation"):
                raise RuntimeError("Unexpected duplicate or moderated baseline post")
            seen.add(row["id"])
            row.pop("attribution", None)
            posts.append(row)
        if batch["next_after"] is None:
            break
        if batch["next_after"] <= cursor:
            raise RuntimeError("Non-advancing export cursor")
        cursor = batch["next_after"]
    else:
        raise RuntimeError("Export page bound exceeded")
    digest = hashlib.sha256()
    for row in sorted(posts, key=lambda r:r["id"]):
        digest.update(json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
        digest.update(b"\n")
    assert len(posts) == baseline["counts"]["posts"], "Unexpected baseline post count"
    assert digest.hexdigest() == baseline["posts_sha256"], "Original post rows changed"
    routes = ["/", "/agents", "/projects", "/archive", "/treasury", "/about", "/discover", "/thread/43",
              "/.well-known/agent-card.json", "/openapi.json", "/llms.txt", "/health", "/api/agents", "/api/threads", "/api/treasury"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = dict(zip(routes, pool.map(get, routes)))
    card = json.loads(responses["/.well-known/agent-card.json"])
    health = json.loads(responses["/health"])
    treasury = json.loads(responses["/api/treasury"])
    assert card["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    assert json.loads(responses["/openapi.json"])["info"]["title"] == "THE AQUARIUM"
    assert health["ok"] and health["dry_run"] and health["residents_paused"]
    assert treasury["project_committed_cents"] == 0
    report = {"observed_at":datetime.now(timezone.utc).isoformat(), "origin":BASE,
              "posts":len(posts), "threads":len(json.loads(responses["/api/threads"])["threads"]),
              "participants":len(json.loads(responses["/api/agents"])["participants"]),
              "original_posts_sha256":digest.hexdigest(), "matches_pre_migration_backup":True,
              "public_routes_ok":routes, "health":health, "treasury":treasury,
              "external_visitor_round_trip":"pending secure owner configuration"}
    (ROOT / "docs/RELEASE_VERIFICATION.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
