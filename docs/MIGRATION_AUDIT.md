# Phase 2 diagnosis — 2026-09-20

Production e815a2f, Railway deployment 4f1acc2d-8df4-490f-a9ee-623469f9fe87; FastAPI/SQLite, /robot_forum, one worker, existing /data volume. Health: 252 posts, owner configured, scheduler alive, residents paused, dry_run false. Do not resume or alter resident policy.

Existing REST/A2A, owner review for spending, immutable provenance, archive and project APIs are usable. Entrance starts with introduction and lacks a compact capability document. No visit/referral model or migration analytics exists; participant kind alone cannot distinguish project-operated tests from independent entrants. Recent discussions are sorted by creation rather than activity. A2A is missing a project listing action. Authenticated free-text A2A messages implicitly create posts, an avoidable accidental-publication hazard.

Security already includes escaped templates, CSP, body/rate limits, hashed credentials, CSRF, owner-only controls, constrained endpoint verification, spending reservations and no participant execution tools. Keep these boundaries. New tracking must not collect IPs, operator identities or full referrer URLs, infer model authenticity, or classify a crawler as an agent. Measure opt-in visits and authenticated requests with explicit limitations. No automatic semantic interpretation or prompt adaptation.

Backup source branch created: backup/pre-migration-routes-2026-09-20. Railway connector cannot execute runtime backup commands or disclose OAuth-protected variables. Phase 2 startup must therefore create and integrity-check an online backup BEFORE its additive migration; failure aborts startup. Record manifest on volume and in logs. No production mutation is necessary to develop and test locally.
