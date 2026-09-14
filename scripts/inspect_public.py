"""Read-only public deployment probe. No authentication, secrets or raw response logging."""
import json
import re
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = "https://heroic-nourishment-production-4815.up.railway.app"

class Summary(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            if re.fullmatch(r"/(?:thread|agent)/[0-9]+", href):
                self.links.append(href)
    def handle_data(self, data):
        self.parts.append(data.strip())

for path in ["/health", "/", "/openapi.json"]:
    try:
        req = Request(BASE + path, headers={"User-Agent": "Aquarium-owner-audit/1.0"})
        with urlopen(req, timeout=20) as response:
            raw = response.read(262145)
            if len(raw) > 262144:
                raise ValueError("response_too_large")
            result = {"path": path, "status": response.status}
            if path == "/health":
                data = json.loads(raw)
                result["health"] = {k: data.get(k) for k in ("ok", "dry_run", "scheduler_alive")}
            elif path == "/":
                parser = Summary()
                parser.feed(raw.decode("utf-8"))
                result["thread_ids"] = sorted(set(x for x in parser.links if x.startswith("/thread/")))
                result["agent_ids"] = sorted(set(x for x in parser.links if x.startswith("/agent/")))
                result["paused_label_present"] = "PAUSED" in parser.parts
                result["aquarium_brand_present"] = "THE AQUARIUM" in parser.parts
            else:
                data = json.loads(raw)
                result["title"] = data.get("info", {}).get("title")
                result["paths"] = sorted(data.get("paths", {}).keys())
            print(json.dumps(result))
    except HTTPError as exc:
        print(json.dumps({"path": path, "http_status": exc.code}))
    except Exception as exc:
        print(json.dumps({"path": path, "error_type": type(exc).__name__}))
