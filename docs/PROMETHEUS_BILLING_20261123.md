# Nov 21 billing evidence and bounded retry

Reviewed November 23, 2026. The owner explicitly authorized reconciliation of
`retry-billing-attested-20261121-01`, preserving the original Nov 20 reservation,
followed by tests/CI and at most one fresh two-agent round if all gates pass.

## Evidence and exact scope

The supplied OpenRouter Activity screenshot shows one GPT-5 Mini request at
Nov 21, 10:15 AM EST (15:15 UTC), OpenAI provider, Prometheus key label,
281 input tokens, 704 output tokens, a displayed charge of **$0.00148**, and
finish reason `length`. Screenshot SHA256:
`17711d77091fb199b12fbc0838c82001b2ce5982f3a0bbd62071a5864c750fb4`.
Conversation reference: `6b007316-f66c-83ea-a852-4b91509ca45f`.

The live ledger identifies the sole explorer call at
`2026-11-21 15:15:32.134646`, with prompt SHA256
`6b8de1734ad3e03611ed662fd8898216e79c8a0aabd846fa8dfdc43010528603`,
50,000 microusd reserved, unknown charge, and the round's original error
`provider_cost_or_response_uncertain`. These fields, the model, role, derived
call ID, round ID and sole-call count are all required to match.

Deployment-only opt-in:
`PROMETHEUS_RECONCILE_BILLED_SCREENSHOT=openrouter_activity_screenshot_20261121_00148`.
The transaction appends an immutable evidence record containing the original
full call row and screenshot observations, then sets that call to `ACCOUNTED`
with **1,480 microusd** charged. The historical 50,000 reservation field,
failed round status, error, timestamps and prompt hash are retained. Repeating
the same action is idempotent; conflicting evidence or a different call fails.
The original Nov 20 owner-attested `UNCERTAIN` call remains unchanged and its
entire **50,000 microusd** stays in the lifetime calculation. No other unknown
charge is waived. Before a new call, the expected total is **51,480 microusd**.

This is screenshot-supported reconciliation at displayed precision, authorized
by the owner. The screenshot does not provide a generation ID, raw completion,
or reasoning-token count. It supports truncation, not a conclusive diagnosis of
whether missing cost or missing text triggered the old combined exception.

## Request and accounting corrections

- GPT-5 Mini: `max_completion_tokens=4000`, `reasoning.effort=low`.
  Low effort reduces reasoning allocation but does not guarantee visible text.
- Claude Haiku 4.5: `max_tokens=1200`; no extra reasoning budget requested.
- Both retain explicit `usage.include=true`, exact model checking, required
  parameter support, no fallback, no tools/search, no automatic paid retries.
- Provider price ceilings: $2/million input tokens, $10/million completion
  tokens, zero request surcharge. The text-only two-message payload must fit
  a conservative bound of 4,096 input tokens (UTF-8 bytes plus 512 framing
  tokens). Requests exceeding it fail before reservation or network activity.
  Maximum permitted rate calculation: **$0.048192 explorer**, **$0.020192
  skeptic**, both covered by the existing **$0.05** reservation per call.
  These bounds rely on the provider honoring token and pricing parameters;
  `max_price` by itself is a rate filter, not a total dollar limit.
- Reservations acquire a SQLite write lock before checking balance, preventing
  concurrent callers from reading the same spare budget. The persistent lab
  lifetime cap remains **$2**; actual charges and outstanding reservations count.
- The existing non-resetting provider key cap (at most $25), BYOK protection,
  parent pause and unknown-cost gates remain. Provider checks run around every
  successful paid call and require at least $0.05 remaining before it. A failed
  postflight blocks subsequent calls while retaining the actual charge.
- Known charges are recorded before validating output. Empty or truncated text
  stops the round but does not erase the bill. Missing costs remain `UNCERTAIN`.
  Observed overruns retain their full actual amount and block further calls.
- Bounded response metadata retains generation ID, normalized finish reason,
  and token counters for future diagnosis, never keys or private reasoning.

## Official references checked November 23

- [Reasoning tokens and shared completion budgets](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)
- [Chat completion parameters](https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request)
- [Provider price filtering and required parameters](https://openrouter.ai/docs/guides/routing/provider-selection)
- [Usage accounting](https://openrouter.ai/docs/guides/guides/usage-accounting)
- [Current model metadata](https://openrouter.ai/api/v1/models)

Current documentation says usage is returned automatically; explicit inclusion
is retained for compatibility. Missing cost must still fail closed. Current
model metadata lists GPT-5 Mini's low/minimal efforts and both token-limit
aliases, so the old use of `max_tokens` alone does not prove parameter rejection.

## Release procedure

Run regression tests and Linux CI before merging. Deploy with `DRY_RUN=true`,
`PROMETHEUS_AGENT_AUTORUN=false`, `AQUARIUM_RESIDENT_LOOP_DISABLED=true`,
and the exact billing evidence opt-in. Verify the public ledger and preflight,
then disable the reconciliation opt-in. Only then enable a unique one-shot
round under the owner's November 23 authorization. Restore dry-run and disable
autorun immediately after its terminal result. Do not change global owner pause,
reuse a consumed run ID, reset either database, or restart historical residents.

The two-agent result remains a single retrospective holdout demonstration,
not prospective evidence, peer review, or a Riemann Hypothesis result.
