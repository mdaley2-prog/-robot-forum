"""Owner CLI: no scheduler, API requests, or hidden migrations."""
import argparse
import json
import os
from pathlib import Path
from db import Database, digest, packed
from core import read_post

PUBLIC_TABLES = ("threads", "aq_participants", "aq_snapshots", "aq_incarnations",
                 "aq_projects", "aq_project_events", "aq_ledger", "aq_budgets", "aq_tombstones")

def verify(database):
    with database.read() as c:
        integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = len(c.execute("PRAGMA foreign_key_check").fetchall())
        previous, valid = "", True
        for r in c.execute("SELECT * FROM aq_audit ORDER BY id"):
            expected = digest(packed([previous, r["created_at"], r["actor"], r["action"], r["details"]]))
            if r["previous_hash"] != previous or r["hash"] != expected:
                valid = False
            previous = r["hash"]
        return {"integrity": integrity, "foreign_key_errors": foreign_keys, "audit_chain_valid": valid,
                "posts": c.execute("SELECT count(*) FROM posts").fetchone()[0]}

def export(database, target):
    path = Path(target)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    count = 0
    with os.fdopen(fd, "w") as out, database.read() as c:
        c.execute("BEGIN")
        for table in PUBLIC_TABLES:
            for row in c.execute("SELECT * FROM " + table):
                out.write(packed({"entity": table, "record": dict(row)}) + "\n")
                count += 1
        for row in c.execute("SELECT * FROM posts ORDER BY id"):
            out.write(packed({"entity": "posts", "record": read_post(c, row)}) + "\n")
            count += 1
    return {"records": count, "output": str(path)}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=("backup", "verify", "export"))
    p.add_argument("--database", required=True)
    p.add_argument("--output")
    args = p.parse_args()
    if not Path(args.database).is_file():
        p.error("Existing database required")
    database = Database(args.database)
    if args.action == "backup":
        result = {"backup": str(database.backup())}
    elif args.action == "verify":
        result = verify(database)
    else:
        if not args.output:
            p.error("--output required for export")
        result = export(database, args.output)
    print(json.dumps(result, sort_keys=True))
    if args.action == "verify" and (result["integrity"] != "ok" or result["foreign_key_errors"] or not result["audit_chain_valid"]):
        raise SystemExit(1)

if __name__ == "__main__":
    main()
