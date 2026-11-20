"""Reproducible laboratory tests: no live source calls or paid inference."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot_forum"))
from db import Database
import prometheus


def sample(year, total=60):
    return [
        {"year": year, "id": f"{year}-award-{i}", "award_id": f"AWD-{i}",
         "vendor": f"uei-{i % 9}", "recipient_name": f"Vendor {i % 9}",
         "recipient_uei": f"UEI-{i % 9}",
         "amount": (i + 1) * 1000.0}
        for i in range(total)]


class LabTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.folder.name) / "forum.sqlite3")
        prometheus.initialize(self.db)

    def tearDown(self):
        self.folder.cleanup()

    def test_payload_is_fixed_hhs_and_bounded(self):
        d = prometheus.payload(2025, 2)
        self.assertEqual(d["limit"], 100)
        self.assertEqual(d["page"], 2)
        self.assertEqual(d["sort"], "Award Amount")
        self.assertEqual(d["order"], "desc")
        self.assertEqual(d["filters"]["award_type_codes"], ["A", "B", "C", "D"])
        self.assertEqual(d["filters"]["agencies"][0]["name"], "Department of Health and Human Services")
        self.assertEqual(d["filters"]["time_period"][0]["start_date"], "2024-10-01")

    def test_rejection_and_vendor_preference(self):
        self.assertIsNone(prometheus.select_record({"Award Amount": -5}, 2025))
        self.assertIsNone(prometheus.select_record({"Award Amount": float("nan")}, 2025))
        raw = {"Award Amount": 2500, "Award ID": "X", "generated_internal_id": "unique",
               "Recipient Name": "  Vendor A ", "Recipient UEI": "aBc123"}
        row = prometheus.select_record(raw, 2025)
        self.assertEqual(row["vendor"], "ABC123")
        self.assertEqual(row["id"], "unique")
        self.assertEqual(row["recipient_name"], "Vendor A")

    def test_deterministic_research_and_checks(self):
        inputs = {2024: sample(2024), 2025: sample(2025)}
        report1 = prometheus.generate_report(inputs)
        report2 = prometheus.generate_report(inputs)
        self.assertEqual(report1["results"], report2["results"])
        self.assertTrue(report1["review"]["passed"])
        self.assertFalse(report1["review"]["independent_peer_review"])
        self.assertEqual(report1["agent_roles"]["model_inference"],
                         "disabled; no AI-generated hypothesis or critique")
        self.assertTrue(all(v["awards"] == 60 for v in report1["results"].values()))
        self.assertTrue(all(v["one_sided_permutation_p"] >= .001 for v in report1["results"].values()))
        self.assertEqual(prometheus.sha({"x": [1,2]}), prometheus.sha({"x": [1,2]}))

    def test_concentration_reacts_to_vendor_values(self):
        a = [{"vendor": "a", "amount": 10}, {"vendor": "b", "amount": 10}]
        b = [{"vendor": "a", "amount": 19}, {"vendor": "b", "amount": 1}]
        self.assertAlmostEqual(prometheus.hhi(a), 0.5)
        self.assertGreater(prometheus.hhi(b), prometheus.hhi(a))
        with self.assertRaises(ValueError):
            prometheus.hhi([{"vendor":"a","amount":0}])

    def test_artifact_integrity_and_idempotent_runs(self):
        self.assertTrue(prometheus.start(self.db, "safe-id"))
        self.assertFalse(prometheus.start(self.db, "safe-id"))
        prometheus.artifact(self.db, "safe-id", "preregistration", prometheus.STUDY)
        result = prometheus.read_run(self.db, "safe-id")
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["artifacts"][0]["sha256"], prometheus.sha(prometheus.STUDY))
        prometheus.finish(self.db, "safe-id", "complete", report={"verified": True})
        result = prometheus.read_run(self.db, "safe-id")
        self.assertEqual(result["report"]["verified"], True)
        self.assertEqual(result["status"], "complete")

    def test_public_fetch_respects_fixed_method_page_and_deduplicates(self):
        requested = []
        def respond(request):
            self.assertEqual(request.url.host, "api.usaspending.gov")
            self.assertEqual(request.method, "POST")
            body = json.loads(request.content)
            requested.append(body["page"])
            if body["page"] == 1:
                rows = [{"Award Amount": float(i+1), "Recipient UEI": f"uei{i % 4}",
                         "Award ID": f"award{i}", "generated_internal_id": f"unique{i}",
                         "Recipient Name": "Example"} for i in range(100)]
            else:
                rows = [{"Award Amount": 1000.0, "Recipient UEI": "uei1",
                         "Award ID": "award1", "generated_internal_id": "unique1",
                         "Recipient Name": "Example"}] + [
                        {"Award Amount": float(i+1), "Recipient UEI": f"uei{i % 4}",
                         "Award ID": f"award{i}", "generated_internal_id": f"unique{i}",
                         "Recipient Name": "Example"} for i in range(100, 150)]
            return httpx.Response(200, json={"results":rows})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                return await prometheus.fetch_year(2025, client)
        data = asyncio.run(run())
        self.assertEqual(requested, [1, 2])
        self.assertEqual(len(data), 150)
        self.assertEqual(len(set(r["id"] for r in data)), 150)

    def test_stale_active_run_is_marked_interrupted(self):
        self.assertTrue(prometheus.start(self.db, "interrupted"))
        prometheus.initialize(self.db)
        self.assertEqual(prometheus.read_run(self.db, "interrupted")["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
