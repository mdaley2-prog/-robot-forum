"""Regression tests for the specific receipt and paid-call safety boundaries."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import sqlite3
from threading import Barrier
import unittest
from unittest.mock import patch

import httpx
import test_prometheus_agents as fixtures
import prometheus_agents as a


class BillingTests(unittest.TestCase):
    setUp = fixtures.AgentChallengeTests.setUp
    tearDown = fixtures.AgentChallengeTests.tearDown
    enable_owner_inference = fixtures.AgentChallengeTests.enable_owner_inference
    def historical_call(self):
        rid = a.BILLING_ROUND
        a.start(self.db, rid)
        a.finish(self.db, rid, "blocked", error="provider_cost_or_response_uncertain")
        callid = hashlib.sha256((rid + ":explorer").encode()).hexdigest()[:28]
        with a.prometheus.connect(self.db) as c:
            c.execute("INSERT INTO lab_agent_calls VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (callid, rid, "explorer", a.EXPLORER_MODEL, "UNCERTAIN", 50000,
                       None, a.BILLING_PROMPT_SHA256, None, None, a.BILLING_CREATED_AT))
        return callid

    def test_receipt_is_scoped_atomic_idempotent_and_immutable(self):
        self.enable_owner_inference()
        callid = self.historical_call()
        with a.prometheus.connect(self.db) as c:
            before = dict(c.execute("SELECT * FROM lab_agent_calls WHERE id=?", (callid,)).fetchone())
        for rid, evidence in (("different-run", a.BILLING_EVIDENCE_CODE), (a.BILLING_ROUND, "guess")):
            with self.assertRaises(a.RoundError):
                a.reconcile_billed_screenshot(self.db, rid, evidence)
        self.assertEqual(a.check_budget(self.db), "lab_outstanding_inference")
        self.assertEqual(a.reconcile_billed_screenshot(self.db, a.BILLING_ROUND, a.BILLING_EVIDENCE_CODE),
                         "accounted_1480_microusd_original_preserved")
        self.assertEqual(a.reconcile_billed_screenshot(self.db, a.BILLING_ROUND, a.BILLING_EVIDENCE_CODE),
                         "already_accounted_from_screenshot")
        with a.prometheus.connect(self.db) as c:
            audit = c.execute("SELECT * FROM lab_agent_billing_evidence").fetchall()
            self.assertEqual(len(audit), 1)
            self.assertEqual(json.loads(audit[0]["original_call"]), before)
            for sql in ("UPDATE lab_agent_billing_evidence SET charged_microusd=0",
                        "DELETE FROM lab_agent_billing_evidence"):
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute(sql)
        report = a.public_round(self.db, a.BILLING_ROUND)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["error"], "provider_cost_or_response_uncertain")
        call = report["inference_calls"][0]
        self.assertEqual((call["state"], call["charged_microusd"], call["reserved_microusd"]),
                         ("ACCOUNTED", 1480, 50000))
        self.assertEqual(call["billing_evidence"]["completion_tokens"], 704)
        self.assertEqual(a.budget_summary(self.db)["charged_or_reserved_microusd"], 1480)
        # Unknown charges on any other call still block.
        a.start(self.db, "another-unknown")
        other = a.reserve(self.db, "another-unknown", "explorer", a.EXPLORER_MODEL, "text")
        a.uncertain(self.db, other)
        self.assertEqual(a.check_budget(self.db), "lab_outstanding_inference")

    def test_receipt_refuses_mismatched_fingerprint(self):
        self.historical_call()
        with a.prometheus.connect(self.db) as c:
            c.execute("UPDATE lab_agent_calls SET prompt_sha256='different'")
        with self.assertRaisesRegex(a.RoundError, "billing_call_scope_mismatch"):
            a.reconcile_billed_screenshot(self.db, a.BILLING_ROUND, a.BILLING_EVIDENCE_CODE)
        self.assertEqual(a.budget_summary(self.db)["charged_or_reserved_microusd"], 50000)

    def test_nov20_attestation_and_full_reservation_survive(self):
        self.historical_call()
        a.start(self.db, a.OWNER_RECONCILIATION_ROUND)
        with a.prometheus.connect(self.db) as c:
            c.execute("INSERT INTO lab_agent_calls VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      ("original-call", a.OWNER_RECONCILIATION_ROUND, "explorer", a.EXPLORER_MODEL,
                       "UNCERTAIN", 50000, None, "original", None, None, a.now()))
        a.reconcile_owner_report(self.db, a.OWNER_RECONCILIATION_ROUND, a.OWNER_RECONCILIATION_EVIDENCE)
        before = a.public_round(self.db, a.OWNER_RECONCILIATION_ROUND)
        a.reconcile_billed_screenshot(self.db, a.BILLING_ROUND, a.BILLING_EVIDENCE_CODE)
        self.assertEqual(a.public_round(self.db, a.OWNER_RECONCILIATION_ROUND), before)
        self.assertEqual(a.budget_summary(self.db)["charged_or_reserved_microusd"], 51480)

    def test_prompt_byte_bound_rejects_before_network_or_reservation(self):
        self.enable_owner_inference()
        a.start(self.db, "large-prompt")
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda request: self.fail("No network permitted"))) as client:
                await a.call_agent(self.db, client, "large-prompt", "explorer", a.EXPLORER_MODEL, "\U0001f600" * 1500)
        with self.assertRaisesRegex(a.RoundError, "request_exceeds_per_call_budget"):
            asyncio.run(run())
        self.assertEqual(a.public_round(self.db, "large-prompt")["inference_calls"], [])
        self.assertLess(a.MAX_INPUT_TOKEN_BOUND * a.PROMPT_PRICE_PER_MILLION +
                        a.EXPLORER_COMPLETION_TOKENS * a.COMPLETION_PRICE_PER_MILLION,
                        a.RESERVED_PER_CALL)

    def test_concurrent_reservations_cannot_cross_lifetime_cap(self):
        self.enable_owner_inference()
        a.start(self.db, "budget-history")
        with a.prometheus.connect(self.db) as c:
            c.execute("INSERT INTO lab_agent_calls VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      ("spent", "budget-history", "explorer", a.EXPLORER_MODEL,
                       "ACCOUNTED", 50000, 1950000, "old", None, None, a.now()))
        for rid in ("race-one", "race-two"):
            a.start(self.db, rid)
        barrier = Barrier(2)
        original = a.check_budget
        def synchronized(db):
            result = original(db)
            barrier.wait(timeout=10)
            return result
        def reserve(rid):
            try:
                return a.reserve(self.db, rid, "explorer", a.EXPLORER_MODEL, "text")
            except a.RoundError:
                return None
        with patch.object(a, "check_budget", synchronized), ThreadPoolExecutor(2) as pool:
            results = list(pool.map(reserve, ("race-one", "race-two")))
        self.assertEqual(sum(x is not None for x in results), 1)
        self.assertEqual(a.budget_summary(self.db)["charged_or_reserved_microusd"], 2000000)

    def test_truncation_is_charged_and_never_accepted_as_complete(self):
        self.enable_owner_inference()
        a.start(self.db, "truncated-json")
        callid = a.reserve(self.db, "truncated-json", "explorer", a.EXPLORER_MODEL, "test")
        data = {"id": "gen-fixture", "model": a.EXPLORER_MODEL,
                "usage": {"cost": .00148, "prompt_tokens": 281, "completion_tokens": 704,
                          "completion_tokens_details": {"reasoning_tokens": 650}},
                "choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}
        with self.assertRaisesRegex(a.RoundError, "provider_output_truncated_cost_accounted"):
            a.account(self.db, callid, data)
        call = a.public_round(self.db, "truncated-json")["inference_calls"][0]
        self.assertEqual(call["charged_microusd"], 1480)
        self.assertEqual(call["reasoning_tokens"], 650)
        self.assertEqual(call["generation_id"], "gen-fixture")

    def test_overrun_preserves_full_actual_charge_and_blocks(self):
        self.enable_owner_inference()
        a.start(self.db, "overrun-call")
        callid = a.reserve(self.db, "overrun-call", "explorer", a.EXPLORER_MODEL, "test")
        with self.assertRaisesRegex(a.RoundError, "provider_cost_exceeded_reservation"):
            a.account(self.db, callid, {"usage": {"cost": .06}})
        self.assertEqual(a.budget_summary(self.db)["charged_or_reserved_microusd"], 60000)
        self.assertEqual(a.check_budget(self.db), "lab_outstanding_inference")

    def test_full_two_agent_round_and_preflight_for_each_call(self):
        self.enable_owner_inference()
        rid = "bounded-full-round"
        a.start(self.db, rid)
        a.prometheus.start(self.db, "pilot-hhs-2024-2025-v1")
        a.prometheus.artifact(self.db, "pilot-hhs-2024-2025-v1", "dataset",
                              {"2024": fixtures.example(2024), "2025": fixtures.example(2025)})
        requests = []
        async def request(client, method, path, **kwargs):
            requests.append((method, path))
            if path == "key":
                return {"data": {"limit": 2, "limit_remaining": 1, "limit_reset": None,
                                 "include_byok_in_limit": True}}
            payload = kwargs["json"]
            content = ({"metric": "hhi", "direction": "increase", "confidence": .6,
                        "rationale": "A test-only hypothesis."} if payload["model"] == a.EXPLORER_MODEL
                       else {"confidence": .4, "critique": "A test-only independent critique."})
            return {"model": payload["model"], "usage": {"cost": .001},
                    "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}]}
        with patch.object(a, "json_request", request):
            asyncio.run(a.execute(self.db, rid, "fixture-key", False))
        self.assertEqual(a.public_round(self.db, rid)["status"], "complete")
        self.assertEqual(requests, [("GET", "key"), ("POST", "chat/completions"), ("GET", "key")] * 2)

    def test_insufficient_provider_remaining_prevents_paid_post(self):
        self.enable_owner_inference()
        a.start(self.db, "low-credit")
        def respond(request):
            self.assertEqual(request.method, "GET")
            return httpx.Response(200, json={"data": {"limit": 2, "limit_remaining": .001,
                 "limit_reset": None, "include_byok_in_limit": True}})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                await a.checked_call(self.db, client, "low-credit", "explorer", a.EXPLORER_MODEL, "test")
        with self.assertRaisesRegex(a.RoundError, "provider_cap_not_proven"):
            asyncio.run(run())
        self.assertEqual(a.public_round(self.db, "low-credit")["inference_calls"], [])

    def test_postflight_failure_retains_charge_and_blocks_next_call(self):
        self.enable_owner_inference()
        a.start(self.db, "postflight-test")
        request_count = 0
        def respond(request):
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                return httpx.Response(200, json={"data": {"limit": 2, "limit_remaining": 1,
                    "limit_reset": None, "include_byok_in_limit": True}})
            if request_count == 2:
                return httpx.Response(200, json={"model": a.EXPLORER_MODEL,
                    "usage": {"cost": .001}, "choices": [{"finish_reason": "stop",
                    "message": {"content": "valid text"}}]})
            return httpx.Response(503)
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                await a.checked_call(self.db, client, "postflight-test", "explorer", a.EXPLORER_MODEL, "test")
        with self.assertRaisesRegex(a.RoundError, "provider_postflight_failed"):
            asyncio.run(run())
        self.assertEqual(request_count, 3)
        self.assertEqual(a.budget_summary(self.db)["charged_or_reserved_microusd"], 1000)
        self.assertEqual(a.check_budget(self.db), "lab_outstanding_inference")
