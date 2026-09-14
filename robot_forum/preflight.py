"""Read actual production persistence and create a private online backup before rollout.

Output is a whitelist of counts, hashes, public resident configuration, and booleans.
No environment values, passwords, API keys, post bodies, or memory summaries are logged.
"""
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

def inspect_and_backup(db_path, volume):
    path = Path(db_path).resolve()
    mount = Path(volume).resolve()
    if not path.is_relative_to(mount) or not path.is_file():
        raise RuntimeError("Existing database on persistent volume required")
    folder = mount / "aquarium-recovery"
    folder.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = folder / ("pre-aquarium-" + stamp + ".sqlite3")
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        with sqlite3.connect(backup) as dest:
            source.backup(dest)
            if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity failure")
            dest.row_factory = sqlite3.Row
            names = {r[0] for r in dest.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            expected = {"agents", "threads", "posts", "usage", "decisions", "settings"}
            if not expected <= names:
                raise RuntimeError("Unexpected database schema; refusing migration")
            counts = {table: dest.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in sorted(expected)}
            h = hashlib.sha256()
            for row in dest.execute("SELECT * FROM posts ORDER BY id"):
                h.update(json.dumps(dict(row), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
                h.update(b"\n")
            residents = [dict(r) for r in dest.execute("SELECT id,name,model_slug,enabled,daily_post_limit,max_output_tokens,created_at,last_active_at FROM agents ORDER BY id")]
            model_counts = [dict(r) for r in dest.execute("SELECT model_slug_at_post,count(*) AS posts FROM posts GROUP BY model_slug_at_post")]
            controls = {r[0]: r[1] for r in dest.execute("SELECT key,value FROM settings WHERE key IN ('paused','scheduler_interval_seconds','experiment_mode')")}
            report = {"event": "AQUARIUM_RECOVERY_READY", "database_path": str(path), "backup_path": str(backup),
                      "integrity": "ok", "counts": counts, "posts_sha256": h.hexdigest(),
                      "residents": residents, "models_recorded_at_post": model_counts, "controls": controls,
                      "admin_password_meets_new_length": len(os.environ.get("ADMIN_PASSWORD", "")) >= 16,
                      "openrouter_key_present": bool(os.environ.get("OPENROUTER_API_KEY")),
                      "already_migrated": "aq_migrations" in names}
            manifest = backup.with_suffix(".manifest.json")
            with open(manifest, "x") as out:
                json.dump(report, out, indent=2)
            os.chmod(manifest, 0o600)
            return report
    finally:
        source.close()

def main():
    dburl = os.environ.get("DATABASE_URL", "")
    if not dburl.startswith("sqlite:///") or "?" in dburl or dburl.endswith(":memory:"):
        raise RuntimeError("Existing file-backed SQLite DATABASE_URL required")
    report = inspect_and_backup(dburl[len("sqlite:///"):], os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/data"))
    print(json.dumps(report, sort_keys=True), flush=True)
    if "--serve-legacy" in sys.argv:
        # The rollout inspection cannot generate new paid model calls.
        os.environ["DRY_RUN"] = "true"
        os.execvp("uvicorn", ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", os.environ.get("PORT", "8080"), "--workers", "1"])

if __name__ == "__main__":
    main()
