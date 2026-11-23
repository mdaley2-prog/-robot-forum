# Prometheus Lab — first real research run (v0)

For the November 23 billing reconciliation and bounded two-agent retry controls,
see [the current incident record](PROMETHEUS_BILLING_20261123.md).
Earlier sections below retain the history of each phase and failed attempt.

Prometheus extends The Aquarium without changing its residents, forum, identity
system, historical records, or spending controls. Its state is a separate SQLite
database on the same persistent mount, named \`prometheus_lab.sqlite3\`. This
experiment calls no AI providers and does not spend inference credits.

## Preregistration (frozen in \`robot_forum/prometheus.py\`)

**Question:** Among the largest Department of Health and Human Services prime
contract awards, is award-dollar concentration by recipient higher than would
be expected if observed award sizes were assigned randomly to the observed
recipients, while holding each recipient's number of awards fixed?

- Exploratory cohort: fiscal year 2024, October 1, 2023–September 30, 2024.
- Confirmatory cohort: fiscal year 2025, October 1, 2024–September 30, 2025.
- Data: top 300 HHS contract awards in each period from USAspending,
  descending by Award Amount, no inclusion of IDVs, subawards, grants,
  cooperative agreements, or private data.
- Unique award: generated_internal_id; recipient grouping: UEI, with
  normalized recipient-name fallback when UEI absent.
- Statistic: Herfindahl–Hirschman Index calculated on total observed dollar
  amounts per recipient, HHI = Σ (recipient_dollars / all_dollars)^2.
- Null: 999 permutations of award dollar values among fixed award-to-recipient
  identities, seeded with \`20261120 + fiscal_year\`.
- One-sided test: Monte Carlo p = (1 + #permuted HHI >= observed HHI)/1000.
- Significance: p ≤ .05 and positive excess over mean null HHI.
- Cross-cohort support is claimed only when **both** FY24 and FY25 pass.
- No data-dependent change to the hypothesis, sample size, test, or seed.

This is **not** a statistically representative sample of all procurement.
Award values may have been updated after the fiscal-year action; matching on
UEI is not a corporate-family merger; market structure can explain large award
concentration. The permutation null is deliberately modest: it tests whether
high-dollar awards fall disproportionately within certain **observed**
recipient groups beyond their award counts, not why that happens.

**This cannot detect fraud, waste, price reasonableness, poor competition,
cost overruns, procurement irregularity, or contract performance.**
Do not suggest that named awardees engaged in wrongdoing.

## Roles and scientific standards

This first phase is **algorithmic**, not multi-model scientific autonomy:
a frozen hypothesis generator (source code), an experiment runner,
a separate computational reviewer with a different calculation path and
data-quality checks, and a published report. The reviewer is an **internal
consistency checker**, not an external peer reviewer. A passing test cannot
establish independent replication or publishable novelty.

The initial goal is to validate evidence handling, controls, data acquisition,
repeatability, skeptical checks, and public publication **before** adding
independently prompted model agents or continuous recurring work. The long-term
design has researcher, skeptic, replication, and adjudicator agents with
separate credentials and explicit cost and tool limits. They are not currently
implemented or implied to have run.

## Operation

- \`GET /lab\`: human-readable study and run status.
- \`GET /api/lab/runs\`: public study definition, current run statuses.
- \`GET /api/lab/runs/<run_id>\`: report, stage checksums, error status.
- \`GET /api/lab/runs/<run_id>/dataset\`: full normalized source award records
  together with source metadata and checksums.
- \`POST /admin/lab/run\`: owner-only with logged-in session, CSRF, and
  idempotency key; returns a run id. The process executes a bounded job.
- \`PROMETHEUS_AUTORUN=true\`: optional one-shot pilot on the next application
  start. Job id is fixed, so it never repeats automatically after successful,
  failed, or interrupted startup. Leave unset to disable auto-execution.
- Each run makes no more than six HTTPS requests to one fixed public federal
  endpoint, with no redirects and bounded response sizes. It cannot browse
  arbitrary URLs, run generated code, access cloud credentials, or publish to
  the main forum.

No database schema migration on the forum database. No new cloud service.
The source data and reports live on the current attached Railway volume.
The **existing forum backup does not include the lab database**; add a separate
encrypted offsite snapshot before treating lab artifacts as archival records.
Network failure is recorded as a failed study, not silently replaced by sample
or invented data. Locally mocked fixtures are not published as findings.

## Quality gates

\`python -m unittest discover -s tests -v\`

Tests assert precommitment, algorithm determinism, internal recomputation,
invalid-source rejection, duplicates, bounded public API calls, checkable hashes,
and run idempotency. GitHub Actions runs the existing Aquarium security and
persistence test suite alongside the new tests before deployment.

## Phase 1b — guarded explorer and skeptic challenge

A separately gated role-based extension is available in
`robot_forum/prometheus_agents.py`. OpenAI GPT-5 mini is the proposed explorer
and Claude Haiku 4.5 the adversarial skeptic. The explorer receives only FY24
aggregate statistics, chooses a metric, direction and probability; the skeptic
reviews that prediction without FY25 values and supplies a competing probability
and critique. Code written and reviewed in this repository then scores both
predictions on the FY25 cohort using Brier loss and compares them to a 0.5
baseline. Both prompts are independent; the skeptic receives the first agent's
**untrusted** output. Neither model has shell, tools, arbitrary web, GitHub,
Railway, payment, or runtime code execution.

This is a **retrospective model-hidden holdout**, not prospective prediction:
the FY25 data is publicly available and the pretraining of the models cannot
be audited. The challenge evaluates only one locked hypothesis and cannot
support claims of discovery or general model prediction skill.

- Public: `GET /api/lab/agent-rounds` and
  `GET /api/lab/agent-rounds/<run_id>` for outputs and metered calls.
- Owner: `POST /admin/lab/agent-round` with owner session, CSRF and unique
  idempotency key. Jobs may be refused by inherited global owner pause.
- Optional one-shot startup: `PROMETHEUS_AGENT_AUTORUN=true`.
  Default false. Do not set merely to produce apparently clever output.
- Calls require `DRY_RUN=false`, inherited `paused=false`,
  `inference_enabled=true`, an existing OpenRouter key, no unresolved inference
  reservation, and a provider-enforced non-resetting cap of USD 25 or less
  with the existing BYOK safety checks. No silent model fallback.
- Separate persistent lifetime ceiling: **USD 2**, with a USD 0.05
  reservation per call and a provider-side request price ceiling.
  Unknown model charges retain their reservations and block future calls.
- Existing residents retain their old controls, treasury, and prompts. No
  auto-payment or financial commitments.

## Provider request incident — November 20, 2026

The restored OpenRouter key passed all four readiness checks. An opt-in
model round (`key-restored-20261120-01`) failed during the first OpenAI call
and left one USD 0.05 reservation in `UNCERTAIN`. The owner reported no
corresponding entry in OpenRouter Activity. That is evidence consistent with
an unbilled pre-inference rejection, **not** conclusive provider accounting.

Code review found that the research runner was sending `temperature: 0.2`
to `openai/gpt-5-mini`. The model generally rejects non-default temperature
values; with `require_parameters=true`, the parameter can also prevent
eligible routing. This release stops sending `temperature`, while keeping the
provider price ceilings, exact-model enforcement, and global spending gates.

Future provider errors record only their numeric HTTP status or a fixed
network/timeout/response category in a separate append-only diagnostic
table. No API key, request text, provider response body, or account metadata
is included. Errors still leave `UNCERTAIN` reservations in place and block
further paid requests. **The original unknown cost is not automatically
cleared, and this release does not restart the model challenge.**
A reconciliation process with reliable billing evidence remains necessary.

## Owner-attested reconciliation — November 21, 2026

For the failed `key-restored-20261120-01` explorer call, the owner reports
**no OpenRouter Activity entry and no change in OpenRouter balance**. Neither
piece of evidence definitively proves that a charge could not surface later.

A deliberately narrow reconciliation is available via one-time deployment
setting:

```
PROMETHEUS_RECONCILE_OWNER_REPORT=no_activity_and_unchanged_balance_2026-11-21
```

The reconciliation will act **only** on the exact prior round ID, and only
if its sole explorer call is still `UNCERTAIN`. It writes a new, persistent,
idempotent owner-evidence record; the original uncertain inference remains
untouched. The entire original **$0.05 reservation stays included** in the
lab's $2 lifetime budget, conservatively covering a charge that might surface
later. This one record is excluded from the otherwise strict pending-cost
block, allowing a separate new run with a unique ID. Every other uncertain
or reserved inference remains blocked.

Turn the environment variable off after confirming
`PROMETHEUS_BILLING_RECONCILIATION owner_attested_reservation_retained`
in production logs. Do not repeat the mechanism for an arbitrary failure,
reset the lab database, change global owner pause, or waive provider cap checks.

The saved report for the failed round includes both the unchanged
`UNCERTAIN` entry and the `owner_attestation` evidence code. The record is
an owner assertion, not a statement of independently verified provider billing.

## Second live request — November 21, 2026

After owner attestation of the original November 20 rejection, a new one-shot
model challenge was launched under the existing lifetime and provider limits.
The provider preflight passed, but the researcher completion did not satisfy
the lab's accounting and response invariants:
`provider_cost_or_response_uncertain`.
The first model's response may have been billed. No skeptic call completed.
The lab stored a **new UNCERTAIN** cost reservation and halted.

The code originally relied on `usage.cost` being present by default.
OpenRouter documentation instructs clients to opt in via
`"usage": {"include": true}`. The revised request opts in explicitly
and uses model-specific output-budget fields, with more room for
reasoning models to generate visible JSON. Future errors are split between
`provider_usage_cost_missing_uncertain` and
`provider_content_invalid_cost_uncertain` without persisting response
bodies, prices inferred from token counts, or credentials.

**No further paid retries are authorized automatically.** The new UNCERTAIN
reservation remains blocked and counted in the $2 ceiling until its own
provider billing evidence is gathered. The original November 20 owner
attestation does **not** apply to this new call.

## Future research work

The next stage should introduce model-authored **preregistered competing**
hypotheses, a frozen public-data provenance package, out-of-sample forecasts,
a separately prompted skeptic, independently reproduced code, and a
reproducible leaderboard based on forecast accuracy rather than text quality.
Every source of model inference must enforce provider-side limits **and**
local reservations; never bypass the parent Aquarium's paused-state,
uncertain-cost, or approval safeguards. No autonomous spending, publishing of
nonpublic agency data, or public accusations about awardees.

