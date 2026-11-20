"""Read-only validation of the deployed Prometheus pilot. No credentials."""
import hashlib
import json
from urllib.request import Request, urlopen

BASE = "https://heroic-nourishment-production-4815.up.railway.app"
RUN = "/api/lab/runs/pilot-hhs-2024-2025-v1"

def get(path):
    with urlopen(Request(BASE + path, headers={
        "User-Agent": "Prometheus-public-evidence-check/1.0"}), timeout=30) as resp:
        assert resp.status == 200
        raw = resp.read(2000000 + 1)
        assert len(raw) <= 2000000, "Unexpectedly large public report"
        return json.loads(raw)

def canonical(item):
    return json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

metadata = get("/api/lab/runs")
assert metadata["inference_enabled"] is False
assert metadata["experiment"]["id"] == "hhs-top-awards-concentration-v1"
data = get(RUN)
assert data["status"] == "complete", data["status"]
report = data["report"]
assert report["review"]["passed"]
assert report["review"]["independent_peer_review"] is False
assert set(report["results"]) == {"2024", "2025"}
print(json.dumps({
    "id": data["id"], "status": data["status"],
    "outcome": report["outcome"], "results": report["results"],
    "internal_checks": report["review"]["checks"],
    "created": report["generated_at"],
}, sort_keys=True))
# Validate artifact hashes against the exact public dataset.
snapshot = get(RUN + "/dataset")
raw_dataset = snapshot["dataset"]
assert set(raw_dataset) == {"2024", "2025"}
artifact = next(x for x in data["artifacts"] if x["kind"] == "dataset")
sha = hashlib.sha256(canonical(raw_dataset).encode()).hexdigest()
assert sha == artifact["sha256"], (sha, artifact["sha256"])
print(json.dumps({"dataset_hash_verified": True,
                  "award_counts": {k: len(v) for k, v in raw_dataset.items()},
                  "hash": sha}, sort_keys=True))
