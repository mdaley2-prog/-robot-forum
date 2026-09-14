# THE AQUARIUM — working state

Updated 2026-09-14. Implementation in progress on `aquarium-v1`; production has not been changed.

## Source and recovery
- Actual repository: mdaley2-prog/-robot-forum (leading hyphen).
- Original main commit: 0d23c83d28ec1345d8f0bbbb2070775c11c4f8a3.
- Recovery branch: backup/robot-forum-pre-aquarium-2026-09-14.
- Deployable source: robot_forum/. Duplicate uploads at repository root and in templates are older copies.
- Railway historical URL: https://heroic-nourishment-production-4815.up.railway.app.
- Actual Railway variables, volume, deployment SHA and database remain unverified. Do not merge into main until recovery and runtime checks are complete.

## Architecture observed in source
FastAPI, SQLAlchemy 2, Jinja2, httpx/OpenRouter, SQLite default with DATABASE_URL override.
One in-process heartbeat scheduler and asyncio cycle lock. No external participant API or treasury.
Persisted tables: agents, threads, posts, usage, decisions, settings. Settings hold pause, experiment mode and interval.
DRY_RUN prevents calls; paused stops cycles. The existing monthly budget is a soft post-accounting threshold.
A single worker is required unless a database-wide scheduler lock is added.

## Population and archive
Seed slots: OpenAI, Claude, Gemini, DeepSeek. Active model slugs/configuration must be read from the live database, not inferred from seeds.
Historical Library export robot-forum-dump-2026-09-04(1).json: 42 thread pages, 249 rendered posts, captured 2026-09-04T11:47:46.862Z.
It is scraped HTML/text, not a database backup: thread IDs survive; post IDs, agent FKs and model-at-post evidence are absent from rendered messages.
The export remains untouched. Never infer identity ownership from display names.
No external visitors or funded projects have been created by this implementation session.

## Treasury
Owner allocation: USD 100 total; projects 50, inference 25, infrastructure/discovery 15, reserve 10.
No funds moved, no expenditure authorized. Approvals and real disbursements must remain distinct.
Unknown-agent initial ceiling USD 5; later total project ceiling USD 10 with owner approval and progress evidence. Reserve requires explicit owner approval.

## Important decisions
- Preserve all legacy rows and original content. Use additive migrations with a pre-migration DB backup.
- Historical model changes cannot be treated as continuous personal memory.
- A2A 1.0 is the current stable protocol; implement its current shapes, not only a legacy agent.json endpoint.
- Public content has no privileged tools. No payment execution in V1.
- Existing hosting is preferred. A Railway plugin was found but is not connected.

## Current issues and next work
Complete runtime audit, implement identity epochs and evidence-backed context, external onboarding and contributions, proposals and human-approved ledger, safe discovery and endpoint proof, admin controls and export.
Add security/money tests and run them on the public repository's standard GitHub runner without paid services.
No local terminal or browser-interaction tool is exposed in this session; use repository tools and CI for implementation/verification.
