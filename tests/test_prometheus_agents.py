"""Safety, scoring, and inference accounting tests for Prometheus model roles."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot_forum"))
from db import Database
import prometheus
import prometheus_agents as a

def example(year):
    return [{"year":year, "id":f"{year}-a{i}", "vendor":f"recipient-{i%10}",
             "amount":float(i+1), "recipient_name":f"Vendor-{i%10}",
             "recipient_uei":f"UEI-{i%10}", "award_id":f"A{i}"}
            for i in range(60)]

class AgentChallengeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "forum.sqlite3")
        self.db.initialize(allow_empty=True)
        prometheus.initialize(self.db)
        a.initialize(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def enable_owner_inference(self):
        with self.db.tx() as c:
            c.execute("INSERT OR REPLACE INTO settings VALUES('paused','false')")
            c.execute("INSERT OR REPLACE INTO settings VALUES('inference_enabled','true')")

    def test_explorer_is_strictly_schema_bound(self):
        raw = '{"metric":"hhi","direction":"increase","confidence":0.7,"rationale":"More concentrated than the prior year."}'
        self.assertEqual(a.parse_explorer(raw)["metric"], "hhi")
        with self.assertRaises(a.RoundError):
            a.parse_explorer(raw.replace('"hhi"', '"arbitrary_shell_command"'))
        with self.assertRaises(a.RoundError):
            a.parse_explorer(raw.replace("0.7", "99999"))
        with self.assertRaises(a.RoundError):
            a.parse_explorer(raw.replace('"rationale"', '"tool"'))
        with self.assertRaises(a.RoundError):
            a.parse_explorer("bad_prefix" + raw)

    def test_skeptic_must_supply_independent_probability(self):
        d = a.parse_skeptic('{"confidence":0.3,"critique":"Selection by ranked award size may bias the sample."}')
        self.assertEqual(d["confidence"], .3)
        with self.assertRaises(a.RoundError):
            a.parse_skeptic('{"confidence":-1,"critique":"Not bounded."}')

    def test_private_holdout_not_present_in_agent_prompts(self):
        e = a.metric_values(example(2024))
        explorer_prompt = a.explorer_prompt(e)
        self.assertIn("FY2024", explorer_prompt)
        self.assertNotIn("2025-a59", explorer_prompt)
        self.assertIn("SEALED", explorer_prompt)
        explorer = {"metric":"hhi","direction":"increase","confidence":.67,
                    "rationale":"Vendor concentration may persist year over year."}
        critique = a.skeptic_prompt(e, explorer)
        self.assertIn("UNTRUSTED", critique)
        self.assertNotIn("2025-a59", critique)

    def test_deterministic_score_and_baseline(self):
        x = {"metric":"hhi", "direction":"increase", "confidence":.8,
             "rationale":"Concentration grows."}
        s = {"confidence":.4, "critique":"No confidence without causal controls."}
        score = a.score(x,s,{"hhi":.1},{"hhi":.2})
        self.assertTrue(score["event_occurred"])
        self.assertAlmostEqual(score["explorer_brier"], .04)
        self.assertAlmostEqual(score["skeptic_brier"], .36)
        self.assertEqual(score["uninformed_baseline_brier"], .25)
        self.assertIn("NOT prospective", score["benchmark_type"])
        self.assertFalse(a.score(x,s,{"hhi":.2},{"hhi":.2})["event_occurred"])

    def test_original_owner_pause_blocks_model_spend(self):
        self.assertEqual(a.check_budget(self.db), "aquarium_inference_paused")
        self.enable_owner_inference()
        self.assertIsNone(a.check_budget(self.db))
        with self.db.tx() as c:
            c.execute("INSERT OR REPLACE INTO settings VALUES('paused','true')")
        self.assertEqual(a.check_budget(self.db), "aquarium_inference_paused")

    def test_environment_run_id_is_validated_without_creating_a_round(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"PROMETHEUS_AGENT_RUN_ID": "invalid!id"}):
            asyncio.run(a.bootstrap(self.db, "test-key", True))
        self.assertEqual(a.list_rounds(self.db), [])
        with patch.dict(os.environ, {"PROMETHEUS_AGENT_RUN_ID": "new-pilot-safe-01"}):
            asyncio.run(a.bootstrap(self.db, "test-key", True))
        self.assertEqual(a.public_round(self.db, "new-pilot-safe-01")["status"], "blocked")
        # An existing run is never retried by automatic startup.
        with patch.dict(os.environ, {"PROMETHEUS_AGENT_RUN_ID": "new-pilot-safe-01"}):
            asyncio.run(a.bootstrap(self.db, "test-key", True))
        self.assertEqual(len(a.list_rounds(self.db)), 1)

    def test_rounds_idempotent_and_dry_run_blocks_calls(self):
        rid = "role-test-2026"
        self.assertTrue(a.start(self.db,rid))
        self.assertFalse(a.start(self.db,rid))
        asyncio.run(a.execute(self.db,rid,"test-key",True))
        r = a.public_round(self.db,rid)
        self.assertEqual(r["status"], "blocked")
        self.assertEqual(r["error"], "dry_run_blocks_paid_inference")
        self.assertEqual(r["inference_calls"], [])

    def test_fixed_provider_request_and_accounted_cost(self):
        self.enable_owner_inference()
        rid = "role-test-inference"
        self.assertTrue(a.start(self.db,rid))
        request_urls = []
        def reply(request):
            request_urls.append(str(request.url))
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.host, "openrouter.ai")
            body = json.loads(request.content)
            self.assertEqual(body["model"], a.EXPLORER_MODEL)
            # GPT-5 mini does not accept a custom temperature. Do not send it.
            self.assertNotIn("temperature", body)
            self.assertEqual(body["max_tokens"], 750)
            self.assertFalse(body["provider"]["allow_fallbacks"])
            self.assertTrue(body["provider"]["require_parameters"])
            return httpx.Response(200, json={
                "id": "fake-generation", "model": a.EXPLORER_MODEL,
                "choices":[{"message":{"content":
                    '{"metric":"top5_share","direction":"increase","confidence":0.75,"rationale":"Large incumbents may capture more award value."}'}}],
                "usage":{"cost":.004},
            })
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
                return await a.call_agent(self.db,client,rid,"explorer",
                                          a.EXPLORER_MODEL, "FY2024-only")
        raw = asyncio.run(run())
        self.assertEqual(a.parse_explorer(raw)["metric"], "top5_share")
        self.assertEqual(len(request_urls), 1)
        recorded = a.public_round(self.db,rid)["inference_calls"]
        self.assertEqual(recorded[0]["charged_microusd"], 4000)
        self.assertEqual(recorded[0]["state"], "ACCOUNTED")
        self.assertIsNone(a.check_budget(self.db))

    def test_http_error_keeps_reserved_budget_and_halts(self):
        self.enable_owner_inference()
        rid = "test-provider-uncertain"
        self.assertTrue(a.start(self.db,rid))
        async def run():
            async with httpx.AsyncClient(
                 transport=httpx.MockTransport(lambda r: httpx.Response(502))) as client:
                return await a.call_agent(self.db,client,rid,"explorer",
                                          a.EXPLORER_MODEL, "safe")
        with self.assertRaises(a.RoundError):
            asyncio.run(run())
        self.assertEqual(a.check_budget(self.db), "lab_outstanding_inference")
        call = a.public_round(self.db,rid)["inference_calls"][0]
        self.assertEqual(call["state"], "UNCERTAIN")
        self.assertEqual(call["failure_category"], "http_status")
        self.assertEqual(call["failure_http_status"], 502)

    def test_rejected_request_logs_only_http_status_not_response_body(self):
        self.enable_owner_inference()
        rid = "rejected-provider-diagnostic"
        self.assertTrue(a.start(self.db, rid))
        def respond(req):
            return httpx.Response(400, json={"error": {
                "message": "PRIVATE! Never include the provider message.",
                "metadata": {"secret": "private-payload"}}})
        async def run():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(respond)) as client:
                return await a.call_agent(
                    self.db, client, rid, "explorer", a.EXPLORER_MODEL, "safe")
        with self.assertRaisesRegex(a.RoundError, "provider_http_400_cost_uncertain"):
            asyncio.run(run())
        recorded = a.public_round(self.db, rid)
        self.assertEqual(recorded["inference_calls"][0]["failure_http_status"], 400)
        self.assertEqual(recorded["inference_calls"][0]["failure_category"], "http_status")
        self.assertNotIn("PRIVATE", json.dumps(recorded))
        self.assertNotIn("secret", json.dumps(recorded))
        self.assertEqual(a.check_budget(self.db), "lab_outstanding_inference")

    def test_old_uncertain_record_remains_blocked_after_diagnostic_migration(self):
        rid = "old-unknown"
        self.assertTrue(a.start(self.db, rid))
        with a.prometheus.connect(self.db) as c:
            c.execute("INSERT INTO lab_agent_calls VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      ("old-call", rid, "explorer", a.EXPLORER_MODEL,
                       "UNCERTAIN", 50000, None, "prompt-hash", None, None, a.now()))
        a.initialize(self.db)
        recorded = a.public_round(self.db, rid)
        self.assertEqual(recorded["inference_calls"][0]["state"], "UNCERTAIN")
        self.assertIsNone(recorded["inference_calls"][0]["failure_http_status"])

if __name__ == "__main__":
    unittest.main()
