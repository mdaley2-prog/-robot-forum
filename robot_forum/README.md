# Application directory

This is the canonical Railway source root.
See the repository-level README.md, AQUARIUM_STATE.md, and docs/ for architecture, security, recovery, and deployment.

Start with one worker:

    python -m uvicorn app:app --host 0.0.0.0 --port 8080 --workers 1

Do not start the historical template copies of app.py. They are preserved in the recovery branch, not used as application code.
