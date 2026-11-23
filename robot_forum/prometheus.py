"""Prometheus Lab: bounded, reproducible procurement research, isolated from forum data.

V0 is a *non-LLM* preregistered experiment. It deliberately makes no paid model
calls or claims of autonomous model discoveries. Public data is untrusted input.
"""
import asyncio
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import sqlite3

import httpx
from fastapi import BackgroundTasks, Depends, Header, Request
from fastapi.responses import JSONResponse

from db import now
import core

SOURCE = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
YEARS = {2024: ("2023-10-01", "2024-09-30"),
         2025: ("2024-10-01", "2025-09-30")}
FIELDS = ["Award ID", "Recipient Name", "Award Amount",
          "Awarding Agency", "Awarding Sub Agency",
          "Recipient UEI", "generated_internal_id"]
PAGES_PER_YEAR = 3
PAGE_SIZE = 100
PERMUTATIONS = 999
SEED = 20261120
STUDY = {
    "id": "hhs-top-awards-concentration-v1",
    "question": "Among the highest-value HHS prime contract awards, do award "
                "dollars concentrate by recipient more than award counts alone predict?",
    "hypothesis": "Amount-weighted HHI exceeds a null that randomly reassigns "
                  "the observed award amounts to the observed award recipients, "
                  "holding recipient award counts fixed.",
    "confirmatory_year": 2025,
    "exploratory_year": 2024,
    "threshold": 0.05,
    "seed": SEED,
    "permutations": PERMUTATIONS,
    "sampling": "The largest 300 contract awards for each fiscal year, sorted "
                "by Award Amount descending. This is a top-value cohort, NOT "
                "a representative sample of all federal contracts.",
    "source": SOURCE,
    "source_documentation": "https://api.usaspending.gov/docs/endpoints/api/search/spending_by_award/",
    "limitations": [
        "Award values are not overruns, losses, waste, fraud, or wrongdoing.",
        "A ranked top-value sample cannot represent all contracts.",
        "Recipient names can vary, merge, or differ from UEI-level vendor identities.",
        "Award data can be corrected after the retrieval date.",
        "The permutation null is descriptive, not causal; no contracting misconduct is inferred.",
        "No independent external scientist has reviewed these results."
    ],
}

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def sha(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()

def payload(year, page):
    start, end = YEARS[year]
    return {
        "filters": {
            "award_type_codes": ["A", "B", "C", "D"],
            "time_period": [{"start_date": start, "end_date": end}],
            "agencies": [{
                "type": "awarding", "tier": "toptier",
                "name": "Department of Health and Human Services"
            }],
        },
        "fields": FIELDS,
        "sort": "Award Amount",
        "order": "desc",
        "limit": PAGE_SIZE,
        "page": page,
        "subawards": False,
    }

def db_path(forum_db):
    return Path(forum_db.path).with_name("prometheus_lab.sqlite3")

@contextmanager
def connect(forum_db):
    p = db_path(forum_db)
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=15000")
    try:
        with c:
            yield c
    finally:
        c.close()

def initialize(forum_db):
    with connect(forum_db) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS lab_runs (
            id TEXT PRIMARY KEY, status TEXT NOT NULL,
            started_at TEXT NOT NULL, ended_at TEXT,
            error TEXT, report TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS lab_artifacts (
            run_id TEXT NOT NULL REFERENCES lab_runs(id),
            kind TEXT NOT NULL, sha256 TEXT NOT NULL,
            content TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, kind))""")
        c.execute("UPDATE lab_runs SET status='interrupted',ended_at=? WHERE status='running'",
                  (now(),))
    os.chmod(db_path(forum_db), 0o600)

def start(forum_db, rid):
    with connect(forum_db) as c:
        existing = c.execute("SELECT id FROM lab_runs WHERE id=?", (rid,)).fetchone()
        if existing:
            return False
        c.execute("INSERT INTO lab_runs(id,status,started_at) VALUES(?,?,?)",
                  (rid, "running", now()))
        return True

def artifact(forum_db, rid, kind, value):
    body = canonical(value)
    with connect(forum_db) as c:
        c.execute("INSERT INTO lab_artifacts VALUES (?,?,?,?,?)",
                  (rid, kind, hashlib.sha256(body.encode()).hexdigest(), body, now()))

def finish(forum_db, rid, status, report=None, error=None):
    with connect(forum_db) as c:
        c.execute("UPDATE lab_runs SET status=?,ended_at=?,report=?,error=? "
                  "WHERE id=? AND status='running'",
                  (status, now(), canonical(report) if report is not None else None,
                   error, rid))

def select_record(raw, year):
    if not isinstance(raw, dict):
        raise ValueError("Unexpected award format")
    amount = raw.get("Award Amount")
    if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
        return None
    identifier = str(raw.get("generated_internal_id") or
                     raw.get("internal_id") or raw.get("Award ID") or "").strip()
    name = str(raw.get("Recipient Name") or "").strip()
    uei = str(raw.get("Recipient UEI") or "").strip().upper()
    vendor = uei if uei not in ("", "NONE", "NULL") else name.casefold()
    if not identifier or not vendor or len(identifier) > 300 or len(vendor) > 300:
        return None
    return {"year": year, "id": identifier, "award_id": str(raw.get("Award ID") or "")[:200],
            "vendor": vendor, "recipient_name": name[:300],
            "recipient_uei": uei[:32], "amount": float(amount)}

async def fetch_year(year, client):
    rows, seen = [], set()
    for page in range(1, PAGES_PER_YEAR + 1):
        # Fixed endpoint; no redirects, no arbitrary URLs, bounded responses.
        async with client.stream("POST", SOURCE, json=payload(year, page)) as response:
            response.raise_for_status()
            if "application/json" not in response.headers.get("content-type", ""):
                raise ValueError("Non-JSON data source response")
            chunks, length = [], 0
            async for chunk in response.aiter_bytes():
                length += len(chunk)
                if length > 750000:
                    raise ValueError("Source response exceeds size limit")
                chunks.append(chunk)
        body = json.loads(b"".join(chunks))
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise ValueError("Unexpected USAspending response")
        results = body["results"]
        if len(results) > PAGE_SIZE:
            raise ValueError("Unexpected pagination size")
        for raw in results:
            item = select_record(raw, year)
            if item and item["id"] not in seen:
                rows.append(item)
                seen.add(item["id"])
        if len(results) < PAGE_SIZE:
            break
    if len(rows) < 40:
        raise ValueError("Insufficient valid awards for FY" + str(year))
    # No hidden random selection: deterministic descending source cohort.
    return rows

def hhi(rows):
    sums = defaultdict(float)
    for row in rows:
        sums[row["vendor"]] += row["amount"]
    total = sum(sums.values())
    if not total > 0:
        raise ValueError("Nonpositive award value total")
    return sum((value / total) ** 2 for value in sums.values())

def evaluate(rows, year):
    if len(rows) < 40 or len({r["vendor"] for r in rows}) < 2:
        raise ValueError("Cohort lacks statistical variation")
    actual = hhi(rows)
    vendor_ids = [r["vendor"] for r in rows]
    amounts = [r["amount"] for r in rows]
    rng = random.Random(SEED + year)
    nulls = []
    # Permute amounts only, holding vendor counts and amount distribution fixed.
    for _ in range(PERMUTATIONS):
        shuffled = amounts[:]
        rng.shuffle(shuffled)
        nulls.append(hhi([{"vendor": v, "amount": a}
                          for v, a in zip(vendor_ids, shuffled)]))
    p = (1 + sum(x >= actual - 1e-12 for x in nulls)) / (PERMUTATIONS + 1)
    expected = sum(nulls) / len(nulls)
    return {"year": year, "awards": len(rows),
            "recipients": len(set(vendor_ids)),
            "award_value_usd": round(sum(amounts), 2),
            "hhi": round(actual, 8), "null_mean_hhi": round(expected, 8),
            "excess_hhi": round(actual - expected, 8),
            "one_sided_permutation_p": round(p, 6),
            "supports_hypothesis": actual > expected and p <= STUDY["threshold"]}

def reviewer(datasets, results):
    """Independent non-LLM algorithmic checks, not peer review."""
    checks = {}
    for yr, rows in datasets.items():
        checks[str(yr)] = {
            "unique_ids": len({r["id"] for r in rows}) == len(rows),
            "nonnegative_finite_amounts": all(math.isfinite(r["amount"]) and
                                              r["amount"] >= 0 for r in rows),
            "valid_vendor_ids": all(bool(r["vendor"]) for r in rows),
            "hhi_recomputed": abs(hhi(rows) - results[yr]["hhi"]) < 0.00000002,
            "not_more_than_requested": len(rows) <= PAGE_SIZE * PAGES_PER_YEAR,
        }
    passed = all(all(v.values()) for v in checks.values())
    return {
        "type": "deterministic_internal_validation",
        "passed": passed, "checks": checks,
        "independent_peer_review": False,
        "methodology_objections": [
            "Selection by largest award may induce concentration and is not random.",
            "UEI/name grouping and award changes may affect calculated concentration.",
            "The null model controls award counts, not industry, program, or market structure.",
            "A significant result is not evidence of impropriety or a novel discovery.",
        ],
    }

def generate_report(datasets):
    results = {y: evaluate(rows, y) for y, rows in datasets.items()}
    critique = reviewer(datasets, results)
    if not critique["passed"]:
        raise ValueError("Internal replication checks failed")
    discovery = results[2024]
    replication = results[2025]
    confirmed = discovery["supports_hypothesis"] and replication["supports_hypothesis"]
    return {
        "study": STUDY, "data_sha256": sha(datasets), "results": {
            str(y): results[y] for y in sorted(results)},
        "review": critique, "outcome": (
            "same-direction signal in both cohorts" if confirmed
            else "hypothesis not supported across both cohorts"),
        "replicated_within_study": confirmed,
        "scientific_status": "bounded observational pilot; not peer-reviewed",
        "agent_roles": {
            "hypothesis": "fixed, preregistered algorithm",
            "experimenter": "deterministic permutation test",
            "skeptic": "independently recomputed internal checks",
            "human_peer_review": "not performed",
            "model_inference": "disabled; no AI-generated hypothesis or critique",
        },
        "generated_at": now(),
    }

async def execute(forum_db, rid):
    try:
        artifact(forum_db, rid, "preregistration", STUDY)
        datasets = {}
        async with httpx.AsyncClient(timeout=30, follow_redirects=False,
                                     trust_env=False) as client:
            for year in sorted(YEARS):
                datasets[year] = await fetch_year(year, client)
        normalized = {str(y): rows for y, rows in datasets.items()}
        artifact(forum_db, rid, "dataset", normalized)
        report = generate_report(datasets)
        artifact(forum_db, rid, "report", report)
        finish(forum_db, rid, "complete", report=report)
        print("PROMETHEUS_LAB_RUN " + rid + " complete " +
              report["outcome"], flush=True)
    except Exception as exc:
        # Never echo source payloads, credentials, or tracebacks publicly.
        error = type(exc).__name__ + ": " + str(exc)[:180]
        finish(forum_db, rid, "failed", error=error)
        print("PROMETHEUS_LAB_RUN " + rid + " failed " + type(exc).__name__,
              flush=True)

async def bootstrap(forum_db):
    initialize(forum_db)
    rid = "pilot-hhs-2024-2025-v1"
    if start(forum_db, rid):
        await execute(forum_db, rid)

def list_runs(forum_db):
    with connect(forum_db) as c:
        return [dict(r) for r in c.execute(
            "SELECT id,status,started_at,ended_at,error FROM lab_runs "
            "ORDER BY started_at DESC LIMIT 30")]

def read_run(forum_db, rid, include_dataset=False):
    with connect(forum_db) as c:
        row = c.execute("SELECT * FROM lab_runs WHERE id=?", (rid,)).fetchone()
        if not row:
            raise core.Rejected(404, "Research run not found")
        result = dict(row)
        result["report"] = json.loads(result["report"]) if result["report"] else None
        artifacts = c.execute("SELECT kind,sha256,created_at FROM lab_artifacts "
                              "WHERE run_id=? ORDER BY kind", (rid,))
        result["artifacts"] = [dict(r) for r in artifacts]
        if include_dataset:
            dataset = c.execute("SELECT content FROM lab_artifacts "
                                "WHERE run_id=? AND kind='dataset'", (rid,)).fetchone()
            result["dataset"] = json.loads(dataset[0]) if dataset else None
        return result

def bind(app, forum_db, owner_write, page):
    initialize(forum_db)
    from prometheus_agents import bind as bind_agent_research
    bind_agent_research(app, forum_db, owner_write)

    @app.get("/lab")
    def lab_page(request: Request):
        return page(request, "lab", lab_runs=list_runs(forum_db))

    @app.get("/api/lab/runs")
    def lab_runs_api():
        return {"experiment": STUDY, "runs": list_runs(forum_db),
                "inference_enabled": False}

    @app.get("/api/lab/runs/{rid}")
    def lab_run_api(rid: str):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{6,80}", rid):
            raise core.Rejected(404, "Research run not found")
        return read_run(forum_db, rid)

    @app.get("/api/lab/runs/{rid}/dataset")
    def lab_dataset_api(rid: str):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{6,80}", rid):
            raise core.Rejected(404, "Research run not found")
        return read_run(forum_db, rid, include_dataset=True)

    @app.post("/admin/lab/run", status_code=202)
    def lab_start(background: BackgroundTasks,
                  session=Depends(owner_write),
                  key: str = Header(alias="Idempotency-Key")):
        if not 8 <= len(key) <= 128 or not re.fullmatch(r"[a-zA-Z0-9_-]+", key):
            raise core.Rejected(422, "Use a unique 8-128 character idempotency key")
        rid = "owner-" + hashlib.sha256(key.encode()).hexdigest()[:24]
        created = start(forum_db, rid)
        if created:
            background.add_task(execute, forum_db, rid)
        return {"run_id": rid, "status_url": "/api/lab/runs/" + rid,
                "reused_existing_run": not created}

