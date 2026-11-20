"""Provider readiness checks never perform paid inference or expose secrets."""
import asyncio
import json
from pathlib import Path
import sys
import unittest

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot_forum"))
import prometheus_agents as agents


class ProviderPreflightTests(unittest.TestCase):
    def test_missing_key_does_not_call_provider(self):
        self.assertEqual(asyncio.run(agents.provider_preflight("")), {"status": "missing_key"})

    def test_ready_key_has_sanitized_cap_checks(self):
        captured = []
        def responder(req):
            captured.append((req.method, str(req.url)))
            return httpx.Response(200, json={"data": {
                "limit": 25, "limit_reset": None, "include_byok_in_limit": True,
                "limit_remaining": 1.00, "byok_usage": 0
            }})
        async def test():
            async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
                return await agents.provider_preflight("synthetic", client)
        output = asyncio.run(test())
        self.assertEqual(output["status"], "ready")
        self.assertEqual(captured, [("GET", "https://openrouter.ai/api/v1/key")])
        self.assertTrue(all(output["checks"].values()))
        self.assertNotIn("synthetic", json.dumps(output))
        self.assertNotIn("byok_usage", json.dumps(output))

    def test_missing_cap_refuses_to_enable(self):
        async def test():
            async with httpx.AsyncClient(transport=httpx.MockTransport(
               lambda req: httpx.Response(200, json={"data":{
                   "limit": 100, "limit_reset": "daily",
                   "include_byok_in_limit": False,
                   "limit_remaining": 10}}))) as client:
                return await agents.provider_preflight("synthetic", client)
        output = asyncio.run(test())
        self.assertEqual(output["status"], "cap_blocked")
        self.assertFalse(output["checks"]["limit_at_most_25"])

    def test_revoked_key_reports_http_status_only(self):
        async def test():
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda req: httpx.Response(401, json={
                        "error": {"message":"this is private response text"}
                    }))) as client:
                return await agents.provider_preflight("synthetic", client)
        output = asyncio.run(test())
        self.assertEqual(output, {"status": "provider_http_error", "http_status":401})

if __name__=="__main__":
    unittest.main()
