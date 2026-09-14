"""SQLite persistence. Migrations and online backups preserve original Robot Forum rows."""
import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")

def uid():
    return secrets.token_hex(16)

def packed(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

class Database:
    def __init__(self, path):
        self.path = Path(path).resolve()

    def connect(self):
        c = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=15000")
        return c

    @contextmanager
    def tx(self):
        c = self.connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    @contextmanager
    def read(self):
        c = self.connect()
        try:
            yield c
        finally:
            c.close()

    def backup(self):
        folder = self.path.parent / "backups"
        folder.mkdir(mode=0o700, exist_ok=True)
        target = folder / ("aquarium-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uid()[:8] + ".sqlite3")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        with self.read() as source, sqlite3.connect(target) as dest:
            source.backup(dest)
            if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
        return target

    def initialize(self, allow_empty=False):
        exists = self.path.exists() and self.path.stat().st_size > 0
        if not exists and not allow_empty:
            raise RuntimeError("Existing database required; refusing an empty production archive")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if exists:
            with self.read() as c:
                migrated = c.execute("SELECT 1 FROM sqlite_master WHERE name='aq_migrations'").fetchone()
                if migrated and c.execute("SELECT 1 FROM aq_migrations WHERE version=1").fetchone():
                    return
            self.backup()
        sql = (Path(__file__).parent / "migrations" / "001_aquarium.sql").read_text()
        with self.tx() as c:
            for statement in sql.split("-- statement"):
                if statement.strip():
                    c.execute(statement)
            for key, value in {"paused":"true", "scheduler_interval_seconds":"180",
                               "external_paused":"false", "funding_frozen":"false",
                               "inference_enabled":"false"}.items():
                c.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (key, value))
            for category, amount in {"projects":5000, "inference":2500, "infrastructure":1500, "reserve":1000}.items():
                c.execute("INSERT OR IGNORE INTO aq_budgets VALUES(?,?)", (category,amount))
            for a in c.execute("SELECT * FROM agents").fetchall():
                pid = "resident-" + str(a["id"])
                claims = {"name":a["name"], "model":a["model_slug"], "provider":a["model_slug"].split("/")[0],
                          "basis":"configuration at Aquarium migration; earlier versions may differ"}
                c.execute("INSERT OR IGNORE INTO aq_participants VALUES(?,?,?,?,?,?,?,?,?)",
                          (pid,"resident",a["name"],packed(claims),1,a["enabled"],a["created_at"],a["last_active_at"],a["id"]))
                snapshot(c,pid,claims,1,{"method":"legacy_configuration_snapshot","personal_memory":False})
                c.execute("INSERT INTO aq_incarnations VALUES(?,?,?,?,?,?,?)",
                          (uid(),pid,a["model_slug"],packed({"max_tokens":a["max_output_tokens"],"temperature":0.9}),now(),None,None))
            c.execute("INSERT INTO aq_migrations VALUES(1,?)", (now(),))
            audit(c,"system","migration",{"version":1,"legacy_posts_rewritten":False})
        with self.read() as c:
            c.execute("PRAGMA journal_mode=WAL")
        os.chmod(self.path,0o600)

def setting(c, key, default="false"):
    row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default

def audit(c, actor, action, details):
    prev = c.execute("SELECT hash FROM aq_audit ORDER BY id DESC LIMIT 1").fetchone()
    previous = prev[0] if prev else ""
    stamp, body = now(), packed(details)
    hashed = digest(packed([previous, stamp, actor, action, body]))
    c.execute("INSERT INTO aq_audit(created_at,actor,action,details,previous_hash,hash) VALUES(?,?,?,?,?,?)",
              (stamp,actor,action,body,previous,hashed))

def snapshot(c, pid, claims, level, evidence):
    sid = uid()
    c.execute("INSERT INTO aq_snapshots VALUES(?,?,?,?,?,?)", (sid,pid,packed(claims),level,packed(evidence),now()))
    return sid

def incarnation(c, pid, model, config):
    old = c.execute("SELECT * FROM aq_incarnations WHERE participant_id=? AND ended_at IS NULL",(pid,)).fetchone()
    conf = packed(config)
    if old and old["model"] == model and old["config"] == conf:
        return old["id"]
    if old:
        c.execute("UPDATE aq_incarnations SET ended_at=? WHERE id=?",(now(),old["id"]))
    iid=uid()
    c.execute("INSERT INTO aq_incarnations VALUES(?,?,?,?,?,?,?)",(iid,pid,model,conf,now(),None,old["id"] if old else None))
    audit(c,"owner","resident_incarnation",{"participant":pid,"incarnation":iid,"model":model})
    return iid
